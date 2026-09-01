# Warung Pintar — Project Status Briefing
*Snapshot: 1 September 2026. Written to hand full context to another assistant with zero prior knowledge.*

---

## 1. What this project is

A **university final-year capstone**: an AI-powered business management system for small
F&B/retail businesses in Southeast Asia (demo persona: an Indonesian café owner with low
digital literacy). The pitch: SME owners track stock/sales/expenses on paper because digital
tools demand new habits — so the tool lives in **WhatsApp**, which they already use daily.

**Three interfaces, one backend:**

| Interface | User | Purpose |
|---|---|---|
| WhatsApp assistant | Owner (primary) | Natural-language queries, receipt photos, corrections, alerts. Understands Bahasa Indonesia / Malaysia / English, mixed freely. |
| POS kiosk (`/pos/{token}`) | Staff | Fast sale entry, PIN login on a shared device. No analytics access. |
| Web dashboard | Owner (secondary) | Visual insight: trends, P&L, stock risk, alerts. |

**Role separation is a graded requirement**, enforced for real: staff/POS JWTs are rejected
at the API with 403, backed by Postgres Row-Level Security — not hidden UI.

**Explicitly out of scope (deliberate, documented):** attendance tracking.

---

## 2. Tech stack (locked by the project brief — don't propose swaps)

- **Frontend:** Next.js 15 (App Router, TS, Tailwind) → Vercel
- **Backend:** FastAPI (Python 3.13, async SQLAlchemy + asyncpg) → Railway
- **DB:** PostgreSQL via Supabase + **pgvector** (no separate vector DB)
- **LLM:** Google Gemini — Flash (`gemini-2.5-flash`) for intent routing / tool calls / reply
  composition; Pro (`gemini-2.5-pro`) for receipt vision; `gemini-embedding-001` (768 dims)
- **Messaging:** WhatsApp Cloud API direct (Meta), not Twilio
- **Jobs:** Railway native Cron (nightly; starts, runs to completion, exits)
- **Auth:** custom FastAPI-signed JWTs — WhatsApp OTP for owners, PIN pad for staff

---

## 3. Build status — what exists and works

**All 11 phases (0–10) are built, plus two live-debugging phases (11–12) and a Session-2 UI
rebuild.** Backend suite: **67 passed, 4 skipped** (the 4 are DB-integration tests that
auto-skip without a live Postgres). Frontend builds and typechecks clean.

### Backend (complete)

- **Schema:** 9 tables (businesses, staff, items, sales, expenses, receipts, alerts +
  metric_baselines, request_logs) plus 2 additive support tables (pending_confirmations,
  login_otps). RLS `tenant_isolation` policy on every business-scoped table, enforced via
  `SET LOCAL app.current_business_id` from the verified JWT.
- **WhatsApp pipeline:** signature-validated webhook → fast 200 ack → background processing.
  One forced Gemini Flash call classifies intent *and* extracts arguments in a single
  round-trip, routing to a **fixed 8-tool set** (never free-form SQL): `get_stock`,
  `get_sales_summary`, `compare_periods`, `get_profit`, `correct_stock`, `get_low_stock`,
  `record_expense`, `search_history` (RAG). A separate composition call writes the reply in
  the owner's language.
- **Vision path:** photo → immediate ack → Supabase Storage (private bucket) → Gemini Pro
  structured parse with a confidence signal → **confirmation gate**: low-confidence or
  ambiguous reads are parked in `pending_confirmations` and the owner must reply YES before
  anything is written. Corrections route through a revision call.
- **Excel import:** separate deterministic openpyxl/pandas path for onboarding stock templates.
- **RAG:** receipt content embedded and searched via pgvector cosine similarity, business-scoped.
- **Jobs:** nightly cron — 30-day rolling baselines cached in `metric_baselines`, z-score
  anomaly detection (|z| > 3), stock-velocity check (days_remaining < 3), delivery via the
  WhatsApp Utility template.
- **Concurrency:** POS sales use the atomic conditional UPDATE (`where current_stock >= qty`);
  a race test proves two simultaneous sales of the last unit → exactly one succeeds.
- **Instrumentation:** `request_logs` captures channel, raw query, classified intent, latency
  and status across WhatsApp / POS / dashboard — this is the CP2 evaluation dataset.
- **Errors:** every user-facing HTTPException detail is Indonesian, with an AST-based
  regression test that fails if a new English one is introduced.

### Frontend (rebuilt in Session 2)

Six dashboard pages (Ringkasan, Penjualan, Stok, Keuangan, Peringatan, Pengaturan) plus
login, a 3-step registration wizard, the POS kiosk, a driver.js first-run tour, and Radix
HelpTips on every non-obvious concept.

**Design direction (owner-approved, from a reference image):** "warung hangat" — warm-light
surfaces, forest-green accent (this *supersedes* the brief's original blue-violet), frosted
glass used as a **material not wallpaper** (one hero surface per page), day-grouped
chronological lists with per-day totals ("Hari ini · Selasa · 15 transaksi · Rp 412.000"),
and reference-grammar stat cards (icon → label → display number → delta chip → sparkline).
Chart palettes were validated computationally for colorblind separation and contrast.

---

## 4. Verified live vs. still unproven — be precise about this

| Item | Status |
|---|---|
| Full backend logic under unit test | ✅ 67 tests passing |
| RLS tenant isolation + concurrent-sale race, against real Postgres | ✅ passed while the DB was alive (4 tests, currently skipped) |
| Gemini **text**: classification → tool → DB → composed reply | ✅ verified live end-to-end (~4s round-trip) |
| Gemini **vision** on real handwritten receipts | ❌ **never run** — the #1 remaining technical risk. 3 sample photos staged in `docs/vision-test-samples/` |
| WhatsApp: templates submitted, webhook registered, live round-trip | ❌ no Meta credentials provisioned |
| Nightly alert delivered to a real phone | ❌ blocked on the above (needs the approved Utility template) |
| Railway / Vercel deploys | ❌ neither CLI installed or authenticated |

---

## 5. 🚨 Current blocker: the Supabase project no longer exists

Diagnosed 1 Sep 2026, three independent confirmations:

- The project host **does not resolve in DNS** (a merely *paused* project still resolves)
- The pooler answers: `(ENOTFOUND) tenant/user app_role.<project_ref> not found`
- Every backend data endpoint returns 500

`backend/.env` was last edited **16 July**; the gap since is consistent with a free-tier
project being paused and then reaped. **Nothing in the code broke.** Fixing it requires
creating a new Supabase project, which needs the owner's account.

**Restore sequence once a new project exists** (~15 min, all scripted):

1. Put the new connection strings in `.env`. Note the deliberate split: `DATABASE_URL` must
   point at the restricted **`app_role`** (no BYPASSRLS); `MIGRATION_DATABASE_URL` at the
   elevated role.
2. `alembic upgrade head` (migrations 0001 + 0002)
3. Create `app_role` with grants + default privileges
4. Create the private `receipts` storage bucket
5. `python -m app.seed` — demo café "Kopi Kenangan Senja"
6. Re-run the 4 RLS/concurrency integration tests to prove isolation still holds

---

## 6. Workaround in place: demo mode

Because the dashboard is unreviewable without data, there is now a **frontend-only demo
mode** (commit `8adc8a6`): `useOwnerData` short-circuits to local fixtures *before any
network request*, so the whole UI renders with realistic data and no database.

- Entered from a button on `/login`, or `?demo=1`
- Gated to dev / `NEXT_PUBLIC_DEMO_MODE=1` — cannot self-enable in a production build
- **Not an auth bypass**: the backend still demands a real JWT on every route
- Every screen carries an amber "data contoh, bukan data asli" banner
- Doubles as a demo-day safety net if Supabase hiccups mid-presentation

---

## 7. Uncommitted work in the tree (owner's own edits, not yet reviewed)

**Backend:** `db_errors.py` (maps DB connect failures to a friendly Indonesian 503),
`dev.py` (Windows event-loop policy entrypoint — **use `python dev.py` locally, not bare
uvicorn**), migration `0002_request_logs_cascade` (fixes a real FK bug where re-seeding
failed once request_logs had rows).

**Frontend:** `ThemeToggle`, `lib/itemCategory.ts` + 15 product photos in `public/items/`
(item rows show a category-matched photo with a 3-tier fallback), `bg-ambient.jpg`,
`ErrorState` + `ItemIcon` components, `formatCompactRupiah` ("1,5jt" / "500rb" chart axes),
a period segmented control on Ringkasan (Hari Ini / Minggu Ini / Bulan Ini / Tahun Ini),
and edits across all six dashboard pages.

---

## 8. Next actions, in dependency order

1. **Create a new Supabase project** → run the restore sequence in §5 (unblocks everything else)
2. **Vision test** — run the 3 staged receipt photos through the vision path and report honest
   confidence/accuracy including failures. Highest-value remaining evidence for the report.
3. **Meta setup** — create the app, submit both templates from `docs/whatsapp-templates.md`
   (Utility `business_alert` + Authentication `login_otp`; review takes 24h+, so submit
   first), register the webhook, allow-list the demo phone, then one live round-trip
4. **Live nightly job** once the Utility template is approved
5. **Deploy** — Railway (API + cron `30 16 * * *`) and Vercel
6. Review and commit the uncommitted work listed in §7

---

## 9. Constraints any plan must respect

- **`DATABASE_URL` must stay on the restricted `app_role`.** Supabase's `postgres` role has
  BYPASSRLS — using it silently disables every tenant-isolation policy. This was a real bug,
  found and fixed in Phase 11.
- **Proactive WhatsApp messages require an approved template.** Free-form messages are only
  legal inside the 24-hour window opened by an inbound message; the nightly job runs outside
  it. This is Meta behavior, not a design preference.
- **The DB schema is locked** — additive changes only (extra indexes, support tables).
- **No free-form text-to-SQL** — the fixed tool set is deliberate (accuracy collapses as FK
  relationships grow).
- The Gemini API key may be in the newer `AQ.<...>` format; that is valid — don't "fix" it.

---

## 10. Repo map & how to run

```
FYP/
  backend/      FastAPI — api/ whatsapp/ ai/ jobs/ models/ schemas/ core/ + alembic/ tests/
  frontend/     Next.js — app/(dashboard) app/pos app/login app/register components/ lib/
  docs/         progress.md (full build log) · demo-script.md · api-contract.md
                whatsapp-templates.md · vision-test-samples/ · PROJECT-STATUS.md (this file)
```

```bash
# backend  → http://localhost:8000  (/docs for OpenAPI)
cd backend && ./.venv/Scripts/python.exe dev.py

# frontend → http://localhost:3000
cd frontend && npm run dev

# tests
cd backend && ./.venv/Scripts/python.exe -m pytest -q
```

**Demo credentials** (after seeding): owner +62 812-000-1111, PIN 1234 · staff Sari 2345 / Budi 3456.

**Full history:** `docs/progress.md` is the authoritative build log — phase by phase,
including every flagged deviation from the brief and the reasoning behind it.
