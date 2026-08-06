"""Business-rule guards + input validation, exercised via the REST API."""


def test_cannot_cancel_delivered(client, agent_auth):
    r = client.post("/cancel", headers=agent_auth,
                    json={"tracking_id": "FX100003", "reason": "x"})
    assert r.status_code == 400
    assert "delivered" in r.json()["detail"].lower()


def test_cannot_redirect_delivered(client, agent_auth):
    r = client.post("/redirect", headers=agent_auth,
                    json={"tracking_id": "FX100003", "new_address": "123 New St, Austin, TX"})
    assert r.status_code == 400


def test_cancel_then_cannot_reschedule(client, agent_auth):
    assert client.post("/cancel", headers=agent_auth,
                       json={"tracking_id": "FX100005"}).status_code == 200
    r = client.post("/reschedule", headers=agent_auth,
                    json={"tracking_id": "FX100005", "new_date": "2026-12-01"})
    assert r.status_code == 400


def test_cancel_creates_notification(client, agent_auth):
    client.post("/cancel", headers=agent_auth, json={"tracking_id": "FX100005", "reason": "test"})
    r = client.get("/notifications/CUST003", headers=agent_auth)
    assert r.status_code == 200
    msgs = [n["message"] for n in r.json()["notifications"]]
    assert any("cancelled" in m.lower() for m in msgs)


def test_invalid_date_rejected(client, agent_auth):
    r = client.post("/reschedule", headers=agent_auth,
                    json={"tracking_id": "FX100001", "new_date": "next tuesday"})
    assert r.status_code == 422  # pydantic validation


def test_invalid_tracking_id_rejected(client, agent_auth):
    r = client.post("/cancel", headers=agent_auth, json={"tracking_id": "'; DROP TABLE"})
    assert r.status_code == 422


def test_track_unknown_is_404(client):
    assert client.get("/track/FX000000").status_code == 404
