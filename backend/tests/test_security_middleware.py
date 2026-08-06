"""Security headers, CORS allow-list, and rate limiting."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import config
from app.core.middleware import RateLimitMiddleware, SecurityHeadersMiddleware


def test_security_headers_present(client):
    r = client.get("/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "Content-Security-Policy" in r.headers


def test_cors_is_not_wildcard():
    from app.core.config import settings
    assert "*" not in settings.ALLOWED_ORIGINS
    assert "http://localhost:4200" in settings.ALLOWED_ORIGINS


def test_disallowed_origin_not_reflected(client):
    r = client.get("/health", headers={"Origin": "https://evil.example.com"})
    # Starlette omits the ACAO header for origins not on the allow-list.
    assert r.headers.get("access-control-allow-origin") != "https://evil.example.com"


def test_rate_limit_returns_429(monkeypatch):
    # Isolated app + low limit so we don't affect the shared client's bucket.
    monkeypatch.setattr(config.settings, "RATE_LIMIT_REQUESTS", 3)
    monkeypatch.setattr(config.settings, "RATE_LIMIT_WINDOW_SECONDS", 60)

    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)

    @app.get("/ping")
    def ping():
        return {"ok": True}

    c = TestClient(app)
    codes = [c.get("/ping").status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200]
    assert 429 in codes[3:]


def test_health_exempt_from_rate_limit(monkeypatch):
    monkeypatch.setattr(config.settings, "RATE_LIMIT_REQUESTS", 1)

    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    c = TestClient(app)
    assert all(c.get("/health").status_code == 200 for _ in range(5))
