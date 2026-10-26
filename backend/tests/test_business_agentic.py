"""
Tests for the business-agentic feature set:

- Groq fallback provider: the same tool-calling loop runs on Groq's
  OpenAI-compatible API when Anthropic is unavailable.
- Billing failover: an out-of-credits Anthropic key demotes the provider
  and the turn transparently retries on Groq.
- Fleet-watch agent: autonomous sweep, proactive notifications, dedupe,
  and route authorization (agent JWT or cron secret).
- Durable customer memory: save/read preference tools + system-prompt
  injection on later turns.
- Human handoff: create_support_case tool, case routes, and the offline
  fallback escalation path.
"""
import json

import pytest

import app.agents.planner_agent as planner
from app.agents import watch_agent
from app.core.config import settings
from app.db.database import SessionLocal
from app.db.models import Shipment, SupportCase
from app.services import llm_service, weather_service
from fakes import FakeAnthropic, msg, tool_call


@pytest.fixture(autouse=True)
def reset_llm_state(monkeypatch):
    """Provider demotion is process-sticky by design; isolate it per test."""
    llm_service._demoted.clear()
    yield
    llm_service._demoted.clear()


# ── Groq provider helpers ──

def groq_tool_call(call_id, name, arguments_json):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": arguments_json}}


def groq_msg(content=None, tool_calls=None):
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {"choices": [{"message": message}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20}}


@pytest.fixture
def stub_groq(monkeypatch):
    """Enable the Groq provider and replay scripted responses in order."""
    state = {"responses": [], "payloads": []}

    def set_responses(responses):
        state["responses"] = list(responses)

    def fake_post(payload):
        state["payloads"].append(payload)
        return state["responses"].pop(0)

    monkeypatch.setattr(settings, "GROQ_API_KEY", "gsk-test")
    monkeypatch.setattr(llm_service, "_groq_post", fake_post)
    state["set"] = set_responses
    return state


# ── Groq agentic loop ──

def test_groq_runs_full_agentic_tool_loop(client, monkeypatch, cust001_auth, stub_groq):
    monkeypatch.setattr(planner, "get_llm_client", lambda: None)  # no Anthropic
    stub_groq["set"]([
        groq_msg(tool_calls=[groq_tool_call("c1", "reschedule_delivery",
                                            json.dumps({"tracking_id": "FX100001", "new_date": "2026-06-05"}))]),
        groq_msg(content="Done — rescheduled to June 5."),
    ])
    resp = client.post("/ask", json={"query": "reschedule FX100001 to 2026-06-05"}, headers=cust001_auth)
    body = resp.json()["response"]
    assert body["llm_provider"] == "groq"
    assert body["actions_taken"][0]["tool"] == "reschedule_delivery"
    assert body["actions_taken"][0]["ok"] is True
    assert "June 5" in body["response"]
    # The mutation really happened.
    assert client.get("/track/FX100001").json()["eta"] == "2026-06-05"
    # Tool results were threaded back in OpenAI format.
    followup = stub_groq["payloads"][1]["messages"]
    assert any(m.get("role") == "tool" and m.get("tool_call_id") == "c1" for m in followup)


def test_anthropic_billing_error_fails_over_to_groq(client, monkeypatch, cust001_auth, stub_groq):
    class BrokeAnthropic:
        class messages:
            @staticmethod
            def create(**kwargs):
                raise Exception("Your credit balance is too low to access the Anthropic API.")

    monkeypatch.setattr(planner, "get_llm_client", lambda: BrokeAnthropic())
    stub_groq["set"]([groq_msg(content="Hello from the fallback brain.")])
    resp = client.post("/ask", json={"query": "hi"}, headers=cust001_auth)
    body = resp.json()["response"]
    assert body["llm_provider"] == "groq"
    assert "fallback brain" in body["response"]
    assert llm_service.is_demoted("anthropic")


def test_no_providers_still_degrades_to_keyword_fallback(client, monkeypatch):
    monkeypatch.setattr(planner, "get_llm_client", lambda: None)
    resp = client.post("/ask", json={"query": "track FX100001"})
    assert resp.json()["response"]["intent"] == "track"


# ── Fleet-watch agent ──

@pytest.fixture
def stub_fleet_weather(monkeypatch):
    """Deterministic weather for the sweep: high risk for NY, low elsewhere."""
    def fake_weather(destination, eta):
        risk = "high" if destination == "NY" else "low"
        return {"available": True, "risk": risk, "conditions": "heavy snowfall",
                "location": destination, "forecast_date": eta}

    def fake_outlook(destination, days=7):
        return {"available": True, "days": [
            {"date": "2026-06-02", "risk": "high"},
            {"date": "2026-06-03", "risk": "moderate"},
            {"date": "2026-06-04", "risk": "low"},
        ]}

    monkeypatch.setattr(weather_service, "destination_weather", fake_weather)
    monkeypatch.setattr(weather_service, "upcoming_daily_risk", fake_outlook)


def test_fleet_watch_flags_and_notifies_once(client, agent_auth, cust001_auth, stub_fleet_weather):
    db = SessionLocal()
    db.query(Shipment).filter(Shipment.tracking_id == "FX100005").update({"status": "delayed"})
    db.commit()
    db.close()

    report = client.post("/agent/fleet-watch", headers=agent_auth).json()
    assert report["scanned"] == 2  # delivered FX100003 excluded
    flagged = {f["tracking_id"]: f for f in report["flagged"]}
    assert "weather_high" in flagged["FX100001"]["flags"]
    assert "2026-06-04" in flagged["FX100001"]["recommended_action"]
    assert "delayed" in flagged["FX100005"]["flags"]
    assert report["notifications_created"] == 2

    # Customer actually got the proactive weather alert.
    notes = client.get("/notifications/CUST001", headers=cust001_auth).json()["notifications"]
    assert any(n["notification_type"] == "weather_alert" for n in notes)

    # Second sweep dedupes against unread notifications.
    report2 = client.post("/agent/fleet-watch", headers=agent_auth).json()
    assert report2["notifications_created"] == 0
    assert len(report2["flagged"]) == 2


def test_fleet_watch_requires_agent_or_cron_secret(client, cust001_auth):
    assert client.post("/agent/fleet-watch").status_code == 401
    assert client.post("/agent/fleet-watch", headers=cust001_auth).status_code == 403
    resp = client.get("/agent/fleet-watch",
                      headers={"Authorization": "Bearer test-cron-secret"})
    assert resp.status_code == 200
    assert "scanned" in resp.json()


# ── Durable customer memory ──

def test_agent_saves_preference_and_it_persists(client, monkeypatch, cust001_auth):
    fake = FakeAnthropic([
        msg(tool_calls=[tool_call("t1", "save_customer_preference",
                                  json.dumps({"customer_id": "CUST001",
                                              "preference": "delivery_window",
                                              "value": "after 6pm on weekdays"}))]),
        msg("Got it — I'll remember you prefer evening deliveries."),
    ])
    monkeypatch.setattr(planner, "get_llm_client", lambda: fake)
    resp = client.post("/ask", json={"query": "always deliver after 6pm please"}, headers=cust001_auth)
    assert resp.json()["response"]["actions_taken"][0]["ok"] is True

    prefs = client.get("/preferences/CUST001", headers=cust001_auth).json()["preferences"]
    assert prefs == [{"preference": "delivery_window", "value": "after 6pm on weekdays"}]


def test_saved_preferences_injected_into_system_prompt(client, monkeypatch, cust001_auth):
    db = SessionLocal()
    from app.db.crud import upsert_preference
    upsert_preference(db, "CUST001", "delivery_window", "after 6pm")
    db.close()

    fake = FakeAnthropic([msg("Evening it is.")])
    monkeypatch.setattr(planner, "get_llm_client", lambda: fake)
    client.post("/ask", json={"query": "when can I get my package?"}, headers=cust001_auth)
    system = fake.calls[0]["system"]
    assert "delivery_window=after 6pm" in system


def test_customer_cannot_read_others_preferences(client, cust002_auth):
    assert client.get("/preferences/CUST001", headers=cust002_auth).status_code == 403


# ── Human handoff (support cases) ──

def test_agent_creates_support_case_with_summary(client, monkeypatch, cust001_auth):
    fake = FakeAnthropic([
        msg(tool_calls=[tool_call("t1", "create_support_case",
                                  json.dumps({"customer_id": "CUST001",
                                              "summary": "Package FX100001 arrived damaged; customer requests inspection and refund.",
                                              "tracking_id": "FX100001",
                                              "priority": "high"}))]),
        msg("I've opened a high-priority case for you."),
    ])
    monkeypatch.setattr(planner, "get_llm_client", lambda: fake)
    resp = client.post("/ask", json={"query": "my package arrived damaged, I want a refund"},
                       headers=cust001_auth)
    body = resp.json()["response"]
    assert body["actions_taken"][0]["ok"] is True
    case_id = body["data"]["case_id"]
    assert case_id.startswith("CASE-")

    cases = client.get("/cases/CUST001", headers=cust001_auth).json()["cases"]
    assert cases[0]["case_id"] == case_id
    assert cases[0]["priority"] == "high"
    notes = client.get("/notifications/CUST001", headers=cust001_auth).json()["notifications"]
    assert any(case_id in n["message"] for n in notes)


def test_fallback_escalation_still_opens_case_offline(client, monkeypatch, cust001_auth):
    monkeypatch.setattr(planner, "get_llm_client", lambda: None)
    resp = client.post("/ask", json={"query": "I want to complain about FX100001, it is broken"},
                       headers=cust001_auth)
    body = resp.json()["response"]
    assert body["intent"] == "escalate"
    assert body["data"]["case_id"].startswith("CASE-")

    db = SessionLocal()
    assert db.query(SupportCase).count() == 1
    db.close()


def test_cases_listing_is_role_scoped(client, agent_auth, cust002_auth, monkeypatch, cust001_auth):
    monkeypatch.setattr(planner, "get_llm_client", lambda: None)
    client.post("/ask", json={"query": "escalate this please"}, headers=cust001_auth)
    # Agents see every case; customers cannot use the global listing.
    assert len(client.get("/cases", headers=agent_auth).json()["cases"]) == 1
    assert client.get("/cases", headers=cust002_auth).status_code == 403
    assert client.get("/cases/CUST001", headers=cust002_auth).status_code == 403


# ── Dijkstra routing + journey map payload ──

def test_dijkstra_shortest_path_la_to_ny():
    from app.services import route_service
    result = route_service.plan_route("Los Angeles, CA", "New York, NY")
    assert result["available"] is True
    hubs = [h["name"] for h in result["route"]["hubs"]]
    assert hubs[0] == "Los Angeles" and hubs[-1] == "New York"
    assert result["route"]["km"] > 3000
    assert result["rerouted"] is False


def test_dijkstra_reroutes_around_high_risk_hub():
    from app.services import route_service
    baseline = route_service.plan_route("Seattle, WA", "Miami, FL")
    middle = [h["name"] for h in baseline["route"]["hubs"]][1:-1]
    assert middle, "expected intermediate hubs on Seattle->Miami"

    # A storm at some intermediate hub must produce a detour that avoids it.
    rerouted_any = False
    for storm_hub in middle:
        result = route_service.plan_route(
            "Seattle, WA", "Miami, FL",
            risk_lookup=lambda hub, storm=storm_hub: "high" if hub == storm else "low",
        )
        if result["rerouted"]:
            rerouted_any = True
            rerouted_hubs = [h["name"] for h in result["route"]["hubs"]]
            assert storm_hub not in rerouted_hubs
            assert "rerouted" in result["explanation"]
            break
    assert rerouted_any, "no storm placement produced a reroute"


def test_journey_endpoint_payload(client, monkeypatch):
    monkeypatch.setattr(weather_service, "destination_weather",
                        lambda destination, eta: {"available": True, "risk": "low"})
    resp = client.get("/journey/FX100001")
    assert resp.status_code == 200
    body = resp.json()
    assert body["tracking_id"] == "FX100001"
    assert 0.0 <= body["progress"] <= 1.0
    assert body["route"]["available"] is True
    assert body["route"]["route"]["hubs"][-1]["name"] == "New York"
    assert body["route"]["route"]["est_transit_hours"] > 0


def test_agent_tool_plans_route(client, monkeypatch, cust001_auth):
    monkeypatch.setattr(weather_service, "destination_weather",
                        lambda destination, eta: {"available": True, "risk": "low"})
    fake = FakeAnthropic([
        msg(tool_calls=[tool_call("t1", "plan_optimal_route", json.dumps({"tracking_id": "FX100001"}))]),
        msg("Your package travels LA to New York via the hub network."),
    ])
    monkeypatch.setattr(planner, "get_llm_client", lambda: fake)
    resp = client.post("/ask", json={"query": "how will FX100001 travel to me?"}, headers=cust001_auth)
    body = resp.json()["response"]
    assert body["actions_taken"][0]["tool"] == "plan_optimal_route"
    assert body["data"]["route"]["available"] is True


# ── Fix-list regressions ──

def test_fallback_hold_actually_executes(client, monkeypatch, cust001_auth):
    monkeypatch.setattr(planner, "get_llm_client", lambda: None)
    resp = client.post("/ask", json={"query": "I'm not home, please hold FX100001 for pickup"},
                       headers=cust001_auth)
    body = resp.json()["response"]
    assert body["intent"] == "hold"
    assert body["data"]["status"] == "hold_requested"
    assert client.get("/track/FX100001").json()["status"] == "hold_requested"


def test_hold_resolves_concrete_facility(client, cust001_auth):
    resp = client.post("/hold", json={"tracking_id": "FX100001"}, headers=cust001_auth)
    assert resp.status_code == 200
    body = resp.json()
    # Destination 'NY' seeds don't match; live seeds ('New York, NY') do.
    assert "FedEx" in body["pickup_location"]


def test_facility_lookup_matches_metro():
    from app.services.facility_service import nearest_facility
    hit = nearest_facility("New York, NY")
    assert hit and "606 W 49th St" in hit["address"]
    assert nearest_facility("Middle of Nowhere, KS") is None


def test_weather_note_when_eta_outside_forecast(client, monkeypatch, stub_groq):
    # Far-future ETA: the service reports the closest day and says so.
    from app.services.weather_service import destination_weather
    monkeypatch.setattr(
        "app.services.weather_service._geocode",
        lambda destination: {"latitude": 40.7, "longitude": -74.0, "name": "New York", "country": "US"})
    monkeypatch.setattr(
        "app.services.weather_service._get_json",
        lambda url, params: {"daily": {
            "time": ["2026-08-29", "2026-08-30"], "weather_code": [75, 0],
            "wind_speed_10m_max": [10, 10], "precipitation_probability_max": [90, 5],
            "temperature_2m_max": [5, 6], "temperature_2m_min": [0, 1]}})
    report = destination_weather("New York, NY", "2026-12-25")
    assert report["eta_within_forecast"] is False
    assert "outside" in report["note"]
    # And high risk on a non-ETA day must NOT fire a customer alert.
    from app.db.database import SessionLocal as _S
    from app.core.security import User as _U
    from app.services import shipment_ops as _ops
    db = _S()
    result = _ops.weather_impact(db, _U(username="agent", role="agent"), "FX100001")
    db.close()
    assert result["notification_created"] is False


def test_notify_once_time_window_blocks_read_refire():
    from app.db.crud import notify_once, mark_notification_read
    from app.db.database import SessionLocal as _S
    db = _S()
    first = notify_once(db, "CUST003", "FX100005", "storm coming", "weather_alert")
    assert first is not None
    mark_notification_read(db, first.id)
    # Read, but still inside the 24h window: no duplicate.
    assert notify_once(db, "CUST003", "FX100005", "storm coming", "weather_alert") is None
    db.close()


def test_fleet_watch_handles_failed_delivery_attempt(client, agent_auth, stub_fleet_weather):
    db = SessionLocal()
    db.add(Shipment(tracking_id="FX100006", status="delivery_attempt_failed",
                    location="Brooklyn, NY", eta="2026-06-03", customer_id="CUST001",
                    origin="Chicago, IL", destination="New York, NY"))
    db.commit()
    db.close()
    report = client.post("/agent/fleet-watch", headers=agent_auth).json()
    flagged = {f["tracking_id"]: f for f in report["flagged"]}
    assert "delivery_attempt_failed" in flagged["FX100006"]["flags"]


# ── Autonomy tiers, verification, commitments, analytics ──

def _weather_low(monkeypatch):
    monkeypatch.setattr(weather_service, "destination_weather",
                        lambda destination, eta: {"available": True, "risk": "low",
                                                  "eta_within_forecast": True})


def test_tier3_cancel_blocked_without_confirmation(client, monkeypatch, cust001_auth):
    fake = FakeAnthropic([
        msg(tool_calls=[tool_call("t1", "cancel_shipment",
                                  json.dumps({"tracking_id": "FX100001"}))]),
        msg("Just to confirm: do you really want to cancel FX100001?"),
    ])
    monkeypatch.setattr(planner, "get_llm_client", lambda: fake)
    resp = client.post("/ask", json={"query": "cancel FX100001"}, headers=cust001_auth)
    body = resp.json()["response"]
    assert body["actions_taken"][0]["ok"] is False
    # The shipment was NOT cancelled: policy enforced in the tool layer.
    assert client.get("/track/FX100001").json()["status"] == "in_transit"


def test_tier3_cancel_with_confirmation_executes_and_verifies(client, monkeypatch, cust001_auth):
    # Turn 1: the model asks (tool refused, platform remembers the ask).
    fake = FakeAnthropic([
        msg(tool_calls=[tool_call("t1", "cancel_shipment", json.dumps({"tracking_id": "FX100001"}))]),
        msg("Do you want me to cancel FX100001?"),
        # Turn 2: the customer said yes in their own words; the same call now succeeds.
        msg(tool_calls=[tool_call("t2", "cancel_shipment", json.dumps({"tracking_id": "FX100001"}))]),
        msg("Done, cancelled."),
    ])
    monkeypatch.setattr(planner, "get_llm_client", lambda: fake)
    sid = "tier3-yes"
    first = client.post("/ask", json={"query": "cancel FX100001", "session_id": sid}, headers=cust001_auth)
    assert first.json()["response"]["actions_taken"][0]["requires_confirmation"] is True
    assert client.get("/track/FX100001").json()["status"] == "in_transit"
    resp = client.post("/ask", json={"query": "yes, cancel FX100001", "session_id": sid}, headers=cust001_auth)
    body = resp.json()["response"]
    assert body["actions_taken"][0]["ok"] is True
    assert body["data"]["verified"] is True
    assert client.get("/track/FX100001").json()["status"] == "cancelled"


def test_mutations_are_read_back_verified(client, cust001_auth, monkeypatch):
    from app.agents.agent_tools import execute_tool
    from app.core.security import User as _U
    db = SessionLocal()
    outcome = execute_tool("reschedule_delivery",
                           {"tracking_id": "FX100001", "new_date": "2026-06-08"},
                           db, _U(username="cust001", role="customer", customer_id="CUST001"))
    db.close()
    assert outcome["ok"] is True
    assert outcome["result"]["verified"] is True


def test_offline_deadline_parsing():
    from datetime import date, timedelta
    assert planner._extract_deadline("need it by 2026-09-04") == "2026-09-04"
    assert planner._extract_deadline("must arrive by tomorrow") == (
        date.today() + timedelta(days=1)).isoformat()
    friday = planner._extract_deadline("deliver before friday")
    assert friday and friday > date.today().isoformat()


def test_commitment_lifecycle_via_monitoring(client, monkeypatch, cust001_auth, agent_auth):
    _weather_low(monkeypatch)
    monkeypatch.setattr(planner, "get_llm_client", lambda: None)

    # Offline router understands the outcome and creates a commitment.
    resp = client.post("/ask", json={"query": "I need FX100001 delivered before 2026-09-04"},
                       headers=cust001_auth)
    data = resp.json()["response"]["data"]
    commitment_id = data["commitment_id"]
    assert data["status"] == "ACTIVE" and data["eta_meets_target"] is True

    # ETA slips past the deadline; the monitoring loop flags the goal.
    db = SessionLocal()
    db.query(Shipment).filter(Shipment.tracking_id == "FX100001").update({"eta": "2026-09-09"})
    db.commit(); db.close()
    report = client.post("/agent/fleet-watch", headers=agent_auth).json()
    assert report["commitments"]["at_risk"] == 1
    assert report["commitments"]["details"][0]["commitment_id"] == commitment_id
    got = client.get("/commitments/CUST001", headers=cust001_auth).json()["commitments"][0]
    assert got["status"] == "AT_RISK" and "deadline" in got["risk_reason"]

    # ETA recovers; the loop reports it back on track (risk changed -> update).
    db = SessionLocal()
    db.query(Shipment).filter(Shipment.tracking_id == "FX100001").update({"eta": "2026-09-03"})
    db.commit(); db.close()
    report2 = client.post("/agent/fleet-watch", headers=agent_auth).json()
    assert report2["commitments"]["active"] == 1
    got = client.get("/commitments/CUST001", headers=cust001_auth).json()["commitments"][0]
    assert got["status"] == "ACTIVE"
    notes = client.get("/notifications/CUST001", headers=cust001_auth).json()["notifications"]
    assert any("back on track" in n["message"] for n in notes)


def test_cost_attribution_and_activity_feed(client, monkeypatch, cust001_auth):
    db = SessionLocal()
    db.add(Shipment(tracking_id="FX100006", status="delivery_attempt_failed",
                    location="Brooklyn, NY", eta="2026-06-03", customer_id="CUST001",
                    origin="Chicago, IL", destination="New York, NY"))
    db.commit(); db.close()

    fake = FakeAnthropic([
        msg(tool_calls=[tool_call("t1", "hold_at_location",
                                  json.dumps({"tracking_id": "FX100006"}))]),
        msg("Held for pickup — you skip another missed attempt."),
    ])
    monkeypatch.setattr(planner, "get_llm_client", lambda: fake)
    resp = client.post("/ask", json={"query": "hold FX100006 for pickup"}, headers=cust001_auth)
    body = resp.json()["response"]
    assert body["data"]["estimated_cost_avoided_usd"] == 17.78
    assert body["data"]["verified"] is True

    feed = client.get("/activity/CUST001", headers=cust001_auth).json()
    assert feed["total_cost_avoided"] >= 17.78
    assert any(e["tool"] == "hold_at_location" and e["ok"] for e in feed["events"])


def test_agent_analytics_endpoint_role_scoped(client, monkeypatch, cust001_auth, agent_auth):
    monkeypatch.setattr(planner, "get_llm_client", lambda: None)
    client.post("/ask", json={"query": "hold FX100001 for pickup"}, headers=cust001_auth)

    assert client.get("/analytics/agent", headers=cust001_auth).status_code == 403
    analytics = client.get("/analytics/agent", headers=agent_auth).json()
    assert analytics["totals"]["agent_actions"] >= 1
    assert any(t["tool"] == "hold_at_location" for t in analytics["tool_usage"])
    assert "commitments" in analytics


def test_journey_trace_checkpoints(client, monkeypatch):
    monkeypatch.setattr(weather_service, "destination_weather",
                        lambda destination, eta: {"available": True, "risk": "low"})
    trace = client.get("/journey/FX100001").json()["trace"]
    assert trace[0]["label"].startswith("Picked up in")
    assert trace[-1]["label"].startswith("Delivery in")
    states = {t["state"] for t in trace}
    assert "current" in states and "upcoming" in states


# ── Offline mini-planner (agentic without a key) ──

def test_fallback_lists_own_shipments(client, monkeypatch, cust001_auth):
    monkeypatch.setattr(planner, "get_llm_client", lambda: None)
    resp = client.post("/ask", json={"query": "what are all my packages?"}, headers=cust001_auth)
    data = resp.json()["response"]["data"]
    assert len(data["shipments"]) == 1  # CUST001 seeds FX100001 in tests
    assert "1 package" in data["message"]


def test_fallback_reschedule_with_date_executes_verified(client, monkeypatch, cust001_auth):
    monkeypatch.setattr(planner, "get_llm_client", lambda: None)
    resp = client.post("/ask", json={"query": "reschedule FX100001 to 2026-09-10"},
                       headers=cust001_auth)
    data = resp.json()["response"]["data"]
    assert data["verified"] is True
    assert "verified" in data["message"]
    assert client.get("/track/FX100001").json()["eta"] == "2026-09-10"


def test_fallback_weather_chains_outlook_when_risky(client, monkeypatch, cust001_auth):
    monkeypatch.setattr(planner, "get_llm_client", lambda: None)
    monkeypatch.setattr(weather_service, "destination_weather",
                        lambda destination, eta: {"available": True, "risk": "high",
                                                  "eta_within_forecast": True,
                                                  "conditions": "heavy snowfall"})
    monkeypatch.setattr(weather_service, "upcoming_daily_risk",
                        lambda destination, days=7: {"available": True, "days": [
                            {"date": "2026-06-02", "risk": "high", "conditions": "snow"},
                            {"date": "2026-06-03", "risk": "low", "conditions": "clear"}]})
    resp = client.post("/ask", json={"query": "will weather delay FX100001?"}, headers=cust001_auth)
    data = resp.json()["response"]["data"]
    assert data["weather"]["risk"] == "high"
    assert data["recommended_date"] == "2026-06-03"  # chained in the same reply
    assert data["outlook"]["days"]


def test_fallback_saves_preference(client, monkeypatch, cust001_auth):
    monkeypatch.setattr(planner, "get_llm_client", lambda: None)
    resp = client.post("/ask", json={"query": "always deliver after 6 pm please"}, headers=cust001_auth)
    data = resp.json()["response"]["data"]
    assert data["saved"] is True
    prefs = client.get("/preferences/CUST001", headers=cust001_auth).json()["preferences"]
    assert prefs[0]["value"] == "after 6 pm"


def test_customer_shipments_route_scoped(client, cust001_auth, cust002_auth):
    resp = client.get("/customers/CUST001/shipments", headers=cust001_auth)
    assert resp.status_code == 200 and len(resp.json()["shipments"]) == 1
    assert client.get("/customers/CUST001/shipments", headers=cust002_auth).status_code == 403
