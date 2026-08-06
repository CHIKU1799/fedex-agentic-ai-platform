# FedE – Proactive Conversational AI for Customer Experience

**Team:** Purple Route 31 | **Digithon Phase 2**

An AI-powered customer support assistant for FedEx that replaces traditional IVR with natural language conversations via text, voice, and barcode scanning.

> **Agentic core:** when an OpenAI key is configured, `/ask` and `/voice` run a
> real **tool-calling agent** (`backend/app/agents/planner_agent.py`) that plans
> and *executes* actions — "reschedule FX100001 to Dec 1" actually reschedules
> the shipment. Without a key it degrades gracefully to an offline keyword
> router that still tracks shipments. See [SECURITY.md](SECURITY.md) for the
> Secure-by-Design posture (OAuth2 auth + authorization, CORS, rate limiting,
> SAST/CodeQL, mic consent).

## Authentication (Secure by Design)

Mutating endpoints (`/reschedule`, `/redirect`, `/cancel`, `/shipment`,
`/notifications/*`) require an OAuth2 bearer token; public tracking does not.

```bash
# Get a token (form-encoded, OAuth2 password flow)
curl -X POST http://127.0.0.1:8000/auth/token \
  -d "username=cust001&password=cust001-demo"
# -> {"access_token":"<jwt>","token_type":"bearer","role":"customer"}

# Use it
curl -X POST http://127.0.0.1:8000/reschedule \
  -H "Authorization: Bearer <jwt>" -H "Content-Type: application/json" \
  -d '{"tracking_id":"FX100001","new_date":"2026-12-10"}'
```

Demo users (dev only): `agent / fedex-agent-demo`, `cust001 / cust001-demo`,
`cust002 / cust002-demo`, `cust003 / cust003-demo`. A customer may only act on
their own shipments; an agent may act on any. To run a pure-UI demo without
auth, set `REQUIRE_AUTH=false` in `.env`.

Run the security-focused test suite:

```bash
cd backend && pytest -q          # 26 tests: auth, authorization, agentic loop, rules
bandit -r app -ll                # static application security testing (SAST)
```

## Prerequisites

- **Python 3.10+** (tested with 3.13)
- **Node.js 18+** (tested with 22.18.0) & **npm 10+**
- **Angular CLI** (installed automatically via npx)
- No Docker required for local dev

## Quick Start

### Backend

```bash
# 1. Navigate to backend
cd backend

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set up environment variables
#    Copy the example and edit as needed
cp ../.env.example ../.env
#    Or on Windows:
copy ..\.env.example ..\.env

# 4. Seed the database (first time only)
python seed_data.py

# 5. Start the server
python -m uvicorn app.main:app --reload --port 8000
```

The backend runs at **http://127.0.0.1:8000**

Interactive API docs (Swagger UI): **http://127.0.0.1:8000/docs**

### Frontend

```bash
# 1. Navigate to frontend (in a separate terminal)
cd frontend

# 2. Install dependencies
npm install

# 3. Start the Angular dev server
npx ng serve --port 4200
```

The frontend runs at **http://localhost:4200**

> **Note:** Both backend and frontend must be running simultaneously. The Angular app communicates with the backend at `http://127.0.0.1:8000`.

## Environment Variables (.env)

Create a `.env` file in the project root (`fedex_agentic_ai_platform/.env`):

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | No | `sqlite:///./fedex.db` | Database connection string |
| `OPENAI_API_KEY` | No* | _(empty)_ | OpenAI API key for AI features |

\* Only required for `/ask` (general AI queries), `/voice` endpoints. All other APIs work without it.

## API Endpoints

### Health Check
```
GET /health
```
Returns `{"status": "ok"}` if the server is running.

### Track Shipment
```
GET /track/{tracking_id}
```
**Example:** `GET /track/FX100001`

**Response:**
```json
{
  "tracking_id": "FX100001",
  "status": "in_transit",
  "location": "Memphis, TN",
  "eta": "2026-06-02",
  "customer_id": "CUST001",
  "origin": "Los Angeles, CA",
  "destination": "New York, NY"
}
```

### AI Query
```
POST /ask
Content-Type: application/json

{"query": "Where is my package FX100002?"}
```
Detects intent (track, reschedule, redirect, cancel, notification, general) and routes accordingly.

### Create Shipment
```
POST /shipment
Content-Type: application/json

{
  "tracking_id": "FX400001",
  "status": "created",
  "location": "Phoenix, AZ",
  "eta": "2026-06-15",
  "customer_id": "CUST003",
  "origin": "Phoenix, AZ",
  "destination": "Las Vegas, NV"
}
```

### Reschedule Delivery
```
POST /reschedule
Content-Type: application/json

{"tracking_id": "FX100001", "new_date": "2026-06-10"}
```
Updates ETA and auto-creates a notification for the customer.

### Redirect Package
```
POST /redirect
Content-Type: application/json

{"tracking_id": "FX100004", "new_address": "123 Main St, Austin, TX"}
```
Updates destination and auto-creates a notification.

### Cancel Shipment
```
POST /cancel
Content-Type: application/json

{"tracking_id": "FX100005", "reason": "Changed my mind"}
```

### Get Notifications
```
GET /notifications/{customer_id}
```
**Example:** `GET /notifications/CUST001`

Returns all proactive notifications (delays, ETA updates, cancellations) for a customer.

### Mark Notification as Read
```
PATCH /notifications/{notification_id}/read
```

### Scan Barcode
```
POST /scan
Content-Type: multipart/form-data

file: <image file with barcode>
```
Requires `pyzbar` native libraries (zbar) installed on the system.

### Voice Chat
```
POST /voice
Content-Type: multipart/form-data

file: <audio file (.wav)>
```
Requires `OPENAI_API_KEY` in `.env`. Pipeline: Speech-to-Text → AI → Text-to-Speech.

## Sample Test Data

After running `python seed_data.py`:

| Tracking ID | Status | Customer ID |
|-------------|--------|-------------|
| FX100001 | in_transit | CUST001 |
| FX100002 | out_for_delivery | CUST001 |
| FX100003 | delivered | CUST002 |
| FX100004 | delayed | CUST002 |
| FX100005 | created | CUST003 |

## Testing with PowerShell

```powershell
# Health check
Invoke-RestMethod -Uri "http://127.0.0.1:8000/health"

# Track shipment
Invoke-RestMethod -Uri "http://127.0.0.1:8000/track/FX100001"

# AI query
Invoke-RestMethod -Uri "http://127.0.0.1:8000/ask" -Method POST `
  -Body '{"query":"Where is my package FX100002?"}' `
  -ContentType "application/json"

# Reschedule
Invoke-RestMethod -Uri "http://127.0.0.1:8000/reschedule" -Method POST `
  -Body '{"tracking_id":"FX100001","new_date":"2026-06-10"}' `
  -ContentType "application/json"

# Get notifications
Invoke-RestMethod -Uri "http://127.0.0.1:8000/notifications/CUST001"
```

## Testing with curl

```bash
# Health check
curl http://127.0.0.1:8000/health

# Track shipment
curl http://127.0.0.1:8000/track/FX100001

# AI query
curl -X POST http://127.0.0.1:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"query":"Where is my package FX100002?"}'

# Reschedule
curl -X POST http://127.0.0.1:8000/reschedule \
  -H "Content-Type: application/json" \
  -d '{"tracking_id":"FX100001","new_date":"2026-06-10"}'

# Get notifications
curl http://127.0.0.1:8000/notifications/CUST001
```

## Frontend UI Components

The Angular frontend provides four main views, accessible via tabs at the top of the page:

### 1. Track (Shipment Tracking)
- Enter a tracking ID to view real-time shipment details
- Displays: status (with color-coded badge), location, ETA, origin, destination
- Status colors: green (delivered), blue (in_transit, out_for_delivery), red (delayed, cancelled), grey (created)

### 2. Ask FedE (AI Chat)
- Natural language chat interface
- Type questions like "Where is my package FX100001?" — the backend detects intent and routes to the correct handler
- Tracking queries work without an OpenAI API key; general questions require the key
- Chat history persists within the session

### 3. Actions (Reschedule / Redirect / Cancel)
- Three sub-tabs for shipment management operations
- **Reschedule:** Change delivery date by providing tracking ID and new date
- **Redirect:** Change delivery address by providing tracking ID and new address
- **Cancel:** Cancel a shipment with an optional reason
- Each action auto-creates a proactive notification for the customer

### 4. Notifications
- View proactive notifications for a customer
- Enter a customer ID to load all notifications (ETA updates, delays, cancellations, redirects)
- Mark individual notifications as read

### 5. Voice Sidebar (FedE Voice Assistant)
Click the orange **"Speak"** button in the header to open the voice sidebar.

**Architecture (from `feature/ui-homepage`):**
- Sidebar slides in from the right with a FedE avatar greeting
- **Hybrid STT engine:** Automatically detects if OpenAI Whisper is available
  - **With API key:** Records audio via `MediaRecorder` → sends to `POST /voice` → backend transcribes with **OpenAI Whisper** (most accurate)
  - **Without API key:** Falls back to **browser SpeechRecognition** → sends text to `POST /ask` (free, no key needed)
- Auto-detects mode on first interaction — no manual configuration
- Responses spoken aloud via browser `speechSynthesis` (TTS — free, no API key)
- Displays conversation as chat bubbles (user messages + FedE responses)
- Tracking results rendered as detail cards with status badges
- Microphone toggle button (mic/stop icon) with recording waveform animation
- Barge-in support: click mic while FedE is speaking to interrupt and ask a new question
- Click backdrop or close button to dismiss

**Voice Flow:**
1. Click **Speak** → sidebar opens → FedE greets: *"Hey, I am FedE. What can I help you with?"*
2. Microphone activates automatically after greeting
3. Speak your query (e.g., *"Where is my package FX100001?"*)
4. Click stop (or auto-detect with Whisper) → processes query → shows result + speaks it
5. Tap mic again for follow-up questions

## Testing the UI

Open **http://localhost:4200** in your browser (both backend and frontend must be running).

### Test Data for Each Tab

After running `python seed_data.py`, use the following data:

#### Track Tab
| Input | Expected Result |
|-------|----------------|
| `FX100001` | In Transit — Memphis, TN — ETA 2026-06-02 |
| `FX100002` | Out for Delivery — Chicago, IL — ETA 2026-05-28 |
| `FX100003` | Delivered — Dallas, TX |
| `FX100004` | Delayed — Denver, CO — ETA 2026-06-05 |
| `FX100005` | Created — Atlanta, GA — ETA 2026-06-10 |

#### Ask FedE Tab
| Input Message | Expected Behavior |
|---------------|-------------------|
| `Where is my package FX100001?` | Returns tracking info: Memphis, TN, in_transit |
| `Track FX100004` | Returns tracking info: Denver, CO, delayed |
| `What is the status of FX100002?` | Returns tracking info: Chicago, IL, out_for_delivery |
| `Hello` | Requires OPENAI_API_KEY — shows error if key not set |

#### Actions Tab
| Action | Tracking ID | Additional Input | Expected Result |
|--------|-------------|------------------|-----------------|
| Reschedule | `FX100001` | New Date: `2026-06-15` | Success — ETA updated |
| Redirect | `FX100002` | New Address: `456 Oak Ave, Houston, TX` | Success — destination updated |
| Cancel | `FX100005` | Reason: `Changed my mind` | Success — status set to cancelled |

#### Notifications Tab
| Customer ID | Expected Notifications |
|-------------|----------------------|
| `CUST001` | 2 notifications (FX100001 transit update, FX100002 out for delivery) |
| `CUST002` | 1 notification (FX100004 delay alert) |
| `CUST003` | 0 notifications initially; appears after performing Actions on FX100005 |

> **Tip:** After performing Reschedule/Redirect/Cancel actions, check the Notifications tab — new notifications are auto-created for the customer.

### What Works Without an OpenAI API Key

| Feature | Works? |
|---------|--------|
| Track shipment | ✅ Yes |
| Ask FedE — tracking queries (e.g. "Where is FX100001?") | ✅ Yes |
| Ask FedE — general AI questions | ❌ Requires API key |
| Reschedule / Redirect / Cancel | ✅ Yes |
| Notifications | ✅ Yes |
| Voice chat — OpenAI Whisper STT (`/voice`) | ❌ Requires API key (most accurate) |
| Voice chat — Browser STT fallback | ✅ Yes (auto-fallback, less accurate with numbers) |
| Barcode scan (`/scan`) | ⚠️ Requires pyzbar native libraries |

## Project Structure

```
fedex_agentic_ai_platform/
├── .env                  ← Environment variables
├── .env.example          ← Template for .env
├── API.md                ← Detailed API documentation
├── docker-compose.yml    ← Docker setup (optional)
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── seed_data.py      ← Sample data loader
│   └── app/
│       ├── main.py       ← FastAPI entry point
│       ├── api/
│       │   └── routes.py ← All API endpoints
│       ├── agents/
│       │   ├── planner_agent.py   ← Intent detection & routing
│       │   ├── tracking_agent.py  ← Shipment lookup
│       │   ├── voice_agent.py     ← Voice pipeline (STT→AI→TTS)
│       │   └── ocr_agent.py       ← Image text extraction
│       ├── services/
│       │   ├── llm_service.py     ← OpenAI chat completion
│       │   ├── speech_service.py  ← OpenAI STT & TTS
│       │   └── barcode_service.py ← Barcode decoding
│       ├── db/
│       │   ├── database.py ← DB connection (SQLite/PostgreSQL)
│       │   ├── models.py   ← Shipment & Notification tables
│       │   └── crud.py     ← Database operations
│       ├── schemas/
│       │   └── request_schema.py ← Request/response validation
│       └── core/
│           ├── config.py  ← Environment config
│           └── logger.py  ← Logging setup
└── frontend/
    ├── package.json
    ├── angular.json
    ├── tsconfig.json
    └── src/
        └── app/
            ├── app.ts               ← Root component (tab navigation)
            ├── app.html             ← Main layout template
            ├── app.css              ← FedEx-themed styles (purple/orange)
            ├── app.config.ts        ← Angular providers (HttpClient)
            ├── services/
            │   └── api.service.ts   ← Backend API integration service
            └── components/
                ├── tracking/
                │   ├── tracking.ts  ← Track shipment component
                │   ├── tracking.html
                │   └── tracking.css
                ├── chat/
                │   ├── chat.ts      ← AI chat component
                │   ├── chat.html
                │   └── chat.css
                ├── actions/
                │   ├── actions.ts   ← Reschedule/Redirect/Cancel
                │   ├── actions.html
                │   └── actions.css
                └── notifications/
                │   ├── notifications.ts   ← Customer notifications
                │   ├── notifications.html
                │   └── notifications.css
                └── voice-sidebar/
                    ├── voice-sidebar.ts   ← Voice assistant (hybrid Whisper/browser STT)
                    ├── voice-sidebar.html ← Sidebar template with chat bubbles
                    └── voice-sidebar.css  ← Slide-in panel & waveform styles
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Framework | FastAPI (Python) |
| Database | SQLite (local) / PostgreSQL (Docker) |
| ORM | SQLAlchemy |
| AI | OpenAI APIs (GPT-4o-mini, Whisper) |
| Voice STT | OpenAI Whisper (primary) / Browser SpeechRecognition (fallback) |
| Voice TTS | Browser speechSynthesis (free) |
| Validation | Pydantic |
| Server | Uvicorn |

## Notes

- The SQLite database file (`fedex.db`) is auto-created in the `backend/` folder on first run.
- `/scan` endpoint requires zbar native libraries. On Windows, install from https://sourceforge.net/projects/zbar/
- `/voice` endpoint uses OpenAI Whisper for speech-to-text. Without an API key, the voice sidebar automatically falls back to browser SpeechRecognition (less accurate with alphanumeric IDs like FX100001).
- General `/ask` queries (non-tracking) require a valid `OPENAI_API_KEY`. Tracking, reschedule, redirect, cancel, and notification APIs work without it.
- Logs are written to `backend/logs/app.log`.
