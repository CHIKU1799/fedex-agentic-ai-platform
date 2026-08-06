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

3. **Ask FedE tab: a read**
   - Type: `where are all my packages?`
   - Narrate: "I never gave a tracking number. The agent chose the
     list_customer_shipments tool itself, with my customer ID from the JWT."

4. **A real mutation**
   - Type: `reschedule FX100001 to next Friday`
   - Then go to the **Track** tab, track FX100001, show the new ETA.
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
