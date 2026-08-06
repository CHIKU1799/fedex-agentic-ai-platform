"""
Secure-by-Design HTTP middleware: security headers + lightweight,
in-memory, per-IP rate limiting (abuse / brute-force / LLM-cost control).

The rate limiter is a fixed-window counter kept in-process. For a single
demo node this is sufficient; in production this moves to Redis (already
provisioned in docker-compose) so limits are shared across replicas.
"""
import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.config import settings

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "X-XSS-Protection": "0",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    # HSTS only meaningful over HTTPS; safe to advertise for prod termination.
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        for header, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self._hits = defaultdict(list)  # client_ip -> [timestamps]

    def _client_ip(self, request: Request) -> str:
        # Honor a single reverse-proxy hop; fall back to socket peer.
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    async def dispatch(self, request: Request, call_next):
        # Never rate-limit health checks or CORS preflight.
        if request.method == "OPTIONS" or request.url.path == "/health":
            return await call_next(request)

        ip = self._client_ip(request)
        now = time.time()
        window = settings.RATE_LIMIT_WINDOW_SECONDS
        bucket = self._hits[ip]
        # Drop timestamps outside the current window.
        cutoff = now - window
        bucket[:] = [t for t in bucket if t > cutoff]

        if len(bucket) >= settings.RATE_LIMIT_REQUESTS:
            retry_after = int(window - (now - bucket[0])) + 1
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Please slow down."},
                headers={"Retry-After": str(retry_after)},
            )

        bucket.append(now)
        return await call_next(request)
