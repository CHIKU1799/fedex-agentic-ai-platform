"""
Weather agentic feature tests.

The Open-Meteo HTTP layer is stubbed at the weather_service boundary so
tests are deterministic and offline. Covers: the pure risk classifier, the
ops-layer proactive notification (creation, dedupe, inactive-shipment
guard), the public REST route, tool dispatch, and the full agentic loop
calling check_weather_impact.
"""
import json

import pytest

import app.agents.planner_agent as planner
from app.services import shipment_ops, weather_service
from fakes import FakeAnthropic, msg, tool_call


def _report(risk="low", **overrides):
    base = {
        "available": True,
        "location": "New York",
        "country": "United States",
        "forecast_date": "2026-06-02",
        "eta": "2026-06-02",
        "eta_within_forecast": True,
        "risk": risk,
        "conditions": "clear sky" if risk == "low" else "heavy snowfall",
        "temperature_max_c": 5.0,
        "temperature_min_c": -2.0,
    }
    base.update(overrides)
    return base


@pytest.fixture
def stub_weather(monkeypatch):
    """Patch the forecast pipeline; returns a setter for the canned report."""
    state = {"report": _report()}

    def set_report(report):
        state["report"] = report

    monkeypatch.setattr(
        shipment_ops.weather_service, "destination_weather",
        lambda destination, eta: state["report"],
    )
    return set_report


# ── Pure risk classifier ──

def test_assess_day_clear_is_low_risk():
    result = weather_service.assess_day(0, 10.0, 5)
    assert result["risk"] == "low"
    assert "clear" in result["conditions"]


def test_assess_day_snow_is_high_risk():
    assert weather_service.assess_day(75, 10.0, 90)["risk"] == "high"


def test_assess_day_thunderstorm_is_high_risk():
    assert weather_service.assess_day(95, 0.0, None)["risk"] == "high"


def test_assess_day_light_rain_is_moderate():
    assert weather_service.assess_day(61, 10.0, 40)["risk"] == "moderate"


def test_assess_day_extreme_wind_escalates_to_high():
    result = weather_service.assess_day(2, 72.0, None)
    assert result["risk"] == "high"
    assert "winds" in result["conditions"]


def test_assess_day_precipitation_probability_bumps_low_to_moderate():
    assert weather_service.assess_day(3, 10.0, 85)["risk"] == "moderate"


def test_destination_weather_unreachable_degrades_gracefully(monkeypatch):
    def boom(url, params):
        raise RuntimeError("network down")
    monkeypatch.setattr(weather_service, "_get_json", boom)
    report = weather_service.destination_weather("New York, NY", "2026-06-02")
    assert report["available"] is False
    assert "reason" in report


# ── REST route ──

def test_weather_route_public_read(client, stub_weather):
    resp = client.get("/weather/FX100001")
    assert resp.status_code == 200
    body = resp.json()
    assert body["tracking_id"] == "FX100001"
    assert body["weather"]["risk"] == "low"
    assert body["notification_created"] is False


def test_weather_route_unknown_tracking_404(client, stub_weather):
    assert client.get("/weather/FX999999").status_code == 404


# ── Proactive notification behaviour ──

def test_high_risk_creates_notification_once(client, stub_weather, cust001_auth):
    stub_weather(_report(risk="high"))
    first = client.get("/weather/FX100001").json()
    assert first["notification_created"] is True

    # Second check must not duplicate the unread alert.
    second = client.get("/weather/FX100001").json()
    assert second["notification_created"] is False

    notifications = client.get("/notifications/CUST001", headers=cust001_auth).json()["notifications"]
    weather_alerts = [n for n in notifications if n["notification_type"] == "weather_alert"]
    assert len(weather_alerts) == 1
    assert "FX100001" == weather_alerts[0]["tracking_id"]


def test_high_risk_on_delivered_shipment_no_notification(client, stub_weather, cust002_auth):
    stub_weather(_report(risk="high"))
    body = client.get("/weather/FX100003").json()  # delivered fixture
    assert body["notification_created"] is False


def test_unavailable_weather_no_notification(client, stub_weather):
    stub_weather({"available": False, "reason": "Weather service is currently unreachable."})
    body = client.get("/weather/FX100001").json()
    assert body["weather"]["available"] is False
    assert body["notification_created"] is False


# ── Weather-aware delivery-date suggestions ──

def _outlook(days):
    return {"available": True, "location": "New York", "days": days}


@pytest.fixture
def stub_outlook(monkeypatch):
    state = {"outlook": _outlook([
        {"date": "2026-06-01", "risk": "high", "conditions": "thunderstorm"},
        {"date": "2026-06-02", "risk": "high", "conditions": "heavy snowfall"},
        {"date": "2026-06-03", "risk": "moderate", "conditions": "rain showers"},
        {"date": "2026-06-04", "risk": "low", "conditions": "clear sky"},
        {"date": "2026-06-05", "risk": "low", "conditions": "mainly clear"},
    ])}

    def set_outlook(outlook):
        state["outlook"] = outlook

    monkeypatch.setattr(
        shipment_ops.weather_service, "upcoming_daily_risk",
        lambda destination, days=7: state["outlook"],
    )
    return set_outlook


def test_delivery_options_recommends_first_low_risk_day(client, stub_outlook):
    resp = client.get("/delivery-options/FX100001")
    assert resp.status_code == 200
    body = resp.json()
    assert body["recommended_date"] == "2026-06-04"
    assert len(body["outlook"]["days"]) == 5


def test_delivery_options_rejects_delivered_shipment(client, stub_outlook):
    assert client.get("/delivery-options/FX100003").status_code == 400


def test_delivery_options_weather_unavailable(client, stub_outlook):
    stub_outlook({"available": False, "reason": "Weather service is currently unreachable."})
    body = client.get("/delivery-options/FX100001").json()
    assert body["recommended_date"] is None
    assert body["outlook"]["available"] is False


# ── Hold at location ──

def test_hold_requires_auth(client):
    assert client.post("/hold", json={"tracking_id": "FX100001"}).status_code == 401


def test_hold_owner_can_hold(client, cust001_auth):
    resp = client.post("/hold", json={"tracking_id": "FX100001"}, headers=cust001_auth)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "hold_requested"
    assert "FedEx facility" in body["pickup_location"]
    # Customer gets notified.
    notes = client.get("/notifications/CUST001", headers=cust001_auth).json()["notifications"]
    assert any("held for pickup" in n["message"] for n in notes)


def test_hold_other_customer_forbidden(client, cust002_auth):
    assert client.post("/hold", json={"tracking_id": "FX100001"}, headers=cust002_auth).status_code == 403


def test_hold_delivered_shipment_rejected(client, cust002_auth):
    assert client.post("/hold", json={"tracking_id": "FX100003"}, headers=cust002_auth).status_code == 400


def test_agent_chains_weather_then_suggestions(client, monkeypatch, cust001_auth, stub_weather, stub_outlook):
    """Full agentic plan: high weather risk, then date suggestions, then answer."""
    stub_weather(_report(risk="high"))
    fake = FakeAnthropic([
        msg(tool_calls=[tool_call("t1", "check_weather_impact", json.dumps({"tracking_id": "FX100001"}))]),
        msg(tool_calls=[tool_call("t2", "suggest_delivery_dates", json.dumps({"tracking_id": "FX100001"}))]),
        msg(content="Snow is likely on your ETA. The first clear day is 2026-06-04; want me to reschedule to then, or hold it for pickup?"),
    ])
    monkeypatch.setattr(planner, "get_llm_client", lambda: fake)

    resp = client.post("/ask", json={"query": "Is weather a problem for FX100001? What should I do?"}, headers=cust001_auth)
    body = resp.json()["response"]
    tools_used = [a["tool"] for a in body["actions_taken"]]
    assert tools_used == ["check_weather_impact", "suggest_delivery_dates"]
    assert all(a["ok"] for a in body["actions_taken"])
    assert body["data"]["recommended_date"] == "2026-06-04"


# ── Offline fallback router ──

def test_fallback_weather_intent(client, stub_weather, cust001_auth):
    # No LLM client patched and no key in tests, so /ask uses the fallback.
    resp = client.post("/ask", json={"query": "will bad weather delay FX100001?"}, headers=cust001_auth)
    assert resp.status_code == 200
    body = resp.json()["response"]
    assert body["intent"] == "weather"
    assert body["data"]["weather"]["risk"] == "low"


def test_fallback_weather_intent_needs_tracking_id(client, stub_weather):
    resp = client.post("/ask", json={"query": "what's the weather like?"})
    body = resp.json()["response"]
    assert body["intent"] == "weather"
    assert "tracking ID" in body["data"]["message"]


# ── Agentic loop ──

def test_agent_checks_weather_and_answers(client, monkeypatch, cust001_auth, stub_weather):
    stub_weather(_report(risk="high"))
    fake = FakeAnthropic([
        msg(tool_calls=[tool_call("t1", "check_weather_impact", json.dumps({"tracking_id": "FX100001"}))]),
        msg(content="Heavy snowfall is expected in New York around your ETA; delivery may be delayed. Want me to reschedule?"),
    ])
    monkeypatch.setattr(planner, "get_llm_client", lambda: fake)

    resp = client.post("/ask", json={"query": "Will weather affect FX100001?"}, headers=cust001_auth)
    assert resp.status_code == 200
    body = resp.json()["response"]
    assert body["actions_taken"] == [
        {"tool": "check_weather_impact", "arguments": {"tracking_id": "FX100001"}, "ok": True}
    ]
    assert body["data"]["weather"]["risk"] == "high"
    assert "reschedule" in body["response"].lower()

    # The tool result fed back to the model carries the structured report.
    tool_result_msg = fake.calls[1]["messages"][-1]["content"][0]
    assert tool_result_msg["type"] == "tool_result"
    assert "high" in tool_result_msg["content"]
