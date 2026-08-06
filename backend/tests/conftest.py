"""
Shared pytest fixtures. Environment is configured BEFORE any app import so
config/database bind to an isolated test SQLite file with a fixed JWT secret
and a rate limit high enough not to interfere with functional tests.
"""
import os

# Must be set before importing app.* (config reads env at import time).
os.environ["DATABASE_URL"] = "sqlite:///./test_fedex.db"
os.environ["REQUIRE_AUTH"] = "true"
os.environ["JWT_SECRET"] = "test-secret-fixed-value"
os.environ["ENV"] = "development"
os.environ["RATE_LIMIT_REQUESTS"] = "100000"
os.environ["OPENAI_API_KEY"] = ""  # default: offline/fallback unless a test injects a fake client

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db.database import Base, engine, SessionLocal
from app.db.models import Shipment, Notification


@pytest.fixture(autouse=True)
def fresh_db():
    """Recreate the schema and seed a known fixture set before each test."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.add_all([
        Shipment(tracking_id="FX100001", status="in_transit", location="Memphis, TN",
                 eta="2026-06-02", customer_id="CUST001", origin="LA", destination="NY"),
        Shipment(tracking_id="FX100003", status="delivered", location="Dallas, TX",
                 eta="2026-05-25", customer_id="CUST002", origin="Miami", destination="Dallas"),
        Shipment(tracking_id="FX100005", status="created", location="Atlanta, GA",
                 eta="2026-06-10", customer_id="CUST003", origin="Atlanta", destination="Portland"),
        Notification(id="n1", customer_id="CUST001", tracking_id="FX100001",
                     message="In transit.", notification_type="eta_update", is_read="false"),
    ])
    db.commit()
    db.close()
    yield


@pytest.fixture
def client():
    return TestClient(app)


def _token(client, username, password):
    resp = client.post("/auth/token", data={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.fixture
def agent_auth(client):
    return {"Authorization": f"Bearer {_token(client, 'agent', 'fedex-agent-demo')}"}


@pytest.fixture
def cust001_auth(client):
    return {"Authorization": f"Bearer {_token(client, 'cust001', 'cust001-demo')}"}


@pytest.fixture
def cust002_auth(client):
    return {"Authorization": f"Bearer {_token(client, 'cust002', 'cust002-demo')}"}
