"""
Vercel serverless entrypoint.

Wraps the FastAPI backend for Vercel's Python runtime. The frontend is
served statically; every /api/* request is rewritten to this function
(see vercel.json), so a small ASGI shim strips the /api prefix before
handing the request to the app, whose routes are defined without it.

Serverless notes:
- The deployment bundle is read-only; SQLite lives in /tmp. Each cold
  start gets a fresh database, re-seeded with the demo fixtures. Demo
  state persists per warm instance, which is exactly what a judged demo
  needs and nothing more.
- Barcode/OCR native libs are not installed here; those imports are
  guarded and /scan degrades gracefully.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

# Must be set before app.core.config is imported anywhere.
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/fedex.db")

from app.main import app as fastapi_app  # noqa: E402

try:
    # Idempotent demo fixtures (shipments, notifications, demo users).
    import seed_data  # noqa: F401,E402
except Exception as exc:  # never let seeding break the function
    print(f"Seed skipped: {exc}")


class ApiPrefixStripper:
    """Remove the /api prefix Vercel's rewrite leaves on the path."""

    def __init__(self, app, prefix="/api"):
        self.app = app
        self.prefix = prefix

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("path", "").startswith(self.prefix):
            scope = dict(scope)
            scope["path"] = scope["path"][len(self.prefix):] or "/"
            scope["raw_path"] = scope["path"].encode()
        await self.app(scope, receive, send)


app = ApiPrefixStripper(fastapi_app)
