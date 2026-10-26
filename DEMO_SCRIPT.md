# FedE Demo Script (Digithon Phase 2, Team Purple Route 31)

## The one-liner

"Most support chatbots tell you what to do. FedE actually does it. You say
'move my package to Friday' and an AI agent plans the steps, checks your
permissions, executes the change in the database, and confirms it back to you
in plain language, by text or by voice."

---

## Demo flow (5 to 7 minutes)

1. **Landing page** (`http://localhost:4200/`)
   - Point at the hero: "acts, not just answers" is the whole thesis.
   - Point at the mock chat card: user request, then a tool chip
     (`reschedule_delivery, authorized, executed`), then confirmation.
     Say: "that chip is the real architecture, you will see it live now."

2. **Sign in** (click Try the live demo)
   - Say: "OAuth2 password flow, the backend issues a short-lived JWT, and the
     role rides inside the token. No session cookies, no shared state."
   - One-click sign in as **Customer 001**.

3. **Ask FedE: a read**
   - Type: `where are all my packages?`
   - Narrate: "I never gave a tracking number. The agent chose the
     list_customer_shipments tool itself, with my customer ID from the JWT."

4. **A real mutation**
   - Type: `reschedule FX100001 to next Friday`
   - Then open **Shipments**, pick FX100001, show the new countdown and date.
   - Say: "that was not a canned reply, the database row changed."

5. **Security punchline**
   - Sign out, sign in as **Customer 002**.
   - Type: `cancel FX100001`
   - The agent refuses: FX100001 belongs to Customer 001. Say: "authorization
     is enforced inside every tool, not in the prompt. Even if you jailbreak
     the model, the tool layer says no."

6. **Voice** (click Speak)
   - Show the microphone consent prompt first: "the mic never opens without
     explicit consent."
   - Speak a query with a pause mid-sentence, show it waits for you to finish.

7. **Graceful degradation** (mention, or show if time)
   - "Remove the AI key and the platform still tracks packages through a
     deterministic fallback. Support never goes down with the model."

---

## Architecture: what is actually in the system

```
Browser (Angular 20)
  Landing (/) -> Sign in (/signin) -> Dashboard (/app, route-guarded)
  Text chat | Voice sidebar | Track | Actions | Notifications
        |  HTTPS + Bearer JWT (auth interceptor)
        v
FastAPI backend (port 8000)
  Middleware: CORS allow-list, security headers, per-IP rate limit
  Auth: OAuth2 password flow -> HS256 JWT (PBKDF2 password hashes, stdlib only)
        |
        v
Planner agent (app/agents/planner_agent.py)
  Claude (Anthropic API, model claude-haiku-4-5) in a tool-calling loop:
    model plans -> requests a tool -> we execute -> feed result back -> repeat
  Max 5 rounds, then graceful wrap-up. No key? Deterministic keyword fallback.
        |
        v
6 typed tools (app/agents/agent_tools.py)
  track_shipment, list_customer_shipments, reschedule_delivery,
  redirect_package, cancel_shipment, get_customer_notifications
        |
        v
Shared ops layer (app/services/shipment_ops.py)
  ONE place for business rules + authorization.
  The REST endpoints call the same functions, so agent and API can never drift.
        |
        v
SQLite (SQLAlchemy ORM), seeded demo data
```

## What happens on one query (the trace to narrate)

Query: "reschedule FX100001 to next Friday" as cust001.

1. Angular sends `POST /ask` with the JWT. Middleware checks rate limit and
   CORS; the token is verified and decoded into role + customer_id.
2. The planner builds the prompt: system prompt (persona + rules), the
   caller's role and customer ID, the last 8 conversation turns, the new query.
3. Claude replies not with text but with a **tool call**:
   `reschedule_delivery(tracking_id="FX100001", new_date="2026-07-17")`.
   The model resolved "next Friday" into a date itself.
4. The backend executes the tool. Inside, the ops layer checks: does this
   shipment exist, does this caller own it, is it in a reschedulable state?
   Only then does it update the row.
5. The tool result (success JSON or a polite error) goes back to Claude, which
   writes the human confirmation. The UI also shows structured data and which
   tools ran (`actions_taken`).
6. The final user and assistant turns are stored in bounded conversation
   memory, so "cancel it" in the next message resolves to FX100001.

## Key design decisions (judge Q&A prep)

- **Why an agent and not intents/regex?** Intent routers break on compound or
  ambiguous requests. The model plans multi-step: list my packages, find the
  delayed one, reschedule it. We keep a regex fallback only for offline mode.
- **Why is authorization not in the prompt?** Prompts are suggestions; code is
  law. Every tool re-checks ownership and role against the JWT. A prompt
  injection can change what the model *asks for*, never what it *is allowed to do*.
- **Why Claude Haiku 4.5?** Cheapest Claude tier, about $0.003 per query at
  our measured ~2.9k tokens, and quality is more than enough for structured
  tool selection. Model is one env var (`ANTHROPIC_MODEL`) if we want to scale up.
- **Why one ops layer?** The agent tools and REST endpoints call identical
  functions, so a rule fixed once is fixed everywhere. Also makes the agentic
  loop testable: 31 pytest tests inject a scripted fake model and assert real
  DB mutations.
- **Voice privacy?** Explicit mic consent per session, auto-off on silence,
  speech handled in the browser (no OpenAI key configured), and when Whisper
  mode is used, temp audio files are deleted after transcription.
- **What if Anthropic is down or the key is missing?** `/health` reports
  capability flags; the planner degrades to a deterministic tracker, mutating
  actions return guidance instead of guessing.
- **Security testing?** Bandit SAST clean, pip-audit in CI, CodeQL workflow,
  security middleware tests, fail-closed JWT secret in production.

## Demo credentials

| Account | Password | Role |
|---|---|---|
| agent | fedex-agent-demo | agent (sees all shipments) |
| cust001 | cust001-demo | customer |
| cust002 | cust002-demo | customer (use to show authorization denial) |
| cust003 | cust003-demo | customer |

## Run it

```
cd backend  && .venv/bin/python -m uvicorn app.main:app --port 8000
cd frontend && npx ng serve
# open http://localhost:4200
```

---

## New beats (feature/business-agentic branch)

6. **Animated journey map**
   - Type: `Show me the route for FX100001`
   - A cartoon map appears: the Dijkstra shortest path across the FedEx hub
     network, per-hub live weather risk dots, a truck driving to its live
     position, and an ETA countdown bubble ("~3d 1h to go").
   - Say: "route is computed with Dijkstra over our hub graph; when a hub
     goes weather-red the algorithm reroutes around it and the agent explains
     why. Slightly longer path, reliable ETA: that is service quality."
   - Tracking any package (`where is FX100001?`) also attaches the map.

7. **Autonomy: the fleet-watch agent**
   - (Agent login) `POST /agent/fleet-watch` or wait for the daily cron.
   - It sweeps every active shipment on its own: delays, failed delivery
     attempts, weather threats. Customers get proactive, deduplicated
     notifications with a recommended fix before they ever ask "where is my
     package?".

8. **Memory**
   - Type: `always deliver my packages after 6pm`
   - The agent stores it (save_customer_preference); every later
     conversation is personalized without repeating it.

9. **Human handoff**
   - Type: `my package arrived damaged, I want a refund`
   - The agent writes the case summary itself, opens CASE-XXXX, notifies the
     customer. Works even in offline fallback mode.

10. **Resilient brain**
    - LLM layer is multi-provider: Anthropic first, Groq (free tier) on
      billing failure, keyword router last. The demo cannot be killed by an
      expired API key.

## Pre-demo checklist

- Hit the live URL once ~2 minutes before presenting: Vercel serverless +
  SQLite in /tmp means a cold start re-seeds demo data (state resets if the
  function sleeps; warm it and it stays for the session).
- ETAs are seeded relative to today, so weather forecasts are always real.
- Demo /scan (barcode) locally only: the barcode/OCR native libraries are
  not installed on Vercel.
- Set a working GROQ_API_KEY (or ANTHROPIC_API_KEY with credits) in the
  environment, otherwise chat runs in keyword-fallback mode (tracking,
  weather, hold, escalation still work; free-form planning does not).

## Roadmap (say "roadmap", do not claim as shipped)

- Sentiment analytics: the prompt already nudges escalation when a customer
  sounds upset, but scored sentiment-based routing/priority is roadmap.
- Proactive milestone notifications (picked up / at hub / out for delivery
  push timeline) is roadmap; today's proactive alerts cover delay, failed
  attempt, and weather.

---

## Goal-driven agent beats (second drop on the branch)

11. **Commitments**: as cust001, type "I need FX100001 delivered before friday".
    FedE takes ownership (C-XXXX), then the fleet-watch loop re-checks it every
    sweep: AT_RISK notifications with a customer-first recommendation, a
    "back on track" update when the risk clears, MET on delivery.
12. **Map tracing**: track any package; the map now draws the traveled path in
    orange with checkmarked hubs, and a checkpoint timeline with times sits
    under the map (est. times for upcoming hubs).
13. **Updates** (customers): alerts, the deliveries FedE is watching, and
    every action it took. **Operations** (agent login): fleet totals,
    deliveries at risk, the recent-actions record, and a "Check every
    shipment now" button.
14. **Governance to narrate**: redirect/cancel are tier 3; the TOOL refuses
    without explicit confirmation (try "cancel FX100001": the agent must ask
    first). Every mutation is read back and verified before success is
    claimed. Cost reasoning uses only the sourced $17.78 failed-attempt
    benchmark, and customer satisfaction outranks cost by policy.

Interview line: "We started with a tool-calling support agent and evolved it
into a goal-driven agent that monitors commitments autonomously, balances
customer outcomes above cost-to-serve, operates inside explicit authority
tiers, verifies its actions, and keeps an auditable record of its decisions."
