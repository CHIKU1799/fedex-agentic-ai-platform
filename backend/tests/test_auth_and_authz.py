"""Authentication + authorization (Secure by Design)."""


def test_public_tracking_needs_no_auth(client):
    r = client.get("/track/FX100001")
    assert r.status_code == 200
    assert r.json()["status"] == "in_transit"


def test_token_rejects_bad_credentials(client):
    r = client.post("/auth/token", data={"username": "cust001", "password": "nope"})
    assert r.status_code == 401
    # Message must not reveal which field was wrong.
    assert "username or password" in r.json()["detail"].lower()


def test_mutation_requires_auth(client):
    r = client.post("/reschedule", json={"tracking_id": "FX100001", "new_date": "2026-12-01"})
    assert r.status_code == 401


def test_customer_cannot_touch_other_customers_shipment(client, cust002_auth):
    # cust002 does NOT own FX100001 (CUST001's shipment).
    r = client.post("/reschedule", headers=cust002_auth,
                    json={"tracking_id": "FX100001", "new_date": "2026-12-01"})
    assert r.status_code == 403
    # And the shipment must be untouched.
    assert client.get("/track/FX100001").json()["status"] == "in_transit"


def test_customer_can_modify_own_shipment(client, cust001_auth):
    r = client.post("/reschedule", headers=cust001_auth,
                    json={"tracking_id": "FX100001", "new_date": "2026-12-01"})
    assert r.status_code == 200
    assert r.json()["status"] == "rescheduled"
    assert client.get("/track/FX100001").json()["eta"] == "2026-12-01"


def test_agent_can_modify_any_shipment(client, agent_auth):
    r = client.post("/reschedule", headers=agent_auth,
                    json={"tracking_id": "FX100001", "new_date": "2026-12-15"})
    assert r.status_code == 200


def test_only_agent_can_create_shipment(client, cust001_auth, agent_auth):
    payload = {"tracking_id": "FX900001", "location": "X", "eta": "2026-12-01",
               "customer_id": "CUST001", "origin": "A", "destination": "B"}
    assert client.post("/shipment", headers=cust001_auth, json=payload).status_code == 403
    assert client.post("/shipment", headers=agent_auth, json=payload).status_code == 200


def test_customer_cannot_read_other_notifications(client, cust002_auth):
    r = client.get("/notifications/CUST001", headers=cust002_auth)
    assert r.status_code == 403


def test_invalid_token_rejected(client):
    r = client.post("/reschedule",
                    headers={"Authorization": "Bearer not.a.real.token"},
                    json={"tracking_id": "FX100001", "new_date": "2026-12-01"})
    assert r.status_code == 401
