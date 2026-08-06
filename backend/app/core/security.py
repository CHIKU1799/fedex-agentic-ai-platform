"""
Secure-by-Design auth primitives.

Deliberately dependency-free: HS256 JWTs and PBKDF2 password hashing are
implemented with the Python standard library so there is no third-party
crypto to audit or keep patched. This is an authentication + authorization
layer (OAuth2 password bearer flow) with role-based access control.

Roles
-----
- ``agent``    : FedEx support staff. May act on any shipment.
- ``customer`` : End user. May only act on their own shipments
                 (enforced by matching ``customer_id``).
"""
import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from app.core.config import settings

# tokenUrl is where clients exchange credentials for a bearer token.
# auto_error=False so anonymous access is possible where we allow it
# (e.g. public tracking) while still protecting mutating routes.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token", auto_error=False)


# ── Password hashing (PBKDF2-HMAC-SHA256) ──────────────────────────

def hash_password(password: str, *, iterations: int = 240_000) -> str:
    salt = hashlib.sha256(str(time.time_ns()).encode()).digest()[:16]
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, iters, salt_b64, hash_b64 = encoded.split("$")
        if algo != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iters))
        # Constant-time comparison — no early-exit timing leak.
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


# ── JWT (HS256) ────────────────────────────────────────────────────

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def create_access_token(*, sub: str, role: str, customer_id: Optional[str] = None) -> str:
    now = int(time.time())
    header = {"alg": settings.JWT_ALGORITHM, "typ": "JWT"}
    payload = {
        "sub": sub,
        "role": role,
        "customer_id": customer_id,
        "iat": now,
        "exp": now + settings.ACCESS_TOKEN_TTL_SECONDS,
    }
    segments = [
        _b64url(json.dumps(header, separators=(",", ":")).encode()),
        _b64url(json.dumps(payload, separators=(",", ":")).encode()),
    ]
    signing_input = ".".join(segments).encode()
    signature = hmac.new(settings.JWT_SECRET.encode(), signing_input, hashlib.sha256).digest()
    segments.append(_b64url(signature))
    return ".".join(segments)


def decode_access_token(token: str) -> dict:
    try:
        header_b64, payload_b64, sig_b64 = token.split(".")
    except ValueError:
        raise ValueError("malformed token")

    signing_input = f"{header_b64}.{payload_b64}".encode()
    expected_sig = hmac.new(settings.JWT_SECRET.encode(), signing_input, hashlib.sha256).digest()
    if not hmac.compare_digest(expected_sig, _b64url_decode(sig_b64)):
        raise ValueError("bad signature")

    payload = json.loads(_b64url_decode(payload_b64))
    if payload.get("exp", 0) < int(time.time()):
        raise ValueError("token expired")
    return payload


# ── Demo user store ────────────────────────────────────────────────
# In production this is a users table / IdP. Passwords are hashed at
# import time so no plaintext credential ever lives in the process.

@dataclass
class User:
    username: str
    role: str
    customer_id: Optional[str] = None


_DEMO_CREDENTIALS = {
    # username: (password, role, customer_id)
    "agent":  ("fedex-agent-demo", "agent", None),
    "cust001": ("cust001-demo", "customer", "CUST001"),
    "cust002": ("cust002-demo", "customer", "CUST002"),
    "cust003": ("cust003-demo", "customer", "CUST003"),
}

_USER_DB = {
    username: {
        "password_hash": hash_password(pw),
        "role": role,
        "customer_id": cid,
    }
    for username, (pw, role, cid) in _DEMO_CREDENTIALS.items()
}


def authenticate_user(username: str, password: str) -> Optional[User]:
    record = _USER_DB.get(username)
    if not record or not verify_password(password, record["password_hash"]):
        return None
    return User(username=username, role=record["role"], customer_id=record["customer_id"])


# ── FastAPI dependencies ───────────────────────────────────────────

def get_current_user_optional(token: Optional[str] = Depends(oauth2_scheme)) -> Optional[User]:
    """Returns the authenticated user, or None for anonymous requests."""
    if not token:
        return None
    try:
        payload = decode_access_token(token)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return User(
        username=payload["sub"],
        role=payload.get("role", "customer"),
        customer_id=payload.get("customer_id"),
    )


GUEST_USER = User(username="guest", role="guest", customer_id=None)


def get_effective_user(user: Optional[User] = Depends(get_current_user_optional)) -> User:
    """
    Resolve a User for endpoints that permit anonymous access (e.g. the /ask
    assistant, which must still answer public tracking questions). An
    unauthenticated caller becomes a least-privilege ``guest`` — able to track,
    but blocked from mutations by ``authorize_shipment_access``. When auth is
    globally disabled (offline demo), the caller is treated as an agent.
    """
    if user is not None:
        return user
    if not settings.REQUIRE_AUTH:
        return User(username="anonymous", role="agent")
    return GUEST_USER


def require_user(user: Optional[User] = Depends(get_current_user_optional)) -> User:
    """Require a valid bearer token when auth is enabled."""
    if not settings.REQUIRE_AUTH:
        # Auth disabled (e.g. offline demo) — treat caller as a privileged agent.
        return user or User(username="anonymous", role="agent")
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def authorize_shipment_access(user: User, customer_id: Optional[str]) -> None:
    """
    Authorization check: agents may touch any shipment; customers only
    their own. Raises 403 on violation. Central choke-point reused by
    both REST routes and the agentic tool layer.
    """
    if user.role == "agent":
        return
    if user.customer_id and customer_id and user.customer_id == customer_id:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You are not authorized to act on this shipment.",
    )
