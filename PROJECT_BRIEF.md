# Build Brief: AI-Powered Business Management System with WhatsApp Assistant

You are building a university final-year capstone project, ahead of schedule. Read this whole document before writing any code — it is the complete, finalized plan. Work through it in the phased order at the bottom. Build ambitiously and completely within each phase rather than pausing for confirmation on every small step; log what you did and why in `docs/progress.md` as you go so decisions are reviewable and fixable afterward instead of re-litigated.

**Scope philosophy:** this is a demo-quality system, not enterprise infrastructure — one person builds this, so stay realistic in scope. But "demo-quality" describes scope, not license for sloppy execution. Whatever you build should actually work: responsive, no lag, no flaky pieces held together with hope. Prioritize a real working vertical slice of every core flow over polishing any single flow to death. Mock/seed data for a fictional café is fine for demoing.

**Explicitly out of scope for this build:** attendance tracking. The written capstone proposal (Chapter 3) mentions attendance as a possible function-calling use case, but it is a deliberate, documented omission here — do not build an `attendance` table, tool, or WhatsApp intent for it.

**If you hit a genuine structural problem with anything below, flag it explicitly and explain why — do not silently deviate from this plan.**

### What's locked vs. what's genuinely open to your judgment

Not everything below carries equal weight — know which is which so you're not treating every line as gospel, and aren't afraid to actually exercise judgment where it's warranted:

**Locked — these came from real research into external constraints and grading-relevant requirements, not arbitrary preference. Follow them; flag loudly if one seems wrong rather than quietly working around it:**
- The tech stack (Section 2) — each choice has a specific reason tied to this project's scale or a real API limitation, not just a default pick.
- The WhatsApp 24-hour session window behavior and the template-message requirement for proactive alerts (Section 4) — this is real Meta API behavior, not a design preference, and it's easy to build something that looks like it works in dev and silently fails in the actual unprompted-alert scenario.
- **The database schema in Section 6 — use the DDL as given, not as a rough sketch to redesign.** This is a finished design, not a placeholder: the 7 entities were specified as ground truth requirements, and the exact columns/types/RLS policies/concurrency-safe update pattern already reflect real correctness and security decisions (multi-tenant isolation, race-condition safety on stock writes). Don't restructure tables, rename columns, or change the RLS approach on your own judgment — if you find an actual bug or gap in it while implementing, flag it and propose a specific fix rather than quietly reshaping it.
- The atomic stock-decrement approach and the anomaly/velocity formulas (Section 7) — these encode actual correctness requirements, not style choices.
- The phase ordering (Section 12) — the vertical-slice-first sequencing is deliberate so there's always something demoable; don't reorder into "build all of the backend, then all of the frontend."

**Open — treat these as strong defaults, not mandates. Use your own judgment, and improve on them where you have a better idea; just note meaningfully different choices in `docs/progress.md` rather than silently drifting:**
- Additive schema refinements only — e.g. an extra index you discover you need once a specific query pattern shows up during a later phase. Not a redesign of what's already there.
- Component structure, file organization within each app, specific small-library choices for utilities not already named in Section 2.
- Visual execution within the chosen design direction (Section 9) — exact spacing, shadow values, animation/transition choices, microcopy, empty states. The direction (iOS-Native Glass, avoid generic AI-template look) is the requirement; the craft that achieves it is yours to bring.
- Anything not explicitly addressed here at all — use good judgment and document the decision.

---

## 1. What this system is

A hybrid AI business management tool for small/medium F&B and retail businesses in Southeast Asia (primary example: a café, but should generalize to any small business tracking sales, stock, and expenses). The owner manages their entire business through a **WhatsApp chat interface** — no app to download, no dashboard to learn. Staff use a **simple POS kiosk screen** to record sales. The owner also has an optional **web dashboard** for visual business insights.

Core problem it solves: small business owners in SEA track stock, sales, and expenses on paper or in their head. Digital tools require training and new habits. WhatsApp is already how these owners communicate daily — the business tool should live there instead.

### The three interfaces

**A. WhatsApp Assistant (primary, owner-facing).** Owner texts the business like they'd text a person — natural language, not a menu bot. Understands Bahasa Indonesia, Bahasa Malaysia, and English, mixed naturally in the same conversation (handle this at the prompt/orchestration level, not with separate per-language pipelines).

**B. Staff POS screen (staff-facing).** Fullscreen kiosk-mode web page for recording sales at the point of transaction. No AI conversation — fast, simple sale entry (item, quantity, price, done). "Simple" means interaction complexity (few taps, big touch targets, no clutter), not visual polish — it should still look premium.

**C. Web dashboard (owner-facing, secondary).** Browser dashboard with AI-derived insights: sales trends, anomaly flags, stock risk indicators, a simple P&L view combining POS sales with parsed receipt/expense data. This is where "AI did the thinking for you" shows up visually, complementing WhatsApp rather than duplicating it.

### Role separation is first-class, not an afterthought

Owners get full access (WhatsApp assistant + dashboard). Staff get POS-only access — no visibility into dashboard analytics, other staff's data, or owner-level commands. Enforce with real permission checks (backend + RLS), not hidden UI. A staff account hitting an owner-only WhatsApp command or API route must be explicitly rejected. Build an explicit test proving this before considering the project done (see Phase 8).

---

## 2. Tech stack (final — build with these; flag before deviating)

- **Frontend (dashboard + staff POS):** Next.js (React) → Vercel
- **Backend:** FastAPI (Python) → Railway
- **Database:** PostgreSQL via Supabase, with the **`pgvector`** extension enabled. (Not Chroma — already on Postgres, so pgvector removes an entire extra moving part: no separate vector DB to run/host/back up, and vector search can `JOIN` against relational data like `business_id` in one SQL query. Chroma is a prototyping tool that degrades under concurrent load past small scale; not worth adding as a second database here.)
- **LLM: Google Gemini API** — used for (a) function calling/tool use, (b) vision (receipt image parsing), (c) natural-language response composition, (d) embeddings. Use **Gemini Flash tier** as the default for intent classification, tool-calling, and reply composition (fast/cheap). Step up to **Gemini Pro tier** specifically for receipt vision-parsing, where extraction accuracy on messy photographed receipts matters more than latency. Confirm exact current model names at build time (Google's naming shifts).
- **Embeddings: Gemini's native embeddings** (`gemini-embedding-001`), multilingual across 100+ languages. No separate embeddings provider (e.g. Voyage AI) is needed — one Google AI API key covers chat, tools, vision, and embeddings.
- **File storage: Supabase Storage** for receipt/ledger images (referenced by `receipts.image_url`) — same platform as the DB, so it's RLS-compatible and it's one less external service to provision. Use a **private bucket**, not public, with access scoped per business (signed URLs or a storage policy keyed off `business_id`, mirroring the Postgres RLS pattern in Section 6) — receipt photos are business-sensitive data and should never be publicly readable by URL guessing.
- **RAG orchestration:** LangChain, using `SupabaseVectorStore` (pgvector-backed) with the embeddings backend pointed at Gemini.
- **Messaging:** WhatsApp Cloud API — Meta's own API directly, not Twilio.
- **Background jobs:** Railway's **native Cron Jobs** (a service that starts on a crontab schedule, runs to completion, exits — no idle billing between runs). Not an always-on APScheduler/Celery worker — that would sit idle 23h59m/day for one nightly job, which is unnecessary cost/complexity at this scale.
- **Auth:** custom-built, not off-the-shelf Supabase Auth flows (see Section 5) — WhatsApp-OTP for owners, PIN pad for staff, both issuing FastAPI-signed JWTs. Postgres Row-Level Security enforced via a per-request `SET LOCAL app.current_business_id`, set from the verified JWT claim.

---

## 3. Repo structure

Single repo, no monorepo tooling (frontend and backend share zero code — different languages, so Turborepo/pnpm-workspaces would be pure overhead):

```
fyp-business-assistant/
  frontend/                  # Next.js — dashboard + POS + owner login
    app/
      (dashboard)/           # owner-only routes
      pos/[businessToken]/   # staff kiosk routes
      login/
    components/
    lib/
  backend/                   # FastAPI
    app/
      api/                   # REST routes (dashboard + POS)
      whatsapp/              # webhook receive/verify, send, media download
      ai/                    # intent classification, tool defs, RAG, vision parsing, response composer
      jobs/                  # anomaly detection, stock velocity — entrypoints for Railway cron
      models/                # SQLAlchemy models
      schemas/               # Pydantic request/response schemas
      core/                  # config, JWT auth, DB session, RLS context helper
    alembic/                 # migrations
    tests/
  docs/
    progress.md              # running build log — see "Working practices" below
    erd.md / api-contract.md # optional supporting docs
  README.md
  .env.example                (one per app, documenting every required variable — never commit real secrets)
```

**First step of all:** `git init` a clean repo here. Do not nest this inside any other existing git repository — start fresh history.

---

## 4. WhatsApp message flow (the core architecture)

Every inbound WhatsApp message is handled like this:

1. `GET /webhooks/whatsapp` — Meta's verification handshake (`hub.challenge` + verify-token check).
2. `POST /webhooks/whatsapp` — validate `X-Hub-Signature-256` (HMAC with the app secret) before processing anything. Return `200 OK` immediately (Meta expects a fast ack; don't make Meta wait on the LLM round-trip or it'll retry the webhook).
3. Resolve which business this message belongs to by matching the sender's phone number against `businesses.owner_phone` (see Section 6 on why, and Section 5 on scope).
4. Branch on `message.type`:
   - **`image`** → send an immediate WhatsApp ack ("Got your receipt, processing...") since vision parsing takes longer than a text round-trip → download the media via Meta's media API → upload it to **Supabase Storage** (private bucket) and store the resulting reference in `receipts.image_url` → send to **Gemini Pro** for vision parsing with structured-output instructions (items, prices, supplier, total, date, **plus a confidence signal**) → **confirmation gate (correctness requirement, not optional polish):** if the extraction is low-confidence or ambiguous (e.g. handwritten Bahasa Indonesia/Malay stock books are the single biggest technical risk in this project — messy handwriting, ambiguous quantities/units), do **not** write to the DB yet. Instead send the owner a WhatsApp confirmation summarizing what was read (e.g. *"I read: 8kg arabica, 2kg sugar — correct? Reply YES to save, or tell me what's wrong."*) and only commit to `receipts`/`expenses`/`items` once the owner confirms. Only skip the confirmation step for genuinely high-confidence, unambiguous extractions. Never silently write a low-confidence parse. Once confirmed (or if confidence was already high): write to `receipts` + `expenses` (+ update `items` stock if it's a stock-relevant receipt) → embed the parsed content with `gemini-embedding-001` → upsert into `receipts.embedding` (pgvector). No LLM intent classification needed here — the message type already tells you it's the vision path.
   - **`document`** (e.g. `.xlsx`) → separate code path from vision: parse with `openpyxl`/`pandas`, used for the onboarding stock-template import (Section 8, Scenario 1). Don't fold this into the vision pipeline — it's a different kind of parsing entirely.
   - **`text`** → run one Gemini Flash call with a forced `classify_intent` tool call, routing to exactly one of:
     - **function calling** — for structured queries against data already in the DB (e.g. "how much arabica coffee stock do I have left", "how much did I sell today"). Gemini picks from a small set of predefined tool/function definitions and calls them with arguments — do **not** let the model generate arbitrary SQL; a fixed tool set avoids the accuracy collapse free-form text-to-SQL suffers as the schema's foreign-key relationships grow.
     - **RAG** — for queries where the answer lives in unstructured history (e.g. "have I bought sugar from this supplier before"). Embed the query with `gemini-embedding-001`, run a pgvector cosine-similarity search over `receipts` scoped to `business_id`, pass the top-k matches back to Gemini as context.
5. All three paths (function calling / RAG / vision) converge into one final **response composition** call to Gemini — turn whatever was retrieved/computed into a natural, conversational reply in the detected language. Keep this as its own step, separate from the "figure this out" logic, so the two concerns don't get tangled.
6. Send the reply via `POST /{phone_number_id}/messages`.
7. Log latency + outcome for every WhatsApp interaction — `business_id`, path taken, the **raw inbound query text**, the **classified intent**, total latency, status (see the `request_logs` fields in Section 6). Capturing the raw query and classified intent alongside path/latency isn't just general instrumentation — it's what makes response-accuracy evaluation (which path was chosen vs. what the query actually needed) computable later from logs alone, without manual reconstruction.

**Proactive side, independent of inbound messages:** a nightly Railway Cron Job runs anomaly detection + stock-velocity checks (Section 7) and pushes any triggered alerts to the owner via WhatsApp.

**Critical WhatsApp constraint — read this before building Phase 5:** WhatsApp only allows free-form ("service") messages within a 24-hour window that opens when the user last messaged you. The nightly proactive alert job runs unprompted, almost certainly outside that window — Meta will reject a free-form message there. You must create and get Meta to **approve a Utility-category message template** before proactive alerts can work at all (e.g. `"⚠️ {{1}} alert for {{2}}: {{3}}. Reply for details."`). Template review can take up to 24 hours or more. **Submit this for review on day one (Phase 0)**, not when you reach Phase 5 — don't let approval lead time block that phase later.

---

## 5. Auth design (reconciled against the written capstone proposal — dashboard-first onboarding)

**Note on a proposal-vs-earlier-brief reconciliation:** an earlier version of this brief implied WhatsApp-first identity ("no password/login anywhere, everything starts in chat"). The written capstone proposal (Chapter 3) is explicit that onboarding **starts on the web dashboard** — the owner registers and logs in there, fills in the business profile, and creates staff accounts, and only uses WhatsApp for day-to-day operation afterward. This section now reflects the proposal. Log this reconciliation in `docs/progress.md` when you implement it.

**Owner → first-time registration (dashboard-first):** the owner's flow starts on the web dashboard, not WhatsApp. They register with their phone number + business name on a sign-up page. To verify the phone number, the backend sends a 6-digit code via a WhatsApp **Authentication-category template** (not a free-form message — this matters: Authentication templates are Meta's purpose-built category for OTP delivery and work regardless of whether a 24-hour session window is open, unlike a free-form service message, so the owner does **not** need to message the business first). Once verified, the owner completes the business profile (name, location, operating hours) and creates staff accounts (name + PIN) — all on the dashboard. This is the one-time setup; only *after* it's done does the owner move to using WhatsApp for day-to-day operation (queries, receipt photos, alerts).

**Owner → returning dashboard login:** same phone-number + WhatsApp-Authentication-template-OTP mechanism as registration, now just verifying an existing `businesses.owner_phone` instead of creating a new record. Backend issues a short-lived JWT (custom, signed by FastAPI — not Supabase Auth's built-in phone/email flows, since there's no separate SMS provider needed this way). If this turns out awkward in practice, a plain Supabase Auth email/magic-link fallback is a reasonable simplification — flag it if you hit friction, don't silently swap it.

**Staff → POS kiosk login:** lightweight **PIN pad**, not a full account system (the kiosk is a shared device). One-time setup: owner generates a pairing link/QR from the dashboard (`/pos/{business_token}`), opened once on the shop's kiosk browser and persisted via `localStorage` so it always boots straight to "pick your name → enter 4-digit PIN" for that business. Backend issues a short-lived, POS-scoped JWT with no dashboard/analytics claims.

**Business resolution for inbound WhatsApp messages:** true multi-tenant production would need one WhatsApp Business number per tenant (real cost/setup per business) — out of scope for a one-person capstone demo. Instead, resolve the business by matching the sender's phone number against `businesses.owner_phone`, using a single shared WhatsApp number (Meta's free test number supports up to 5 allow-listed recipient numbers — plenty for a demo). Keep the schema/routing logic multi-tenant-correct; only the "one number per business" scaling detail is explicitly deferred.

---

## 6. Database schema

7 core entities (businesses, staff, items, sales, expenses, receipts, alerts) plus 2 support tables (a cached rolling-baseline table for anomaly detection, and a request-log table for latency instrumentation). Full DDL:

```sql
create extension if not exists vector;
create extension if not exists pgcrypto;

create type staff_role as enum ('owner', 'staff');
create type expense_source as enum ('manual', 'receipt');
create type alert_type as enum ('anomaly', 'low_stock');
create type alert_severity as enum ('low', 'medium', 'high');

create table businesses (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  business_type text not null default 'cafe',
  owner_phone text not null unique,
  whatsapp_number text,
  language_preference text not null default 'id',
  timezone text not null default 'Asia/Jakarta',
  onboarding_completed_at timestamptz,
  created_at timestamptz not null default now()
);

create table staff (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  name text not null,
  role staff_role not null default 'staff',
  phone text,
  pin_hash text not null,
  is_active boolean not null default true,
  created_at timestamptz not null default now()
);
create index idx_staff_business on staff(business_id);

create table items (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  name text not null,
  unit text not null,
  current_stock numeric(12,3) not null default 0 check (current_stock >= 0),
  cost_price numeric(12,2) not null default 0,
  sell_price numeric(12,2) not null default 0,
  reorder_threshold numeric(12,3) not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index idx_items_business on items(business_id);

create table sales (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  item_id uuid not null references items(id),
  quantity numeric(12,3) not null check (quantity > 0),
  unit_price numeric(12,2) not null,
  total_price numeric(12,2) not null,
  staff_id uuid not null references staff(id),
  sold_at timestamptz not null default now()
);
create index idx_sales_business_time on sales(business_id, sold_at desc);

create table expenses (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  amount numeric(12,2) not null,
  category text,
  description text,
  source expense_source not null default 'manual',
  receipt_id uuid,
  occurred_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);
create index idx_expenses_business_time on expenses(business_id, occurred_at desc);

create table receipts (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  image_url text not null,
  parsed_data jsonb not null default '{}',
  supplier text,
  total_amount numeric(12,2),
  embedding vector(768), -- gemini-embedding-001, truncated to 768 dims for storage/speed at this scale
  occurred_at timestamptz,
  created_at timestamptz not null default now()
);
create index idx_receipts_business on receipts(business_id);
create index idx_receipts_embedding on receipts using hnsw (embedding vector_cosine_ops);

alter table expenses add constraint fk_expenses_receipt
  foreign key (receipt_id) references receipts(id) on delete set null;

create table alerts (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  type alert_type not null,
  related_item_id uuid references items(id),
  metric text,
  severity alert_severity not null default 'medium',
  message text not null,
  is_sent boolean not null default false,
  is_acknowledged boolean not null default false,
  created_at timestamptz not null default now()
);
create index idx_alerts_business_time on alerts(business_id, created_at desc);

-- cache for the 30-day rolling baseline used by anomaly detection (avoid recomputing from raw sales on every query)
create table metric_baselines (
  business_id uuid not null references businesses(id) on delete cascade,
  metric text not null,
  rolling_mean numeric(14,4) not null,
  rolling_stddev numeric(14,4) not null,
  computed_at timestamptz not null default now(),
  primary key (business_id, metric)
);

-- latency/outcome instrumentation, plus evaluation fields (raw query + classified intent)
-- so CP2 response-accuracy evaluation can be computed from logs alone, without manual reconstruction
create table request_logs (
  id bigint generated always as identity primary key,
  business_id uuid references businesses(id),
  channel text not null, -- 'whatsapp' | 'dashboard' | 'pos'
  path text,
  raw_query text, -- raw inbound message text, for WhatsApp channel entries
  classified_intent text, -- e.g. 'function_call' | 'rag' | 'vision' | 'clarify'
  latency_ms integer not null,
  status text not null,
  created_at timestamptz not null default now()
);
create index idx_request_logs_business_time on request_logs(business_id, created_at desc);
```

**Row-Level Security** — every business-scoped table gets this pattern (repeat for `staff`, `items`, `sales`, `expenses`, `receipts`, `alerts`, `metric_baselines`, `request_logs`):

```sql
alter table items enable row level security;
alter table items force row level security;
create policy tenant_isolation on items
  using (business_id = current_setting('app.current_business_id')::uuid)
  with check (business_id = current_setting('app.current_business_id')::uuid);
```

FastAPI sets `SET LOCAL app.current_business_id = '<uuid>'` at the start of every request transaction, from the verified JWT claim — never from a client-supplied header. Background jobs that must scan across *all* businesses run under the Supabase `service_role` key (which bypasses RLS by design) and loop per-business explicitly in code — never a cross-tenant query by accident.

**Concurrency-safe stock decrement** (handles two staff selling the last unit at the same moment) — used by the POS sale endpoint, no application-level locking needed:

```sql
update items
set current_stock = current_stock - :qty, updated_at = now()
where id = :item_id and current_stock >= :qty
returning current_stock;
```

Zero rows returned → reject the sale as "insufficient stock." Postgres's row-level lock on `UPDATE` handles the race correctly on its own.

---

## 7. Business logic algorithms (real math, not "the AI figures it out")

- **Anomaly detection:** nightly cron refreshes `metric_baselines` (30-day rolling mean/stddev per business per metric, from `sales`/`expenses`), then computes today's `z = (today_value - rolling_mean) / rolling_stddev`. `|z| > 3` → insert an `alerts` row (`type = 'anomaly'`). The baseline is cached and refreshed once/night, not recomputed on every WhatsApp query.
- **Stock velocity / reorder point:** `average_daily_usage` from trailing 7–14 days of `sales` grouped by `item_id`; `days_remaining = current_stock / average_daily_usage`. Below a configurable threshold (default 3 days) → insert an `alerts` row (`type = 'low_stock'`). Trigger this check synchronously right after a sale write too (not just nightly), so a sudden depletion surfaces immediately rather than waiting for the next cron run.

These are deterministic backend calculations run on a schedule and/or triggered after relevant writes — not something the LLM decides. The LLM only composes the natural-language surfacing of these results.

---

## 8. Core functional scenarios (build these — this is the feature list)

1. **Onboarding (dashboard-first — see Section 5)** — owner registers and verifies their phone on the **web dashboard** (WhatsApp Authentication-template OTP), fills in the business profile (name, location, operating hours), and creates staff accounts (name + PIN), all on the dashboard. Only once that's done does the owner move to **WhatsApp** for the initial stock population: uploading an Excel stock template (parsed with `openpyxl`/`pandas`) or sending a photo of their existing stock book/ledger (Gemini vision parse). The photo path goes through the same **low-confidence confirmation gate** as Scenario 5 below — never silently write parsed opening stock numbers from an ambiguous read of a handwritten ledger.
2. **Staff sale recording** — staff logs into the POS kiosk, records a sale; immediately updates stock levels and feeds into anomaly/velocity calculations.
3. **Owner structured query via WhatsApp** — e.g. "kopi arabica skrg berapa kg" → function-calling path → direct factual answer.
4. **Owner open-ended question via WhatsApp** — e.g. "bulan ini untung ga?" → function-calling aggregation across sales/expenses, composed into a natural answer.
5. **Receipt photo processing** — photo → vision parsing → **if low-confidence/ambiguous, a WhatsApp confirmation summarizing the reading before anything is saved (see Section 4's confirmation gate)** → structured expense/stock entry only after confirmation (or immediately, for high-confidence reads) → confirmation reply.
6. **Receipt history query via WhatsApp** — e.g. "pernah beli gula dari supplier ini?" → RAG path over embedded receipt history.
7. **Manual stock correction** — owner corrects a stock figure directly via WhatsApp (e.g. after a physical count).
8. **Proactive nightly alerts** — scheduled job runs anomaly + velocity checks, pushes triggered alerts via the approved WhatsApp template.

---

## 9. UI/UX design direction

Target users are non-technical SME owners with low digital literacy — but "easy to use" is not an excuse for it to look basic. The bar: first reaction should be "wait, this looks really good" — not "this looks like a school project."

**Chosen direction: iOS-Native Glass.** Light/dark-adaptive, translucent frosted panels (`backdrop-blur`), SF Pro/Inter typography, a blue-violet gradient accent, segmented controls and sheet-style modals mimicking native iOS interaction patterns, rounded corners, smooth transitions/micro-interactions, card-based dashboard layout. This was picked over a warm-minimal café-toned option and a dark-slate analytics-tool option specifically because it read as the least generic/AI-template-like of the three in mockup comparisons.

Two things to actively manage while building this, given that reasoning:
- **Force the POS route to a light/high-contrast variant** of the glass theme regardless of what the dashboard does — a shop counter in bright daylight will fight with translucent dark glass panels. Don't let the dashboard's light/dark adaptation bleed into the POS.
- **"Less AI-looking" is the actual target, not just this color palette.** Actively avoid generic "frosted-card SaaS template" output — vary card treatments, avoid default component-library spacing/shadows, put real craft into the details (custom icons/illustration touches, considered empty states, thoughtful microcopy). If you have access to design-focused skills (e.g. a skill oriented around avoiding generic AI-generated interface aesthetics, or ones focused on layout rhythm, color restraint, typography, and final polish passes), use them proactively during every UI-building phase — treat this as a standing step, not a one-off nice-to-have.

**In-app guidance:** contextual "?" help affordances throughout the dashboard and especially onboarding — tap one, get a short walkthrough of what that feature means and how to use it. Two-tier implementation:
- **Inline contextual help** (anomaly flags, stock velocity, non-obvious dashboard concepts): a small custom `<HelpTip>` component built on a headless popover primitive (e.g. Radix UI `Popover`) — custom because there are only a handful of these and they need to match the design system exactly.
- **First-run guided tour** (onboarding flow specifically): a lightweight, framework-agnostic step-spotlight library (e.g. `driver.js`) rather than hand-building spotlight/sequencing logic. Trigger once via `businesses.onboarding_completed_at is null`; mark complete when the tour finishes.

---

## 10. Performance & reliability (build these in from day one, not retrofitted)

- WhatsApp function-calling replies should return within a few seconds under normal conditions. Vision/receipt parsing can reasonably take longer (image round-trip through the LLM) — but must send an immediate WhatsApp acknowledgment first.
- Dashboard pages: fetch what's needed in parallel, not sequentially (avoid waterfalls). Paginate/lazy-load data-heavy views (full sales history) instead of dumping everything at once.
- Index frequently-queried columns (`business_id`, timestamps, foreign keys — see DDL above, already reflects this). Avoid N+1 query patterns. Use connection pooling (Supabase's pooler).
- Don't recompute the 30-day anomaly baseline from scratch on every query — it's cached in `metric_baselines` and refreshed nightly (see Section 7).
- Handle concurrent POS writes correctly (see the atomic stock-decrement SQL in Section 6) — two staff selling the last unit simultaneously must not corrupt the stock count.
- Log response times for WhatsApp interactions and key API calls from the start (see `request_logs` table, including the raw query text + classified intent fields) — cheap now, and this is the exact data the CP2 evaluation phase needs for task success rate and response accuracy, so it must exist from the start rather than being reconstructed later.

---

## 11. External setup checklist (do these before/alongside Phase 0–1)

1. **Google AI Studio / Gemini API key** — covers chat, tool-calling, vision, and embeddings.
2. Meta developer account → create a Meta App → add the WhatsApp product → use the **free test phone number** (up to 5 allow-listed recipient numbers, no business verification needed for a demo).
3. **Submit two WhatsApp message templates for review on day one** — approval can take up to 24h+; don't let either block a later phase: (a) the **Utility** alert template for proactive nightly alerts (Phase 5), and (b) an **Authentication**-category OTP template for dashboard login/registration (Phase 0/1 — needed as soon as the dashboard login flow exists, so this one is actually more urgent than the alert template).
4. Local webhook testing needs a public HTTPS tunnel (ngrok or Cloudflare Tunnel) pointed at the FastAPI backend; register that URL + a verify token in the Meta App's webhook config, subscribe to the `messages` field.
5. Supabase project — enable the `pgvector` extension, grab the connection string, `service_role` key (backend-only, never shipped to the frontend), and `anon` key if needed. Also create a **private Supabase Storage bucket** for receipt/ledger images with a business-scoped access policy (see Section 2).
6. Vercel project linked to `frontend/`; Railway project linked to `backend/` (API service) + a second Railway service configured as a **Cron Job** pointed at the nightly jobs entrypoint, both referencing the same Supabase `DATABASE_URL`.
7. `.env.example` files for both apps documenting every required variable.

If any of these external accounts/keys aren't provisioned yet, scaffold everything with placeholder env vars first and note in `docs/progress.md` exactly what's blocked on which credential — don't stall the whole build waiting on one signup.

---

## 12. Build sequence — demoable vertical slice first

Work through these in order. Each phase should end with something concretely demoable, not partial scaffolding.

1. **Phase 0 — scaffold.** Repo init (fresh git history), Next.js + FastAPI skeletons, Supabase project + full schema/RLS migration (Section 6), private Supabase Storage bucket for receipts, Vercel + Railway deploy pipelines wired to a trivial healthcheck endpoint end-to-end. Submit both WhatsApp templates now (Utility alert template + Authentication OTP template — see Section 11). Create `docs/progress.md` (see "Working practices" below).
2. **Phase 1 — thin vertical slice (the demo backbone).** Seed one demo café + demo items. POS staff-PIN login + record-a-sale using the atomic stock update. WhatsApp webhook verified + one working function-calling flow ("how much stock do I have") round-tripping through Gemini back to WhatsApp. This alone proves POS-write → WhatsApp-read across the whole stack — get here before building anything else out.
3. **Phase 2 — function-calling breadth.** More tools (sales totals, period comparisons, manual stock correction), multi-language handling in the classification/composition prompts, request-latency logging.
4. **Phase 3 — vision path.** Receipt photo → immediate ack → Gemini vision parse → `expenses`/`items`/`receipts` writes. Separate Excel/document stock-template ingestion path for onboarding.
5. **Phase 4 — RAG path.** Gemini embeddings on receipt content, pgvector similarity search, "have I bought X before" flow.
6. **Phase 5 — proactive alerts.** Nightly Railway cron job (baseline refresh + z-score anomaly + stock velocity), delivery via the (by-now-approved) WhatsApp template.
7. **Phase 6 — web dashboard.** Sales trend charts, anomaly/stock-risk cards, P&L view; parallelized data fetching, pagination on sales history, RLS-scoped queries throughout.
8. **Phase 7 — onboarding polish.** WhatsApp-driven business setup flow (Excel/photo stock import), contextual help tooltips, guided tour.
9. **Phase 8 — role-separation hardening.** Explicit tests proving a staff JWT gets rejected on owner-only WhatsApp commands and dashboard API routes; RLS policy verification pass; concurrent-sale race test.
10. **Phase 9 — performance pass.** Confirm latency logging in place, connection pooling configured, N+1 query audit, caching verified in practice under repeated queries.
11. **Phase 10 — visual polish + demo script.** Finish the design direction across all screens, prepare seed data and a rehearsed demo flow.

Section 10's performance/reliability items are threaded through at the point they first become relevant (indexes in Phase 0's DDL, atomic stock updates in Phase 1, baseline caching in Phase 5, parallel fetch/pagination in Phase 6, logging from Phase 2 onward) — not saved for a single retrofit pass at the end.

---

## 13. Working practices

- **`docs/progress.md`** — created in Phase 0, updated at the end of every phase: what's done, key decisions made (especially any place you deviated from or resolved an ambiguity in this brief), current phase, open TODOs. This is the source of truth if a build session gets picked up fresh later — read it before resuming work if you're starting cold.
- **Build big within a phase, but don't skip logging why.** You have latitude to make complete, ambitious progress through each phase in one go rather than pausing for confirmation on every file — that's intentional. But every non-obvious decision (an interpretation of an ambiguous requirement, a deviation from this brief, a third-party API quirk you had to work around) belongs in `docs/progress.md` so it's reviewable afterward instead of silently lost.
- **Don't silently swap tech choices.** Everything in Section 2 is intentional and was decided with reasoning already worked through. If something in this stack turns out to be a genuinely bad fit once you're building against it, say so explicitly and explain why, rather than quietly substituting something else.
