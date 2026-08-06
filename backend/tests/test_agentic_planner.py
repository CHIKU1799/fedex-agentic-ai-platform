"""
The heart of the "is it actually agentic?" question.

These tests inject a scripted fake LLM that emits real tool calls, then assert
that the database was genuinely mutated (or correctly blocked by authorization)
as a result of the planner's tool-calling loop — not that a canned string was
returned. This is the difference between the old keyword router and a real
agent that plans and acts.
"""
import json

import app.agents.planner_agent as planner
from fakes import FakeAnthropic, msg, tool_call


def _patch_llm(monkeypatch, fake):
    monkeypatch.setattr(planner, "get_llm_client", lambda: fake)


def test_agent_executes_real_reschedule(client, monkeypatch, cust001_auth):
    fake = FakeAnthropic([
        msg(tool_calls=[tool_call("c1", "reschedule_delivery",
             json.dumps({"tracking_id": "FX100001", "new_date": "2026-12-01"}))]),
        msg(content="Done — your delivery is now set for 1 December 2026."),
    ])
    _patch_llm(monkeypatch, fake)

    r = client.post("/ask", headers=cust001_auth,
                    json={"query": "please move FX100001 to December 1st 2026"})
    assert r.status_code == 200
    body = r.json()["response"]
    assert body["intent"] == "agentic"
    assert body["actions_taken"][0] == {"tool": "reschedule_delivery",
                                        "arguments": {"tracking_id": "FX100001", "new_date": "2026-12-01"},
                                        "ok": True}
    # The real mutation happened:
    assert client.get("/track/FX100001").json()["eta"] == "2026-12-01"
    assert client.get("/track/FX100001").json()["status"] == "rescheduled"


def test_agent_tool_respects_authorization(client, monkeypatch, cust002_auth):
    # cust002 does not own FX100001; the tool must refuse and the DB stay put.
    fake = FakeAnthropic([
        msg(tool_calls=[tool_call("c1", "cancel_shipment",
             json.dumps({"tracking_id": "FX100001", "reason": "malicious"}))]),
        msg(content="Sorry, you're not authorized to cancel that shipment."),
    ])
    _patch_llm(monkeypatch, fake)

    r = client.post("/ask", headers=cust002_auth,
                    json={"query": "cancel FX100001"})
    assert r.status_code == 200
    assert r.json()["response"]["actions_taken"][0]["ok"] is False
    # Untouched:
    assert client.get("/track/FX100001").json()["status"] == "in_transit"


def test_agent_multi_step_track_then_answer(client, monkeypatch, agent_auth):
    fake = FakeAnthropic([
        msg(tool_calls=[tool_call("c1", "track_shipment",
             json.dumps({"tracking_id": "FX100001"}))]),
        msg(content="Your package is in transit in Memphis, TN."),
    ])
    _patch_llm(monkeypatch, fake)

    r = client.post("/ask", headers=agent_auth, json={"query": "where is FX100001?"})
    assert r.status_code == 200
    body = r.json()["response"]
    assert body["data"]["status"] == "in_transit"
    assert "memphis" in body["response"].lower()


def test_fallback_when_no_api_key(client, monkeypatch):
    # No LLM available -> deterministic keyword router still tracks.
    monkeypatch.setattr(planner, "get_llm_client", lambda: None)
    r = client.post("/ask", json={"query": "track FX100001"})
    assert r.status_code == 200
    body = r.json()["response"]
    assert body["intent"] == "track"
    assert body["data"]["tracking_id"] == "FX100001"


def test_fallback_mutation_gives_guidance_not_action(client, monkeypatch):
    monkeypatch.setattr(planner, "get_llm_client", lambda: None)
    r = client.post("/ask", json={"query": "cancel my shipment"})
    body = r.json()["response"]
    assert body["intent"] == "cancel"
    assert "/cancel" in body["data"]["message"]
