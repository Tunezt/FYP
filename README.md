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

Local Postgres is the development default; Supabase is a deploy target (M12). Nothing
below needs a Supabase, Meta, Railway or Vercel account — the assistant's Gemini calls need
a Google AI Studio key, everything else runs offline.

### 1. Database — Postgres 16 + pgvector

With Docker:

```bash
docker compose up -d        # pgvector/pgvector:pg16, named volume, runs scripts/db-bootstrap.sql once
```

Without Docker (Windows, no admin needed — downloads a stock Postgres 16 + pgvector build
into the git-ignored `.pg16/` folder):

```bash
python scripts/local-pg.py start       # also: stop | status | psql | reset
```

Either way you get superuser `postgres`/`postgres`, database `warung_pintar` on port 5432,
and the restricted `app_role` created by `scripts/db-bootstrap.sql`. The app must connect
as `app_role`: it has no `BYPASSRLS`, so Row-Level Security actually enforces tenant
isolation. The superuser is for alembic only.

### 2. Backend

```bash
cd backend
python -m venv .venv && .venv/Scripts/activate   # or source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                              # local DB URLs are already filled in
alembic upgrade head
python -m app.seed                                # demo café, 30 days of sales
python dev.py                                     # http://localhost:8000
```

`python dev.py` is the entrypoint on Windows, not bare `uvicorn`: it selects the event-loop
policy asyncpg needs there. On macOS/Linux `uvicorn app.main:app --reload --port 8000` also
works.

Smoke check: `curl localhost:8000/health/db` should report the database as reachable.

### 3. Frontend

```bash
cd frontend
npm install
cp .env.example .env.local                        # points at http://localhost:8000
npm run dev                                       # http://localhost:3000
```

Log in as the demo owner (+62 812-000-1111): the OTP is not sent anywhere in development,
it is printed in the backend log as `[DEV] OTP for ... is ...`.

### 4. Tests and gates

```bash
cd backend && python -m pytest -q      # expect 71 passed, 0 skipped
```

The four DB-integration tests (RLS isolation and the concurrent-sale race) run against
`DATABASE_URL` whenever it points at localhost. **If they skip, local Postgres is down** —
`python scripts/local-pg.py status` or `docker compose ps`. To point them elsewhere set
`INTEGRATION_DATABASE_URL`.

Before every commit all four gates in `docs/BUILD-ROADMAP.md` §2 must pass: the test
suite, `alembic upgrade head && alembic downgrade -1 && alembic upgrade head`,
`npm run build` in `frontend/`, and `python -m app.seed`.

### Optional: WhatsApp webhook

Expose the webhook for Meta with a tunnel: `ngrok http 8000`, then register
`https://<tunnel>/webhooks/whatsapp` + your verify token in the Meta App dashboard and
subscribe to the `messages` field.

## Demo

`docs/demo-script.md` is the rehearsed ±8-minute flow. Demo café: **Kopi Kenangan
Senja** — owner +62 812-000-1111 (PIN 1234), staff Sari (2345) / Budi (3456).
`docs/api-contract.md` summarizes every endpoint; `docs/progress.md` is the full
build log including what is still blocked on credentials.

## External services checklist

See `docs/progress.md` for what is currently blocked on which credential. Required:
Google AI Studio key (Gemini), Meta App with WhatsApp product + free test number, two
approved message templates (Utility alert + Authentication OTP — submit on day one,
review takes 24h+), Supabase project with pgvector + a private `receipts` storage bucket,
Vercel + Railway projects (API service + cron service).
