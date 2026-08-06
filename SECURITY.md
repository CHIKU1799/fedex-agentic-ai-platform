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

## 2. CORS

- Explicit allow-list from `ALLOWED_ORIGINS` (default `http://localhost:4200`).
  **Never** `*` alongside credentials (both a spec violation and over-broad).
- Methods/headers scoped to what the SPA actually uses.
  Implementation: `backend/app/main.py`.

## 3. HTTP security headers

Applied to every response via `SecurityHeadersMiddleware`
(`backend/app/core/middleware.py`): `X-Content-Type-Options: nosniff`,
`X-Frame-Options: DENY`, `Content-Security-Policy`, `Referrer-Policy`,
`Strict-Transport-Security`.

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

`backend/tests/` (26 tests, all passing):
- `test_auth_and_authz.py` — token issuance, bad-credential rejection,
  anonymous mutation blocked (401), cross-customer access blocked (403),
  agent override, tampered/invalid token rejection.
- `test_agentic_planner.py` — the agent's tool calls respect authorization
  (a non-owner's cancellation is refused and the DB is unchanged).
- `test_business_rules.py` — state guards + input validation (incl. an
  injection-style tracking ID rejected with 422).
- `test_security_middleware.py` — headers present, CORS not wildcard,
  rate-limit returns 429.

## Threat-model notes / follow-ups

- Move JWT secret and OpenAI key into a managed vault (HashiCorp Vault / cloud
  KMS) for production; `.env` is for local dev only and is git-ignored.
- Add refresh-token rotation and token revocation list for longer sessions.
- Enable DAST (e.g. OWASP ZAP baseline) against a deployed preview in CD.
