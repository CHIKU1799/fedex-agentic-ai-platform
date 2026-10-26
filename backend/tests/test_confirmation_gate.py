"""
The tier-3 confirmation belongs to the customer, not the model.

A model that passes confirmed=true, retries inside the same turn, or is
driven by a different user's session must never get a cancel or redirect
through. Only the customer's own "yes" in the next message opens the gate,
and only for the exact shipment that was asked about.
"""
import json

import app.agents.planner_agent as planner
from app.agents import confirmations
from fakes import FakeAnthropic, msg, tool_call


def _patch(monkeypatch, fake):
    monkeypatch.setattr(planner, "get_llm_client", lambda: fake)


def test_model_cannot_self_confirm_in_the_same_turn(client, monkeypatch, cust001_auth):
    fake = FakeAnthropic([
        msg(tool_calls=[tool_call("t1", "cancel_shipment", json.dumps({"tracking_id": "FX100001"}))]),
        # The bypass seen live: retry immediately with confirmed=true.
        msg(tool_calls=[tool_call("t2", "cancel_shipment", json.dumps({"tracking_id": "FX100001", "confirmed": True}))]),
        msg("Done. FX100001 has been cancelled."),
        # After the integrity nudge the model comes clean.
        msg("I have not cancelled FX100001 yet. Do you want me to go ahead?"),
    ])
    _patch(monkeypatch, fake)
    r = client.post("/ask", json={"query": "cancel FX100001", "session_id": "bypass"}, headers=cust001_auth)
    body = r.json()["response"]
    assert all(a["ok"] is False for a in body["actions_taken"])
    assert body["actions_taken"][1]["requires_confirmation"] is True
    assert client.get("/track/FX100001").json()["status"] == "in_transit"
    # And the lie about it was caught by the integrity guard.
    assert body["integrity"] == "corrected"


def test_confirmed_flag_alone_never_opens_the_gate(client, monkeypatch, cust001_auth):
    fake = FakeAnthropic([
        msg(tool_calls=[tool_call("t1", "cancel_shipment", json.dumps({"tracking_id": "FX100001", "confirmed": True}))]),
        msg("Would you like me to cancel FX100001?"),
    ])
    _patch(monkeypatch, fake)
    r = client.post("/ask", json={"query": "cancel FX100001 right now", "session_id": "flag"}, headers=cust001_auth)
    assert r.json()["response"]["actions_taken"][0]["requires_confirmation"] is True
    assert client.get("/track/FX100001").json()["status"] == "in_transit"


def test_a_no_clears_the_pending_ask(client, monkeypatch, cust001_auth):
    fake = FakeAnthropic([
        msg(tool_calls=[tool_call("t1", "cancel_shipment", json.dumps({"tracking_id": "FX100001"}))]),
        msg("Do you want me to cancel FX100001?"),
        msg(tool_calls=[tool_call("t2", "cancel_shipment", json.dumps({"tracking_id": "FX100001"}))]),
        msg("Okay, I will leave it."),
    ])
    _patch(monkeypatch, fake)
    sid = "said-no"
    client.post("/ask", json={"query": "cancel FX100001", "session_id": sid}, headers=cust001_auth)
    r = client.post("/ask", json={"query": "No, do not cancel FX100001. Leave it as it is.", "session_id": sid}, headers=cust001_auth)
    assert r.json()["response"]["actions_taken"][0]["ok"] is False
    assert client.get("/track/FX100001").json()["status"] == "in_transit"


def test_yes_only_covers_the_shipment_that_was_asked_about(client, monkeypatch, cust001_auth):
    fake = FakeAnthropic([
        msg(tool_calls=[tool_call("t1", "cancel_shipment", json.dumps({"tracking_id": "FX100001"}))]),
        msg("Cancel FX100001?"),
        # Customer says yes; the model tries a different shipment.
        msg(tool_calls=[tool_call("t2", "cancel_shipment", json.dumps({"tracking_id": "FX100002"}))]),
        msg("Hmm."),
    ])
    _patch(monkeypatch, fake)
    sid = "other-id"
    client.post("/ask", json={"query": "cancel FX100001", "session_id": sid}, headers=cust001_auth)
    r = client.post("/ask", json={"query": "yes, go ahead", "session_id": sid}, headers=cust001_auth)
    assert r.json()["response"]["actions_taken"][0]["ok"] is False


def test_sessions_are_scoped_to_the_user(client, monkeypatch, cust001_auth, cust002_auth):
    # cust001 is asked to confirm; cust002 reuses the same session_id and says yes.
    fake = FakeAnthropic([
        msg(tool_calls=[tool_call("t1", "cancel_shipment", json.dumps({"tracking_id": "FX100001"}))]),
        msg("Cancel FX100001?"),
        msg(tool_calls=[tool_call("t2", "cancel_shipment", json.dumps({"tracking_id": "FX100001"}))]),
        msg("No."),
    ])
    _patch(monkeypatch, fake)
    sid = "shared-session"
    client.post("/ask", json={"query": "cancel FX100001", "session_id": sid}, headers=cust001_auth)
    r = client.post("/ask", json={"query": "yes, go ahead", "session_id": sid}, headers=cust002_auth)
    assert r.json()["response"]["actions_taken"][0]["ok"] is False
    assert client.get("/track/FX100001").json()["status"] == "in_transit"
    # cust001's own pending ask is still intact and scoped to them.
    assert confirmations.pending(confirmations.session_key("cust001", sid))["tracking_id"] == "FX100001"


def test_customer_answer_parsing():
    assert confirmations.customer_answer("Yes, I confirm: cancel FX100001. Go ahead.") is True
    assert confirmations.customer_answer("No, do not cancel FX100001. Leave it as it is.") is False
    assert confirmations.customer_answer("where is my package?") is None
