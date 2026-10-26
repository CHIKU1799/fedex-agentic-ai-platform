# Secure by Design — FedE

Security is built into the FedE architecture, not bolted on. This document
maps each control to where it lives in the code, so it can be reviewed and
demonstrated. It directly addresses the "Secure by Design" evaluation
criteria (CORS, SAST/DAST, CodeQL, secrets management, OAuth2 auth +
authorization, and microphone consent).

## 1. Authentication & Authorization (OAuth2)

- **OAuth2 password flow** issues short-lived HS256 JWTs at `POST /auth/token`.
  Implementation: `backend/app/core/security.py`.
- **Passwords** are stored as PBKDF2-HMAC-SHA256 hashes (240k iterations,
  per-credential salt) and compared in constant time. No plaintext credential
  ever lives in the process.
- **JWTs** are signed and verified with `hmac.compare_digest` — a tampered
  signature or an expired token is rejected (verified by tests + live).
- **No third-party crypto dependency**: auth uses only the Python standard
  library, so there is no external crypto package to keep patched.
- **Role-based authorization** (`authorize_shipment_access`):
  - `agent` — FedEx staff; may act on any shipment.
  - `customer` — may act **only on their own** shipments (customer_id match).
  - `guest` — anonymous; may use public tracking only.
- **Single choke-point**: both the REST routes and the agentic tool layer call
  `app/services/shipment_ops.py`, so a rule or access check can never be
  enforced in one entry point and forgotten in the other.
- **Fail-closed secrets**: in `ENV=production` the app refuses to start without
  a real `JWT_SECRET` (`app/core/config.py`).

Demo credentials (development only): `agent / fedex-agent-demo`,
`cust001 / cust001-demo`, `cust002 / cust002-demo`, `cust003 / cust003-demo`.

## 1b. Agent governance (the model cannot overstep)

- **Autonomy tiers live in code** (`app/agents/agent_tools.py`, `AUTONOMY_TIERS`):
  read-only tools act silently; reversible changes act then inform; high-impact
  changes (redirect, cancel) are **refused by the tool** until the customer has
  confirmed. The UI turns that refusal into a verification pop-up with one
  clear yes or no; the Shipments screen asks the same way, and cancelling makes
  the customer type the tracking number.
- **The confirmation is held by the platform, not the model**
  (`app/agents/confirmations.py`). A refusal records what was asked, scoped to
  user + session, and the turn is locked so the model can only ask. The gate
  opens only when the customer's *own next message* says yes, and only for
  that exact tool and shipment. A `confirmed=true` argument from the model is
  stripped and ignored. This closed a live bypass found in testing, where the
  model retried with `confirmed=true` in the same turn.
- **Conversation memory is scoped to the signed-in user** as well as the
  client session id, so a guessed or shared session id never exposes another
  customer's history or pending confirmation.
- **Every mutation is verified** by re-reading the row (`_VERIFY_CHECKS`)
  before success is reported; a mismatch is returned as an error the model
  must relay honestly.
- **Integrity guard** (`app/agents/planner_agent.py`): if the customer asked
  for a change, no mutating tool ran, and the reply still claims completion,
  the planner gives the model one correction round and otherwise replaces the
  reply with an honest one. The response carries `integrity: clean |
  corrected | overridden` and the UI shows the correction.
- **Every action is logged** (`agent_events`) with who, what, outcome and
  verification, surfaced to customers under Updates and to agents under
  Operations.

## 2. CORS

- Explicit allow-list from `ALLOWED_ORIGINS` (default `http://localhost:4200`).
  **Never** `*` alongside credentials (both a spec violation and over-broad).
- Methods/headers scoped to what the SPA actually uses.
  Implementation: `backend/app/main.py`.

## 3. HTTP security headers

API responses get them from `SecurityHeadersMiddleware`
(`backend/app/core/middleware.py`): `X-Content-Type-Options: nosniff`,
`X-Frame-Options: DENY`, `Content-Security-Policy: default-src 'none'`,
`Referrer-Policy`, `Strict-Transport-Security`.

The static Angular app gets its own set from `vercel.json` `headers`: a
strict **Content-Security-Policy** (`script-src 'self'`, `connect-src 'self'`,
fonts only from Google Fonts, `object-src 'none'`, `frame-ancestors 'none'`,
`upgrade-insecure-requests`), HSTS with preload, `Permissions-Policy`
(microphone only for the app itself; camera, geolocation, payment denied),
`Cross-Origin-Opener-Policy`, `X-Frame-Options`, `Referrer-Policy: no-referrer`.

## 3b. Sessions in the browser

- The access token is kept in **sessionStorage**, so it ends with the tab and
  never lingers in a shared browser. Its expiry is read from the JWT and shown
  in the app rail ("Session ends in 42 min").
- A `401` from any endpoint, or a token past its expiry, signs the customer
  out and returns them to sign-in with a plain explanation
  (`frontend/src/app/auth.interceptor.ts`).
- Chat replies are rendered through an HTML-escaping formatter plus Angular's
  sanitizer, so model output cannot inject markup.

## 4. Rate limiting (abuse / brute-force / LLM-cost control)

Per-client-IP fixed-window limiter (`RateLimitMiddleware`), configurable via
`RATE_LIMIT_REQUESTS` / `RATE_LIMIT_WINDOW_SECONDS`. Returns `429` with
`Retry-After`. Health checks and CORS preflight are exempt. In production this
moves to the already-provisioned Redis for cross-replica limits.

## 5. Input validation

All request bodies are validated at the edge (`app/schemas/request_schema.py`):
tracking IDs must match `FX\d+` / digit patterns, dates must be `YYYY-MM-DD`,
addresses and free-text are length-bounded. Malformed input is rejected with
`422` before it reaches the database or the LLM tool layer.

## 6. Microphone consent & auto-off (privacy by design)

Frontend `voice-sidebar` (`frontend/src/app/components/voice-sidebar/`):
- The microphone is **never** opened without explicit customer consent
  (consent dialog gates every activation, not just the first).
- Consent is **session-scoped** and reset when the panel closes.
- The mic **auto-turns-off** after a request completes and after an inactivity
  timeout; hardware `MediaStream` tracks are always released.
- A persistent "Microphone is off" indicator reassures the user of state.
- Captured audio is deleted from disk immediately after transcription
  (`backend/app/services/speech_service.py`).

## 7. SAST / DAST / CodeQL (CI/CD)

`.github/workflows/`:
- **`ci.yml`** — runs the pytest suite, **Bandit** SAST (fails on medium+
  severity), and a `pip-audit` dependency scan on every push/PR.
- **`codeql.yml`** — GitHub **CodeQL** `security-and-quality` analysis for both
  Python and TypeScript/JavaScript, on push/PR and a weekly schedule.

Local SAST: `cd backend && bandit -r app -ll` (currently: 0 medium/high issues).

## 8. Test coverage of security behavior

`backend/tests/` (99 tests, all passing):
- `test_auth_and_authz.py` — token issuance, bad-credential rejection,
  anonymous mutation blocked (401), cross-customer access blocked (403),
  agent override, tampered/invalid token rejection.
- `test_agentic_planner.py` — the agent's tool calls respect authorization
  (a non-owner's cancellation is refused and the DB is unchanged).
- `test_business_rules.py` — state guards + input validation (incl. an
  injection-style tracking ID rejected with 422).
- `test_security_middleware.py` — headers present, CORS not wildcard,
  rate-limit returns 429.
- `test_integrity_guard.py` — an unbacked "Done!" is corrected into a real,
  verified action or replaced with an honest reply; tier-3 refusals are
  flagged for the confirmation pop-up; honest questions are not flagged.
- `test_confirmation_gate.py` — the model cannot self-confirm in the same
  turn or with a `confirmed` flag; a "no" clears the ask; a "yes" covers only
  the shipment asked about; sessions are scoped per user.

## Threat-model notes / follow-ups

- Move JWT secret and OpenAI key into a managed vault (HashiCorp Vault / cloud
  KMS) for production; `.env` is for local dev only and is git-ignored.
- Add refresh-token rotation and token revocation list for longer sessions.
- Enable DAST (e.g. OWASP ZAP baseline) against a deployed preview in CD.
