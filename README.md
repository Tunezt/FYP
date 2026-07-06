# Warung Pintar — AI-Powered Business Management with a WhatsApp Assistant

Final-year capstone project. A hybrid AI business management tool for small F&B/retail
businesses in Southeast Asia. The owner runs the business by texting it on **WhatsApp**
(Bahasa Indonesia / Bahasa Malaysia / English, mixed freely), staff record sales on a
**POS kiosk** screen, and an optional **web dashboard** shows AI-derived insights.

```
frontend/   Next.js — owner dashboard, staff POS kiosk, login/registration  → Vercel
backend/    FastAPI — REST API, WhatsApp webhook, AI orchestration, cron jobs → Railway
docs/       progress log, API contract, demo script
```

## Architecture at a glance

- **WhatsApp Cloud API** webhook → FastAPI. Text messages run one Gemini Flash
  `classify_intent` call that routes to **function calling** (structured DB queries via a
  fixed tool set), **RAG** (pgvector similarity over embedded receipt history), or a
  clarification. Image messages skip classification and go straight to **Gemini Pro
  vision** receipt parsing with a low-confidence confirmation gate. All paths converge in
  one response-composition call that replies in the sender's language.
- **PostgreSQL (Supabase) + pgvector**, multi-tenant with Row-Level Security enforced by
  `SET LOCAL app.current_business_id` per request transaction, set from the verified JWT.
- **Proactive alerts**: a nightly Railway Cron Job refreshes 30-day rolling baselines,
  runs z-score anomaly detection and stock-velocity checks, and pushes alerts through an
  approved WhatsApp **Utility template** (free-form messages are rejected outside the
  24-hour session window — this is real Meta behavior).
- **Auth**: owners register/log in on the dashboard with a WhatsApp
  **Authentication-template OTP**; staff use a PIN pad on a paired kiosk. Both flows issue
  FastAPI-signed JWTs with distinct scopes (owner vs. pos).

## Running locally

### Backend

```bash
cd backend
python -m venv .venv && .venv/Scripts/activate   # or source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                              # fill in real values
alembic upgrade head                              # against your Supabase DATABASE_URL
uvicorn app.main:app --reload --port 8000
```

Expose the webhook for Meta with a tunnel: `ngrok http 8000`, then register
`https://<tunnel>/webhooks/whatsapp` + your verify token in the Meta App dashboard and
subscribe to the `messages` field.

### Frontend

```bash
cd frontend
npm install
cp .env.example .env.local                        # point at the backend
npm run dev
```

### Tests

```bash
cd backend && pytest                # pure-logic + mocked tests always run
DATABASE_URL=postgres://... pytest  # additionally runs RLS / concurrency tests
```

### Seed demo data

```bash
cd backend && python -m app.seed   # fictional café: Kopi Kenangan Senja, items, staff, 30 days of sales
```

## Demo

`docs/demo-script.md` is the rehearsed ±8-minute flow. Demo café: **Kopi Kenangan
Senja** — owner +62 812-0000-1111 (PIN 1234), staff Sari (2345) / Budi (3456).
`docs/api-contract.md` summarizes every endpoint; `docs/progress.md` is the full
build log including what is still blocked on credentials.

## External services checklist

See `docs/progress.md` for what is currently blocked on which credential. Required:
Google AI Studio key (Gemini), Meta App with WhatsApp product + free test number, two
approved message templates (Utility alert + Authentication OTP — submit on day one,
review takes 24h+), Supabase project with pgvector + a private `receipts` storage bucket,
Vercel + Railway projects (API service + cron service).
