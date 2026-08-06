"""More-agentic features: token usage reporting, conversation memory,
and the list_customer_shipments tool."""
import json

import app.agents.planner_agent as planner
from app.services import conversation_store
from fakes import FakeAnthropic, msg, tool_call


def _patch_llm(monkeypatch, fake):
    monkeypatch.setattr(planner, "get_llm_client", lambda: fake)


def test_usage_is_summed_across_tool_loop(client, monkeypatch, cust001_auth):
    fake = FakeAnthropic([
        msg(tool_calls=[tool_call("c1", "track_shipment",
             json.dumps({"tracking_id": "FX100001"}))], usage=(600, 25)),
        msg(content="It's in transit in Memphis.", usage=(750, 40)),
    ])
    _patch_llm(monkeypatch, fake)

    r = client.post("/ask", headers=cust001_auth, json={"query": "where's FX100001?"})
    usage = r.json()["response"]["usage"]
    assert usage["model_calls"] == 2
    assert usage["prompt_tokens"] == 1350
    assert usage["completion_tokens"] == 65
    assert usage["total_tokens"] == 1415


def test_conversation_memory_replays_prior_turns(client, monkeypatch, cust001_auth):
    sid = "sess-abc"
    conversation_store.reset(sid)

    # Turn 1
    _patch_llm(monkeypatch, FakeAnthropic([msg(content="Your package FX100001 is in transit.")]))
    client.post("/ask", headers=cust001_auth,
                json={"query": "track FX100001", "session_id": sid})

    # Turn 2: the fake asserts that turn 1 is present in the replayed messages.
    fake = FakeAnthropic([msg(content="Rescheduled it.")])
    _patch_llm(monkeypatch, fake)
    client.post("/ask", headers=cust001_auth,
                json={"query": "reschedule it to next week", "session_id": sid})

    sent_messages = fake.calls[0]["messages"]
    roles_and_text = [(m["role"], m.get("content", "")) for m in sent_messages]
    # Prior user + assistant turn must be replayed before the new user message.
    assert ("user", "track FX100001") in roles_and_text
    assert any(r == "assistant" and "in transit" in t for r, t in roles_and_text)


def test_list_customer_shipments_tool(client, monkeypatch, cust001_auth):
    fake = FakeAnthropic([
        msg(tool_calls=[tool_call("c1", "list_customer_shipments",
             json.dumps({"customer_id": "CUST001"}))]),
        msg(content="You have 1 active package."),
    ])
    _patch_llm(monkeypatch, fake)

    r = client.post("/ask", headers=cust001_auth, json={"query": "what are all my packages?"})
    body = r.json()["response"]
    assert body["actions_taken"][0]["tool"] == "list_customer_shipments"
    assert body["data"]["shipments"][0]["tracking_id"] == "FX100001"


def test_list_shipments_authorization(client, monkeypatch, cust002_auth):
    # cust002 must not be able to list CUST001's shipments via the agent.
    fake = FakeAnthropic([
        msg(tool_calls=[tool_call("c1", "list_customer_shipments",
             json.dumps({"customer_id": "CUST001"}))]),
        msg(content="Sorry, you can only view your own packages."),
    ])
    _patch_llm(monkeypatch, fake)

    r = client.post("/ask", headers=cust002_auth, json={"query": "list CUST001 packages"})
    assert r.json()["response"]["actions_taken"][0]["ok"] is False


def test_fallback_reports_zero_usage(client, monkeypatch):
    monkeypatch.setattr(planner, "get_llm_client", lambda: None)
    r = client.post("/ask", json={"query": "track FX100001"})
    assert r.json()["response"]["usage"]["total_tokens"] == 0
