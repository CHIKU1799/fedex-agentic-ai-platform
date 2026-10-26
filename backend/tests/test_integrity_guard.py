"""
Integrity guard: the agent may never claim a change it did not make.

A scripted fake model answers "Done!" to a hold request without calling
any tool. The planner must push back once; if the model then acts, the
change is real and verified; if it keeps claiming, the reply is replaced
with an honest one. Either way the customer is never told a lie.
"""
import json

import app.agents.planner_agent as planner
from fakes import FakeAnthropic, msg, tool_call


def _patch_llm(monkeypatch, fake):
    monkeypatch.setattr(planner, "get_llm_client", lambda: fake)


def test_unbacked_claim_is_corrected_into_a_real_action(client, monkeypatch, cust001_auth):
    fake = FakeAnthropic([
        msg(content="Done! FX100001 is now held for pickup at the nearest FedEx facility."),
        msg(tool_calls=[tool_call("c1", "hold_at_location", json.dumps({"tracking_id": "FX100001"}))]),
        msg(content="Done, FX100001 is now held for pickup."),
    ])
    _patch_llm(monkeypatch, fake)

    r = client.post("/ask", headers=cust001_auth, json={"query": "Hold FX100001 for pickup"})
    assert r.status_code == 200
    body = r.json()["response"]
    assert body["integrity"] == "corrected"
    assert [a["tool"] for a in body["actions_taken"]] == ["hold_at_location"]
    assert body["actions_taken"][0]["ok"] is True and body["actions_taken"][0]["verified"] is True
    assert client.get("/track/FX100001", headers=cust001_auth).json()["status"] == "hold_requested"


def test_persistent_unbacked_claim_is_replaced(client, monkeypatch, cust001_auth):
    fake = FakeAnthropic([
        msg(content="Done! I've rescheduled FX100001 to Friday."),
        msg(content="All set, FX100001 has been rescheduled."),
    ])
    _patch_llm(monkeypatch, fake)

    before = client.get("/track/FX100001", headers=cust001_auth).json()
    r = client.post("/ask", headers=cust001_auth, json={"query": "Reschedule FX100001 to friday"})
    body = r.json()["response"]
    assert body["integrity"] == "overridden"
    assert body["actions_taken"] == []
    assert "not made that change" in body["response"]
    assert client.get("/track/FX100001", headers=cust001_auth).json() == before


def test_tier3_refusal_is_flagged_for_the_ui(client, monkeypatch, cust001_auth):
    fake = FakeAnthropic([
        msg(tool_calls=[tool_call("c1", "cancel_shipment", json.dumps({"tracking_id": "FX100001"}))]),
        msg(content="Cancelling is permanent. Do you want me to go ahead?"),
    ])
    _patch_llm(monkeypatch, fake)

    before = client.get("/track/FX100001", headers=cust001_auth).json()
    r = client.post("/ask", headers=cust001_auth, json={"query": "cancel FX100001"})
    body = r.json()["response"]
    assert body["actions_taken"][0]["requires_confirmation"] is True
    assert body["actions_taken"][0]["ok"] is False
    assert body["integrity"] == "clean"  # asking is honest, not a claim
    assert client.get("/track/FX100001", headers=cust001_auth).json() == before


def test_honest_question_is_not_flagged(client, monkeypatch, cust001_auth):
    fake = FakeAnthropic([msg(content="Which Friday do you mean? I have not changed anything yet.")])
    _patch_llm(monkeypatch, fake)
    r = client.post("/ask", headers=cust001_auth, json={"query": "Reschedule FX100001 to friday"})
    assert r.json()["response"]["integrity"] == "clean"
