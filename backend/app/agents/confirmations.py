"""
Platform-held confirmations for high-impact (autonomy tier 3) actions.

The model never gets to decide that the customer confirmed. When a tier-3
tool is refused, the platform remembers what was asked (per user + session).
The confirmation is granted only when the customer's OWN next message says
yes, and only for that exact tool and shipment. Anything else clears it.
Policy lives here, in code, so a jailbroken or over-eager model cannot
pass ``confirmed=true`` and skip the customer.
"""
import re
import time
from collections import OrderedDict
from typing import Optional

TTL_SECONDS = 15 * 60
MAX_PENDING = 500

_pending: "OrderedDict[str, dict]" = OrderedDict()

_YES = re.compile(
    r"^\s*(yes|yeah|yep|yup|sure|ok(ay)?|confirm(ed)?|i confirm|go ahead|do it|proceed|please do|"
    r"affirmative|correct|that'?s right)\b|\b(i confirm|go ahead|please proceed|yes,? (cancel|redirect|do it|go ahead))\b",
    re.I,
)
_NO = re.compile(r"\b(no|nope|don'?t|do not|never ?mind|cancel that|stop|leave it|keep it|not now)\b", re.I)


def session_key(username: str, session_id: str) -> str:
    """Confirmations and history are scoped to the signed-in user AND the
    client session, so one user can never confirm or read another's."""
    return f"{username or 'anon'}|{session_id or '-'}"


def remember(key: str, tool: str, tracking_id: str) -> None:
    _pending[key] = {"tool": tool, "tracking_id": (tracking_id or "").upper(), "at": time.time()}
    _pending.move_to_end(key)
    while len(_pending) > MAX_PENDING:
        _pending.popitem(last=False)


def pending(key: str) -> Optional[dict]:
    p = _pending.get(key)
    if not p:
        return None
    if time.time() - p["at"] > TTL_SECONDS:
        _pending.pop(key, None)
        return None
    return dict(p)


def clear(key: str) -> None:
    _pending.pop(key, None)


def customer_answer(text: str) -> Optional[bool]:
    """True for an explicit yes, False for a no, None when it is neither."""
    t = (text or "").strip()
    if _NO.search(t) and not _YES.search(t):
        return False
    if _YES.search(t):
        return True
    return None


def resolve(key: str, query: str) -> Optional[dict]:
    """Consume the pending ask for this turn. Returns the grant (tool +
    tracking_id) only when the customer explicitly said yes; any other
    message clears the pending ask, because the customer moved on."""
    p = pending(key)
    if not p:
        return None
    clear(key)
    return p if customer_answer(query) is True else None


def reset() -> None:
    _pending.clear()
