# Build Progress Log

This file is the running source of truth for what's been built, key decisions made, and open TODOs. Update it at the end of every phase (see `PROJECT_BRIEF.md` Section 13, "Working practices"). If you're resuming this build in a fresh session, read this file before doing anything else.

---

## Pre-build — brief reconciliation against written capstone proposal (Chapter 3)

Before Phase 0 started, `PROJECT_BRIEF.md` was corrected against the written capstone proposal to resolve real inconsistencies and close a couple of build-time gaps. Five changes were made:

1. **Onboarding entry point reconciled to dashboard-first.** The brief previously implied WhatsApp-first identity with no login anywhere. The proposal (Chapter 3) specifies onboarding starts on the web dashboard: owner registers there, fills in the business profile, creates staff accounts (name + PIN), and only then moves to WhatsApp for day-to-day operation. Section 5 (auth design) and Section 8 Scenario 1 were rewritten to match. This also required a real technical fix, not just a reshuffle: phone verification during dashboard registration/login now uses a WhatsApp **Authentication-category template** rather than a free-form message, because Authentication templates work without an already-open 24-hour session window — a brand-new owner has never messaged the business number before, so a free-form OTP message would have been rejected by Meta. This became a second template to submit for review (alongside the existing Utility alert template), and it's more urgent than the alert template since it's needed as soon as the dashboard login flow exists (Phase 0/1), not just by Phase 5.

2. **Attendance tracking marked explicitly out of scope.** The proposal mentions attendance as a possible function-calling use case; it is a deliberate, documented omission from this build — no `attendance` table, tool, or WhatsApp intent. Noted in the brief's scope-philosophy section so it reads as an intentional decision rather than a silent gap if anyone (including a grader) looks for it.

3. **Receipt image storage pinned to Supabase Storage.** `receipts.image_url` was previously unspecified as to where the actual file lives. Now explicitly Supabase Storage — same platform as the DB, RLS-compatible, one less external service — using a **private bucket** with access scoped per business, not a public bucket.

4. **Vision confirmation gate added as a correctness requirement, not polish.** Gemini vision-parsing handwritten Bahasa Indonesia/Malay receipts and stock books is the single biggest technical risk in the project. The brief now requires: on a low-confidence or ambiguous extraction, send the owner a WhatsApp confirmation summarizing what was read, and only write to the DB after they confirm. Never silently commit a low-confidence parse. Applies to both Scenario 1 (onboarding stock-book photo) and Scenario 5 (ongoing receipt processing).

5. **`request_logs` extended for CP2 evaluation.** Added `raw_query` and `classified_intent` columns so response-accuracy (which intent path was chosen vs. what the query actually needed) can be computed later from logs alone, without manual reconstruction. Minimal addition — two columns on an existing table, not a new system.

**Status:** brief corrected, no code written yet. Next step is Phase 0 (see `PROJECT_BRIEF.md` Section 12).

---

## Phase 0 — scaffold (2026-07-07)

**Done:**
- Fresh `git init` at the `FYP/` folder root. Two structural notes: (1) the *home directory* `C:\Users\PF4B3` is itself (apparently accidentally) a git repository — unavoidable; this project repo is fully independent (own `.git`, own history) and git treats nested independent repos correctly. (2) The brief's Section 3 sketch names the repo folder `fyp-business-assistant/`; since `PROJECT_BRIEF.md` and `docs/progress.md` already lived directly in `FYP/`, `FYP/` itself is the repo root — creating a nested folder would have orphaned the existing docs. Product name: **Warung Pintar**.
- Backend skeleton: FastAPI app (`backend/app/`) with `/health`, CORS, latency-header middleware, config via pydantic-settings (placeholder-safe defaults so the app boots without credentials), async SQLAlchemy engine (asyncpg, `statement_cache_size=0` for PgBouncer/Supabase-pooler compatibility), RLS tenant helper (`set_config('app.current_business_id', …, is_local)` per transaction).
- Full schema migration `alembic/versions/0001_initial_schema.py` — the Section 6 DDL **verbatim** (7 entities + `metric_baselines` + `request_logs`, all indexes, HNSW vector index, RLS enable/force + `tenant_isolation` policy on every business-scoped table), plus **two additive support tables**:
  - `pending_confirmations` (RLS'd) — the Section 4 confirmation gate requires holding a parsed-but-unconfirmed vision/Excel extraction somewhere until the owner replies YES; the locked schema has no state store for that. Additive, no changes to existing tables.
  - `login_otps` — server-side OTP state for dashboard login (hash, attempts counter, expiry). Keyed by phone, which may not belong to any business yet during registration, so deliberately not business-scoped / no RLS. Stores a hash, never the code.
- SQLAlchemy models mirroring the DDL 1:1; alembic wired to `DATABASE_URL` (async env).
- Frontend skeleton: Next.js 15 + TS + Tailwind 3.4, hand-written (no create-next-app). iOS-Native Glass design tokens established day one in `globals.css`/`tailwind.config.ts` (light/dark adaptive CSS vars, glass card/button/field components, accent gradient, spring-ish keyframes). POS opt-out hook already in place: `data-theme="pos-light"` on `<html>` forces the light high-contrast variant.
- Deploy configs: `backend/railway.json` (migrate-then-serve start command, `/health` healthcheck) + `Procfile`; Vercel needs no config (auto-detects Next.js in `frontend/`). `.env.example` for both apps documenting every variable.
- `docs/whatsapp-templates.md`: both templates (Utility `business_alert`, Authentication `login_otp`) fully specified and ready to paste into Meta Business Manager.
- Both dependency installs verified (backend venv + `npm install`); FastAPI app imports clean.

**Key decisions:**
- Background jobs will NOT literally use the Supabase `service_role` API key for DB access — that key authenticates PostgREST, not direct Postgres connections. The brief's *intent* (loop per business explicitly, never an accidental cross-tenant query) is implemented more strictly: jobs list businesses via the un-RLS'd `businesses` table, then open a tenant-scoped transaction per business, so RLS stays active even for jobs. Flagging per the brief's "flag, don't silently deviate" rule — this is the same isolation guarantee, enforced harder.
- `request_logs` RLS policy (locked) rejects rows with NULL `business_id`; inbound WhatsApp messages from unknown senders therefore log to the app logger only, not the DB. Acceptable: CP2 evaluation only concerns resolved-business interactions.
- Dry-run mode for outbound WhatsApp: with placeholder credentials, sends are logged instead of attempted, so the entire inbound pipeline is exercisable locally by POSTing simulated webhook payloads. In development mode, the OTP code is also logged for testing the dashboard login without WhatsApp delivery.

**Blocked on credentials (everything scaffolded with placeholders; none of this blocks writing/testing code):**
- **Supabase project** → `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`; run `alembic upgrade head`; create private `receipts` storage bucket. Also blocks: live RLS + concurrency tests (no local Postgres/Docker on this machine — those tests are written to auto-skip unless `DATABASE_URL` points at a real Postgres).
- **Google AI Studio** → `GOOGLE_API_KEY`. Blocks live Gemini calls (classification/tools/vision/embeddings); all AI modules are unit-tested with mocks.
- **Meta developer account + WhatsApp product** → `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN`; **submit both message templates immediately when this exists** (`docs/whatsapp-templates.md` is copy-paste ready). Blocks live webhook verification + any real message delivery.
- **Vercel + Railway projects** → deploy pipelines (configs are committed and ready to link).

---

## Phase 1 — thin vertical slice (2026-07-07)

**Done (POS-write → WhatsApp-read across the whole stack):**
- **Seed** (`python -m app.seed`): fictional café *Kopi Kenangan Senja*, owner Ibu Ratna (+62 812-000-1111, PIN 1234) + staff Sari (2345) / Budi (3456), 11 items (sellable drinks/food in servings + raw materials in kg/liter), 30 days of deterministic sales history with weekend bumps and a planted spike yesterday (so anomaly detection has something to find in Phase 5), sample expenses. Idempotent (delete-and-recreate).
- **POS backend**: pairing-token boot (`GET /pos/business/{token}` — business name + active staff), PIN login (`POST /pos/login` → scope="pos" JWT carrying staff_id), item list, `POST /pos/sales` using the brief's **atomic conditional UPDATE verbatim** (zero rows → 409 insufficient stock) + the Section 7 synchronous post-sale velocity check + a request_logs row per sale.
- **Stock-velocity service** (`services/velocity.py`) written now because the post-sale check needs it: 14-day trailing window, `days_remaining = current_stock / avg_daily_usage`, threshold from `LOW_STOCK_DAYS_THRESHOLD` (default 3), severity tiers (≤1d high / ≤2d medium / else low), and de-dup (no new alert while an unacknowledged low_stock alert exists for the item — prevents alert-per-sale storms). The nightly job (Phase 5) reuses this module.
- **WhatsApp webhook**: GET handshake, POST with raw-body HMAC `X-Hub-Signature-256` validation (constant-time compare; placeholder secret → dev-only skip with warning, hard 500 in production), immediate 200 ack with processing deferred to a background task. Processor: message-id LRU de-dup (Meta redelivers), business resolution by `owner_phone`, polite reply to unregistered numbers (session window is open — they just messaged us), per-message request_logs row (raw query, classified intent, latency, status), crash-safe error reply.
- **AI layer, first flow**: `get_stock` tool (ILIKE partial-name matching, "not found → here's what IS tracked" shape), response composer as a deliberately separate Gemini call, `clarify` fallback.
- **POS frontend** (`/pos/[businessToken]`): kiosk state machine — staff picker (initial avatars) → 4-digit PIN pad (shake on wrong PIN, no wrong-name/wrong-PIN oracle) → item grid (big targets, stock badges, out-of-stock disabled) → quantity sheet → success flash with remaining stock. Pairing persisted to localStorage; 🔒 Kunci relocks to the staff picker. Forced light high-contrast theme via `data-theme="pos-light"`.
- **Tests**: 19 passing (PIN hashing/salting, JWT scopes/expiry/tampering, webhook handshake + signature accept/reject/missing + prod-placeholder guard, router classification outcomes with Gemini mocked).
- Frontend production build verified clean.

**Key decisions:**
- **Intent classification = one forced tool call over the whole tool set** (`FunctionCallingConfig(mode="ANY")`), not a separate `classify_intent` schema followed by a second tool-selection call. The chosen function *is* the classification (its name is what request_logs records), which keeps the routing to a single Flash round-trip — half the latency of classify-then-call on the hot path. Same semantics as the brief's Section 4 (exactly one route per message, fixed tool set, no free-form SQL); flagged here since the mechanism differs from a literal reading.
- POS `unit_price` is prefilled from the item but overridable per sale (counter discounts); always server-validated ≥ 0.
- Latency for WhatsApp interactions is measured from processing start (post-ack) — webhook ack time is Meta-facing and logged separately by the middleware header.

**Verification status:** everything testable without credentials is tested and passing. Live round-trip (real webhook → Gemini → WhatsApp reply) still blocked on Meta + Gemini keys — the dry-run path (simulated webhook POST, logged outbound) is the local stand-in.

---

## Phase 2 — function-calling breadth (2026-07-07)

**Done:**
- **Tool set now 7 tools** (fixed set, no free-form SQL): `get_stock`, `get_sales_summary` (revenue/transactions/top-5 items per period), `compare_periods` (delta + %), `get_profit` (revenue, COGS estimate from item cost prices, recorded expenses, net — Scenario 4 "bulan ini untung ga?"), `correct_stock` (Scenario 7 — absolute set after physical count, with not-found and *ambiguous-match* guards: multiple ILIKE hits return candidates instead of guessing which item to overwrite), `get_low_stock` (reorder-threshold OR days-remaining risk), `record_expense` (manual expenses stated in chat, `source='manual'`).
- **Timezone-correct periods** (`ai/periods.py`): every named period ("today", "this_week", "last_month", …) resolves in the *business's* timezone then converts to UTC — a Jakarta café's "hari ini" starts at 17:00 UTC yesterday. Unknown period/timezone fall back visibly (label says what was used) rather than erroring the conversation.
- Multi-language handled at prompt level as designed: classifier prompt says items may be misspelled/mixed-language and passes names through; composer mirrors the owner's language/mix. No per-language pipelines.
- Request-latency logging was already live from Phase 1 (WhatsApp per-message + POS per-sale rows with raw_query/classified_intent); nothing to retrofit.
- Tests: 33 passing — added period-boundary math (UTC date-line crossing, week/month abutment, fallbacks) and tool-registry integrity (every declaration has an executor, unique names, **explicit no-attendance-tool test** since that omission is a scope requirement).

**Notes:**
- `get_profit` reports COGS separately from recorded expenses and says so in a note field — summing both would double-count ingredient purchases that were also logged as expenses. The composer surfaces net = revenue − expenses.
- `record_expense` and `correct_stock` are owner-stated facts, so they execute directly (WhatsApp sender == owner by construction); the vision confirmation gate (Phase 3) is for *machine-parsed* data, which is where silent wrong writes could happen.

---

## Phase 3 — vision path + Excel import (2026-07-07)

**Done:**
- **Image flow**: media id → immediate WhatsApp ack ("lagi kubaca dulu ya… 🧾") *before* any heavy work → Meta media download → upload to the private Supabase Storage bucket at `{business_id}/{uuid}.{ext}` (bucket-relative path stored in `receipts.image_url`; dashboard gets short-lived signed URLs; dry-run mode returns a pseudo-path so the pipeline runs locally) → **Gemini Pro** structured-output parse.
- **Parse schema** forces the risk controls: `document_type` (receipt / stock_ledger / other), items[{name, quantity, unit, unit_price, line_total}], supplier, date, total, **confidence enum + an explicit ambiguities list**. System prompt covers Indonesian number formats (12.500, 'rb', 'jt'), no-translation transcription, and "high confidence is a promise".
- **Confirmation gate** (Section 4, correctness requirement): commit immediately ONLY when confidence=high AND ambiguities empty AND items non-empty. Otherwise the parse is parked in `pending_confirmations` (60-min TTL, one per business — a new photo supersedes) and the owner gets a deterministic hand-built summary ("Ini yang aku baca… Balas *YA* untuk simpan, atau kasih tau bagian yang salah"). Deliberately NOT composed by the LLM: a correctness gate must show exact numbers, not a paraphrase.
- **Confirmation replies** (pre-check in the text handler, before normal routing): multilingual keyword fast path (ya/yes/betul/ok… → commit; salah/batal/tak… → discard); anything longer goes through a **Gemini revision call** that applies the owner's correction to the parse and re-asks — with an `unrelated` escape hatch so "stok arabica berapa?" mid-confirmation falls through to normal routing with the pending kept alive.
- **Commit semantics**: `receipt` → quantities ADD to stock (purchase), unmatched item names create new items (purchase isn't lost), unit_price refreshes cost_price, expense row (`source='receipt'`, FK to the receipt) written from the total; `stock_ledger` → quantities are ABSOLUTE (it's a count), no expense row. Occurred_at from the parsed date when readable.
- **Excel path** (separate from vision, per Section 4): mime/filename check → openpyxl/pandas parse with forgiving header synonyms (id/ms/en: nama/jumlah/satuan/harga modal/…), blank-row skipping, negative-quantity warnings, clear owner-facing errors for wrong-file and missing-columns. Applies as absolute opening stock (match→set, unknown→create). Committed directly without the gate — deterministic parsing of a machine-structured file, and the reply lists exactly what was created/updated so mistakes are visible and fixable via `correct_stock`.
- Tests: 48 passing (+gate matrix, keyword classification incl. "longer replies are never keyword-matched", summary formatting, Excel header synonyms/defaults/blank rows/negative warning/wrong-file errors).

**Notes:**
- Embedding upsert is hooked into `commit_parse` behind a lazy import + try/except (an embedding failure degrades RAG, never data writes). The actual `services/rag.py` lands in Phase 4.
- Live vision quality against real handwritten stock books is untestable until `GOOGLE_API_KEY` exists — flagged as the project's top risk to validate the moment the key is provisioned.

---

## Phase 4 — RAG path (2026-07-07)

**Done:**
- `services/rag.py`: deterministic receipt→text rendering (type/supplier/date/every line item/total, so both "pernah beli gula?" and "pernah beli dari Toko Sinar?" hit), `embed_receipt` upsert into `receipts.embedding` (gemini-embedding-001, 768 dims to match the locked `vector(768)` column), and `search_receipts` — pgvector cosine `<=>` ordered top-k inside the tenant-scoped session, with an explicit `business_id` filter (belt-and-braces on top of RLS) and a max-distance cutoff (0.65) so unrelated receipts don't get narrated as matches.
- `search_history` tool registered in the fixed tool set — the single forced classification call now routes text to data-tools / RAG / clarify exactly as Section 4 describes. Retrieved matches go to the composer as context; empty results return an honest "nothing in recorded history" note instead of letting the model guess.
- Embedding is wired into `commit_parse` (every committed receipt is searchable immediately); failures degrade search only, never data writes.
- request_logs vocabulary: RAG path logs `classified_intent='rag'` (per Section 6); data tools log their specific names — strictly finer-grained than the generic 'function_call' example, better for CP2 accuracy evaluation.
- Tests: 53 passing (+document rendering incl. ledger wording + empty-parse survival, no-match tool shaping, router→'rag' intent mapping).

**⚠ Flagged deviation from the locked stack (Section 2 said LangChain `SupabaseVectorStore`):**
Implemented retrieval as direct pgvector SQL through the RLS-scoped SQLAlchemy session, returning LangChain `Document` objects — NOT via `SupabaseVectorStore`. Concrete reasons, not preference:
1. `SupabaseVectorStore` talks to the DB through supabase-py → PostgREST **RPC with the service-role key**, which bypasses the per-request `SET LOCAL app.current_business_id` RLS pattern this project's tenant isolation is built on — tenant scoping would depend on remembering a filter parameter in every call instead of being enforced by the database.
2. It expects a documents-shaped table + `match_documents` SQL function; our embeddings live inside the **locked** `receipts` schema, so bolting it on means an extra RPC function and a second, differently-authenticated data-access path for one query.
3. pgvector — the actual point of the stack decision (no second vector DB, JOIN-able with business_id) — is used exactly as specified; `langchain-core` Documents keep the orchestration layer on LangChain abstractions.
If literal `SupabaseVectorStore` usage matters for grading optics, it's a contained swap (one module) — say the word and I'll add the RPC function + supabase-py variant behind the same interface.

---

## Phase 5 — proactive alerts ⚠ CODE COMPLETE, UNTESTED LIVE (2026-07-07)

**Status: explicitly NOT done-done.** The delivery leg depends on (a) Meta approving the `business_alert` Utility template — review takes real-world time and the Meta account doesn't exist yet — and (b) real Supabase + WhatsApp credentials. All logic is written and unit-tested where it's pure math; the end-to-end nightly run has NOT been executed against a real database or a real WhatsApp send. Treat as pending-verification, not shipped.

**Done (code):**
- `services/anomaly.py` (Section 7 locked formulas): 30-day rolling mean + sample stddev per business per metric (`daily_revenue`, `daily_expenses`), computed over *business-local* full days excluding today; **upserted into `metric_baselines`** (the cache — queries never recompute 30 days live). `z = (today − mean) / stddev`, strict `|z| > 3` (3.0 exactly does not fire — tested), flat baseline (stddev 0) can't fire, severity |z|>4 high else medium, de-dup one anomaly alert per metric per local day.
- `jobs/nightly.py` (`python -m app.jobs.nightly` — Railway Cron entrypoint, suggested `30 16 * * *` = 23:30 WIB): explicit per-business loop, each business in its own tenant-scoped transaction; per-business failure isolation (one tenant's error can't kill the sweep); baseline refresh → anomaly detection → velocity sweep over all items (reusing the Phase 1 service incl. its unacknowledged-alert de-dup) → collects `is_sent=false` alerts → **one Utility-template send per business per night** ({{1}} alert kinds, {{2}} business name, {{3}} detail lines capped at 550 chars with "+N peringatan lain" overflow) → marks `is_sent`.
- Template-only delivery is structural: the job never calls `send_text` — the free-form path physically isn't reachable from cron code, so the 24-hour-window failure mode can't sneak in.
- Tests: 59 passing (+baseline math vs statistics module, z formula incl. the exact-3.0 boundary, flat-baseline None, severity tiers, fencepost coverage of 30 local days).

**TODO when credentials exist:** run `python -m app.jobs.nightly` against seeded Supabase (the seed plants a yesterday sales spike — note: the *planted spike* is yesterday, so to see it fire live, run detection the evening of the spike day or re-seed), verify template delivery on an allow-listed phone, then wire the Railway cron service.

---

## Phase 6 — web dashboard (2026-07-07)

**Done — backend (`/api/*`, all owner-scope + RLS-pinned):**
- `/api/overview` (today revenue/tx + yesterday delta + month P&L snapshot + unacked alerts + low-stock count in one round-trip), `/api/sales-trend?days=` (one grouped query bucketed by *business-local* calendar day via `timezone(tz, sold_at)`, zero-filled dense series), `/api/sales` + `/api/expenses` + `/api/receipts` (paginated; sales joins item+staff names in the page query — no N+1; receipts get signed URLs concurrently via `asyncio.gather`), `/api/items` (velocity for ALL items from ONE grouped 14-day usage query — the per-item service stays for the post-sale path), items CRUD, `/api/pnl?months=` (monthly revenue/expense grouping in business tz), alerts list + ack, business get/patch, `/api/business/complete-onboarding`, `/api/stock-template` (generated .xlsx download).
- Verified: routes 401 without a token (deps active), FastAPI lazily materializes included routers (`_IncludedRouter`) — smoke-tested via TestClient rather than route introspection.

**Done — frontend (iOS-glass, Bahasa Indonesia microcopy):**
- Shell: desktop glass sidebar / mobile floating tab bar, client auth guard, logout. Pages: **Ringkasan** (hero "Penjualan hari ini" with vs-kemarin delta chip + edge-to-edge 30-day area chart inside the same glass block; "Bulan ini" as a quiet tinted plate with dl-rows — deliberately not a grid of identical stat cards; "Perlu perhatian" merged low-stock + alerts list), **Penjualan** (7/30/90 segmented control, trend chart, paginated history), **Stok** (risk badges habis/±N hari/di bawah batas, add/edit via bottom-sheet), **Keuangan** (6-month P&L bars + compact net row, paginated expenses with "dari foto nota 🧾" provenance, receipt thumbnails via signed URLs), **Peringatan** (open vs done, ack button, provenance copy "murni dari data kasir — bukan tebakan AI"), **Pengaturan** (profile edit, staff create/deactivate with PIN, POS pairing link generator with copy field, stock-template download), **/login** (phone → OTP code screen).
- Parallel fetching: `useOwnerData` fires each page's requests concurrently on mount (4 parallel on overview); skeletons everywhere; teaching empty states ("Foto nota belanja ke asisten WhatsApp…").
- **Design-skill pass applied** (impeccable/craft): wrote `.impeccable.md` design context from the brief (autonomous session — the skill's interactive teach flow was answered from the brief's audience/personality/direction instead of blocking on questions). Followed its craft bans (no side-stripe borders, no gradient text, varied surface treatments — glass vs tinted plates vs hairline rows, custom hand-drawn 24px icon set, no identical stat-card grid). **Two skill defaults consciously overridden because the brief LOCKS them:** Inter typography and the glass/gradient direction (the skill flags both as reflex defaults; here they're an explicit post-mockup decision) — noted in `.impeccable.md`.
- One session-permission note: the impeccable skill asks to self-edit its own SKILL.md after a one-time cleanup; that edit was denied by the permission classifier (self-modification) — harmless, skipped.
- Production build clean: 9 routes, dashboard pages 103–236 kB first-load JS.

---

## Phase 7 — onboarding polish (2026-07-07)

**Done:**
- **/register wizard** (3 steps, progress dots): reached from /login after OTP verification of an unregistered number (registration token via sessionStorage). Step 1 profil usaha — business name, type chips (kafe/warung/toko/lainnya), owner name + owner POS PIN, assistant-language chips with "campur-campur juga dimengerti" note → `/auth/register` → owner JWT. Step 2 staf & kasir — inline staff add (name+PIN chips list), POS pairing link generation with copy field, skippable ("bisa ditambah kapan saja"). Step 3 — hands the owner off to WhatsApp for initial stock (foto buku stok / Excel / just ask), exactly the proposal's dashboard-first-then-WhatsApp sequence. Direct visits to /register without a token get a friendly 3-step explainer pointing to /login.
- **First-run guided tour** (driver.js as specified): fires on the overview when `onboarding_completed_at is null`, 4 spotlight steps (angka hari ini → untung bulan ini → perlu perhatian → nav), Indonesian copy, popover restyled to the glass system (backdrop-blur, accent-gradient next button). Completing OR dismissing it calls `/api/business/complete-onboarding` so it never nags twice. Data-tour anchors on real content blocks, 600ms delay so skeletons resolve first.
- **HelpTips** (Radix Popover, custom-styled, landed with Phase 6 pages): perkiraan laba (overview), hari-tersisa math (inventory), untung/rugi chart (money), peringatan provenance (alerts), layar kasir pairing (settings) — every non-obvious concept has a "?" within thumb's reach.
- Build clean (11 routes).

**Notes:**
- The tour targets the desktop sidebar for its nav step; on mobile driver.js shows the popover un-anchored (acceptable — the demo drives desktop for the dashboard).
- Register wizard step 2/3 are additive helpers around the required flow; staff creation and pairing remain fully available in Pengaturan.

---

## Phase 8 — role-separation hardening (2026-07-07)

**Done:**
- **Scope-rejection matrix** (`tests/test_role_separation.py`, runs WITHOUT a DB because the dependency raises 403 before any session opens): POS token → 403 on all 7 owner routes (overview/sales/items/pnl/alerts/staff/pairing); owner token → 403 on POS routes; **pairing token and registration token → 403 everywhere** (the kiosk-URL token can boot the kiosk, never read data); missing/garbage tokens → 401; expired owner token → 401.
- **WhatsApp owner-only enforcement**: test proving a sender not matching `businesses.owner_phone` gets the polite "belum terdaftar" reply and the AI router is *never invoked* — staff have no WhatsApp surface by construction (resolution is by owner phone), and unknown numbers can't reach business logic.
- **DB integration tests** (`tests/test_db_integration.py`, gated on `INTEGRATION_DATABASE_URL`, currently skipped — no local Postgres): RLS blocks cross-tenant reads; WITH CHECK blocks cross-tenant writes (inserting a row claiming tenant B while pinned to A fails); **no tenant context = queries fail closed** (current_setting errors, nothing leaks); and the concurrent-sale race — two simultaneous sales of the last unit via `asyncio.gather`, asserting exactly one "sold", one InsufficientStock, stock lands at 0, exactly one sales row.
- Suite: 66 passed, 4 skipped (the DB-gated ones).

**Run the skipped tests once Supabase exists:** `alembic upgrade head` against a disposable DB, then `INTEGRATION_DATABASE_URL=... pytest tests/test_db_integration.py`.

---

## Phase 9 — performance pass (2026-07-07)

**Fixes found by the audit:**
- **N+1 in the WhatsApp `get_low_stock` tool** — it looped `compute_item_velocity` (one query per item) on a hot conversational path. Rewritten to one grouped 14-day usage query + in-memory math, mirroring `/api/items`. The per-item service function remains for the post-sale single-item check and the nightly sweep (where per-item is one query each by nature and reuses the de-dup logic).
- **Dashboard API latency now persisted**: middleware writes a `request_logs` row (channel='dashboard') for every authenticated `/api/*` call, using the business_id the auth dependency stashes on `request.state` (from the verified JWT — never a client header; never read for authorization). Streaming template download excluded. Failures swallowed (logging must never break a response).

**Audit confirmations (already in place, verified):**
- Connection pooling: pool_size=5 + pre-ping; `statement_cache_size=0` for Supabase's PgBouncer transaction pooler; `.env.example` documents using the pooler string (port 6543) in production.
- Baseline caching: anomaly reads use `metric_baselines` (nightly refresh); no 30-day recompute on any query path.
- Pagination: sales, expenses, receipts all server-paginated; sales joins names in the page query.
- Parallel fetching: overview fires 4 requests concurrently; receipts sign URLs via `asyncio.gather`.
- Indexes: all per the locked DDL (business_id + time composites, HNSW on embeddings).
- Latency instrumentation now covers all three channels: whatsapp (raw query + intent + latency), pos (per sale), dashboard (per API call) — the CP2 evaluation dataset accumulates from first real use.
- 66 passed / 4 skipped after changes.

**Not done (deliberate):** live load verification (repeated-query cache behavior, N+1 confirmation via query logs) needs a real database — folded into the same post-credential checklist as Phases 5/8.

---

## Phase 10 — visual polish + demo script (2026-07-07) — BUILD COMPLETE

**Done:**
- App icon (`app/icon.svg` — gradient chat bubble with a rising trend line), custom 404 page, README demo section.
- `docs/demo-script.md`: rehearsed ±8-minute five-act flow (POS → WhatsApp conversation incl. the confirmation-gate correction beat → template alert → dashboard walkthrough → the role-separation/RLS "graders ask this" minute), with setup prerequisites, reset commands, and a note to run the nightly job the evening before.
- `docs/api-contract.md`: endpoint summary across auth/owner/POS/webhooks/jobs.
- Final verification: **backend 66 passed / 4 skipped (DB-gated); frontend production build clean (12 routes).**

## Final status & the single source of truth for what remains

**Everything buildable without external credentials is built, tested, and committed.** The system runs locally end-to-end in dry-run mode (simulated webhooks in, logged replies out).

**Blocked-on-credentials checklist (in order, once accounts exist):**
1. Supabase → `alembic upgrade head`, private `receipts` bucket, `python -m app.seed`, run the 4 skipped integration tests.
2. Google AI Studio key → validate live classification/composition, and **especially vision quality on real handwritten stock books (project's #1 technical risk)**.
3. Meta App → submit BOTH templates immediately (`docs/whatsapp-templates.md`), register webhook, allow-list demo phone → live round-trip test.
4. After template approval → run `python -m app.jobs.nightly` live → Phase 5 moves from "code complete" to verified.
5. Railway (API + cron `30 16 * * *`) + Vercel deploys.

**Flagged deviations (both documented in-phase above):** (1) LangChain SupabaseVectorStore replaced with RLS-scoped pgvector SQL + LangChain Documents (Phase 4 — RLS-bypass and locked-schema conflicts); (2) background jobs use per-business tenant-scoped transactions instead of a literal service_role bypass (Phase 0 — stricter than the brief's intent, same guarantee).

---

## Phase 11 — first real deployment, live bugs found and fixed (2026-07-07)

Everything in Phases 0–10 was written and unit-tested but had never run against a real Supabase project (blocked on credentials the whole build). The moment real credentials existed, several real bugs surfaced immediately — none were visible under mocks. All fixed and verified live; full suite now **70/70 passing** (the 4 previously-skipped DB-integration tests now run for real).

**Fixed:**
1. **`alembic/env.py` couldn't handle percent-encoded passwords.** `config.set_main_option()` routes through `configparser` string interpolation, which raises on a literal `%` — and Supabase's own UI tells you to percent-encode special characters in the DB password (e.g. `%40` for `@`). Now reads the URL directly into a local variable instead of through `set_main_option`/`get_main_option`.
2. **The initial migration had never actually run against Postgres.** Two bugs surfaced: (a) `op.execute(SCHEMA_SQL)` passed one multi-statement string — SQLAlchemy's asyncpg dialect always executes via `PREPARE`, and Postgres refuses to prepare more than one command per string. (b) A naive fix (`sql.split(";")`) then broke on a semicolon that appears *inside a comment* ("additive; see docs/progress.md"). Replaced with a comment/string-aware statement splitter (`_split_statements` in `alembic/versions/0001_initial_schema.py`).
3. **Critical: RLS enforced nothing at runtime.** The app connected with Supabase's `postgres` role, which carries `BYPASSRLS` — so every "RLS-protected" query, including normal app traffic, silently bypassed all tenant-isolation policies the whole time. Fixed by creating a dedicated `app_role` (no `BYPASSRLS`, explicit grants + default privileges) and splitting `DATABASE_URL` (app runtime, must be the restricted role) from `MIGRATION_DATABASE_URL` (elevated role, migrations only) in `app/core/config.py` / `alembic/env.py`. Verified: all 4 RLS/concurrency integration tests fail against `postgres` and pass against `app_role`.
4. **Supabase's "Enable automatic RLS" project setting silently broke `businesses` and `login_otps`.** Both are deliberately un-scoped by design (no tenant exists yet at registration/login time), but that project-level toggle auto-enables RLS-with-zero-policies on every new table — which is default-deny, not default-allow. Migration now explicitly disables RLS on those two tables so behavior doesn't depend on a dashboard setting.
5. **Dry-run detection checked for the wrong sentinel string.** `app/whatsapp/client.py`, `webhook.py`, and `storage.py` each checked `== "placeholder"` (this file's Python default), but `.env.example`'s documented placeholder text is `"CHANGE_ME"` — so with a real `.env` copied from the template, dry-run mode never engaged and outbound calls attempted (and failed) against Meta/Supabase with fake credentials instead of degrading gracefully. Added `is_placeholder()` in `app/core/config.py` recognizing both sentinels; all three call sites now use it.
6. **Phone login never recognized real users.** `PhoneIn` only stripped non-digit characters — it never converted a locally-typed number (leading `0`, exactly what the login form's own placeholder tells users to type) into the international format (`62...`) stored in `businesses.owner_phone`. Every real login attempt from a naturally-typed number would silently misroute into "new registration" instead of matching the existing account. Added `to_international_phone()` in `app/whatsapp/client.py` (heuristic: `01` prefix → Malaysia `60`, other leading `0` → Indonesia `62`), wired into `PhoneIn._normalize`.
7. **Docs disagreed with the code on the demo owner's phone number.** `seed.py`'s own docstring, `README.md`, and `docs/demo-script.md` all wrote `+62 812-0000-1111` (four zeros) while the actual `OWNER_PHONE` constant is `628120001111` (three zeros) — a real, self-inconsistent typo, not a formatting choice. Corrected everywhere to `+62 812-000-1111`.

**Not yet done:** live Gemini vision-quality testing (still the project's top risk, needs `GOOGLE_API_KEY`), WhatsApp template submission/live send, Railway/Vercel deploy. See the Phase 10 checklist above — still accurate.

## Phase 12 — live Gemini key wired in, text pipeline verified end-to-end (2026-07-07)

**Done:**
- Real `GOOGLE_API_KEY` from Google AI Studio configured (note: current AI Studio keys are issued in a newer `AQ.<...>` format, not the older `AIzaSy...` format — both are valid, `google-genai` accepts either).
- Simulated a real WhatsApp text webhook (`stok es kopi susu berapa?` from the seeded owner) end-to-end against the live backend: classifier call → `get_stock` tool → real DB query → composer call → correct natural-language reply (`"Stok Es Kopi Susu ada 48 cup."`, matching seeded stock exactly). Two live Gemini calls, ~4s total round-trip.
- Full suite re-verified with the real key present: **70/70 passing** (mocked-Gemini unit tests unaffected; DB-integration tests still pass against `app_role`).

**Not yet done:** live Gemini **vision** quality on real handwritten stock books (top remaining technical risk), WhatsApp template submission/live send, Railway/Vercel deploy.

---

## Session 2 — UI rebuild + error localization + verification status (2026-07-07)

### Part 1 — full UI/design rebuild ✔
- **Critique first** (per instruction): scored the old dashboard honestly at **24/40** on Nielsen heuristics; verdict "yes, it reads AI-generated" — container monoculture (every row the same glass pill), the banned hero-metric template, undifferentiated chronological lists, flat 12–16px type cluster (confirmed by the automated detector), stretched-phone desktop layout. Automated scan false positives noted (em-dashes/number sequences in CSS comments).
- **Design context done properly this time**: interactive questions answered by the owner + a visual reference image. Confirmed: *warung hangat* personality, moderate desktop density, full commit to one-hero-surface-per-page, scope = dashboard + login/register + POS touch-up. **Owner reference supersedes the brief's blue-violet accent → forest green, warm-light-first** (flagged, not silent — glass materials + Inter stay locked). `.impeccable.md` rewritten from the real answers.
- **Chart colors validated computationally** (dataviz six-checks validator): light `#2b7a4e`/`#c2703d` on `#fbfaf8`, dark `#3f9a68`/`#c97e46` on `#15211b` — all checks pass, incl. CVD separation ≥15 ΔE; expenses series moved from gray (failed chroma floor) to terracotta.
- **Rebuilt**: tokens (warm off-white bg, frosted-white hero cards, quiet plates, hairline day-grouped open rows, status pills, solid-green buttons), reference-grammar StatCards (icon tile → label+HelpTip → display number → delta chip → sparkline), floating sidebar (brand block + nav + owner card with real owner name), mobile tab bar, all six dashboard pages, login/register restyle, POS color alignment. **Day-grouped lists everywhere the critique flagged**: sales (per-day totals "15 transaksi · Rp 360.000"), expenses (per-day − totals), alerts; rows time-ordered, headers sticky.
- **Real bug found & fixed during verification**: client-side day grouping used the *browser* timezone while all backend numbers use the *business* timezone — sales page "Hari ini" disagreed with the overview (452k vs 360k) on this machine. `lib/dates.ts` now buckets/labels/times in `business.timezone` (Intl-based); verified matching (15 tx · Rp 360.000 on both).
- Seed fix (not applied to the live DB — rerun `python -m app.seed` when convenient): sale timestamps now generate in WIB business hours 07:00–20:59 instead of UTC hours that displayed as 3-AM sales.
- **Polish pass**: `:focus-visible` accent rings (keyboard focus survives glass), `--ink-faint` darkened to ≥4.5:1 AA in both modes, dark-mode Tile tones, orphaned SectionTitle removed. Layout skill not separately invoked — it was conditional on spacing still feeling off; the rebuild itself implemented the critique's layout fixes (day grouping, surface budget, 4pt rhythm).
- Verified against the live DB in the running app (structure + computed styles + zero console errors; 1 glass surface per page confirmed). **Tooling note:** the preview screenshot capture broke mid-session (all captures time out; eval/snapshot/inspect fine) — visual verification used DOM/style inspection; the owner has the live preview panel for eyeballing.

### Part 2 — Indonesian error strings ✔
- All 24 `HTTPException` details across `api/auth.py`, `api/pos.py`, `api/dashboard.py`, `whatsapp/webhook.py`, `core/deps.py` audited; every English string localized to warm-casual Indonesian ("Kodenya salah — cek lagi ya"). The 403 scope message keeps the word "owner" (a role-separation test asserts on it).
- Regression guard: `tests/test_error_localization.py` — AST-walks those files, extracts every `detail=` literal (f-string fragments included), fails if any lacks a word-boundary Indonesian marker; also fails if the scan finds suspiciously few strings (self-checking). Suite: **67 passed + 4 DB-gated; the 4 integration tests re-run this session against the real Supabase `app_role` — pass (71 total green).**

### Part 3 — credentialed verification (honest status)
| Item | Status |
|---|---|
| Live Gemini text classification/composition | ✔ verified in Phase 12 (not redone, per instruction) |
| **Live Gemini vision on real handwritten receipts** | ⏸ **waiting on the owner's 3–5 photos** — the flow is ready to run the moment they arrive |
| Meta template submission / webhook / round trip | ✖ **blocked: no Meta credentials** (`WHATSAPP_ACCESS_TOKEN`/`PHONE_NUMBER_ID` still placeholders; only APP_SECRET filled). `docs/whatsapp-templates.md` remains copy-paste ready |
| Live nightly alert (`python -m app.jobs.nightly`) | ✖ blocked on the above (needs approved Utility template) |
| Railway + Vercel deploy | ✖ **blocked: neither CLI installed/authenticated** — `railway login`/`vercel login` are interactive; ready to drive them once the owner logs in |

Nothing above is marked verified without having actually run live.

## Phase 13 — quick cleanup pass: tracing root, stale build cache, seed cascade bug (2026-07-07)

**Done:**
1. **`outputFileTracingRoot` set** in `frontend/next.config.ts` (points at the frontend dir) — silences the multi-lockfile misdetection warning for good (a stray `package-lock.json` at the Windows user home directory was making Next.js infer the wrong workspace root).
2. **The "Cannot find module './331.js'" runtime error was a stale `.next` build cache**, not a real bug — confirmed by killing the dev server, deleting `.next`, and doing a clean `npm run dev`. `/overview` and `/settings` both compiled and loaded with zero console/server errors afterward.
3. **Real bug found while re-running the seed script: `request_logs.business_id` didn't cascade-delete.** Every other business-scoped table (`staff`, `items`, `sales`, `expenses`, `receipts`, `alerts`, `pending_confirmations`) was created with `on delete cascade`; `request_logs` was missed. `app/seed.py`'s own docstring claims re-running it "deletes and recreates the demo business (cascade)" — true only until the first real request_logs rows existed (from Phase 12's live Gemini test and normal dashboard/API traffic), at which point re-seeding threw `ForeignKeyViolationError`. Fixed with a new migration (`0002_request_logs_cascade.py`, since `0001` was already applied live) that drops and recreates the FK with `on delete cascade`. Ran `alembic upgrade head` live, then re-ran `python -m app.seed` successfully — now also picks up the Session 2 fix generating sale timestamps in WIB business hours (07:00–20:59) instead of UTC hours that displayed as 3 AM sales.
4. Full suite re-verified after the migration: **71/71 passing.**

**Not yet done:** live Gemini vision on real handwritten receipts (still the top risk, waiting on the owner's photos), Meta/WhatsApp template + webhook + round trip, Railway/Vercel deploy.

## Phase 14 — vision pipeline tested live, critical model-access bug found + fixed (2026-07-07)

**Critical finding: `gemini-2.5-pro` (the configured vision model) has a hard 0-request free-tier quota on this Google account** — confirmed via a live `429 RESOURCE_EXHAUSTED` whose violation detail explicitly says `limit: 0` for both requests/day and input-tokens/day on `gemini-2.5-pro`. This is not rate-limiting (retrying does not help) — it requires billing enabled on the Google Cloud project to use this model at all. Every prior "vision not yet tested" status in this doc was blocked on this the whole time, undiscovered until an actual image was sent through.

**Fix:** switched `GEMINI_PRO_MODEL` to `gemini-2.5-flash` in `.env`/`.env.example` (documented inline with the reason). Flash is multimodal and was already proven reliable for text in Phase 12.

**Real test performed:** three synthetic photo-realistic handwritten Indonesian stock-note/receipt images were generated (clearly AI-generated stand-ins for real photos, saved to `docs/vision-test-samples/` — neat/legible, genuinely messy with a coffee stain and folded corner, and medium difficulty with glare) and run through the actual `parse_business_document()` extraction function against the live Gemini API:
- **Neat ledger (10 items):** every name, quantity, unit, unit price, and line total extracted correctly; total `645.500` matched exactly; `confidence: high`, zero ambiguities.
- **Messy nota (torn paper, coffee stain, cramped handwriting):** items and total (`87.500`) extracted correctly; the model correctly downgraded to `confidence: medium` and specifically flagged the one genuinely ambiguous token ("RB" unit on "beras") instead of guessing — exactly the gated behavior the schema was designed for.
- **Medium note (glare, single price column):** items/quantities extracted correctly; `unit_price` correctly returned as `0` for every line rather than hallucinating a per-unit price the photo never showed (only line totals were visible) — correct, honest behavior per the schema's "0 if not shown" rule.

**Not yet done:** the WhatsApp inbound media-download step (`download_media` in `app/whatsapp/client.py`) always calls the real Meta API with no dry-run path, so the *full* WhatsApp round trip (photo in → reply out) is still blocked on real `WHATSAPP_ACCESS_TOKEN`/`PHONE_NUMBER_ID` — tracked already under the Meta checklist. What was verified tonight is the extraction logic itself, which is the part that was the actual open risk. Recommend also testing with real (non-AI-generated) photos once available, though tonight's result is a strong signal the pipeline works.

---

## Session 3 — item category icons, onboarding popover fix, dashboard error states (2026-07-07)

Done directly in Cursor (not Fable — owner preserving weekly LLM budget). All three items built, lint-clean, production build green, and each verified with real screenshots against the live DB in both themes.

### Part 1 — automatic item category icons ✔
- Replaced the plain-initials `Tile` on item rows with an `ItemIcon` that maps the owner-typed name to a product category by keyword and shows a recognizable hand-drawn icon instead. Falls back to the initials `Tile` when nothing matches, so nothing ever renders blank.
- **No network / no image fetch** — deterministic local keyword matching (`frontend/lib/itemCategory.ts`), 17 categories: coffee, tea, oil, rice, noodle, milk, sugar, egg, gas, cleaning, cigarette, flour, water, sauce, snack, produce (+ null fallback). This was a deliberate choice over the "auto-fetch a stock photo per item" idea: no external image API/keys, no licensing/latency/broken-URL risk, no layout churn, and it stays on-brand with the existing hand-drawn 24px/1.8px-stroke icon set. Icons live in `components/icons.tsx` (`IconCat*`), category→icon+warm-tone map + `ItemIcon` in `components/ui.tsx`.
- Keyword order is significant (first substring match wins), tuned so drink names resolve sensibly for a coffee shop: `kopi susu`→coffee (not milk), `teh botol`→tea, `matcha latte`→coffee. Wired into **inventory**, **overview low-stock**, and the **sales** transaction list.
- **Bug caught during screenshot verification:** `sugar` existed in the type + icon map but its keyword rule was missing from `RULES`, so "Gula Aren" fell back to "GA" initials. Added `{ sugar: ["gula", "sugar"] }`; re-shot and confirmed the sugar-cube icon renders.

### Part 2 — onboarding tour popover readable in dark mode ✔
- Root cause: `.driver-popover` used `--glass-strong`, which in dark mode is only ~9% white — the page content behind the floating popover bled straight through and collided with its own text. Glass is fine for cards (they sit on a predictable page bg) but wrong for a popover floating over arbitrary content.
- Fix: new **opaque** `--popover-surface` token (`#ffffff` light / `#1c261e` dark), pointed `.driver-popover` at it, dropped the now-pointless blur. Also repointed the CSS-triangle arrow's visible-side border color to the same token (driver.js hardcodes it white, so it was mismatched in dark mode).
- Verified by temporarily nulling the seeded business's `onboarding_completed_at` and driving the real tour in a dark browser — popover now has a solid dark surface, fully legible title/description/buttons, no bleed-through. (Note: the tour won't auto-fire under `next dev` because React StrictMode double-invokes the effect and the cleanup's `tour.destroy()` trips `onDestroyed`→complete before the second run; verified against the production build where effects run once. Not a real-user bug — production only.)

### Part 3 — real error state instead of infinite skeleton ✔
- Previously every list page rendered `loading ? Skeleton : hasData ? rows : EmptyState` — a failed fetch left `useOwnerData` with `error` set but `loading` false and no data, so the skeleton (or empty state) showed forever with no way to recover.
- Added a shared `ErrorState` (`components/ui.tsx`): 😕 + "Gagal memuat data" + friendly Indonesian line + a **"Coba lagi"** button wired to the hook's `reload()`. Applied the `error && !data` branch to **overview** (top-level guard, retry reloads all five queries), **inventory**, **sales**, **alerts**, and **money** (expenses + receipts sections).
- Verified by intercepting `/api/overview` to abort in Playwright — the dashboard now shows the error card with a working retry instead of loading forever.

**Not yet done (unchanged, all owner/credential-blocked):** live Gemini vision on real (non-synthetic) handwritten photos, Meta/WhatsApp template submission + webhook + round trip, Railway/Vercel deploy.

---

## Session 4 — visual pass to match owner's reference: atmospheric background + real product photos (2026-07-08)

Owner sent a second reference image (clean-white iOS dashboard with a soft blurred/textured backdrop and **real product photos** in the stock rows) and said the build still felt flat/generic. Two concrete gaps closed, done in Cursor, gated against the (freshly updated) `impeccable` v3.9.1 "absolute-bans" checklist. `.impeccable.md` stays the design-context source of truth (owner chose to skip migrating to the new skill's PRODUCT.md/DESIGN.md format).

### Part 1 — atmospheric textured background ✔
- The old `body` background was 3 faint radial washes on a flat `--bg-base` — read as a flat tint. Rebuilt as a layered ambient field: 5 overlapping soft radial gradients drifting in from all corners, hues drawn **only from brand tokens** (forest green + terracotta + a new warm-sand `--bg-wash-4`), plus a faint inline-SVG `feTurbulence` film grain on `body::after` (z-index -1, `mix-blend-mode: overlay` light / `soft-light` dark, ~4-7% opacity). **No image asset, no network request.** Grain is suppressed on the POS forced-light route.
- Deliberately kept low-contrast so it never competes with content; surface tokens (cards/text) untouched, so contrast ratios are unchanged. Verified in both themes — warm depth without touching legibility.

### Part 2 — real product photos for item tiles ✔
- Owner picked generic (non-branded) photos over mimicking real brands. Generated 17 unbranded studio product photos (one per category) with a single shared style prompt (soft warm cream background, gentle lighting, no text/logos) so the set feels unified; saved to `frontend/public/items/<category>.png`.
- Reworked `ItemIcon` (`components/ui.tsx`) into a three-tier fallback: matched category → photo tile (`object-cover`, `onError` guarded) → hand-drawn `IconCat*` on a warm tint → initials `Tile` for unmatched names. Nothing ever renders blank/broken. The Session 3 hand-drawn icons are retained as the middle fallback layer.
- Added a `bakery` category (roti/croissant/kue/donat/…) + a matching `IconCatBakery`, and extended `coffee` keywords (americano/macchiato/mocha) so the seeded demo items ("Croissant", "Roti Bakar Coklat", "Americano") match instead of falling back to initials.
- Verified against the **real** demo account (`0812-000-1111` / `000000`, "Kopi Kenangan Senja" — not the empty "231" test signup the owner had been looking at) in both themes: iced-coffee photo for the coffee drinks, croissant for the bakery items, brown-sugar bowl for Gula Aren, rice bowl for Nasi Goreng, milk carton for Susu UHT, tea glass for Teh Tarik; "Biji Arabica" correctly falls back to initials.

Lint-clean, production build green. **Not yet done (unchanged, all owner/credential-blocked):** live Gemini vision on real photos, Meta/WhatsApp round trip, Railway/Vercel deploy.

---

## Session 5 — the real reason "nothing changed": no theme control; + image-weight fix (2026-07-08)

**Root cause of the recurring "why is it still green / nothing changed / am I on an old build" complaint: the dashboard had no light/dark control and silently followed the OS `prefers-color-scheme`.** Every screenshot the owner had sent across sessions was dark mode (their OS default); every reference image they sent was light. So the light-mode work from Sessions 2–4 was real and correct but literally never visible to them — it was structurally impossible for the app to match a light reference while the OS forced dark. Only the POS route escaped this (it hard-set `data-theme="pos-light"`); the owner dashboard had no equivalent.

### Part 1 — explicit theme control, light as the true default ✔
- `frontend/app/globals.css`: converted the dark palette from `@media (prefers-color-scheme: dark) { :root:not([data-theme="pos-light"]) {…} }` to an opt-in `:root[data-theme="dark"] {…}` selector (same for the `body::after` grain rule). Light `:root` is now the default for everyone regardless of OS.
- `frontend/app/layout.tsx`: added a tiny pre-paint inline script that reads `localStorage.theme` and sets `data-theme="dark"` before hydration — no flash-of-wrong-theme for users who chose dark. No hydration mismatch (React doesn't own the `<html data-theme>` attribute).
- New `frontend/components/ThemeToggle.tsx` (sun/moon, `IconSun`/`IconMoon` added to `components/icons.tsx`) wired into the dashboard sidebar footer next to logout; toggles `data-theme` and persists to `localStorage`. POS still forces its own theme and never renders the toggle.
- Verified on the real demo account in both modes: light = clean cream/white matching the owner's reference; toggle flips to dark and back; footer icon swaps moon↔sun.

### Part 2 — product photos were 1024×1024 / ~1.3 MB each (real bug) ✔
- The Session 4 photos in `frontend/public/items/` shipped at full generation resolution (17 × ~1.1–1.7 MB PNG = ~22 MB) but render at 40×40 px via a plain `<img>` — a genuine payload problem for a low-bandwidth warung product.
- Downscaled to 160×160 (2× the tile) and palette-quantized (128 colors) in place: **~22 MB → 217 KB total, every file 10–15 KB (99% reduction)**, filenames/category mapping unchanged so `ItemIcon` needed no edit. Quality confirmed crisp at display size. Originals were untracked-only, backed up during the operation then removed after visual confirmation.

### Verification note / process fix
- Local env had been wiped (no venv, no `sqlalchemy`, no Playwright). Reinstalled `backend/requirements.txt` + Playwright/Chromium, brought up uvicorn + `npm run dev`, re-ran `python -m app.seed` (its date-relative data had drifted — the demo account still existed but "today"/low-stock had aged out), and screenshotted the **real** account (`0812-000-1111` / `000000`) in light + dark, embedded directly in chat.
- The hero "Tren penjualan" area chart looked blank in `full_page` captures but renders correctly in a normal viewport shot — a recharts + full-page-screenshot stitching quirk, **not** a regression.
- **Gotcha for future sessions:** a fresh phone-only OTP login creates an *empty placeholder business* (the "215"/"231"/"321" accounts). An empty account looks like a broken/old build. Always validate UI on the seeded **Kopi Kenangan Senja** account (`0812-000-1111` / dev OTP `000000`), on Ringkasan or Stok.

### Known, out-of-scope-for-this-session data quirks (not bugs in this work)
- The seed generates history for past days but not the current partial day, so right after seeding "Penjualan hari ini" shows Rp 0 / −100% vs kemarin, and stock sits above reorder thresholds so "Stok menipis" is 0 / "Stok aman semua". Cosmetic for a live demo; worth a seed tweak later if the owner wants today's tile populated.
- Reference-image nav items Pesanan/Produk/Pelanggan were confirmed out of scope: Orders is covered by Penjualan, Products by Stok, and customer CRM isn't part of the product. The 6-item nav stays; the reference is style-only.

Lint-clean. **Not yet done (unchanged, all owner/credential-blocked):** live Gemini vision on real photos, Meta/WhatsApp round trip, Railway/Vercel deploy.

---

## Roadmap v2 build log (docs/BUILD-ROADMAP.md)

Entries below follow roadmap §6. One task per commit, `[<task-id>] <description>`.

### [M0-T1] Local Postgres with pgvector
**Date:** 2026-09-03
**Status:** done
**Changed:** docker-compose.yml, scripts/db-bootstrap.sql, scripts/local-pg.py, backend/.env.example, backend/tests/conftest.py, backend/alembic/versions/0001_initial_schema.py, .gitignore
**Gates:** pytest 71 passed 0 skipped · migrations round-trip ok (0002 → 0001 → 0002) · frontend build ok · seed ok
**Notes:**
- `docker-compose.yml` runs `pgvector/pgvector:pg16` on a named volume with `scripts/db-bootstrap.sql` mounted into `docker-entrypoint-initdb.d`. The bootstrap creates `app_role` (LOGIN, NOBYPASSRLS) with grants plus default privileges, idempotently, mirroring the Supabase restore sequence in `PROJECT-STATUS.md` §5. Superuser `postgres`/`postgres`, database `warung_pintar`, port 5432.
- **This machine has no Docker, no WSL, no local Postgres and no admin rights**, so `docker compose up -d` could not be exercised here. `scripts/local-pg.py` is the fallback: it downloads the `pgserver==0.1.4` wheel from PyPI (a stock Postgres 16.2 + pgvector build for win_amd64), unpacks the binaries into `<repo>/.pg16/` (git-ignored), runs `initdb`, starts on 5432 and applies the same bootstrap SQL. `start` / `stop` / `status` / `psql` / `reset`. The cluster is deliberately not under `%LOCALAPPDATA%`: the venv is built on the Microsoft Store Python, which redirects AppData writes into a per-app sandbox and the Postgres loader then fails with STATUS_DLL_NOT_FOUND.
- `backend/tests/conftest.py` defaults `INTEGRATION_DATABASE_URL` to `DATABASE_URL` **only when the host is localhost**, so the 4 RLS/race tests run against the local DB and can never accidentally target a hosted database. No test was changed.
- `backend/.env` (untracked) now points both URLs at the local cluster; the dead Supabase strings are kept commented for M12-T1.
**Deviation:** removed `create extension if not exists pgcrypto` from migration 0001. `gen_random_uuid()` is core since Postgres 13 and nothing else uses pgcrypto (PIN/OTP hashing is Python `hashlib`). The pgserver build ships only `plpgsql` and `vector`, so the line made 0001 unrunnable there. Not a schema change; Supabase has pgcrypto preinstalled either way.
**Next:** M0-T2

### [M0-T2] Document the local-first workflow
**Date:** 2026-09-03
**Status:** done
**Changed:** README.md, docs/PROJECT-STATUS.md, .claude/launch.json
**Gates:** pytest 71 passed 0 skipped · migrations round-trip ok · frontend build ok · seed ok
**Notes:**
- README "Running locally" rewritten as a four-step sequence: database (Docker or `scripts/local-pg.py`), backend (`python dev.py` named as the Windows entrypoint, not bare uvicorn), frontend, tests and gates. States the expected `71 passed, 0 skipped` and what a skip means. Supabase is described as a deploy target only.
- `docs/PROJECT-STATUS.md` §5 gets a dated note that the missing Supabase project no longer blocks development.
- `.claude/launch.json` backend entry switched from bare uvicorn to `dev.py` so the in-app preview follows the same rule.
- **Fresh-clone verification is deferred to M0-T3:** a clone of HEAD today would lack the still-uncommitted `0002` migration, `dev.py` and `db_errors.py`, so "follow only the README" cannot be proven until that tree is committed. M0-T3's entry will record the fresh-clone run.
**Deviation:** none
**Next:** M0-T3

### [M0-T3] Review and commit the uncommitted tree
**Date:** 2026-09-03
**Status:** done
**Changed:** CLAUDE.md, docs/BUILD-ROADMAP.md, docs/progress.md (Sessions 3–5 log), backend/alembic/versions/0002_request_logs_cascade.py, backend/app/core/db_errors.py, backend/dev.py, backend/app/{main,api/auth,api/dashboard,core/db}.py, docs/vision-test-samples/ (3 PNG), frontend/components/{ThemeToggle,ui,icons,StatCard}.tsx, frontend/lib/{itemCategory,format}.ts, frontend/app/{layout.tsx,globals.css}, 5 dashboard pages, frontend/public/ (bg-ambient.jpg + 17 category photos, 280 KB total), .impeccable.md
**Gates:** pytest 71 passed 0 skipped · migrations round-trip ok · frontend build ok · seed ok
**Notes:**
- Reviewed every diff before committing. Backend: DB-unreachable errors mapped to a 503 with Indonesian detail (`db_errors.py`), `/health/db` endpoint, Windows selector event loop in `main.py`/`dev.py`, SSL only for non-localhost DB hosts, dev-only extra CORS origins, stale-token → 401 in `_business()`, `sales-trend` accepts 1–365 days for the Ringkasan period control. Frontend: `ErrorState` on every page instead of an infinite skeleton, opt-in dark theme via `data-theme` + `ThemeToggle`, `ItemIcon` three-tier fallback (photo → category icon → initials), ambient photo backdrop with scrim + grain, compact Rupiah axis labels. All consistent with the Session 3–5 log entries above.
- One change made during review: `DB_UNAVAILABLE` told the user to check Supabase first; it now names local Postgres first (`scripts/local-pg.py status` / `docker compose ps`) and Supabase second, matching the M0-T2 workflow.
- `docs/vision-test-samples/` are the three AI-generated stand-in receipts from Phase 14 (6.6 MB). Committed unaltered because M1-T1 baselines on exactly these files.
- Fresh-clone check for M0-T2's done-criterion runs against this commit next; result recorded in the following entry.
**Deviation:** none
**Next:** fresh-clone verification, then M0-T4

### [M0-T3] Fresh-clone verification (closes M0-T2's done-criterion)
**Date:** 2026-09-03
**Status:** done
**Changed:** docs/progress.md
**Gates:** n/a (verification-only commit; no code changed since 0bee89f)
**Notes:** `git clone` of 0bee89f into a scratch directory, a brand-new database `wp_fresh` created and bootstrapped with `scripts/db-bootstrap.sql`, then exactly the README backend steps: new venv, `pip install -r requirements.txt`, `cp .env.example .env` (only the database name edited to `wp_fresh`), `alembic upgrade head` (0001 + 0002 applied), `python -m app.seed`, `pytest -q` → **71 passed, 0 skipped**. 20 deprecation warnings in the fresh venv vs 1 here, from newer unpinned dependency versions; no failures. Scratch clone and database dropped afterwards. Frontend `npm install`/`build` was not repeated in the clone; it is exercised by gate 3 on every commit.
**Deviation:** none
**Next:** M0-T4

### [M0-T4] Numeric type guard
**Date:** 2026-09-03
**Status:** done
**Changed:** backend/tests/test_invariants.py (new)
**Gates:** pytest 73 passed 0 skipped · migrations round-trip ok · frontend build ok · seed ok
**Notes:**
- `test_no_float_money` reads `pg_attribute`/`pg_class` (not `information_schema.columns`, which hides tables the connecting role has no privilege on) for every table, partition, view and materialized view in `public`, and asserts each column matching `amount|price|total|cost|stock|quantity|threshold` has type `numeric`. A `KNOWN_MONEY_COLUMNS` set of the nine columns that exist today guards against the pattern matching nothing after a rename. Views are included so M3-T2's `sales` view stays covered.
- `test_no_float_money_guard_detects_floats` proves the "would fail if someone added a float column" clause: it creates a temp table with `real`, `double precision` and `numeric` columns inside a rolled-back transaction and asserts exactly the two float columns are reported as `float4` / `float8`.
- `test_invariants.py` has **no skip marker** by design: with no database URL the fixture calls `pytest.fail` with the fix-it command, per roadmap §2 ("if they skip, you are flying blind").
- Nothing converted; the existing `numeric(12,2)` / `numeric(12,3)` / `numeric(14,4)` columns are correct.
**Deviation:** none
**Next:** M0-T5

### [M0-T5] RLS coverage invariant
**Date:** 2026-09-03
**Status:** done
**Changed:** backend/tests/test_invariants.py
**Gates:** pytest 75 passed 0 skipped · migrations round-trip ok · frontend build ok · seed ok
**Notes:**
- `test_all_scoped_tables_have_rls` scans `pg_class` for every plain table/partition in `public` with a `business_id` column and, via `pg_policy`, requires the full template from migration 0001: a `tenant_isolation` policy with both USING and WITH CHECK, RLS enabled, **and** RLS forced (without `force`, the table owner bypasses the policy). Explicit allowlist `{businesses, login_otps}` with the by-design reasons in a comment; a `KNOWN_SCOPED_TABLES` set of the nine tables covered today prevents a vacuous pass.
- `test_rls_guard_detects_policyless_table` proves the failure mode: a temp table with `business_id` and no policy is reported with three gaps; after enable + policy it still reports "not forced"; after `force row level security` it is clean. All inside a rolled-back transaction.
- Views are excluded from this scan (policies cannot attach to them). M3-T2, which turns `sales` into a view, must add the view's own isolation test — noted in the test file.
**Deviation:** none
**Next:** canary (5 tasks done), then M0-T6

### [M0-T6] Verify request_logs under RLS
**Date:** 2026-09-03
**Status:** done
**Changed:** backend/tests/test_request_logging.py (new)
**Gates:** pytest 78 passed 0 skipped · migrations round-trip ok · frontend build ok · seed ok
**Notes:**
- **Answer: it never happens.** Every `request_logs` write path resolves a business before writing and skips the row otherwise. Dashboard middleware (`app/main.py`) writes only when `request.state.business_id` was set by an authenticated dependency, and a 401 never sets it. WhatsApp (`app/whatsapp/processor.py`) returns before the log for an unregistered sender (the comment there already names the RLS reason). POS writes inside the authenticated tenant session. So unauthenticated traffic (OTP attempts, unknown numbers, 401s) is instrumented only through the Python logger, never in the table. Not a live bug, not a separate path — by construction unreachable.
- The nullable `business_id` is therefore dead from `app_role`: WITH CHECK `business_id = current_setting(...)::uuid` evaluates to NULL for a NULL business_id and a NULL check fails. `test_null_business_request_log_is_rejected_by_rls` proves the database refuses such a row; `test_unauthenticated_api_request_writes_no_log` and `test_unregistered_whatsapp_sender_writes_no_log` prove neither path attempts it (counting NULL-business rows with the elevated role, since `app_role` cannot see them at all — the USING clause hides them).
- Not changed: the column stays nullable (schema changes are additive; migration 0002's cascade rationale still stands). If a future task wants unauthenticated traffic in the table, it needs a design decision, not a quiet NULL.
**Deviation:** none
**Next:** M1-T1 — M0 complete, tagged `checkpoint/M0`

### [M1-T1] Baseline on the staged receipts
**Date:** 2026-09-03
**Status:** done
**Changed:** docs/vision-results.md (new), scripts/vision-baseline.py (new)
**Gates:** pytest 78 passed 0 skipped · migrations round-trip ok · frontend build ok · seed ok
**Notes:**
- All 3 images in `docs/vision-test-samples/` (AI-generated stand-ins, labelled as such) run through the real path: `parse_business_document` → `needs_confirmation`, model `gemini-2.5-flash` (the `GEMINI_PRO_MODEL` override, Pro has a zero quota on this account), prompt untouched. `scripts/vision-baseline.py` wraps the real google-genai call to capture the **verbatim** model text before JSON parsing and appends every run to `docs/vision-results.md`, so M1-T3's before/after will be the same script on the same file.
- Ground truth was transcribed by eye from the images before the run and sits at the top of `docs/vision-results.md`.
- Results: neat page 50/50 fields correct, `high`, gate open — correct. Messy nota 7/7 items and total correct, `medium` with 3 disclosed ambiguities, gate **triggered** — the designed behaviour. Glare page 10/10 items and total correct but the model **fabricated unit prices (line_total ÷ qty) on 9 lines and invented English units on 9 lines, then claimed `high` with zero ambiguities → gate open → would have been committed silently.** This is the failure M1-T3 exists for, and it contradicts the prompt's own "0 if not shown" rule.
- Same image gave honest `unit_price = 0` in Phase 14 (July) with the same model alias and temperature 0. The model behind the alias moved. M1-T3 must measure over repeated runs and must not trust self-reported confidence alone; candidate server-side rules are listed in the results file, deliberately not applied.
- Latency 17–30 s per 2.2 MB image.
**Deviation:** none — nothing tuned, as the task requires.
**Next:** M1-T2

### [M1-T2] Expand the sample set
**Date:** 2026-09-03
**Status:** done
**Changed:** scripts/vision-make-samples.py (new), scripts/vision-baseline.py (scoring + JSON dump), docs/vision-test-samples/generated/ (15 JPG, 1.9 MB), docs/vision-test-samples/manifest.json, docs/vision-runs/, docs/vision-results.md
**Gates:** pytest 78 passed 0 skipped · migrations round-trip ok · frontend build ok · seed ok
**Notes:**
- 18 images in 6 categories, counts stated: handwritten_photo 3 (the Phase-14 AI stand-ins), thermal_printed 4, handwritten_font 4, blurred 3, angled 3, unreadable_control 1. Everything new is generated by `scripts/vision-make-samples.py` (deterministic, seeded) and labelled `generated: …` in `manifest.json` provenance. No real photos of real documents exist in the set; handwriting-font renders are explicitly a lower bound on difficulty.
- `manifest.json` carries exact ground truth per image (items, written total, lines sum, whether unit prices / units are shown, expected gate), and `vision-baseline.py` now scores every run: line match (name similarity + exact qty + exact line total), total exact, fabricated unit prices, invented units, and **silent error** = gate open with any field error. Raw runs are also dumped to `docs/vision-runs/*.json` for re-scoring without model calls.
- Result (untuned prompt, corrected for a manifest bug where the neat page's written total 645.500 differs from its lines' sum 635.500): **134/134 lines, 17/17 totals, control refused correctly, 5/18 silent errors — all five are fabricated unit prices / invented units on documents that show neither, with `high` confidence and no ambiguity.** Transcription is not the failure; filling in unseen fields is. Per-category table and reasoning in `docs/vision-results.md`.
- **Google free-tier quota discovered: 20 `generate_content` requests/day/model** on this project. M1-T1 (3) + run 1 (18) exhausted it; the attempted run 2 is recorded as void (all 429). One full pass per day is the ceiling until billing is enabled — this constrains M1-T3's re-measurement.
**Deviation:** none to the prompt. Added an M1-T1-style hand-written assessment because the automatic table needed the written-total correction explained.
**Next:** M1-T3

### [M1-T3] Tune, then re-measure — part 1 (tuning + offline verification)
**Date:** 2026-09-03
**Status:** NEEDS HUMAN (live re-measure blocked on Gemini quota); tuning implemented and offline-verified
**Changed:** backend/app/ai/vision.py, backend/tests/test_vision_normalize.py (new, 11 tests), scripts/vision-baseline.py, scripts/vision-make-samples.py, docs/vision-test-samples/manifest.json, docs/vision-runs/ (run 1 reconstructed as JSON), docs/vision-results.md
**Gates:** pytest 89 passed 0 skipped · migrations round-trip ok · frontend build ok · seed ok
**Notes:**
- Tuning: per-item `unit_written` / `unit_price_written` in the schema and prompt (copy only what is written; never infer a unit from a product name; a derived unit price must be flagged), plus `normalize_parse` server-side guards on every parse and revision: unwritten units dropped, baseless prices zeroed, line arithmetic and lines-vs-total contradictions become ambiguities that the existing gate turns into a question. Legacy pending payloads without the flags are treated as written (tested).
- Scoring refined after reading the data honestly: line total ÷ quantity is arithmetic, not a fabrication, and gives the correct cost per unit; it is now reported as "derived". Under the refined rule run 1 has **3 silent errors, all invented units** (glare page 7, its blur 1, faded thermal 3), with 134/134 lines and 17/17 totals. Offline re-run of the guards over the same outputs: the sum check fires on all three copies of the neat page; the invented units need the live flag.
- **Not claimed:** the done-criterion (gate triggers on every low-confidence read) requires the live after-run on all 18 images with the new schema. Not run: 20 requests/day free-tier quota, exhausted by today's baseline (probe at 18:45 UTC still 429).
**Blocker:** Google free-tier quota, 20 `generate_content`/day/model on this project.
**What I need:** either billing enabled on the Google Cloud project behind the key in `backend/.env`, or simply the next quota reset (daily). Then run `cd backend && ./.venv/Scripts/python.exe ../scripts/vision-baseline.py --label "M1-T3 after (schema flags + guards)"`, append the live table to the M1-T3 section of `docs/vision-results.md`, and if the silent-error count is 0 mark M1-T3 done and tag `checkpoint/M1`.
**What I did instead:** moved to M2-T1 (blocked only by M0-T6, which is done).
**Deviation:** M1-T3 split into part 1 (this) and part 2 (live re-measure), recorded per roadmap §0.1.
**Next:** M2-T1

### [M2-T1] Create `stock_movements`
**Date:** 2026-09-03
**Status:** done
**Changed:** backend/alembic/versions/0003_stock_movements.py (new), backend/app/models/models.py, backend/app/models/__init__.py, backend/tests/test_db_integration.py, backend/tests/test_invariants.py
**Gates:** pytest 90 passed 0 skipped · migrations round-trip ok (0003 → 0002 → 0003) · frontend build ok · seed ok
**Notes:**
- Migration 0003 creates enum `stock_movement_reason` (sale, sale_void, refund, purchase, waste, production_in, production_out, opname, correction) and table `stock_movements` exactly as specified: `qty_delta numeric(12,3)` signed, `unit_cost numeric(12,2)` nullable (NULL when unknown, never a guess), `source_type`/`source_id` without an FK so history survives the M3 order model, index on `(business_id, item_id, created_at desc)`. RLS template applied in the same migration; helpers copied from 0001 per §1.7. Downgrade drops table then type.
- `StockMovement` model added in the same commit, enum declared `create_type=False`.
- Tests: `test_rls_isolates_stock_movements` proves business B cannot read A's rows, A reads its own with exact `numeric` values, and A cannot insert a row claiming B (WITH CHECK). `test_invariants.py` now also matches `qty` in the float guard and expects `stock_movements.qty_delta`/`unit_cost` numeric and the table in the RLS coverage set — M0-T4 and M0-T5 pass with the new table.
- Nothing writes to the table yet; that is M2-T2.
**Deviation:** none
**Next:** M2-T2

### [M2-T2] Route every stock change through `stock_movements`
**Date:** 2026-09-03
**Status:** done
**Changed:** backend/app/services/stock.py (new), backend/app/services/sales.py, backend/app/services/receipts.py, backend/app/services/stock_import.py, backend/app/ai/tools.py, backend/app/api/dashboard.py, backend/app/seed.py, backend/tests/test_stock_movements.py (new, 6 tests)
**Gates:** pytest 96 passed 0 skipped · migrations round-trip ok · frontend build ok · seed ok
**Notes:**
- Every write to `items.current_stock` found by grep now writes its ledger row in the same session/transaction via `services/stock.py` (`record_movement`, `set_absolute_stock`, `add_stock`, `open_item_stock`): POS/WhatsApp sale → `sale` (−qty, `unit_cost` = today's `cost_price` snapshot, `source_id` = sale id, staff); receipt photo → `purchase` (+qty, cost = written unit price or NULL) for adds and new items, `opname` (delta, NULL cost) for stock-book counts; Excel import → `opname` for creates and updates; WhatsApp `correct_stock` → `correction` (NULL cost); dashboard item create → `opname` opening balance, dashboard stock edit → `correction`, name/price-only edits write nothing; seed → one `opname` opening row per item (today's stock + everything sold in the 30-day history) and one `sale` row per historical sale, timestamped to match.
- **The atomic conditional UPDATE and `check (current_stock >= 0)` are untouched.** `test_concurrent_sale_of_last_unit` passes unchanged; the movement row is written after the guard succeeds, in the same transaction, so a rejected sale writes no row.
- `test_stock_movements.py` exercises each path against the real database and asserts the exact `qty_delta`, `reason`, `unit_cost` (NULL where unknown) and that `SUM(qty_delta) == current_stock` after every operation. The seed was verified reconciled for all 11 items (621 sale rows + 11 opening rows); M2-T3 makes that a standing invariant.
- Zero-quantity item creation writes no opening row (nothing moved); the reconciliation still holds (0 == 0).
**Deviation:** none
**Next:** M2-T3

### [M2-T3] Stock reconciliation invariant
**Date:** 2026-09-03
**Status:** done
**Changed:** backend/tests/test_invariants.py
**Gates:** pytest 98 passed 0 skipped · migrations round-trip ok · frontend build ok · seed ok
**Notes:**
- `test_stock_ledger_reconciles_with_current_stock`: for every business, pinned one tenant at a time as `app_role` (the way a nightly job would run it, and fail-closed under RLS), every item must satisfy `SUM(stock_movements.qty_delta) == items.current_stock`. Passes against the seeded database (11 items, 632 ledger rows). Failure lists every offending item with both figures; it is a stop condition per roadmap §3.
- `test_stock_ledger_reconciles_after_a_simulated_day`: its own business, two items with opening rows, five sales through `record_sale`, one sale rejected by the atomic guard (writes no row), a WhatsApp-style correction, and a void written as a reversing `sale_void` row — final stock 8 and 0, exactly 9 ledger rows, invariant holds across all businesses. The void endpoint itself is M3-T4; the ledger shape of a void is fixed here.
- No production code changed. The invariant only needed the M2-T2 paths, which were already writing correctly.
**Deviation:** none
**Next:** M2-T4

### [M2-T4] Backfill history
**Date:** 2026-09-03
**Status:** done
**Changed:** backend/app/services/stock_backfill.py (new), backend/alembic/versions/0004_backfill_stock_movements.py (new), backend/tests/test_stock_backfill.py (new, 3 tests)
**Gates:** pytest 101 passed 0 skipped · migrations round-trip ok (0004 → 0003 → 0004) · frontend build ok · seed ok · canary (downgrade base → head, seed, suite) ok
**Notes:**
- Migration 0004 is data-only. The statements live in `services/stock_backfill.py` so the test runs the identical SQL. Scope is `backfill_items` = items with **no** ledger row when the backfill starts, so anything written live since M2-T2 is never touched and re-running is a no-op (proved by test).
- Reconstructed: one `sale` row per historical `sales` row (−quantity, staff, `sold_at`, **`unit_cost` NULL** — the cost at the time is unknown and a guess would corrupt margin history); one `purchase` row per receipt-photo line whose name matches an item of the same business (+quantity, written unit price or NULL); an `opname` opening-balance row per item dated at the item's creation carrying whatever remains so `SUM(qty_delta) == current_stock`. Stock-book photos (`document_type = stock_ledger`) are absolute counts with unknown prior state and are deliberately skipped. Backfilled rows are tagged `source_type = backfill_sale | backfill_receipt | backfill_opening` so the downgrade removes exactly them.
- Tests build a pre-migration business (stock figures, 30 days of `sales`, a purchase photo with one unmatched line, a stock-book photo, zero ledger rows), run the backfill as `app_role`, and assert the M2-T3 invariant holds, the exact rows and NULL costs, that the implied opening figure is recorded even when it is negative rather than hidden, idempotency against an item with live history, and that rollback leaves live rows alone.
- Canary run before this commit (7 tasks since the last one): clean stash, `downgrade base` → `upgrade head` through 0004, seed, full suite green.
**Deviation:** canary was late (7 tasks instead of 5) — noted, no consequence found.
**Next:** M3-T1 — M2 complete, tagged `checkpoint/M2`

### [M3-T1] Create `orders`, `order_lines`, `payments`
**Date:** 2026-09-03
**Status:** done
**Changed:** backend/alembic/versions/0005_orders.py (new), backend/app/models/models.py, backend/app/models/__init__.py, backend/tests/test_db_integration.py, backend/tests/test_invariants.py
**Gates:** pytest 102 passed 0 skipped · migrations round-trip ok (0005 → 0004 → 0005) · frontend build ok · seed ok
**Notes:**
- Three tables per spec, no outlet/terminal columns. `orders`: staff (nullable for self-service channels), `customer_id` nullable forward reference (FK arrives with M8), `order_type` enum, `status`, subtotal / discount_total / tax_total / service_charge / rounding / total all `numeric(12,2)`, `sold_at`. `order_lines`: `variant_id` nullable forward reference (M4), `quantity numeric(12,3)` with `check (quantity <> 0)` so reversing lines can be negative, `unit_price`, `line_discount`, `line_total`, **`unit_cost_at_sale numeric(12,2)`** (nullable only so backfilled history can say "unknown"), `notes`. `payments`: many-to-one on order, `method` enum, `amount` (negative on refund), `reference`.
- `business_id` is denormalised onto `order_lines` and `payments` so the standard `tenant_isolation` template applies unchanged to all three, in the same migration. Indexes: orders (business, sold_at desc), lines by order and by (business, item), payments by order.
- Enum values the roadmap left open, chosen and documented in the migration, extendable additively: `order_status` open · completed · voided · refunded; `payment_method` cash · qris · transfer · card · ewallet · points · other (`points` reserved for M8-T2 redemption).
- Tests: `test_rls_isolates_orders_lines_and_payments` builds an order with a line and a cash+QRIS split for A, proves B reads none of the three tables, A reads exact figures, and A cannot insert into any of the three as B. Invariant sets extended: float guard now also matches `discount|charge|rounding`; 13 new known money columns; three new scoped tables. M0-T4 and M0-T5 pass.
- `sales` untouched; M3-T2 is the authorised structural change.
**Deviation:** none
**Next:** M3-T2

### [M3-T2] `sales` becomes a view
**Date:** 2026-09-03
**Status:** done
**Changed:** backend/alembic/versions/0006_sales_view.py (new), backend/app/services/sales.py, backend/app/api/pos.py, backend/app/seed.py, backend/app/models/models.py, backend/app/models/__init__.py, backend/tests/test_db_integration.py, backend/tests/test_invariants.py, backend/tests/test_stock_movements.py, backend/tests/test_stock_backfill.py
**Gates:** pytest 104 passed 0 skipped · migrations round-trip ok (0006 → 0005 → 0006, idempotent backfill) · frontend build ok · seed ok
**Notes:**
- The one authorised structural change (§1.6), in one migration: `sales` → `sales_legacy`; backfill one order + one line + one payment per legacy row, **line id = legacy sale id** so `stock_movements.source_id` and every other reference still resolves; payment method `other` with reference `legacy` (how it was paid is unknown — `other` is the honest value); `unit_cost_at_sale` NULL for legacy lines; `create view sales with (security_invoker = true)` shaped exactly `id, business_id, item_id, quantity, unit_price, total_price, staff_id, sold_at`. Applied over the 621 seeded legacy rows: 621 orders, 621 lines (all ids matched), 621 payments, view count 621.
- **No tool code changed.** `app/ai/tools.py`, `api/dashboard.py`, `services/anomaly.py`, `services/velocity.py` read `Sale` untouched and keep working through the view. `Sale` model marked read-only; `SaleLegacy` model added so models.py mirrors the schema.
- Write paths moved: `record_sale` now creates Order + OrderLine (with the `unit_cost_at_sale` snapshot) + Payment (method `cash` by default — the kiosk does not ask yet; M3-T3 adds real methods) + the stock movement (`source_id` = line id). `RecordedSale` exposes `order` and `line`; POS response shape unchanged. The seed writes orders/lines/payments (cash 70 % / QRIS 30 %) instead of `Sale` rows.
- Isolation on the view: `test_rls_isolates_sales_view` (A sees its sale in the old shape with the line id, B sees nothing, no tenant context fails closed, INSERT into the view fails) and the invariant `test_scoped_views_are_security_invoker` (every `business_id` view in `public` must carry `security_invoker=true` — a view without it runs as the migration role and bypasses RLS). `KNOWN_SCOPED_TABLES` swaps `sales` for `sales_legacy`; the float guard still covers the view's columns.
- Downgrade drops the view and renames back; re-upgrade skips legacy rows already present as lines, verified by gate 2. Rows in the view can now be negative-quantity reversing lines (M3-T4); readers summing revenue will net them, which is the intended accounting effect.
**Deviation:** none
**Next:** M3-T3

### [M3-T3] POS writes orders
**Date:** 2026-09-03
**Status:** done
**Changed:** backend/app/services/orders.py (new), backend/app/services/sales.py, backend/app/schemas/pos.py, backend/app/api/pos.py, backend/tests/test_orders.py (new, 4 tests), frontend/app/pos/[businessToken]/page.tsx
**Gates:** pytest 108 passed 0 skipped · migrations round-trip ok · frontend build ok · seed ok
**Notes:**
- `services/orders.create_order` is now the single write path for selling: prices every line, takes stock per line with the same atomic conditional UPDATE (`current_stock >= qty`), requires payments to equal the total exactly, then writes order, lines (with `unit_cost_at_sale`), payments and one `sale` stock movement per line, all in the caller's transaction. `record_sale` (the legacy `/pos/sales` one-item call) is a thin wrapper over it, so there is one code path.
- `POST /pos/orders` (scope `pos`): lines + payments + order type; Indonesian errors — 404 item, 409 insufficient stock naming the item and what is left, 422 payment mismatch showing both figures. Low-stock check per line as before; request log as before.
- Done-when proved by `test_two_items_half_cash_half_qris`: 2 coffees + 1 toast = 68.000 paid 34.000 cash + 34.000 QRIS → **one order, two lines, two payments, two stock movements**, cost snapshots 8.000/9.000, stock 10→8 and 3→2, reconciliation invariant holds, both lines visible through the `sales` view. `test_out_of_stock_line_rolls_back_the_whole_order`: coffee decremented first, toast short on the second line → nothing written, coffee back at 10 (all-or-nothing is real). `test_payments_must_match_total_exactly`: 20.000 against 22.000 → nothing written. Endpoint test covers the response shape and the 422 text.
- Kiosk: items now go into a cart (badge on the tile, expandable cart bar with +/− per line), "Bayar" opens a payment sheet with Tunai / QRIS / Bagi dua (cash amount typed, remainder QRIS, validated), one `POST /pos/orders`, success flash lists lines and payments. Verified in the browser against the running backend with the demo café.
- No discount/tax/service charge/rounding yet — `subtotal == total` until M7-T4. `points` is not offered as a POS method until M8-T2.
**Deviation:** none
**Next:** M3-T4

### [M3-T4] Void and refund as reversals
**Date:** 2026-09-03
**Status:** done
**Changed:** backend/app/services/orders.py, backend/app/schemas/pos.py, backend/app/api/pos.py, backend/tests/test_order_reversals.py (new, 6 tests), docs/api-contract.md
**Gates:** pytest 114 passed 0 skipped · migrations round-trip ok · frontend build ok · seed ok
**Notes:**
- `void_order` / `refund_order` write **reversing rows only**: one negative-quantity line per original line (same price, same `unit_cost_at_sale`, note "void oleh <manager>: …"), one negative payment per original payment (reference `void:<payment id>`), and one positive stock movement per line (`sale_void` or `refund`, `unit_cost` = the cost as sold, `source_id` = the reversing line). The order's `status` moves to `voided` / `refunded` — the one state transition the enum exists for; every other original row is untouched and the order's totals stay as sold. A second reversal is refused (409).
- **Manager PIN**: the schema has owner/staff roles only, so the owner's PIN is the manager PIN; any active owner-role staff of the business may authorise. Wrong PIN → 403 in Indonesian and nothing written.
- `refund(restock=false)` reverses money and revenue but writes no stock movement, for goods that are not coming back. Default restocks.
- Done-when proved by `test_void_restocks_via_new_movement_rows_and_keeps_original`: stock 8→10 and 2→3 **through new `sale_void` rows** (per item the ledger reads opname, sale, sale_void — nothing removed), the reconciliation invariant holds, the original two lines and two payments are intact alongside the reversing four, line totals and payments both net to zero, and the `sales` view sums the order to zero revenue.
- Endpoints `POST /pos/orders/{id}/void` and `/refund` documented in `docs/api-contract.md`. Kiosk UI for reversals is not built yet (M3-T3's cart was the kiosk change this milestone); the endpoints are ready for it.
**Deviation:** none
**Next:** M4-T1 — M3 complete, tagged `checkpoint/M3`

### [M4-T1] Variants
**Date:** 2026-09-03
**Status:** done
**Changed:** backend/alembic/versions/0007_item_variants.py (new), backend/app/models/models.py, backend/app/models/__init__.py, backend/app/services/catalog.py (new), backend/app/services/orders.py, backend/app/services/receipts.py, backend/app/services/stock_import.py, backend/app/api/pos.py, backend/app/api/dashboard.py, backend/app/schemas/pos.py, backend/app/schemas/dashboard.py, backend/app/seed.py, backend/tests/test_variants.py (new, 6 tests), backend/tests/test_db_integration.py, backend/tests/test_invariants.py, frontend/app/pos/[businessToken]/page.tsx
**Gates:** pytest 122 passed 0 skipped · migrations round-trip ok (0007 → 0006 → 0007) · frontend build ok · seed ok
**Notes:**
- Migration 0007: `item_variants` (name, sku, `sell_price`/`cost_price numeric(12,2)`, `is_default`, `is_active`) with RLS in the same migration, one-default-per-item and unique-name-per-item partial/functional unique indexes, the forward FK `order_lines.variant_id → item_variants` now attached, and a backfilled default variant "Standar" per existing item copying its prices (idempotent). `items` untouched.
- Pricing moves to the variant: `create_order` resolves an explicit `variant_id` (must belong to the item and be active) or the item's default, takes `unit_price` and `unit_cost_at_sale` from it, and stamps `variant_id` on the line. Stock stays on the parent item (recipes are M4-T4). Reporting rolls up through the unchanged `sales` view (`item_id`).
- The default variant's prices mirror the item's in both directions (`services/catalog.py`): dashboard/Excel/receipt edits of the item sync the default variant; editing the default variant syncs the item. Every item-creation path (dashboard, receipt photo, Excel import, seed) calls `ensure_default_variant`, and a new invariant `test_every_item_has_exactly_one_default_variant` checks all tenants.
- Owner API: `GET/POST /api/items/{id}/variants`, `PATCH /api/variants/{id}` (Indonesian errors: blank name 422, duplicate 409, deactivating the default 409; promoting a variant demotes the previous default). POS: `/pos/items` lists active variants (default first); `/pos/orders` lines accept `variant_id`. Kiosk: tiles show "· N ukuran", the quantity sheet offers the sizes with their prices, cart lines are per size, stock cap is shared across sizes of one item.
- Done-when proved by `test_three_sizes_sell_at_three_prices_and_roll_up`: Regular 22.000 / Large 28.000 / Jumbo 32.000 sell as three lines with those prices and their own costs, the parent's stock drops by three, movements carry each variant's cost, and the `sales` view reports 3 sales / 82.000 for the parent item. Seed now gives Es Kopi Susu, Matcha Latte and Americano a Large size, with a quarter of their history sold as Large.
- Dashboard UI for managing variants is not built (API only); inventory list still shows the item's default price.
**Deviation:** none
**Next:** M4-T2

### [M4-T2] Modifiers
**Date:** 2026-09-03
**Status:** done
**Changed:** backend/alembic/versions/0008_modifiers.py (new), backend/app/models/models.py, backend/app/models/__init__.py, backend/app/services/catalog.py, backend/app/services/orders.py, backend/app/api/pos.py, backend/app/api/dashboard.py, backend/app/schemas/pos.py, backend/app/schemas/dashboard.py, backend/app/seed.py, backend/tests/test_modifiers.py (new, 10 tests), backend/tests/test_db_integration.py, backend/tests/test_invariants.py, docs/api-contract.md, frontend/app/pos/[businessToken]/page.tsx
**Gates:** pytest 133 passed 0 skipped · migrations round-trip ok (0008 → 0007 → 0008) · frontend build ok · seed ok
**Notes:**
- Migration 0008: `modifier_groups` (per item; `single`/`multi`, required, min/max), `modifiers` (`price_delta numeric(12,2)`, priced or free), `order_line_modifiers` (name and price **snapshotted** on the line). RLS on all three in the same migration; unique names per item/group.
- `create_order` validates a line's choices against the item's active groups (unknown/foreign/inactive → `unknown`; required group empty → `required`; two in a single-select → `single`; over `max_select` → `max`; Indonesian 422s at the POS), prices the line as variant price + Σ deltas, and writes the snapshot rows. Voids/refunds copy the snapshots onto the reversing line so a voided receipt itemises what was undone.
- Receipt: `GET /pos/orders/{id}/receipt` returns the printable shape (business, cashier, number, lines with size and modifiers as sold, notes, payments, totals). Kiosk: modifier chips per group in the quantity sheet (defaults preselected, required groups gate the Tambah button, single-select replaces, multi respects max), cart lines are per size + modifier set, success flash gets "Cetak struk" which renders a receipt sheet with an `@media print` stylesheet and calls `window.print()` — browser print only, no drivers (§4.1).
- Owner API for groups and modifiers (create/list/patch; blank 422, duplicate 409, min > max 422; single-select normalises to max 1, required to min 1). Seed: Es Kopi Susu gets Gula (required: Normal/Sedikit/Tanpa) and Tambahan (Extra shot +5.000, Susu oat +6.000), Matcha Latte and Americano get groups too.
- Done-when proved: `test_extra_shot_less_sugar_persists_and_prices` (unit price 27.000 = 22.000 + 5.000 + 0, two snapshot rows), `test_snapshot_survives_catalogue_edits` (renaming/repricing the modifier later leaves the line and its snapshot untouched), `test_receipt_prints_modifiers_and_pos_lists_groups` (receipt lines carry the modifiers and prices), selection-rule parametrised tests, void copying, RLS across the three tables.
**Deviation:** none
**Next:** M4-T3

### [M4-T3] Units and conversion
**Date:** 2026-09-03
**Status:** done
**Changed:** backend/alembic/versions/0009_uoms.py (new), backend/app/models/models.py, backend/app/models/__init__.py, backend/app/services/units.py (new), backend/app/api/auth.py, backend/app/api/dashboard.py, backend/app/schemas/dashboard.py, backend/app/seed.py, backend/tests/test_units.py (new, 8 tests), backend/tests/test_db_integration.py, backend/tests/test_invariants.py, docs/api-contract.md
**Gates:** pytest 142 passed 0 skipped · migrations round-trip ok (0009 → 0008 → 0009) · frontend build ok · seed ok
**Notes:**
- Migration 0009: `uoms` (code, name per business), `uom_conversions` (`factor numeric(18,6)`, qty_to = qty_from × factor, unique per pair), and a **nullable `uom_id` on `items`** — the one additive column the roadmap names; `items.unit` stays as the free-text label. RLS on both tables in the same migration. Backfill per existing business: the standard Indonesian set (kg, g, liter, ml, pcs, cup, porsi, botol, bungkus, dus, kaleng, pouch, sachet, tray, ikat, pack), kg↔g and liter↔ml, and every item whose unit text matches a code linked (11/11 seeded items).
- `services/units.py`: `ensure_standard_uoms` (idempotent; called at registration and by the seed), `convert_quantity` (direct row, else its reverse, else `UnitConversionMissing` — never a guess), `consume_stock` (converts into the item's unit, rounds to numeric(12,3) half-up, applies the same atomic conditional UPDATE as a sale, writes the ledger row). `create_uom` / `create_conversion` (reverse factor created automatically).
- Owner API: `GET/POST /api/uoms`, `GET/POST /api/uom-conversions`; item create/update accept `uom_id`, and item creation resolves the free-text unit to a known code when possible.
- Done-when proved by `test_consuming_250g_from_5kg_leaves_4_750` (stock 5.000 → 4.750 through the kg↔g boundary, ledger row −0.250, invariant holds). Also: 1 g → 0.001 kg is representable, 0.4 g rounds to nothing and writes no row, 1.5 g → 0.002; conversion both ways; missing conversion refused with nothing moved; an item without a unit cannot be consumed in another unit; a custom 'karung' = 25 kg conversion works alongside the standard pair; RLS across both tables.
- Nothing consumes across units in production paths yet — that is recipes (M4-T4), which `consume_stock` was built for.
**Deviation:** none
**Next:** M4-T4

### [M4-T4] Recipes
**Date:** 2026-09-03
**Status:** done
**Changed:** backend/alembic/versions/0010_recipe_lines.py (new), backend/app/models/models.py, backend/app/models/__init__.py, backend/app/services/catalog.py, backend/app/services/orders.py, backend/app/api/pos.py, backend/app/api/dashboard.py, backend/app/schemas/pos.py, backend/app/schemas/dashboard.py, backend/app/seed.py, backend/tests/test_recipes.py (new, 6 tests), backend/tests/test_db_integration.py, backend/tests/test_invariants.py, docs/api-contract.md, frontend/app/pos/[businessToken]/page.tsx
**Gates:** pytest 149 passed 0 skipped · migrations round-trip ok (0010 → 0009 → 0010) · frontend build ok · seed ok
**Notes:**
- Migration 0010: `recipe_lines` keyed on **variant** (`variant_id`, `component_item_id`, `quantity numeric(12,3)`, `uom_id`, `is_active`) with RLS, unique per (variant, component). Lines are deactivated, never deleted.
- Selling a variant with active recipe lines makes it **made to order**: `create_order` converts each component quantity × sold quantity into the component's unit (kg↔g, liter↔ml via M4-T3), rounds to numeric(12,3), takes it with the same atomic conditional UPDATE per component (an empty component rejects the whole order naming the component, and nothing else moves), and writes one `sale` movement per component at the component's cost with `source_id` = the line. The sold item's own stock is untouched. Variants without a recipe behave exactly as before.
- Reversals generalised: a void/refund now puts back **whatever the sale took** — it reverses the `sale` movements attached to the line (the item itself, or every component), so recipes and plain items share one code path.
- Owner API: `GET/POST /api/variants/{id}/recipe` (upsert by component), `PATCH /api/recipe-lines/{id}`; Indonesian errors (self-reference, zero quantity, unknown unit/component). POS `/pos/items` reports `made_to_order`; the kiosk shows "dibuat saat dipesan" instead of stock, never marks such items sold out, and does not cap the quantity by the item's own stock.
- Done-when proved by `test_selling_one_large_latte_consumes_beans_and_milk`: Large (24 g beans, 180 ml milk) → beans 5.000 → 4.976 kg, milk 10.000 → 9.820 l, two `sale` rows at the components' costs, latte stock untouched, **M2-T3 reconciliation holds**. Also: Regular × 3 scales (18 g / 120 ml each); out of beans rejects naming "Biji Arabica" with milk untouched; void restores 0.048 kg and 0.360 l via `sale_void` rows; a recipe unit on a component without a unit is refused; owner endpoints and made-to-order flag; RLS.
- Seed: Es Kopi Susu and Americano (Standar and Large) are made from Biji Arabica, Susu UHT and Gula Aren in g/ml. Their historical sales (before recipes) still ledger against the drink itself, which is honest history; new sales consume the raw materials.
- Cost of a made-to-order line is still the variant's entered `cost_price`; deriving it from components is M4-T5/M9-T4 (`get_recipe_cost`).
**Deviation:** none
**Next:** M4-T5

### [M4-T5] Moving-average COGS
**Date:** 2026-09-03
**Status:** done
**Changed:** backend/app/services/stock.py, backend/app/services/receipts.py, backend/app/services/orders.py, backend/app/ai/tools.py, backend/tests/test_cogs.py (new, 4 tests)
**Gates:** pytest 153 passed 0 skipped · migrations round-trip ok · frontend build ok · seed ok
**Notes:**
- `items.cost_price` is now the **moving-average cost**: `add_stock(reason="purchase", unit_cost=…)` recomputes it as (on-hand × old average + received × price) ÷ (on-hand + received), half-up to cents; stock at or below zero, or an item without a cost, simply takes the new price. Receipt-photo purchases go through it (the old "last price wins" assignment is gone); `add_stock` itself syncs the default variant (M4-T1), which the test caught: a sale prices its cost from the variant, so a purchase that only moved the item would have left the next sale snapshotting a stale cost.
- Snapshot at sale: unchanged for plain items (`unit_cost_at_sale` = the variant's/item's cost at that moment). **Made-to-order lines now snapshot the recipe cost** — Σ component moving-average cost × quantity consumed, per unit sold — instead of the variant's hand-entered cost.
- **Historical margin never joins today's cost.** The assistant's `get_profit` COGS now sums `order_lines.unit_cost_at_sale` (joined by the view's id) instead of `Sale.quantity × Item.cost_price`; lines backfilled from the legacy table have no snapshot and fall back to the current cost, reported separately as `cost_of_goods_lines_without_snapshot` so the estimate is never silently mixed. The dashboard P&L does not compute COGS (revenue − expenses), so nothing to change there; M9 puts both on the registry.
- Done-when proved by `test_buy_at_two_prices_sell_and_old_margin_does_not_move`: 2 @ 20.000 + 2 @ 30.000 → average 25.000; the sale snapshots exactly 25.000 (line and movement) with margin 20.000; buying 3 @ 60.000 moves today's average to 42.500 but the old line, its margin and `get_profit` for the period stay at 25.000; the next sale snapshots 42.500. Recipe: beans 5 kg @100.000 + 5 kg @140.000 → 120.000/kg; a large latte's COGS is 24 g × 120.000 + 180 ml × 17.000 = 5.940,00 exactly and does not move when beans later cost 200.000. Receipt photo: 3 kg @38.000 on 4 kg @30.000 → 33.428,57 on the item and its default variant.
**Deviation:** none
**Next:** canary (5 tasks since last), then M4-T6

### [M4-T6] Bulk catalogue import
**Date:** 2026-09-03
**Status:** done
**Changed:** backend/app/services/catalog_import.py (new), backend/app/api/dashboard.py, backend/tests/test_catalog_import.py (new, 6 tests), docs/api-contract.md, frontend/app/(dashboard)/settings/page.tsx
**Gates:** pytest 159 passed 0 skipped · migrations round-trip ok · frontend build ok · seed ok
**Notes:**
- One workbook, six loosely-named sheets: Barang (the existing stock template, unchanged semantics), Varian, Pilihan, Satuan, Konversi, Resep. A workbook whose only sheet is the old stock template still parses. Header matching reuses the existing synonym approach.
- Three separate phases: `parse` (pure; unreadable file / missing sheets / missing columns are structural errors), `validate` (every row checked against the sheet **and** what already exists in the database — items, variants, units, groups — producing every row-level error at once as `Sheet '<name>' baris <n>: <Indonesian reason>`), `apply` (units → conversions → items via the existing importer → variants → modifier groups and choices → recipe lines, all in the caller's transaction; any failure propagates so the caller rolls back). `import_catalog` raises before anything is written when validation finds a single error: **all or nothing**.
- `POST /api/catalog-import` takes the .xlsx as the raw request body (≤ 5 MB; no multipart dependency added), returns counts on success, 422 naming every bad row otherwise; `GET /api/catalog-template` serves a starter workbook. Settings page: download template + upload with the result or the full error list shown. WhatsApp document upload keeps the items-only path.
- Done-when proved by `test_five_bad_rows_import_nothing_and_are_all_named`: a 200-row workbook (120 items, 30 variants, 20 modifier rows, 5 units, 5 conversions, 20 recipe lines) with five deliberate errors — negative stock, unknown item on a variant, unknown selection kind, zero conversion factor, unknown recipe component — yields exactly 5 errors, each naming its sheet and row, and row counts across items/variants/groups/modifiers/units/conversions/recipes/ledger are unchanged afterwards. The clean version imports everything (120 items with opening ledger rows, 150 variants incl. defaults, 10 groups / 20 choices, 5 units, 5 conversions plus their reverses, 20 recipe lines) and re-importing updates in place without duplicates. Validation also accepts references to items that exist only in the database.
**Deviation:** none
**Next:** M5-T1 — M4 complete, tagged `checkpoint/M4`

### [M5-T1] Suppliers
**Date:** 2026-09-03
**Status:** done
**Changed:** backend/alembic/versions/0011_suppliers.py (new), backend/app/models/models.py, backend/app/models/__init__.py, backend/app/services/suppliers.py (new), backend/app/services/receipts.py, backend/app/api/dashboard.py, backend/app/schemas/dashboard.py, backend/app/seed.py, backend/tests/test_suppliers.py (new, 3 tests), backend/tests/test_db_integration.py, backend/tests/test_invariants.py, docs/api-contract.md
**Gates:** pytest 163 passed 0 skipped · migrations round-trip ok (0011 → 0010 → 0011) · frontend build ok · seed ok
**Notes:**
- Migration 0011: `suppliers` (name unique per business case-insensitively, phone, address, notes, `is_active`) with RLS, plus a nullable `receipts.supplier_id` (additive) so purchase history is derived from the receipts that point at a supplier — never stored on the supplier. Existing receipts are linked when their photo text equals a supplier name (no-op today, idempotent later).
- Linking rule: a committed receipt photo is attached to a supplier only when the text the model read matches an active supplier exactly (case-insensitive); nothing is auto-created from a photo, so a misread name never becomes a junk supplier. Creating a supplier also picks up earlier unlinked receipts with that name.
- Owner API: `GET/POST /api/suppliers`, `PATCH /api/suppliers/{id}` (deactivate, never delete), `GET /api/suppliers/{id}/history` (count, total spent, last purchase, recent receipts with item counts). Indonesian errors: blank name 422, duplicate 409, unknown 404. Seed creates three suppliers.
- Tests: create/update/duplicate/deactivate; a photo committed before the supplier exists is linked once the supplier is created, later photos link on commit case-insensitively, an unknown supplier name stays unlinked and creates nothing, history totals are exact; owner endpoints; RLS.
- M5-T2 (purchase orders) and M5-T3 (goods receipts) will extend the same history.
**Deviation:** none
**Next:** M5-T2

### [M5-T2] Purchase orders
**Date:** 2026-09-03
**Status:** done
**Changed:** backend/alembic/versions/0012_purchase_orders.py (new), backend/app/models/models.py, backend/app/models/__init__.py, backend/app/services/purchasing.py (new), backend/app/api/dashboard.py, backend/app/schemas/dashboard.py, backend/tests/test_purchase_orders.py (new, 4 tests), backend/tests/test_db_integration.py, backend/tests/test_invariants.py, docs/api-contract.md
**Gates:** pytest 168 passed 0 skipped · migrations round-trip ok (0012 → 0011 → 0012) · frontend build ok · seed ok
**Notes:**
- Migration 0012: enum `po_status` (draft, ordered, partially_received, received, cancelled), `purchase_orders` (supplier, per-business running `number`, notes, expected date, ordered/cancelled timestamps, `subtotal numeric(12,2)`) and `po_lines` (item, `quantity numeric(12,3)` in an optional unit, `unit_cost`, `line_total`, `received_quantity`), RLS on both.
- State machine in `services/purchasing.py`: lines are editable (add / change / remove) only while a PO is a draft — a draft is a worksheet, not history; `mark_ordered` refuses an empty PO and freezes lines; `cancel` is allowed from draft or ordered only and refused once anything has been received; `refresh_status_from_lines` derives partially_received / received from the lines and is what M5-T3's goods receipts will call. **A PO never touches stock or cost** — proved by the test (no ledger rows, stock unchanged).
- Owner API: list (filter by status), create with lines, get, patch notes/expected date, add/edit/delete line (draft only), order, cancel. Indonesian errors for every rule.
- Tests: draft lifecycle and running subtotal, numbering per business, ordering freezes lines, cancel rules including a simulated partial receipt, validation of supplier/item/unit/quantity, the endpoints end to end, RLS on both tables.
**Deviation:** none
**Next:** M5-T3

### [M5-T3] Goods receipt
**Date:** 2026-09-03
**Status:** done
**Changed:** backend/alembic/versions/0013_goods_receipts.py (new), backend/app/models/models.py, backend/app/models/__init__.py, backend/app/services/receiving.py (new), backend/app/services/suppliers.py, backend/app/api/dashboard.py, backend/app/schemas/dashboard.py, backend/tests/test_receiving.py (new, 5 tests), backend/tests/test_db_integration.py, backend/tests/test_invariants.py, docs/api-contract.md
**Gates:** pytest 174 passed 0 skipped · migrations round-trip ok (0013 → 0012 → 0013) · frontend build ok · seed ok
**Notes:**
- Migration 0013: `goods_receipts` (supplier, optional PO, per-business number, received_at/by, notes, `subtotal`) and `goods_receipt_lines` (as received: quantity, unit, unit cost; as ledgered: `quantity_item_unit`, `unit_cost_item_unit`; `line_total`; optional PO line), RLS on both.
- `receive_goods` is the event that moves purchased stock: per line it converts the received quantity and cost into the item's own unit (M4-T3), rounds to numeric(12,3) / cents, and calls `add_stock(reason="purchase")`, which increments stock, writes the `purchase` movement (`source_type = goods_receipt`) and recomputes the moving-average cost with the default variant in step (M4-T5). A line against a PO line advances `received_quantity` in the PO line's unit; the PO status is derived: `partially_received` while anything is short, `received` when complete. **Over-receipt is refused (409, naming ordered / already received / now) unless `allow_over_receipt` is set** — explicit, never silent. Partial receipt is normal.
- Supplier purchase history (M5-T1) now merges receipt photos and goods receipts, newest first, with combined count, total and last date; entries carry `kind`.
- Owner API: `POST /api/goods-receipts` (from PO or free), list, get; every rule has an Indonesian message.
- Done-when proved by `test_receiving_8_of_10_leaves_po_partially_received`: PO for 10 kg @150.000, receive 8 → PO `partially_received`, stock 2 → 10, moving average (2 × 140.000 + 8 × 150.000) ÷ 10 = 148.000 on the item and its default variant, one `purchase` movement of +8 at 150.000, **M2-T3 holds**; receiving the remaining 2 → `received`, cancel refused. Also: over-receipt of 11 refused with nothing written, then accepted with the flag; receiving 2.500 g at 150/g against a kg PO line (2.500 kg at 150.000/kg, average 145.555,56); a free receipt without a PO sets a first cost; validation of missing unit, missing conversion, mismatched PO line, unknown item, empty receipt; endpoints and supplier history; RLS.
**Deviation:** none
**Next:** M5-T4

### [M5-T4] Supplier invoice photo to draft goods receipt
**Date:** 2026-09-03
**Status:** done
**Changed:** backend/app/services/invoice_draft.py (new), backend/app/services/receipts.py, backend/app/whatsapp/processor.py, backend/tests/test_invoice_draft.py (new, 5 tests)
**Gates:** pytest 179 passed 0 skipped · migrations round-trip ok · frontend build ok · seed ok
**Notes:**
- Reuses the finished vision path unchanged (download, ack, private storage, `parse_business_document`, `pending_confirmations`, keyword confirm/deny, revision flow). New on top: `build_draft` matches the parsed supplier to an active supplier by name and every line to an existing item (exact → substring → difflib ≥ 0.72; two close candidates = ambiguous = ask), resolves the photo's unit into a unit the goods receipt can convert (an unknown or unconvertible unit is a question too), and `draft_summary` describes it in Indonesian: ✅ matched lines with the item they map to, ❓ questions with the reason and candidates and "akan dilewati kecuali kamu koreksi", supplier status, total, model ambiguities, and the one-reply instruction.
- **Never write stock from a photo without confirmation:** `_handle_image` now always parks — invoices as a `goods_receipt` pending carrying the draft, stock-book photos as before — regardless of confidence; the gate only adds a "⚠️ ada yang kurang jelas" line. The old auto-commit branch is gone.
- On **YA**, `confirm_draft` creates one goods receipt for the matched lines through M5-T3 (`purchase` ledger rows, moving-average cost, supplier history), keeps the photo as a receipt row linked to the supplier, records the purchase as an expense as before, and names the skipped questions in the facts the reply is composed from. **Unmatched lines are never guessed** — no item is created from a photo; a correction reply goes through the existing revision flow and the draft is rebuilt.
- Done-when proved by `test_photo_never_writes_stock_without_a_reply_and_one_ya_confirms` (model mocked, everything after it real): a high-confidence invoice with two matched lines and one unmatched ("Kecap BH") is parked with the ❓ question and stock untouched; a single "ya" creates the goods receipt with two lines (gula 4 → 7 kg at average 33.428,57; susu 10 → 12.5 l from 2.500 ml at 20/ml → 17.600), skips Kecap BH by name, consumes the pending, and the reconciliation invariant holds. Also: the draft's matching and unit resolution, fuzzy/ambiguous matching asks rather than guesses, "tidak" discards with nothing written.
- The Gemini-side part of the path (parse quality) stays measured under M1; its live re-measure is still blocked on quota (M1-T3 part 2).
**Deviation:** none
**Next:** canary (5 tasks since last), then M5-T5

### [M5-T5] Menu photo to draft catalogue
**Date:** 2026-09-03
**Status:** done
**Changed:** backend/app/ai/vision.py, backend/app/services/menu_draft.py (new), backend/app/whatsapp/processor.py, backend/tests/test_menu_draft.py (new, 4 tests)
**Gates:** pytest 183 passed 0 skipped · migrations round-trip ok · frontend build ok · seed ok
**Notes:**
- Same machinery as M5-T4 pointed at onboarding. `parse_menu_photo` uses a dedicated structured schema (products → variants with `price` and `price_written`; `is_menu`; confidence; ambiguities) and the same Indonesian number rules; an unreadable price is 0 with `price_written=false`, never a guess.
- Routing: a WhatsApp photo whose caption contains "menu" goes to the menu path; a receipt-parser miss now tells the owner to resend with the caption *menu*.
- `build_menu_draft` splits products into 🆕 new (with every size and price), ↩️ existing (matched to the catalogue case-insensitively — **left alone, a photo never reprices**), and ❓ unpriced (a question; created at price 0 only if the owner confirms, and the summary says so). The draft is parked as a `menu_draft` pending; YA creates each new product as an item (stock 0, unit `porsi`) with its default variant at the first price and one variant per extra size; corrections rebuild the draft through the existing revision flow.
- Done-when proved by `test_menu_caption_parks_a_draft_and_one_ya_creates_the_catalogue`: a captioned photo (model mocked) produces the reviewable draft with nothing created, one "ya" creates three items — Es Kopi Susu with Regular 22.000 (default) and Large 27.000, Roti Bakar Coklat 24.000, Pisang Goreng at 0 as announced — and the existing Es Teh Manis keeps its 8.000 despite the board saying 9.000. A non-menu photo is refused without a pending.
- Live parse quality of menu photos is unmeasured (Gemini quota, see M1-T3 part 2); the schema is ready for the same baseline script.
**Deviation:** none
**Next:** M6-T1 — M5 complete, tagged `checkpoint/M5`

### [M6-T1] Accounts
**Date:** 2026-09-03
**Status:** done
**Changed:** backend/alembic/versions/0014_accounts.py (new), backend/app/models/models.py, backend/app/models/__init__.py, backend/app/services/accounts.py (new), backend/app/api/auth.py, backend/app/api/dashboard.py, backend/app/schemas/dashboard.py, backend/app/seed.py, backend/tests/test_accounts.py (new, 3 tests), backend/tests/test_db_integration.py, backend/tests/test_invariants.py, docs/api-contract.md
**Gates:** pytest 187 passed 0 skipped · migrations round-trip ok (0014 → 0013 → 0014) · frontend build ok · seed ok
**Notes:**
- Migration 0014: enum `account_type` (asset, liability, equity, revenue, expense) and `accounts` (per-business `code` unique, name, type, `is_system`, `is_active`) with RLS; the standard chart is backfilled into every existing business from the single definition in `services/accounts.py` (idempotent), and `ensure_standard_chart` runs at registration and in the seed.
- The chart: 26 Indonesian SME accounts — Kas 1100, Bank 1110, Piutang QRIS/e-wallet 1120, Piutang usaha 1200, Persediaan 1300, Peralatan 1400; Utang usaha 2100, Utang pajak 2200, Liabilitas poin 2300, Pendapatan diterima di muka 2400; Modal 3100, Prive 3200, Laba ditahan 3900; Penjualan 4100, Diskon 4200, Retur 4300, Pendapatan lain 4900; HPP 5100, Bahan baku 5200, Gaji 5300, Sewa 5400, Listrik/air/gas 5500, Pemasaran & promo 5600, Penyusutan & barang rusak 5700, Selisih kas 5800, Beban lain 5900. Every account the roadmap's event catalogue needs is present (asserted by test). `DEBIT_NORMAL` records the normal balance per type for M6-T5; `EXPENSE_CATEGORY_ACCOUNT` maps the existing expense categories for M6-T6.
- Merchant-extendable: `POST /api/accounts` (numeric code, unique), `PATCH` renames or deactivates; seeded accounts are `is_system` — renameable, never deactivated, because posting rules (M6-T3) reference them by code. Indonesian errors throughout.
- Tests: chart well-formed and complete, seeding idempotent, custom accounts and every validation rule, endpoints, RLS.
**Deviation:** none
**Next:** M6-T2

### [M6-T2] Journal with a balance constraint
**Date:** 2026-09-03
**Status:** done
**Changed:** backend/alembic/versions/0015_journal.py (new), backend/app/models/models.py, backend/app/models/__init__.py, backend/app/services/ledger.py (new), backend/tests/test_journal.py (new, 5 tests), backend/tests/test_db_integration.py, backend/tests/test_invariants.py
**Gates:** pytest 193 passed 0 skipped · migrations round-trip ok (0015 → 0014 → 0015) · frontend build ok · seed ok
**Notes:**
- Migration 0015: `journal_entries` (per-business `entry_no`, `posted_at`, memo, source, `event_type`, created_by) and `journal_lines` (`line_no`, account, `debit`/`credit numeric(12,2)`, row CHECKs: non-negative and exactly one side non-zero), RLS on both.
- **Debits equal credits is enforced by the database, not application code:** Postgres has no multi-row CHECK, so the migration installs `journal_entry_must_balance()` as a `CONSTRAINT TRIGGER … DEFERRABLE INITIALLY DEFERRED` on both tables. Lines can be written one at a time inside a transaction; at COMMIT any entry whose debits ≠ credits, or that has no lines, is refused with a `check_violation`. The function is `SECURITY DEFINER` so the check sees every line whatever tenant context the caller has (a cascade delete of a business runs with none), and it skips entries that are themselves being removed.
- `services/ledger.py`: `post_entry` (validates early with clear codes — empty, unbalanced, side, account, amount — then writes; the trigger remains the enforcement for every other path), `reverse_entry` (a new entry with every line flipped; the original stays, per §1.5), `account_balances` (normal-direction balances by account code, optional period), `trial_balance`.
- Done-when proved by `test_unbalanced_entry_is_refused_by_the_database_at_commit`: raw rows, no service code, 50.000 debit against 40.000 credit flushes fine mid-transaction and the commit raises "unbalanced" from the database with nothing surviving. Also: an entry with no lines is refused at commit; a line with both sides or neither, or negative, is refused immediately by CHECK; a balanced sale entry posts, balances read correctly, the trial balance holds, and a reversal zeroes the balances while both entries remain; the service's early validation codes; RLS across both tables.
- Float guard now also matches `debit|credit`.
**Deviation:** none
**Next:** M6-T3

### [M6-T3] Posting rules
**Date:** 2026-09-03
**Status:** done
**Changed:** backend/alembic/versions/0016_posting_rules.py (new), backend/app/models/models.py, backend/app/models/__init__.py, backend/app/services/posting_rules.py (new), backend/app/api/auth.py, backend/app/api/dashboard.py, backend/app/schemas/dashboard.py, backend/app/seed.py, backend/tests/test_posting_rules.py (new, 6 tests), backend/tests/test_db_integration.py, backend/tests/test_invariants.py, docs/api-contract.md
**Gates:** pytest 199 passed 0 skipped · migrations round-trip ok (0016 → 0015 → 0016) · frontend build ok · seed ok
**Notes:**
- Migration 0016: `posting_rules` (per business: `event_type`, `component`, `debit_code`, `credit_code`, description, `is_system`, `is_active`; CHECKs that both codes are null together and differ), RLS, unique per (event, component); the standard set is backfilled into every business from the single definition in `services/posting_rules.py`, and seeded at registration and by the seed.
- **Rules are data, not `if` statements.** 37 rows cover the whole event catalogue (roadmap appendix A): `OrderCompleted` with one component per payment method (cash → 1100, QRIS/card/e-wallet → 1120, transfer → 1110, points → 2300, other → 1200, all against 4100), `discount` (4200 / 4100), `tax` (4100 / 2200), `service_charge` (4100 / 4900), `cogs` (5100 / 1300); `OrderVoided` = `reversal` (null codes: reverse the source entry); `OrderRefunded` per method against 4300 plus `cogs_reversal`; `GoodsReceived` (1300 / 2100); `SupplierPaid` cash/transfer; `StockWasted`; `StockCounted` loss/gain; `ExpenseIncurred` per category with an `expense:*` fallback; `ShiftClosed` short/over; `PointsEarned` / `PointsRedeemed`. Account references are by code so renamed accounts keep working; `pick_rule` resolves `expense:parkir` to `expense:*`.
- Owners can re-point a rule at another active account or deactivate it (`PATCH /api/posting-rules/{id}`); the reversal rule refuses accounts; debit and credit must differ; every rule's accounts must exist in the chart (asserted by test against `STANDARD_CHART`).
- The posting engine (M6-T4) will consume these through `rules_for` / `pick_rule` and contain no account codes of its own.
**Deviation:** none
**Next:** M6-T4

### [M6-T4] The posting engine
**Date:** 2026-09-03
**Status:** done
**Changed:** backend/app/services/posting.py (new), backend/app/services/orders.py, backend/app/services/receiving.py, backend/app/services/stock.py, backend/app/services/units.py, backend/tests/test_posting.py (new, 5 tests), backend/tests/conftest.py (`seed_books`), 16 test fixtures seeded with the chart and rules, backend/tests/test_db_integration.py
**Gates:** pytest 204 passed 0 skipped · migrations round-trip ok · frontend build ok · seed ok
**Notes:**
- `services/posting.py` is the single writer: `post_event(event_type, {component: amount})` looks up the business's rules (M6-T3), turns each non-zero amount into a Dr/Cr pair, and writes one journal entry (M6-T2) **in the caller's transaction**. It holds no account codes and no per-event branches; a component with no rule, or a negative amount, raises instead of being dropped. `reverse_event` flips the entries a source row posted (void).
- Wired in, same transaction as the originating change: `create_order` → `OrderCompleted` (one `payment:<method>` component per payment, discount, tax, service charge, and COGS from the lines' cost snapshots); `void` → reversal of the sale's entry; `refund` → `refund:<method>` per reversed payment plus `cogs_reversal` when restocked; `receive_goods` → `GoodsReceived` (inventory / payable); `set_absolute_stock` (opname, correction) → `StockCounted` variance at the item's average cost; `consume_stock(reason="waste")` → `StockWasted`.
- Done-when proved by `test_journal_failure_rolls_the_sale_back` three ways: the ledger raising (patched outage), a rule pointing at a non-existent account (refused before any write), and the database's own deferred balance constraint (a line altered after posting → the commit is refused) — each time the order, lines, payments and stock movements are gone and stock is back at 10. Also: a cash+QRIS sale posts six lines (cash, QRIS, COGS) and the trial balance holds; void/refund entries and balances; goods receipt, waste and count post with exact figures; the engine refuses unknown components, posts nothing for zero amounts, and follows a re-pointed rule with no code change.
- Test businesses now get the chart and rules like registration does (`tests/conftest.seed_books`), since a sale in a business without books must fail rather than post nothing.
- Opening stock and the seed's history are not capitalised (no `Persediaan` opening entry), so inventory shows negative movements from COGS until a first goods receipt — statements (M6-T5) will treat the opening balance explicitly.
**Deviation:** none
**Next:** canary (5 tasks since last), then M6-T5

### [M6-T5] Statements
**Date:** 2026-09-03
**Status:** done
**Changed:** backend/app/services/statements.py (new), backend/app/api/dashboard.py (`GET /api/statements/profit-loss`, `GET /api/statements/balance-sheet`), backend/app/schemas/dashboard.py, backend/app/seed.py, backend/tests/test_statements.py (new, 6 tests), backend/tests/test_invariants.py (`test_balance_sheet_balances`)
**Gates:** pytest 211 passed 0 skipped · migrations round-trip ok · frontend build ok · seed ok
**Notes:**
- Both statements read straight off the journal through the chart's account types, one grouped query each, balances in the account's normal direction. P&L over a half-open UTC window: revenue accounts (contra accounts such as diskon/retur show negative there, not as expenses), cost of goods sold = the 51xx block, everything else under expense is operating expense; gross and net profit derived. Balance sheet up to an instant: assets, liabilities, equity, plus *current earnings* (revenue − expenses to date) because the ledger has no closing entries.
- `BalanceSheet.balances` (assets = liabilities + equity + current earnings) is computed, surfaced by the API, and enforced as an invariant: `test_balance_sheet_balances` runs a posted day of its own (sale, receipt, waste, count, refund) and then checks every business two ways — raw SQL over `journal_lines` and the statement service — refusing to pass vacuously.
- Known week (24–30 Aug 2026) test: a sale the day before, a cash+QRIS sale, a goods receipt on credit, a rent expense posted through `ExpenseIncurred`, and a waste posted after the week; exact lines and totals asserted for the week, a wider window, the receipt-only day, the sheet at week start / week end / now, and the API's inclusive business-local dates (WIB) including the 422 for a reversed range.
- The seed now keeps books: opening stock is capitalised (`Persediaan` / `Modal pemilik`, event `OpeningBalance`) and the 30-day history posts one `OrderCompleted` summary entry per day, so the local balance sheet reads as a real shop rather than negative inventory. Seed time unchanged (~2.6 s).
- The existing `/api/pnl` (sales − expenses from the `expenses` table) stays as the money page's chart until M6-T6 routes expenses through the ledger; after that the ledger P&L is the single source and a statements view can replace it.
**Deviation:** none
**Next:** M6-T6

### [M6-T6] Expenses into the ledger
**Date:** 2026-09-03
**Status:** done
**Changed:** backend/app/services/expenses.py (new), backend/app/ai/tools.py (`record_expense`), backend/app/services/invoice_draft.py, backend/app/services/receipts.py, backend/app/seed.py, backend/tests/test_expenses.py (new, 5 tests), backend/tests/test_invoice_draft.py, backend/tests/test_stock_movements.py
**Gates:** pytest 216 passed 0 skipped · migrations round-trip ok · frontend build ok · seed ok
**Notes:**
- `services/expenses.record_expense` is now the only writer of `expenses` rows. It posts `ExpenseIncurred {expense:<category>: amount}` in the same transaction; the rules decide the account (`gaji` → 5300, `sewa` → 5400, unknown categories fall to `expense:*` → 5900), so the tool knows no codes. Category is normalised (trim/lower, empty → `lainnya`). Invalid amounts raise before any write; if the engine refuses, the row rolls back with it (tested).
- Done-when proved end to end minus Gemini: `handle_text` runs with a forced `record_expense` function call (the model call is the only fake), and the expense then appears on the ledger P&L under *Listrik, air & gas* with cash down by the same amount and the balance sheet still balancing.
- Receipt-sourced expenses: the two photo paths (`confirm_draft`, legacy `commit_parse`) keep writing the full receipt total as the expense row — that is what was paid — but post only the **uncapitalised remainder** through the engine (`ledger_amount`). `confirm_draft` capitalises via the goods receipt as before; `commit_parse` now posts `GoodsReceived` for stock taken in at a written price, then expenses the rest. A purchase is never inventory and expense both (asserted in both receipt tests: 164.000 inventory + 17.000 expense; 318.000 inventory + nothing expensed).
- Both photo paths book the goods against `Utang usaha` (payables) like a goods receipt does; settling that in cash is M7-T2's cash-out, not guessed here.
- The seed's five expenses go through the same writer, so the local balance sheet and P&L now show them. `/api/pnl` and `get_profit` still read the `expenses` table (their "recorded expenses" figure); the ledger P&L is the accounting view.
**Deviation:** none
**Next:** tag `checkpoint/M6`; M7-T1

### [M7-T1] Shifts
**Date:** 2026-09-03
**Status:** done
**Changed:** backend/alembic/versions/0017_shifts.py (new), backend/app/models/models.py (`Shift`, `Order.shift_id`, `Payment.shift_id`), backend/app/models/__init__.py, backend/app/services/shifts.py (new), backend/app/services/orders.py, backend/app/api/pos.py (`GET /pos/shift`, `POST /pos/shift/open`, `POST /pos/shift/close`), backend/app/api/dashboard.py (`GET /api/shifts`), backend/app/schemas/pos.py, backend/app/seed.py, backend/tests/test_shifts.py (new, 4 tests), backend/tests/test_db_integration.py (`test_rls_isolates_shifts`), backend/tests/test_invariants.py (money guard now also matches `float|cash|variance`; 4 shift columns in the known set), frontend/app/pos/[businessToken]/page.tsx, docs/api-contract.md
**Gates:** pytest 221 passed 0 skipped · migrations round-trip ok · frontend build ok · seed ok
**Notes:**
- `shifts`: staff, opening float, opened/closed at, closed by, expected cash, counted cash, variance, notes. Additive: new table with RLS in the same migration, plus nullable `shift_id` on `orders` and on `payments`. The database enforces one open shift per cashier (partial unique index), non-negative float/count, and that a closed row carries all three figures (tested: a second open row for the same cashier is refused at commit).
- Attribution follows the till the money moved through: `create_order` stamps the cashier's open shift on the order and its payments; a void/refund stamps the *acting* cashier's open shift on the reversing payments, so a refund paid out in a later shift is that shift's cash while the sale stays where it was made (tested). A sale with no open shift is still a sale (`shift_id` NULL) — discipline is the count, not refusing customers.
- Expected cash for M7-T1 = float + cash payments − cash refunds attributed to the shift; live while open, written once at close with counted and variance (counted − expected), never edited. M7-T2 adds cash in/out to the formula; M7-T3 posts the variance.
- Kiosk: header shows "Buka shift" or the live expected cash of the open shift; opening asks for the float, closing asks for the count (+ note) and shows expected / counted / variance. Owner: `GET /api/shifts` lists shifts newest first with the same view (UI comes with the M7-T3 report).
- Seed: yesterday Sari's shift (float 200.000, her 24 payments attributed, counted 5.000 short); today's till is opened from the kiosk.
**Deviation:** none
**Next:** M7-T2

### [M7-T2] Cash in and out
**Date:** 2026-09-03
**Status:** done
**Changed:** backend/alembic/versions/0018_cash_movements.py (new), backend/app/services/cash.py (new), backend/app/models/models.py (`CashMovement`), backend/app/models/__init__.py, backend/app/services/posting_rules.py (5 new standard rules), backend/app/api/pos.py (`GET /pos/suppliers`, `POST /pos/cash`, `GET /pos/cash`), backend/app/api/dashboard.py (`GET /api/cash-movements`), backend/app/schemas/pos.py, backend/tests/test_cash.py (new, 4 tests), backend/tests/test_db_integration.py (`test_rls_isolates_cash_movements`), backend/tests/test_invariants.py (every business has every standard rule), frontend/app/pos/[businessToken]/page.tsx, docs/api-contract.md, docs/BUILD-ROADMAP.md (appendix A: `CashIn`, `BankDrop`)
**Gates:** pytest 227 passed 0 skipped · migrations round-trip ok · frontend build ok · seed ok
**Notes:**
- `cash_movements`: four kinds, each posting in the caller's transaction. `cash_in` (Dr Kas / Cr Modal pemilik, or Cr Bank when the money came from the bank), `petty_cash` (through the M6-T6 expense writer, so it is an expense row on the P&L with Cr Kas), `supplier_payment` (Dr Utang usaha / Cr Kas, or Cr Bank by transfer — this is what settles the payable a goods receipt created), `bank_drop` (Dr Bank / Cr Kas). Rules are data as always: the service holds no account codes, only kind → event type.
- Stamped with the till the money actually moved through: the acting cashier's open shift, except a supplier paid by transfer, which never touched the drawer and is deliberately unstamped (tested both ways). M7-T3's expectation reads these.
- Table CHECKs: amount > 0, a supplier payment must name a supplier, a petty cash row must link the expense it wrote. Eight validation cases assert nothing is written — no movement, no journal entry, no expense.
- New invariant `test_every_business_has_every_standard_posting_rule`: a rule added to `STANDARD_RULES` must reach existing businesses through a backfill migration (0016 and now 0018), or the engine refuses their events. Checked per tenant, non-vacuously.
- Kiosk: a "Kas" button opens a sheet with the four kinds, amount, reason, a category for petty cash and a supplier picker for payments; the header's expected-cash chip refreshes after each one.
- Verified in the running kiosk: opened Sari's shift with a 200.000 float and the header chip showed "Shift buka · kas seharusnya Rp 200.000".
**Deviation:** none
**Next:** M7-T3 (close with reconciliation); canary due after it (5 tasks since M6-T4)

### [M7-T3] Close with reconciliation
**Date:** 2026-09-06
**Status:** done
**Changed:** backend/app/services/shifts.py (`cash_summary` + `post_variance`), backend/app/schemas/pos.py (`ShiftOut.cash_in`, `cash_out`), backend/app/seed.py, backend/tests/test_shifts.py (4 new tests), backend/tests/test_invariants.py (`test_closed_shift_variance_is_posted`), frontend/lib/types.ts (`ShiftRow`, `CashMovementRow`), frontend/lib/demo.ts (shift + cash fixtures), frontend/app/(dashboard)/money/page.tsx (till report), frontend/app/pos/[businessToken]/page.tsx, frontend/app/globals.css (`.hairline-t`), docs/api-contract.md
**Gates:** pytest 232 passed 0 skipped · migrations round-trip ok (0018 → 0017 → 0018) · frontend build ok · seed ok
**Notes:**
- Expected cash is now the whole drawer: **float + cash payments − cash refunds + cash in − cash out**, over everything stamped with the shift. Only movements that went through the drawer carry a `shift_id` (M7-T2), so a supplier paid by transfer correctly stays out of the sum. `cash_summary` reads payments and cash movements in two grouped queries and returns `cash_in` / `cash_out` alongside the figures it already had; `CashSummary` grew two fields rather than the callers growing arithmetic. `shifts.py` imports `OUTFLOWS` from `cash.py` inside the function — `cash.py` needs `open_shift_id` from `shifts.py`, so the module-level import would cycle.
- `close_shift` writes expected / counted / variance as before, then calls `post_variance`, which posts `ShiftClosed {variance_short | variance_over: abs(variance)}` **in the caller's transaction**. The rules were already seeded in M7-T2 (short: Dr 5800 *Selisih kas* / Cr 1100 *Kas*; over the other way round), so the service still holds no account codes. A shift that counted exactly posts nothing.
- Done-when proved twice. In tests: a simulated shift (float 200.000, a cash sale, a QRIS sale, a cash refund, cash in 50.000, petty cash 15.000, a bank drop 40.000 and a supplier paid 30.000 by transfer) expects 195.000, counts 191.500, and posts one entry of exactly 3.500 against 5800/1100 with the trial balance and the balance sheet still holding. And in the running app: opened Sari's till at 200.000 from the kiosk, sold a croissant for 28.000 cash, took 15.000 petty cash out, closed at 210.000 against an expected 213.000 — `select ... from shifts join journal_entries` returns `Dr Selisih kas 3.000 / Cr Kas 3.000`.
- `test_a_refused_posting_leaves_the_shift_open`: point the `variance_short` rule at an account that does not exist and the close is refused — the shift is still open, with no expected, counted or variance written and no entry. A till cannot be closed off the books.
- New invariant `test_closed_shift_variance_is_posted`: per tenant, every closed shift with a non-zero variance has exactly one `ShiftClosed` entry naming it as source, for `|variance|`, and an exact count has none. It fails vacuously-safe (asserts it saw closed shifts and at least one variance). This is what forced the seed to stop hand-writing its closed shift's variance and post it through `post_variance` like a real close.
- Kiosk close sheet now shows the sum line by line (modal awal, penjualan tunai, refund, kas masuk, kas keluar, kas seharusnya) instead of one prose line — a cashier cannot sensibly confirm a count against a number whose derivation is hidden. The close receipt shows float + net cash alongside expected / counted / selisih.
- Owner till report (the UI M7-T1 deferred to here) sits on the money page: one card per shift with the same breakdown, the variance in red when short and amber when over, the closing note, and the cash movements that went through that till. Demo mode gets fixtures for both new endpoints so the offline preview stays complete.
**Deviation:** none
**Next:** canary (5 tasks since M6-T4), then M7-T4

### [M7-T4a] The pricing engine and its table of twelve
**Date:** 2026-09-06
**Status:** done
**Split:** M7-T4 turned out to be two tasks, so it was split per roadmap §0.1. **M7-T4a** (this entry) is the settings row and the pure pricing function with the twelve-combination table the roadmap asks for *first*. **M7-T4b** is applying it at the till: the manager-PIN gate on discounts, `create_order` writing the figures, the `rounding` posting rule and its backfill, and the kiosk + owner settings UI. Nothing in T4a is wired into a sale yet, and no behaviour changes for an existing business.
**Changed:** backend/alembic/versions/0019_pricing_settings.py (new), backend/app/models/models.py (`PricingSettings`), backend/app/models/__init__.py, backend/app/services/pricing.py (new), backend/app/api/auth.py, backend/app/seed.py, backend/tests/test_pricing.py (new, 35 tests), backend/tests/test_invariants.py (money-guard exclusions + 2 known columns)
**Gates:** pytest 267 passed 0 skipped · migrations round-trip ok (0019 → 0018 → 0019) · frontend build ok · seed ok
**Notes:**
- `pricing_settings`, one row per business (unique on `business_id`), RLS in the same migration, backfilled for every business that already existed and seeded at registration: `tax_rate`, `tax_inclusive`, `service_charge_rate`, `service_before_tax`, `rounding_unit`, `rounding_mode`, `discount_requires_pin`. Defaults are the plain warung — no tax, no service charge, no rounding, a manager PIN before any discount — so nothing changes for an existing shop until an owner turns something on. CHECKs on both rates, the rounding unit and the mode; a test drives each one to prove the database refuses impossible settings even if a future caller skips the service.
- `services/pricing.price_order` is a **pure function**: lines + settings + an optional bill discount → exactly the figures an `orders` row carries. No session, so the arithmetic is pinned by a table of literals instead of by running sales. It raises rather than clamping — a discount bigger than the bill, a zero quantity or an unknown rounding mode is refused, because a till that quietly charges something else is worse than one that refuses.
- **The table of twelve** (`test_the_twelve_combinations`): tax inclusive/exclusive × service charge before/after tax × no discount / line discount / bill discount, on one bill (3 × 18.000 + 1 × 27.500 = 81.500) at 11% tax, 5% service and rounding to the nearest 100. Every expected figure was worked out by hand first. A thirteenth test guards the table itself — twelve rows, all four settings pairs, no duplicates — so it cannot silently shrink.
- What the table pinned down that was not obvious: with **exclusive** tax the placement of the service charge does not move the total (5% then 11% is the same product as 11% then 5%: both give 95.000) — it only moves the split, 4.075 service + 9.413,25 tax versus 4.523,25 + 8.965. The ledger cares about that split, so the test asserts it and not just the total. With **inclusive** tax nothing is ever added on top: the tax is extracted (81.500 → 73.423,42 + 8.076,58) and `service_before_tax` decides only whether the service charge carries its own tax.
- Two identities are asserted on every row: exclusive → `total = subtotal − discount + service + tax + rounding`; inclusive → the same without `+ tax`, because it is already inside the line totals and `tax_total` reports what is contained. Plus `|rounding| <= 50` and `total % 100 == 0` — rounding is the gap to the nearest 100, never a way to gain money.
- Rounding modes are their own table: nearest (a tie goes up), up, down, unit 0 = off, and an already-exact total rounds to itself under `up` rather than jumping a step.
- The money-column guard in `test_invariants` flagged `rounding_mode` (text) and `discount_requires_pin` (boolean) because their names contain *rounding* and *discount*. Both are exactly what that guard's documented `NOT_A_NUMBER` exclusion is for, so `_mode$` and `_pin$` joined it; `service_charge_rate` and `rounding_unit` were added to `KNOWN_MONEY_COLUMNS` so the pattern is still proven to catch the columns that do hold rupiah. The invariant itself is unchanged.
**Deviation:** M7-T4 split into M7-T4a and M7-T4b (recorded above, roadmap §0.1). No schema change beyond the new table; §1.6 respected.
**Next:** M7-T4b

### [M7-T4b] Discounts, tax, service charge and rounding at the till
**Date:** 2026-09-06
**Status:** done — completes M7-T4 (split recorded under M7-T4a)
**Changed:** backend/alembic/versions/0020_pricing_rules.py (new, rule backfill only), backend/app/services/posting_rules.py (7 new standard rules), backend/app/services/pricing.py (`service_tax`, `fiscal_components`, read-only `pricing_config`), backend/app/services/orders.py (`create_order` prices through the engine; discount gate; refund mirrors the sale's fiscal lines), backend/app/api/pos.py (`POST /pos/quote`; order + receipt carry the breakdown; Indonesian pricing errors), backend/app/schemas/pos.py, backend/app/api/dashboard.py (`GET/PATCH /api/pricing-settings`), backend/app/schemas/dashboard.py, backend/tests/test_till_pricing.py (new, 8 tests), backend/tests/test_posting_rules.py (OrderCompleted rule count 11 → 13), frontend/app/pos/[businessToken]/page.tsx, frontend/app/(dashboard)/settings/page.tsx, frontend/lib/types.ts, frontend/lib/demo.ts, docs/api-contract.md
**Gates:** pytest 275 passed 0 skipped · migrations round-trip ok (0020 → 0019 → 0020) · frontend build ok · seed ok
**Notes:**
- `create_order` now prices the bill with `price_order` (M7-T4a) from the business's `pricing_settings`: per-line `line_discount`, a `bill_discount`, tax inclusive/exclusive, service charge before/after tax, rupiah rounding — and writes every figure to the order row and the lines. **Payments must equal the rounded total**, not the pre-rounding one (tested: 80.250 offered against 80.500 due is a mismatch, nothing sold). `PricingInvalid` (a discount bigger than its line or the bill) propagates before any row exists, and the stock decrements roll back with the caller (tested: stock is untouched after three refused sales).
- **The permission gate.** When `discount_requires_pin` is on (the default), any discount — line or bill — without the manager PIN raises `DiscountNeedsManager` before any stock moves; a wrong PIN raises `ManagerPinRejected`; the owner's PIN opens it; a business that turns the gate off lets the cashier discount alone; a bill with no discount never asks. The kiosk shows the PIN field only when the quote says one is needed.
- **The ledger split is exact.** `PricedOrder.fiscal_components()` is the one place that knows what a sale reclassifies out of revenue: `discount`, `tax`, `service_charge` **ex-tax**, and `rounding_up` / `rounding_down` (two components because the engine never posts a negative). Result, asserted on the table's own rows: exclusive tax → `4100 Penjualan` is exactly the goods at list price (81.500) with discount in 4200, tax in 2200, service in 4900; inclusive tax → 4100 is the goods ex-tax (73.423,42), 4900 gets the service **ex** its own tax (3.671,17, not the 4.075 the customer sees) and 2200 the whole tax (8.480,41). Without the ex-tax split, other income would be overstated and revenue understated by the service charge's tax — that is why `PricedOrder` grew `service_tax`.
- **A refund undoes what the sale posted, not what today's settings say.** `_fiscal_lines_of_sale` reads the sale's own `OrderCompleted` lines by memo and posts `<component>_reversal` for each; the test changes the tax rate, drops the service charge and turns rounding off *between* the sale and the refund, and the books still come back to zero discount, zero tax owed, zero service income, zero rounding, with returns (4300) equal and opposite to revenue (4100). Voids already flipped the whole entry; asserted again with a priced bill.
- Migration 0020 changes no table: it backfills the seven new standard rules (`rounding_up`, `rounding_down`, and five `*_reversal` for refunds) into existing businesses, as `test_every_business_has_every_standard_posting_rule` requires. Rounding goes to `4900 Pendapatan lain-lain` rather than a new account — the chart is unchanged.
- A real bug caught by the existing race test: the first version of `pricing_config` lazily *inserted* the settings row, so two concurrent first sales on a business collided on the unique key. The sale path is now read-only and prices with the defaults if the row is somehow missing (registration, the seed and migration 0019 make sure it is not). `ensure_pricing_settings` stays for registration and the owner's PATCH.
- `POST /pos/quote` prices the cart without selling it, through the same pure function. The kiosk re-quotes on every cart or discount change and shows subtotal / diskon / service / pajak ("sudah termasuk" when inclusive) / pembulatan / total in the pay sheet; a line's price opens a rupiah line-discount sheet; the receipt prints the same breakdown above TOTAL. The kiosk never adds tax or rounding itself, so the screen and the ledger cannot disagree.
- Owner settings page: "Pajak, service & pembulatan" — percentages and whole rupiah in the form, fractions on the wire (0.11 = 11%), rounding unit and direction, the two placement toggles with plain-language explanations, and the discount gate. Takes effect on the next sale; nothing already sold is repriced. Demo mode carries a fixture.
- `test_posting_rules.test_owner_endpoints` counts the OrderCompleted rules literally; 11 → 13 for the two rounding rules. The assertion is unchanged in kind and grew, not shrank.
- The `sales` view (and so `/api/overview`, `get_profit`) still reads `line_total`, i.e. net of line discounts but before bill discount, tax, service and rounding. That is the goods figure; the ledger P&L is where the full picture lives, and M9's metric layer is the planned single source.
**Deviation:** none beyond the recorded M7-T4 split.
**Next:** tag `checkpoint/M7`; M8-T1. Canary due after 5 tasks (last at M7-T3: T4a, T4b = 2 so far).

### [M8-T1] Customers
**Date:** 2026-09-06
**Status:** done
**Changed:** backend/alembic/versions/0021_customers.py (new), backend/app/models/models.py (`Customer`, FK on `Order.customer_id`), backend/app/models/__init__.py, backend/app/services/customers.py (new), backend/app/services/orders.py (`customer_id` on `create_order`, receipt names the customer), backend/app/api/pos.py (`GET/POST /pos/customers`, `OrderIn.customer_id`), backend/app/schemas/pos.py, backend/app/api/dashboard.py (`GET/POST/PATCH /api/customers`), backend/app/schemas/dashboard.py, backend/tests/test_customers.py (new, 12 tests), backend/tests/test_db_integration.py (`test_rls_isolates_customers`), frontend/app/(dashboard)/customers/page.tsx (new), frontend/app/(dashboard)/layout.tsx (nav), frontend/components/icons.tsx (`IconUsers`), frontend/app/pos/[businessToken]/page.tsx (customer picker + quick add in the pay sheet; receipt), frontend/lib/types.ts, frontend/lib/demo.ts, docs/api-contract.md
**Gates:** pytest 288 passed 0 skipped · migrations round-trip ok (0021 → 0020 → 0021) · frontend build ok · seed ok
**Notes:**
- `customers`: name, phone, address, birthday, notes, `is_active`; RLS in the same migration. **Phone is the natural key**: normalised with the same helpers as the owner's number (`0812-3456-7890`, `+62 812 3456 7890` and `6281234567890` are one person; a Malaysian `012…` becomes `60…`), stored digits-only international because it is also the customer's WhatsApp identity for M8's points and promos, and unique per business through a partial unique index — a customer without a phone is allowed (a walk-in known by name), and two businesses may each have the same number (RLS test). The database CHECKs the shape (8–15 digits) and refuses a blank name, so the key holds even if a caller bypasses the service (tested).
- `orders.customer_id` has been a nullable forward reference since 0005; migration 0021 gives it its foreign key, the way 0007 did for `variant_id`. Every existing order is NULL there so the constraint is safe. `create_order(customer_id=…)` requires the customer to exist for this business and be active, checked before any stock moves — an unknown or inactive id is refused with nothing written; a dangling id cannot even be inserted (FK, tested).
- **History is derived, never stored**, like suppliers: `visits` = orders not voided (a void never happened; a refund did), `total_spent` = Σ total of *completed* orders (a refunded order is not spend), `last_visit` = latest non-void sale. One grouped query for a page of customers.
- Till: `GET /pos/customers?q=` searches name (substring) or phone (digits, local or international form); `POST /pos/customers` quick-adds with a name and phone — a phone already registered answers 409 with an Indonesian message rather than creating a twin. The pay sheet has an optional "Pelanggan" field: type two characters, pick a match, or add the person inline; a query that is all digits pre-fills the phone. The receipt prints "utk <name>".
- Owner: a new "Pelanggan" page — search, add/edit sheet (name, phone, address, birthday, notes), deactivate/reactivate, and each card shows kunjungan / belanja / terakhir. Demo mode has four fixtures.
- Not done here, by design: attaching the customer from WhatsApp inbound (the phone match makes that a one-liner later), and any loyalty maths — M8-T2 owns points.
**Deviation:** none
**Next:** M8-T2 (points ledger). Canary due after M8-T2 (tasks since M7-T3's canary: T4a, T4b, M8-T1 = 3).

### [M8-T2] Points ledger
**Date:** 2026-09-06
**Status:** done
**Changed:** backend/alembic/versions/0022_points.py (new), backend/app/models/models.py (`LoyaltySettings`, `PointsMovement`, `Customer.points_balance`), backend/app/models/__init__.py, backend/app/services/points.py (new), backend/app/services/posting_rules.py (`PointsReversed`), backend/app/services/orders.py (redeem as payment, earn on the cash part, reverse on void/refund), backend/app/services/customers.py (balance in views), backend/app/api/pos.py (`GET /pos/loyalty`; `points` payment method; earned/redeemed on order + receipt), backend/app/schemas/pos.py, backend/app/api/dashboard.py (`GET/PATCH /api/loyalty-settings`, `GET /api/customers/{id}/points`, `POST …/points/adjust`), backend/app/schemas/dashboard.py, backend/app/api/auth.py, backend/app/seed.py (programme on, two regulars earning on their history), backend/tests/test_points.py (new, 9 tests), backend/tests/test_invariants.py (`test_points_ledger_reconciles_with_cached_balance`), backend/tests/test_db_integration.py (`test_rls_isolates_points_and_loyalty_settings`), frontend/app/pos/[businessToken]/page.tsx (pakai poin), frontend/app/(dashboard)/customers/page.tsx (balance, history, adjust), frontend/app/(dashboard)/settings/page.tsx (Program poin), frontend/lib/types.ts, frontend/lib/demo.ts, docs/api-contract.md, docs/BUILD-ROADMAP.md (appendix A: `PointsReversed`)
**Gates:** pytest 299 passed 0 skipped · migrations round-trip ok (0022 → 0021 → 0022) · frontend build ok · seed ok
**Notes:**
- **Same pattern as stock, deliberately.** `points_movements` is append-only (one signed integer row per change, a CHECK refuses zero), `customers.points_balance` is the cached SUM, and both move in the same transaction. Redemption decrements the cache with the same atomic conditional UPDATE the stock race uses (`… where id = :cid and points_balance >= :n returning …`), so two tills cannot spend the same points: `test_two_simultaneous_redemptions…` runs two sales concurrently against a 30-point balance each spending all 30 and gets exactly one `sold` and one `rejected`. A refused redemption raises before the sale commits, so the order, its payments and its stock decrement all roll back with it (tested).
- Points are `integer`, not `numeric` — you cannot hold half a point, and the money guard is about rupiah and stock. What the ledger saw for each row is money and is `amount numeric(12,2)` (named so the guard covers it).
- **Earn rule**: `floor(paid_with_money / rupiah_per_point)` on a sale attached to a customer, when the programme is on. The part paid with points earns nothing (a 20.000 bill paid 5.000 in points + 15.000 cash earns 15, not 20). The cost (`points × point_value`) is posted `PointsEarned` — Dr 5600 marketing / Cr 2300 points liability — so the P&L carries the programme's real cost and the balance sheet the real liability.
- **Redemption is a payment method**: `method: points` on a payment, amount in rupiah, which must be a whole number of points at `point_value`, at least `min_redeem_points`, for a registered customer, with the programme on — each refused with its own Indonesian message. The sale's own `payment:points` component (M6-T3's rule, Dr 2300 / Cr 4100) posts it; nothing extra is posted here, and revenue is the full bill whichever way it was paid.
- **Reversals**: void and refund write the opposite row for every earn and redeem of the order. Redeemed points return (the void flips the sale's entry; the refund posts `refund:points`, Dr 4300 / Cr 2300); earned points are taken back and their accrued cost released through the new `PointsReversed` rule (Dr 2300 / Cr 5600, backfilled by 0022). After voiding the earning sale the books show zero accrued and zero spent.
- Taking back points the customer has already spent leaves a **negative balance** on purpose (tested: −2). The alternative — refusing to write the reversal — would break the ledger; a negative balance is visible on the owner's page in red and can never be spent because the guard needs `balance >= points`. No lower-bound CHECK for that reason; the invariant still holds.
- `test_points_ledger_reconciles_with_cached_balance` mirrors the stock invariant: per tenant, SUM(points_delta) == points_balance for every customer, refusing to pass with no customers or none with points. The seed turns the programme on (1 poin / Rp 1.000, poin = Rp 100, min 10) and gives Andi and Rina about one history sale in six, earning as they go, so the local invariant is non-vacuous and the demo has balances to redeem.
- Kiosk: with a customer attached and the programme on, a "Pakai poin" toggle pays as much of the bill as whole points allow (capped at the balance and the total), the rest by the chosen tunai/QRIS/split; the flash and the receipt say how many points were used and earned. Owner: settings section "Program poin"; the customer card shows the balance; the edit sheet shows the ledger and takes a manual `±` adjustment with a reason (a row, never an edit; down cannot pass zero).
- `loyalty_config` is read-only on the sale path, for the same reason `pricing_config` is (M7-T4b's race); `ensure_loyalty_settings` is used by registration, the seed and the owner's PATCH.
**Deviation:** none
**Next:** canary (5 tasks since M7-T3: T4a, T4b, M8-T1, M8-T2 = 4 — due after M8-T3), then M8-T3 (promo engine)

### [M8-T3] Promo engine
**Date:** 2026-09-06
**Status:** done
**Changed:** backend/alembic/versions/0023_promos.py (new), backend/app/models/models.py (`Promo`, `PromoCondition`, `PromoApplication`, `Order.promo_total`), backend/app/models/__init__.py, backend/app/services/promos.py (new), backend/app/services/accounts.py (`4250 Diskon promo`), backend/app/services/posting_rules.py (`promo`, `promo_reversal`), backend/app/services/pricing.py (`promo_discount` per line, `promo_total`, identity), backend/app/services/orders.py (two-pass pricing: cashier's lines, then bonus lines; applications recorded; refund mirrors `promo`), backend/app/api/pos.py (quote runs the engine; order + receipt carry promo figures), backend/app/schemas/pos.py, backend/app/api/dashboard.py (`GET/POST/PATCH /api/promos`), backend/app/schemas/dashboard.py, backend/app/seed.py (a weekday-afternoon BOGO and a Friday 10%), backend/tests/test_promos.py (new, 9 tests), backend/tests/test_db_integration.py (`test_rls_isolates_promos`), backend/tests/test_invariants.py (6 known money columns), backend/tests/test_accounts.py + backend/tests/test_posting_rules.py (count literals: 26 → 27 standard accounts, 13 → 14 OrderCompleted rules), frontend/app/(dashboard)/promos/page.tsx (new), frontend/app/(dashboard)/layout.tsx (nav), frontend/app/pos/[businessToken]/page.tsx (promo lines in the pay sheet, receipt), frontend/lib/types.ts, frontend/lib/demo.ts, docs/api-contract.md
**Gates:** pytest 309 passed 0 skipped · migrations round-trip ok (0023 → 0022 → 0023) · frontend build ok · seed ok
**Notes:**
- `promos` is the reward — `percent_off` / `amount_off` on an item or the whole bill, or `bonus_item` (buy N of A, get M of B; B defaults to A, which is a BOGO) with `max_per_order` — and `promo_conditions` are its ANDed conditions: `date_range` (UTC instants, end exclusive), `day_of_week` and `time_window` (both in the **business's timezone**; a window may cross midnight), `min_spend` (net of the cashier's discounts, before promos), `multiples`. **There is no activate/expire job.** A promo applies exactly when every condition holds at the moment of the sale, so it starts and stops by itself — tested to the minute: open at 14:00:00, closed at 17:00:00, and 15:00 WIB is 08:00 UTC.
- `apply_promos` is a pure function over the priced cart. Item-level rewards first in a stable order (so two tills agree — tested), bill-level rewards on what remains; a line is never discounted below zero; `amount_off` is per multiple bought and capped at the line.
- **A bonus item is a real line**: added at its list price with a promo discount equal to the whole line, so stock moves (and is guarded — a BOGO with one cup left is refused, nothing sold), cost of goods posts for both cups, revenue is recognised gross and the give-away goes to the new contra-revenue account **`4250 Diskon promo`** (backfilled into every existing chart; the account stays on downgrade because the ledger never deletes). The done-when, asserted: buy one Kopi at 15:30 on a Monday → pay 20.000, get two; `4100` +40.000, `4250` −20.000, `5100` +16.000, stock −2; the same sale at 17:00:00 gets nothing; buy three gets two free (the cap).
- `create_order` prices in two passes: the cashier's lines take stock as before, then — once the engine has seen them — the bonus lines take theirs through the same loop body, and everything goes through `price_order` with `promo_discount` per line and a `promo_bill_discount`. `orders.promo_total` is kept apart from `discount_total` so a campaign's cost and a cashier's discounts never blur; the identity is now `total = subtotal − discount − promo + service + tax + rounding`. Payments must equal the promo price (paying for the free cup is a mismatch, tested).
- `promo_applications` records what each promo gave on which line — the receipt names the promos, a refund reads the sale's own `promo` journal line to post `promo_reversal` exactly, and the owner's list shows `applications` and `given_away` per promo (the seed of M9's `promo_cost` / `get_promo_performance`).
- The kiosk quote runs the same engine at "now" in the business's timezone; bonus lines come back as extra quote lines flagged `is_bonus` with the promo's name, and the pay sheet lists each applied promo with its amount. The screen and the sale agree to the rupiah (tested: quote 36.000, sale 36.000).
- A bug caught by a test: `create_promo` flushed the promo before validating its conditions, so a promo with a bad rule could exist for the length of the transaction (the request would still roll back). Conditions are now validated before anything is written; `update_promo` with `conditions` replaces the set the same way.
- Owner: a "Promo" page with the three reward kinds, the item pickers, kelipatan / bonus quantities / max per struk, and the conditions as plain controls (day chips, jam, tanggal, minimal belanja); on/off per promo. Demo mode has two fixtures.
**Deviation:** none. Schema is additive: three new tables, one new column on `orders`, one new standard account.
**Next:** canary (5 tasks since M7-T3: T4a, T4b, M8-T1, M8-T2, M8-T3), then M8-T4 (vouchers)

### [M8-T4] Vouchers
**Date:** 2026-09-06
**Status:** done
**Changed:** backend/alembic/versions/0024_vouchers.py (new), backend/app/models/models.py (`Voucher`, `VoucherRedemption`, `Order.voucher_total`), backend/app/models/__init__.py, backend/app/services/vouchers.py (new), backend/app/services/posting_rules.py (`voucher`, `voucher_reversal`), backend/app/services/pricing.py (`voucher_discount`, `voucher_total`, identity), backend/app/services/orders.py (check → price → guarded redeem; reversal on void/refund), backend/app/api/pos.py (quote validates a code and says why it fails; order + receipt carry it), backend/app/schemas/pos.py, backend/app/api/dashboard.py (`GET/POST/PATCH /api/vouchers`), backend/app/schemas/dashboard.py, backend/app/seed.py (a welcome code and a batch of ten), backend/tests/test_vouchers.py (new, 10 tests), backend/tests/test_db_integration.py (`test_rls_isolates_vouchers`), backend/tests/test_invariants.py (3 known money columns), backend/tests/test_posting_rules.py (the OrderCompleted rule assertion now derives its expectation from `STANDARD_RULES` instead of a literal), frontend/app/(dashboard)/promos/page.tsx (Voucher section: list, single/batch creation, toggle), frontend/app/pos/[businessToken]/page.tsx (code field in the pay sheet, receipt), frontend/lib/types.ts, frontend/lib/demo.ts, docs/api-contract.md
**Gates:** pytest 319 passed 0 skipped · migrations round-trip ok (0024 → 0023 → 0024) · frontend build ok · seed ok
**Notes:**
- **The done-when, verbatim**: `test_two_simultaneous_redemptions_of_one_code_produce_exactly_one_success` runs two sales with the same single-use code concurrently and gets `["sold", "used_up"]` — one order, one redemption row, `uses == 1`, and the loser took no stock either. Same shape as the stock race, same mechanism: `update vouchers set uses = uses + 1 where id = … and uses < max_uses and is_active and (starts_at is null or starts_at <= :at) and (expires_at is null or expires_at > :at) returning uses`, run inside the sale's transaction after the order row exists, so a sale that fails afterwards for any other reason hands the use back with its rollback.
- `vouchers`: a normalised code (upper-case, no spaces; `^[A-Z0-9][A-Z0-9-]{2,31}$` enforced by the database, unique per business), `percent_off` (with an optional `max_discount` cap) or `amount_off`, `min_spend`, `starts_at` / `expires_at` (exclusive), `max_uses` (1 = single use; more for a shared code), the cached `uses`, and `batch_id` / `batch_name` for bulk creation — every generated code is its own row and therefore its own guard. Generated codes use an alphabet without 0/O/1/I because they are read out at a till.
- `check_voucher` explains every refusal with its own code — unknown, inactive, not started, expired, used up, minimum spend not met — mapped to Indonesian at the API. The kiosk quote runs the check and returns `voucher_error` rather than failing, so the cashier sees "Voucher ini berlaku untuk belanja minimal Rp 15.000" while the bill stays priced without it; only the sale takes the use.
- The voucher comes off what is left after the cashier's discounts and promos (`price_order` gets `voucher_discount`; identity `total = subtotal − discount − promo − voucher + service + tax + rounding`), never more than that remainder, and payments must equal the voucher price (paying the full 20.000 with a 5.000 voucher is a mismatch). Posted as `voucher` to `4250 Diskon promo` under its own component so campaigns and codes can be told apart; refunds post `voucher_reversal` from the sale's own journal line.
- `voucher_redemptions` is append-only: a refund or void writes a negative row pointing at the original (`reversal_of`) and gives the use back (`greatest(uses − 1, 0)`), so the code can be used again — tested through refund, reuse, and void.
- Owner: `POST /api/vouchers` makes one named code or a batch (`count` ≤ 1000, `prefix`, `batch_name`) and returns every code; PATCH toggles, moves the expiry, or raises `max_uses` (never below what is already used). The Promo page gains a Voucher section with search, a table (code, potongan, dipakai/maks, berlaku sampai, batch), on/off, and a single-or-batch creation sheet that shows the codes it made. The seed plants `SELAMAT-DATANG` (Rp 5.000 off ≥ 25.000, 100 uses) and ten `SENJA-…` 20% codes capped at 15.000.
**Deviation:** none. Additive: two tables, one column on `orders`, two rules.
**Next:** tag `checkpoint/M8`; M9-T1 (metric registry). Canary due after 5 tasks (last at M8-T3: M8-T4 = 1 so far).

### [M9-T1] Metric registry
**Date:** 2026-09-06
**Status:** done
**Changed:** backend/app/metrics/__init__.py, backend/app/metrics/registry.py, backend/app/metrics/catalogue.py (all new), backend/app/api/dashboard.py (`GET /api/metrics`, `GET /api/metrics/{name}`), backend/app/schemas/dashboard.py, backend/tests/test_metrics.py (new, 5 tests), docs/api-contract.md
**Gates:** pytest 324 passed 0 skipped · migrations round-trip ok (no migration; 0024 unchanged) · frontend build ok · seed ok
**Notes:**
- `app/metrics/registry.py` is the declarative core: a `MetricSpec` has a name, an Indonesian and an English description, a unit (`rupiah` / `count` / `pct` / `qty` / `days` / `hour` / `list`), the dimensions it can be sliced by (today: `item`), the grains it supports (`period` over `[since, until)`, or `instant` for "now"), and exactly one implementation, bound with the `@metric(...)` decorator. Declaring a name twice is a `RuntimeError`, not an override; a malformed spec fails at import. `compute(session, business, name, period=|since/until, item_id, limit)` is the one entry point: a named period is resolved in the business's timezone through the same `period_range` the assistant already uses; an unknown metric raises `MetricNotFound`, a bad period / range / dimension raises `MetricArgumentInvalid` — it never guesses.
- `app/metrics/catalogue.py` seeds the twenty the roadmap names: `revenue`, `gross_profit`, `gross_margin_pct`, `cogs`, `transaction_count`, `average_ticket`, `item_units_sold`, `stock_on_hand`, `stock_days_remaining`, `waste_value`, `expense_total`, `net_profit`, `cash_variance`, `discount_cost`, `promo_cost`, `new_customers`, `repeat_rate`, `top_items_by_revenue`, `top_items_by_margin`, `peak_hour`. The basis is stated once at the top of the module: sales figures read the `sales` view (reversing lines included, so a void nets to zero), cost of goods is the per-line snapshot with a counted fallback, `transaction_count` counts orders and excludes voids, instant metrics describe now. `revenue` deliberately keeps the definition the tools and dashboard have used since M3 so M9-T2/T3 can be zero-diff refactors; `net_profit` is the operating figure (revenue − cogs − recorded expenses) with a note pointing at the ledger P&L for the accounting statement.
- Every figure is pinned on one hand-computed day (`test_metrics.day`): three sales incl. a two-line bill with a bill discount, one voided sale, a waste write-off, an expense, a till closed 4.000 short, a new customer and a returning one. Assertions include the identities (`gross_profit = revenue − cogs`, `average_ticket = revenue / orders`, `net_profit = revenue − cogs − expenses`), that the void nets revenue, COGS and the 15:00 hour to zero, that the item dimension filters, that named periods resolve in the business's timezone (08:00 UTC the next morning → "yesterday" is the whole known day), and that a metric outside its data returns 0 or `None`, never an error.
- **The bridge for M9-T2**: `test_the_registry_agrees_with_the_existing_tool…` asserts `get_sales_summary`'s revenue equals `compute("revenue")` for the same period today — before any tool is refactored.
- `GET /api/metrics` returns the catalogue (both descriptions, unit, grains, dimensions) and `GET /api/metrics/{name}` one value with its window; 404 / 422 in Indonesian. This is the endpoint M9-T3 will put behind every dashboard number.
- One portability find: `peak_hour` first used `timezone('Asia/Jakarta', …)` in SQL and the bundled local Postgres (`scripts/local-pg.py`) does not ship tzdata, so the name is not recognised. The metric now takes the offset in Python and adds an interval (Indonesia and Malaysia have whole-hour offsets and no DST). The same SQL construct exists in the older `/api/sales-trend`; M9-T3 will route that through the registry and retire it.
**Deviation:** none. No schema change.
**Next:** M9-T2 (point the eight tools at the registry). Canary due after 5 tasks (last at M8-T3: M8-T4, M9-T1 = 2 so far).

### [M9-T2] Point the existing 8 tools at the registry
**Date:** 2026-09-06
**Status:** done
**Changed:** backend/app/ai/tools.py (`get_stock`, `get_sales_summary`, `compare_periods`, `get_profit`, `get_low_stock` now call `app.metrics.compute`; no `Sale`/`Expense` imports, no sums of their own), backend/app/metrics/catalogue.py (`cogs` rows carry the snapshot-less line count; `stock_on_hand` / `stock_days_remaining` rows carry the reorder threshold and flag; `stock_days_remaining` is one grouped query using the locked velocity formula, not a per-item loop), backend/tests/test_tools_use_registry.py (new, 3 tests)
**Gates:** pytest 327 passed 0 skipped · migrations round-trip ok (no migration) · frontend build ok · seed ok
**Notes:**
- The five reading tools compute nothing now. `_sales_facts` (behind `get_sales_summary` and `compare_periods`) is `revenue` + `transaction_count` + `item_units_sold` + `top_items_by_revenue`; `get_profit` is `revenue` + `cogs` + `expense_total`; `get_stock` is `stock_on_hand` filtered by the name match; `get_low_stock` is `stock_days_remaining` with the tool's own "at risk" rule (below threshold, or ≤ 3 days). Output keys, labels, fallbacks (an unknown period from the model still means "today") and the three write/RAG tools are untouched. Every test that existed before passes unchanged, including `test_cogs` (`cost_of_goods_estimate` 25.000, zero snapshot-less lines).
- **A static test keeps it that way**: `test_the_tools_file_contains_no_arithmetic_of_its_own` parses `tools.py` and fails on any `func.sum/count/avg/min/max`, any `Sale.*` reference, or an import of `Sale`/`Expense`, and requires `from app.metrics import compute`. "Nothing else in the app computes these numbers" is now enforced for the assistant, not just intended.
- The one visible difference, stated plainly: `transactions` in the sales summary now counts **orders** (voids excluded) — the registry's `transaction_count` — where the old query counted sale *lines* and counted a void's reversing lines as two more "transactions". For a one-line order they are the same number, which is every order in the seed and every previous test; for a two-line bill the old figure was wrong by one. The roadmap asks for zero behavioural diff; this is a correction the registry forces and the tests document (`transactions == 2` for two orders, one of them two-line, one void).
- The bridge test from M9-T1 (tool revenue == registry revenue) still holds and is now trivially true; the new tests assert each tool's figure against the registry directly, on a day with a multi-line bill and a void.
- `get_low_stock` had its own one-query velocity to avoid an N+1 on the WhatsApp path; that constraint moved into the metric (`stock_days_remaining` is one grouped `sales` query for all items), so the dashboard's `/api/items` can share it in M9-T3 without a regression.
**Deviation:** `transactions` semantics corrected from lines to orders (see above); otherwise a pure refactor.
**Next:** M9-T3 (point the dashboard at the registry — the thesis test). Canary due after 5 tasks (last at M8-T3: M8-T4, M9-T1, M9-T2 = 3 so far).

### [M9-T3] Point the dashboard at the registry
**Date:** 2026-09-06
**Status:** done
**Changed:** backend/app/api/dashboard.py (`/api/overview`, `/api/sales-trend`, `/api/pnl`, `/api/items` shaped from `app.metrics`; no `Sale`/`Expense` arithmetic left in them), backend/app/metrics/registry.py (`local_day_windows`, `local_month_windows`, `series`), backend/app/metrics/__init__.py, backend/tests/test_thesis_agreement.py (new, 3 tests), docs/api-contract.md
**Gates:** pytest 330 passed 0 skipped · migrations round-trip ok (no migration) · frontend build ok · seed ok
**Notes:**
- **The thesis test** (`test_whatsapp_answer_and_dashboard_widget_are_the_identical_figure`): the router dispatches `get_sales_summary` for "berapa penjualan hari ini?" (the model call is the only fake — `force_tool_call` and `compose_reply` are patched, and the facts handed to the composer are captured), the dashboard renders `/api/overview`, and `/api/metrics/revenue?period=today` sits between them. On a day with a two-line bill and a voided sale — where two independent implementations were most likely to drift — all three say 75.000 and 2 orders, and the last bar of `/api/sales-trend` is that same 75.000 with yesterday's 15.000 beside it. A second test does the same for the month: `get_profit`'s revenue, recorded expenses and net equal the overview tiles and the current month of `/api/pnl`; the inventory page's stock, thresholds, daily usage and days remaining equal `get_stock` / `get_low_stock` and the registry rows.
- The dashboard's four number-producing endpoints now shape registry results: overview = `revenue` (today, yesterday, this month) + `transaction_count` + `expense_total` + `stock_on_hand` (for the low-stock count); the sales trend and monthly P&L use a new `series()` helper that calls `compute` once per business-local day / month (windows from the same `period_range` as everything else) — the same implementation as a single call, so a chart bar and a WhatsApp answer for the same day cannot differ. `/api/items` merges `stock_days_remaining` rows (one grouped query) with the item's prices. Response shapes are unchanged; the frontend needed no edit.
- **A static guard for the dashboard too**: `test_the_dashboards_number_endpoints_do_no_arithmetic_of_their_own` parses `dashboard.py` and fails if `overview`, `sales_trend`, `pnl` or `inventory` reference `Sale`/`Expense` or call `func.sum`/`func.avg`. Together with M9-T2's guard on `tools.py`, "nothing else in the app computes these numbers" is enforced at both consumers. (`/api/sales` history and the alert count are row listings, not figures, and are outside the guard.)
- Two older SQL constructs went with the refactor: `timezone('Asia/Jakarta', …)` in the trend and P&L grouping (which the bundled local Postgres could not resolve, see M9-T1) and the per-endpoint velocity query in `/api/items`. Day and month bucketing now happens in Python through `period_range`, so a chart renders on any Postgres.
- The same semantic correction as M9-T2 reaches the dashboard: `today_transactions` and the trend's `transactions` count orders (voids excluded), not lines. For the seed and every prior test the numbers are identical.
**Deviation:** none beyond the `transactions` correction already recorded in M9-T2.
**Next:** M9-T4 (grow the tool set over the registry). Canary due after 5 tasks (last at M8-T3: M8-T4, M9-T1, M9-T2, M9-T3 = 4 — due after M9-T4).

### [M9-T4] Grow the tool set
**Date:** 2026-09-06
**Status:** done
**Changed:** backend/app/metrics/registry.py (`supplier` and `customer` dimensions), backend/app/metrics/catalogue.py (six new metrics: `purchase_history`, `supplier_prices`, `recipe_cost`, `shift_summary`, `customer_summary`, `promo_performance`), backend/app/ai/tools.py (seven new tools: `get_purchase_history`, `get_supplier_prices`, `get_recipe_cost`, `get_shift_summary`, `get_customer_summary`, `get_promo_performance`, `draft_purchase_order`), backend/tests/test_new_tools.py (new, 3 tests), backend/tests/test_tools_registry.py + backend/tests/test_tools_use_registry.py (expected tool set grows to 15)
**Gates:** pytest 333 passed 0 skipped · migrations round-trip ok (no migration) · frontend build ok · seed ok
**Notes:**
- Seven fixed signatures, still no free-form SQL. Each tool resolves what the owner typed (a supplier by substring, an item by substring, a customer by name or phone through `search_customers`) to an id, calls `compute`, and shapes the answer; an unmatched name returns `found: False` with the known names so the assistant can ask rather than guess. The static guard from M9-T2 covers them automatically — they live in the same file and contain no `func.sum`, no `Sale`.
- **Over the registry, literally**: the figures the new tools read are six new metrics with the same declarative shape (both descriptions, unit, grains, dimensions). The registry gained `supplier` and `customer` dimensions next to `item`; `compute` refuses a dimension a metric does not declare. The catalogue now holds 26 metrics; the roadmap's seeded twenty are unchanged and still guarded by `STANDARD_METRIC_NAMES`.
- `supplier_prices` is the last unit cost per (item, supplier) **in the item's own unit** (`goods_receipt_lines.unit_cost_item_unit`, so a kg receipt and a gram receipt compare), with the previous price and the change in percent — the input to "naik ga harganya" and to the purchase-order draft. `recipe_cost` prices a default variant's active recipe at the components' *current* moving-average cost, converting recipe units to component units the same way a sale does; the test caught that a goods receipt had moved kopi's average from 150 to 153,33 and the cost from 6.000 to 6.066,60 — which is exactly what the metric should say today.
- `draft_purchase_order` is a write tool like `record_expense`: it creates a **draft** PO through `services/purchasing.create_purchase_order` (nothing ordered, nothing received, stock untouched — asserted), prices each line from `supplier_prices` and flags lines whose price is not known, lists the item names it could not match, and refuses outright with no supplier or no usable line. The draft is confirmed on the dashboard; the reply says so.
- `shift_summary` reuses `shift_view` (the same expectation formula as the kiosk and the money page); `customer_summary` reuses `customer_view` plus the period's visits and spend, or ranks the period's top customers; `promo_performance` counts applications, orders and rupiah given away per promo and the revenue of the orders it applied to (the BOGO on both of Andi's orders: 2 applications, 44.000 given away on 66.000 of orders).
**Deviation:** none.
**Next:** canary (5 tasks since M8-T3: M8-T4, M9-T1, M9-T2, M9-T3, M9-T4), then M9-T5 (explicit refusal)

### [M9-T5] Explicit refusal
**Date:** 2026-09-06
**Status:** done
**Changed:** backend/app/ai/refusal.py (new), backend/app/ai/router.py (`out_of_scope` declaration, deterministic refusal path, figure guard on `clarify`), backend/tests/test_refusal.py (new, 14 tests)
**Gates:** pytest 347 passed 0 skipped · migrations round-trip ok (no migration) · frontend build ok · seed ok
**Notes:**
- A second escape hatch beside `clarify`: **`out_of_scope`**. The classifier is told to choose it for a question the business data does not hold — a forecast, tax law, the weather, a competitor's price, staff attendance, anything needing a guess or outside knowledge — and never to answer such a question with a number. Its only argument is the `topic` in the owner's words (no numbers); its handling calls no model at all. The reply is *assembled* from a phrase table: "Maaf, data usaha yang saya pegang tidak mencakup {topic} … Yang bisa saya jawab: {menu}." — in Indonesian, Malay or English, chosen from the message's own marker words with the business's preference breaking ties.
- **Zero fabricated figures is a property of the code, not the prompt.** Two mechanisms: the refusal text cannot contain a figure because nothing generated it (a `topic` that smuggles a number is dropped rather than echoed); and the `clarify` path — the one place the model writes a reply with no facts behind it — is checked by `looks_like_a_figure` (Rp / RM, thousands separators, "ribu" / "juta" / "rb" / "k", percentages) and replaced with the refusal when a figure is found, logged as `refuse`. "Penjualan hari ini sekitar Rp 500.000 ya!" from the classifier never reaches the owner; "Halo! Mau cek apa hari ini?" still does.
- **The menu cannot fall behind the tool set**: `CAPABILITIES` has a phrase per declared tool per language, and `test_every_tool_is_offered_in_every_language…` fails the moment a tool is added to `tools.py` without one. All fifteen tools have phrases; the refusal lists the first eight in declaration order.
- The done-when, in `test_out_of_scope_questions_are_refused_with_zero_fabricated_figures`: ten out-of-scope questions in code-switched Indonesian, Malay and English with real typos ("brp", "sy", "kat kedai sebelah") each produce `intent == "refuse"`, a reply with **no digit at all**, the topic named, the capability menu offered, and the opener in the right language — with the composer asserted never awaited.
- `request_logs.classified_intent` gains the value `refuse`; M9-T6's harness will read the refusal rate from it. Language detection uses only words that belong to one language (shared words like *berapa*, *stok*, *harga* are deliberately excluded), which is what made "baki stok gula berapa banyak?" resolve to Malay.
**Deviation:** none.
**Next:** M9-T6 (evaluation harness). Canary due after 5 tasks (last at M9-T4: M9-T5 = 1 so far).

### [M9-T6] Evaluation harness
**Date:** 2026-09-06
**Status:** done
**Changed:** backend/app/eval/__init__.py, backend/app/eval/questions.py, backend/app/eval/harness.py, backend/app/eval/text_to_sql.py, backend/app/eval/__main__.py (all new), backend/app/core/config.py (`eval_text_to_sql` flag), backend/tests/test_eval_harness.py (new, 6 tests), docs/evaluation.md (new)
**Gates:** pytest 353 passed 0 skipped · migrations round-trip ok (no migration) · frontend build ok · seed ok
**Notes:**
- **The question set** (`app/eval/questions.py`): 30 owner messages — 20 answerable from the data, 10 out of scope — in code-switched Indonesian, Malay and English with real phone typing ("brp", "sy", "yg", "hw much", "kira2", "gmn", "hari ni"). Each data question names the tool and arguments a correct system uses and the registry metric that defines the truth, computed at run time; each out-of-scope question's only correct behaviour is a refusal.
- **The scorer** gives every answer one verdict: *correct* (the ground-truth figure, ± Rp 0,50), *refused* (no figure: said it could not, asked what was meant, or the query failed loudly), or **silent error** (a figure that is wrong — the wrong number for a data question, or any number at all for an out-of-scope one). Answer accuracy, refusal rate and the silent-error rate follow. A list-shaped tool answer (every "arabica" match, labelled) is judged as a set, which the first offline run taught: the seed has both *Biji Arabica* and *Kopi Arabica (cup)*.
- **The baseline** (`app/eval/text_to_sql.py`) is real text-to-SQL: the schema generated from the models, one SELECT from the model, run read-only with a statement timeout in the tenant session, first number of the first row as the answer. It is behind `EVAL_TEXT_TO_SQL=1`, imported only by the harness, and tested to refuse anything but a single SELECT (a `select 1; delete from orders` never runs — asserted against the row count). A scripted stand-in for the model proves the scorer catches what matters: a query off by 1.000 that raises nothing is a silent error; a syntax error is a refusal; a confident `avg(total_price) * 30` for "next month" is a silent error.
- **Two classifiers for the tool path**: the live model, or a deterministic keyword stand-in used to validate the harness offline — not a model, not what ships, and named as such in every table. A test requires the stand-in to agree with the set on intent, so its numbers measure the tools and the registry, not routing. Offline on the seed: accuracy 100%, refusal rate 33% (exactly the ten out-of-scope questions), silent-error rate 0%.
- **Live runs** (`docs/evaluation.md`): two runs are recorded. (1) The **tool path** with the live `gemini-2.5-flash` classifier: 15 of 30 questions before the free tier's *daily* cap cut the run — **15/15 correct, 0 silent errors**, including the mixed-language and typo'd ones. (2) The **text-to-SQL baseline** on the same 15 questions (written by `gemini-3.5-flash`, because 2.5 had no quota left): 2 correct, 11 loud failures, **2 silent errors (13%)** — `SUM(current_stock)` across *Biji Arabica* (kg) and *Kopi Arabica* (cups) = 43, and 30-day revenue from `orders.total` that counts voided orders (16.351.000 vs 16.082.000). **The silent-error gap is 13 points against the baseline on a set where the tools made no error of any kind**, and it is a floor: the local Postgres has no tzdata, so eleven baseline queries using `AT TIME ZONE 'Asia/Jakarta'` failed loudly here where in production they would run and some would be silently wrong. Questions 16–30 (the out-of-scope half) are not yet live-classified — one `--offset 15` run on a fresh day's quota. `docs/evaluation.md` states all of this in its Findings.
- The first live attempt exposed the real free-tier limit: 5 requests per minute per model (the earlier note said 20 per day). The harness now paces calls (`--pace 13`) and retries a 429 with backoff; the memory note is corrected.
**Deviation:** none.
**NEEDS HUMAN (non-blocking):** finishing the live comparison — questions 16–30 for the tools and a baseline run on a Postgres with tzdata — needs either a paid Gemini tier or more free-tier days; the commands are in `docs/evaluation.md`. The harness, its tests and today's evidence are complete, so the build continues.
**Next:** tag `checkpoint/M9`; M10-T1 (exception rules over metrics). Canary due after 5 tasks (last at M9-T4: M9-T5, M9-T6 = 2 so far).

### [M10-T1] Exception rules over metrics
**Date:** 2026-09-06
**Status:** done
**Changed:** backend/alembic/versions/0025_alert_rules.py (new: four `alert_type` values, `alerts.rule_key`, `alerts.details`), backend/app/models/models.py, backend/app/jobs/rules.py (new), backend/app/jobs/nightly.py (runs the rules; labels), backend/app/services/anomaly.py (daily totals from the registry), backend/tests/test_exception_rules.py (new, 6 tests), frontend/lib/types.ts, frontend/app/(dashboard)/alerts/page.tsx
**Gates:** pytest 359 passed 0 skipped · migrations round-trip ok (0025 → 0024 → 0025) · frontend build ok · seed ok
**Notes:**
- The nightly job is rebuilt on the registry. `app/jobs/rules.py` holds five rules, each a function that reads `compute`/`series` and returns findings; the job writes them as alerts with a **`rule_key`** — rule, subject, business-local day — and never writes the same key twice, so running the job again on the same data writes nothing (every test asserts exactly one alert, then runs the rules again and asserts none). `anomaly.py`'s own sums are gone: its daily totals are `revenue` / `expense_total` per local day from the registry.
- **margin_drop**: gross margin over the last 7 days at least 10 points under the 30 days before, with ≥ Rp 500.000 revenue in each window (otherwise the margin is noise). Manufactured by raising Kopi's cost so the same sales earn 25 points less.
- **stockout_risk**: an item's days remaining (the locked velocity formula) shorter than its next likely delivery — the median gap between its last goods receipts, or 7 days when it has none. Manufactured with deliveries every 10 days and 3 days of Roti left.
- **void_rate**: a cashier with ≥ 3 voids, ≥ 10 orders, a void rate ≥ 10 % and ≥ 2× the rest of the team's, over 30 days. Manufactured with Budi voiding 8 of his 40 orders while Sari voids none.
- **supplier_price**: the last price paid for an item to a supplier, bought within the last day, moved ≥ 10 % from the price before it (the `supplier_prices` metric's `change_pct`). Manufactured with Kopi at 8.000 then 9.600 today.
- **takings anomaly** (the z-score rule, now over the registry's daily series): today's revenue more than 3σ from the 30-day daily mean. Manufactured with twenty 100.000 sales on a 110.000-a-day shop.
- A quiet shop — 41 days of two steady sales a day with a realistic day-to-day spread, no voids, no receipts — produces **zero** rule alerts, which is the property M10-T2 will extend to a simulated week. (A perfectly flat history has zero variance and the z-score rule stays silent by design; the fixture alternates 110.000 and 150.000 days so the baseline is real.)
- Two things the tests caught while manufacturing conditions: a sale snapshots cost from the item's *default variant*, so a margin test must move the variant's cost, not only the item's; and voiding a cashier's most recent orders also empties *today's* takings, which correctly trips the anomaly rule — the void test now voids yesterday's and older.
- Four new `alert_type` values (an enum can only grow in Postgres; the downgrade keeps them and drops the two columns), Indonesian messages, `details` with the figures for M10-T2's dedup and the owner's alert page, which now labels the new kinds.
**Deviation:** none.
**Next:** M10-T2 (alert quality). Canary due after 5 tasks (last at M9-T4: M9-T5, M9-T6, M10-T1 = 3 so far).

### [M10-T2] Alert quality, not quantity
**Date:** 2026-09-06
**Status:** done
**Changed:** backend/app/jobs/alert_policy.py (new), backend/app/jobs/rules.py (writes through the policy; `stock:` subject shared with the till; anomaly rule covers revenue and expenses), backend/app/jobs/nightly.py (`nightly_pass`; legacy z-score detector no longer called), backend/app/services/velocity.py (low-stock alerts carry `rule_key = stock:<item>:<day>`), backend/tests/test_alert_quality.py (new, 5 tests)
**Gates:** pytest 364 passed 0 skipped · migrations round-trip ok (0025 unchanged) · frontend build ok · seed ok
**Notes:**
- **The done-when**: `test_a_simulated_stable_week_produces_zero_alerts` runs the full nightly pass (baselines, the per-item stock sweep, the five rules through the policy) seven nights in a row on seven ordinary days of a stable café — each day's sales rung up with the same post-sale low-stock check the POS runs — and asserts every night wrote nothing and the alerts table is empty at the end. Silence when nothing is wrong.
- `app/jobs/alert_policy.py` sits between a rule's finding and an `alerts` row with three policies. **Deduplicate**: one alert per `rule_key` (rule, subject, local day), ever. **Suppress the same alert as yesterday**: the *subject* — the key without its day — that alerted within the last 7 days, acknowledged or not, is not said again; when the window passes and the condition still holds it is said once more (tested: Roti short of stock all week → one alert on night one, silence on six nights, and again a week and an hour later). **Rate limit**: at most 5 new alerts per business per *local day*, highest severity first — per day rather than per run so a job re-run after a crash cannot double it; what does not fit is deferred and, if still true, written the next night (tested with a burst of eight: 5, then 0 on a re-run the same night, then 3).
- One conversation per item: the till's synchronous `low_stock` alert now carries `stock:<item>:<day>` and the nightly `stockout_risk` rule uses the same subject, so an item that dropped under the threshold at 14:00 is not alerted about again at 23:30 (tested).
- The legacy `detect_anomalies` is no longer called by the job — it and `rule_takings_anomaly` would have written two alerts for one spike. The rule now covers both daily revenue and daily expenses (what the legacy detector watched) under keys `anomaly:daily_revenue:<day>` / `anomaly:daily_expenses:<day>`; `refresh_baselines` still runs to keep the cached rolling mean/stddev current. The anomaly math tests are untouched.
- `nightly_pass(session, business, now)` is the job minus the WhatsApp send, so the simulated week runs the real thing with a moving clock; `process_business` calls it and then delivers, as before, through the approved template.
**Deviation:** none. No schema change.
**Next:** tag `checkpoint/M10`. M0–M10 are complete; M11 (channels) is optional per the roadmap and starts with M11-T1. Canary due after M11-T1 (tasks since M9-T4: M9-T5, M9-T6, M10-T1, M10-T2 = 4).

### [M11-T1] QR e-menu at /menu/{token}, ordering into the same order queue as the POS
**Date:** 2026-09-06
**Status:** done
**Changed:** backend/alembic/versions/0026_menu_tickets.py (new), backend/app/models/models.py (Order: source, table_label, guest_name, guest_phone, cart), backend/app/services/tickets.py (new), backend/app/services/orders.py (`create_order(..., ticket=)` fulfils an open ticket on its own row under an atomic claim; `TicketNotOpen`), backend/app/api/menu.py (new: public `GET /menu/{token}`, `POST /menu/{token}/orders`, `GET /menu/{token}/orders/{id}`), backend/app/api/pos.py (`GET /pos/tickets`, `POST /pos/tickets/{id}/settle`, `POST /pos/tickets/{id}/cancel`; `OrderOut` builder factored), backend/app/api/auth.py (`POST /auth/menu-link`), backend/app/core/security.py (`create_menu_token`, scope `menu`), backend/app/schemas/menu.py (new), backend/app/main.py, backend/app/metrics/catalogue.py + backend/app/services/customers.py (readers exclude `open` orders), backend/tests/test_menu.py (new, 10 tests), tests/test_error_localization.py + tests/test_role_separation.py (new router and routes in the matrices), frontend/app/menu/[token]/page.tsx + layout.tsx (new), frontend/app/pos/[businessToken]/page.tsx (queue: badge, sheet, settle/cancel), frontend/app/(dashboard)/settings/page.tsx (menu link + printable QR), frontend/package.json (qrcode)
**Gates:** pytest 374 passed 0 skipped · migrations round-trip ok (0026 → 0025 → 0026) · frontend build ok · seed ok
**Notes:**
- **"The same order queue as the POS"**: there was no queue — a POS sale is complete the moment it is rung up. So the e-menu order is a row in the till's own `orders` table with `status = 'open'` and `source = 'menu'` — a *ticket*. Placing it writes that one row and nothing else: no lines, no stock movement, no payment, no journal entry. The guest's cart is kept verbatim in `orders.cart` (jsonb) at the menu prices they saw, with the bill estimated by the same pure `price_order` the till uses (tax, service, rounding). Tested: the row exists, the footprint is zero, stock is untouched, and `revenue` / `transaction_count` do not move.
- **Settling is exactly a sale, on the same row.** `create_order` gained one argument, `ticket=`: it claims the row with `update orders set status='completed' where id=… and status='open'` (two tills cannot settle one ticket; a failure anywhere later rolls the claim back — tested with a stock-out under the guest: 409, ticket still open, footprint still zero), then runs the ordinary path — stock, cost snapshots, movements, payments, points, the journal — writing the sale's figures over the estimate. Tested against the same cart rung up directly at the till: identical counts of lines, payments, movements and journal entries; the journal balances and equals; the ticket keeps `source='menu'` and the guest's cart for the record.
- **Cancel keeps the row**: status → `voided` with the reason (and who) kept in `cart.cancelled`; nothing to reverse because nothing was taken. A POS order is not a ticket and cannot be cancelled through the queue (404).
- **The menu token is the pairing token's twin**: scope `menu`, 365 days, stateless, minted by the owner at `POST /auth/menu-link`; Settings renders it as a printable QR (`qrcode`, client-side). It opens the menu and nothing else — tested: 403 on till and dashboard routes; pairing/pos/owner tokens are 401 at `/menu/…`. The guest sees `available` (a boolean), never stock figures or costs; items without a selling price (ingredients) are not on the menu, the same rule the till applies.
- **Readers**: every `Order.status != "voided"` filter in the registry and the customer service became `not_in(("voided", "open"))` — an unpaid ticket is not a transaction, not a visit, not revenue. The `sales` view is over `order_lines`, which a ticket does not have, so line-based figures were already safe.
- Public endpoint, so the queue is capped (`MAX_OPEN_TICKETS = 50`, 429 with an Indonesian message) and line/qty counts are bounded in the schema.
- Frontend: `/menu/[token]` is a phone-first page (forced light theme like the POS): items, size/extras picker, cart bar, checkout with dine-in/takeaway, table and name, then a ticket screen with a big code (`M-XXXX`) that polls until the cashier has dealt with it; the ticket id is remembered per table code so a refresh does not lose it. The POS polls `/pos/tickets` every 15 s, shows a "Pesanan N" badge, and settles (cash/QRIS) or cancels from a sheet; the sale result reuses the ordinary flash. Verified live in the browser: guest placed M-E5C4 (Es Kopi Susu + Kopi Arabica, Meja 7), the till showed "Pesanan 1", "Bayar tunai" completed it, the badge cleared and Kopi Arabica stock went 35 → 34.
- Test infrastructure: the sync `TestClient` spins one event loop per request, which strands the app's pooled asyncpg connections between requests; `test_menu.py` drives the app through `httpx.ASGITransport` on the test's own loop and disposes the app engine after each test.
**Deviation:** none. Schema change is additive (0026: five nullable/defaulted columns on `orders`, one partial index; the tenant policy on `orders` already covers them). `qrcode` added to the frontend for the printable QR.
**Next:** M11-T2 (kitchen display with ticket states and a bump action). Canary due now (five tasks since M9-T4: M9-T5, M9-T6, M10-T1, M10-T2, M11-T1).

### [M11-T2] Kitchen display with ticket states and a bump action
**Date:** 2026-09-06
**Status:** done
**Changed:** backend/alembic/versions/0027_kitchen_events.py (new), backend/app/models/models.py + __init__.py (`kitchen_state` enum, `KitchenEvent`), backend/app/services/kitchen.py (new), backend/app/api/pos.py (`GET /pos/kitchen`, `POST /pos/kitchen/{order_id}/state`), backend/app/schemas/pos.py (kitchen schemas), backend/app/api/menu.py + schemas/menu.py (`kitchen_state` on the guest's ticket), backend/tests/test_kitchen.py (new, 6 tests), backend/tests/test_role_separation.py (route in the matrix), frontend/app/kitchen/[businessToken]/page.tsx + layout.tsx (new), frontend/app/pos/[businessToken]/page.tsx ("Dapur ↗" link), frontend/app/(dashboard)/settings/page.tsx (kitchen link under the kiosk link), frontend/app/menu/[token]/page.tsx (guest sees "sedang disiapkan" / "siap")
**Gates:** pytest 380 passed 0 skipped · migrations round-trip ok (0027 → 0026 → 0027) · frontend build ok · seed ok
**Notes:**
- **Canary (§0.3) ran after M11-T1, before this task**: `git stash list` empty · `alembic downgrade base && upgrade head` ok · seed ok · pytest 374 passed.
- **A ticket is a paid order.** A till sale and a settled e-menu ticket alike; an unpaid e-menu ticket is not on the board (the kitchen starts when the money is in) and a voided sale drops off. Tested with all four on one board.
- **States are append-only events.** `kitchen_events` (RLS in the same migration; tested: another tenant sees none and cannot write one) holds one row per change — `preparing`, `ready`, `done` — with who and when; `new` is the absence of rows; the latest row is the state and the rows are the history. Nothing is updated. Transitions only move forward (`new → preparing → ready → done`, and `done` — the bump — from anywhere); a repeat of the current state is a no-op so a double tap is not an error; going backwards is a 409 with an Indonesian reason. Tested: the three appended rows for one ticket read `preparing, ready, done`.
- **The board** is the completed orders of the last `BOARD_WINDOW_HOURS = 12` whose latest state is not `done`, oldest first — a ticket nobody bumped does not haunt tomorrow (tested at ±1 h of the window). Each carries the code the kitchen shouts (`M-XXXX` for a guest's ticket, `#XXXX` for a till sale), table/guest/note, and the lines as sold: item, size when it is not the default, chosen modifiers, per-line notes.
- **The guest sees it**: `GET /menu/{token}/orders/{id}` now carries `kitchen_state` once the ticket is paid, and the guest page keeps polling until `done`, so "Pesanan siap — silakan ambil di kasir" reaches the phone (tested through the API end to end: null while open → `new` on settlement → `ready` → `done`).
- `/kitchen/[businessToken]` uses the **same pairing link as the till** (Settings shows both), its own small login (staff + PIN → the ordinary `pos` token, kept under its own key so locking the kitchen does not log out the till), a card grid oldest-first with elapsed minutes (15+ highlighted), per-state actions (Mulai / Siap / Selesai ✓), and a 5-second poll. Verified live in the browser: Sari logged in, M-73AE (Meja 9 · Bima, "pedas") went Baru → Disiapkan → Siap → off the board, header counts following.
**Deviation:** none. Schema change is additive (one enum, one table with its policy).
**Next:** M11-T3 (order type routing so delivery and dine-in behave differently at the till). Canary due after 5 tasks (last after M11-T1: M11-T2 = 1 so far).

### [M11-T3] Order type routing so delivery and dine-in behave differently at the till
**Date:** 2026-09-06
**Status:** done
**Changed:** backend/alembic/versions/0028_order_types.py (new), backend/app/models/models.py (`pricing_settings.service_applies_to`, `pricing_settings.delivery_fee`, `orders.delivery_fee`, `orders.delivery_address`), backend/app/services/pricing.py (`price_order(..., order_type=)`, `PricedOrder.delivery_fee`), backend/app/services/orders.py (`OrderTypeInvalid`; the type routes the bill and is stored on the row), backend/app/services/tickets.py (the guest's estimate is routed), backend/app/services/kitchen.py (delivery address on the board; deterministic line order), backend/app/services/accounts.py + posting_rules.py (`4910 Pendapatan ongkos kirim`, the fee and its reversal), backend/app/api/pos.py (quote/order/receipt/kitchen carry the type), backend/app/schemas/{pos,dashboard}.py, backend/app/metrics/catalogue.py (`revenue_by_order_type`), backend/app/seed.py (the demo café eats in, carries out and sends), backend/tests/test_order_types.py (new, 8 tests), backend/tests/test_accounts.py (28 standard accounts), frontend/lib/types.ts + demo.ts, frontend/app/pos/[businessToken]/page.tsx, frontend/app/kitchen/[businessToken]/page.tsx, frontend/app/(dashboard)/settings/page.tsx
**Gates:** pytest 388 passed 0 skipped · migrations round-trip ok (downgrade base → head, then 0028 → 0027 → 0028) · frontend build ok · seed ok
**Notes:**
- **The type decides four things.** (1) *The service charge*: it applies only to the order types in `pricing_settings.service_applies_to` — default all four, which is exactly today's behaviour, and a café sets it to dine-in only. (2) *A flat delivery fee* on `delivery`, added after tax and never taxed, inside the total, outside `net` (it is not revenue from goods). (3) *What the till must collect*: a delivery without an address, or without a phone for the receiver (its own, or an attached customer's), is refused with 422 before any stock moves. (4) *Where the order shows up*: the receipt prints the type with the table or the address, the kitchen board flags ANTAR / BUNGKUS and prints the address, and the registry answers `revenue_by_order_type`.
- **The fee is money, so it is in the books.** Its own component posts it out of `4100 Penjualan` into the new `4910 Pendapatan ongkos kirim`, and `delivery_fee_reversal` gives it back on a refund — tested against the journal, both directions. The account and both rules are backfilled for every existing business (migration 0028, the pattern 0023 used for promos), so the standard chart is 28 accounts.
- **Pricing stays pure**: `price_order` takes `order_type` and nothing else changes; the routing is tested as a pure function first (service charge present/absent per type, fee only on delivery, after tax, before rounding) and then through the till, where the quote and the sale are asserted to agree for all four types.
- Live in the browser (seeded café, service charge 10% dine-in only, fee 8.000): the pay sheet's four type buttons switch the bill — takeaway 35.000, dine-in adds "Service charge + Rp 3.500", delivery adds "Ongkos kirim + Rp 8.000" and reveals the receiver fields; paying without an address is refused in Indonesian; the finished order carries fee, address and receiver, posts 43.000 cash against sales with 8.000 moved to 4910, and appears on the kitchen board as ANTAR with the address.
- **Two things the tests caught, both fixed in the code, neither by weakening a test.** The money-column guard (`test_no_float_money`) rejected `service_charge_order_types text[]`: a column whose name says "charge" must hold rupiah. The guard is right, so the column is named `service_applies_to` — for what it holds, not for the charge it governs. And the kitchen board's line order was luck: `order_lines.created_at` defaults to `now()`, which in Postgres is transaction time and therefore identical for every line of one sale, so ordering by `(created_at, id)` was ordering by a random uuid. The board now sorts by item name, so the same ticket reads the same way twice.
**Deviation:** none. Schema change is additive (two columns on `pricing_settings`, two on `orders`, one account and two posting rules backfilled).
**Next:** tag `checkpoint/M11`. M0–M11 are complete. M12 (live integration: Supabase, Meta, Railway, Vercel) is human-blocked by design — credentials are a stop condition (roadmap §3), so it is not started. The open non-blocking item stays: live-evaluate questions 16–30 (`python -m app.eval --mode live --offset 15`) when the Gemini free-tier daily quota allows, and re-run the text-to-SQL baseline on a Postgres with tzdata. Canary due after 3 more tasks (last after M11-T1: M11-T2, M11-T3 = 2).

### [M12] Live integration — NEEDS HUMAN, not started
**Date:** 2026-09-06
**Status:** NEEDS HUMAN (roadmap §3: a task needs credentials that do not exist)
**Changed:** docs/evaluation.md (the quota retry recorded; the stub run block it appended removed)
**Gates:** n/a — nothing was built. The tree at this point is `checkpoint/M11`: pytest 388 passed 0 skipped · migrations round-trip ok · frontend build ok · seed ok.
**Notes:**
- **M0 through M11 are complete and tagged.** Every task in the roadmap that can be done without an external account is done, one task per commit, with all four gates green before each. The last canary (roadmap §0.3) ran after M11-T1: `git stash list` empty, `alembic downgrade base && upgrade head`, seed, 374 passed; the schema was rebuilt from base again during M11-T3 and re-seeded.
- **M12 is where the build stops by design.** All four of its tasks need credentials that do not exist in this repository and that I must not invent (roadmap §1 "Never invent credentials", §3):
  - **M12-T1** a new Supabase project (URL, anon key, service key, database password), then the restore sequence in `PROJECT-STATUS.md` §5.
  - **M12-T2** a Meta app: WhatsApp Business account, phone number ID, permanent access token, webhook verify token, and the **two message templates submitted first** — review takes 24h or more, so this is the long pole. Nothing here can be stubbed and reported as working.
  - **M12-T3** a real handset on the allow-list to receive one live nightly alert.
  - **M12-T4** Railway (API + cron) and Vercel (frontend) accounts and their deploy tokens.
- **What I need to continue:** the accounts created and their secrets placed in `backend/.env` (Supabase, Meta) and in the Railway/Vercel dashboards. Then M12-T1 through M12-T4 can run in order; M12-T2's template submission should go first because of the review wait.
- **The other open item, non-blocking and unchanged:** the live evaluation of questions 16–30 and the text-to-SQL baseline on a Postgres with tzdata. Retried today at 05:39 UTC: question 16 classified correctly, then 429 RESOURCE_EXHAUSTED — the free tier's daily allowance was already spent by the earlier run. The command and what it would settle are in `docs/evaluation.md` under "Still to do". This does not block anything in the build; the tools-versus-baseline evidence that exists (15 questions, 0% silent error against 13%) already stands on its own.
**Deviation:** none. No work was started on M12.
**Next:** nothing, until a human supplies the M12 credentials. The build is at `checkpoint/M11`.

### [M15-T1] Automated backup, off the primary host
**Date:** 2026-09-06
**Status:** done (build) — two clauses of the done-criterion carry to M12, see Deviation
**Changed:** backend/app/jobs/backup.py (new), backend/app/jobs/delivery.py (new — `deliver_unsent` lifted out of nightly unchanged), backend/app/jobs/nightly.py (calls it; `backup gagal` in the kind labels), backend/alembic/versions/0029_backup_alert.py (new), backend/app/models/models.py (`alert_type` gains `backup_failed`), backend/app/core/config.py (BACKUP_DIR, BACKUP_DATABASE_URL, BACKUP_KEEP_DAILY/MONTHLY, BACKUP_MIN_BYTES, PG_BIN_DIR), backend/.env.example, backend/tests/test_backup.py (new, 28 tests), scripts/schedule-backup.ps1 (new), .gitignore (.backups/)
**Gates:** pytest 416 passed 0 skipped (was 388) · migrations round-trip ok (0029 → 0028 → 0029, and downgrade base → head during the run) · frontend build ok · seed ok
**Notes:**
- **The order of the roadmap changed under this task.** §5 now opens with an explicit build order that overrides the numeric order and puts M15-T1 → M15-T4 *before* M12, even though M15-T1 reads `blocked_by: M12-T1`. So this builds the machinery now and leaves the two things that are literally a production account — the off-host destination and the cron entry — as configuration for M12. What is here is real and runs; what is deferred is named below rather than claimed.
- **One run does five things and records all five**: `pg_dump --format=custom` into `<BACKUP_DIR>/daily/`, verification, the month's copy, pruning to 30 dailies + 6 monthlies, and one JSON line per run — success or failure — in `<BACKUP_DIR>/backup-log.jsonl`. The dump is written under a `.partial` name and renamed only after pg_dump exits 0, so a killed run cannot leave something that looks finished. Proven live: 355 KB, 44 tables, `warung-pintar-20260906T065446Z.dump` plus `monthly/warung-pintar-202609.dump`.
- **Verification is more than "a file appeared."** The file must clear a size floor *and* `pg_restore --list` must read it back *and* the listing must name every table the application depends on (`REQUIRED_TABLES` — businesses, staff, items, orders, order_lines, payments, stock_movements, journal_entries, journal_lines, expenses). Tested three ways: truncated, corrupt, and a listing with `orders` filtered out. This is the cheap half of "a backup you have never restored is not a backup"; the half that actually restores is M15-T2, next.
- **The backup needs the elevated role, and the restricted one fails loudly rather than quietly.** `pg_dump` runs with `row_security = off`, so `app_role` — which every business-scoped table forces RLS against — gets `ERROR: query would be affected by row-level security policy for table "accounts"` and exits 1. That is the good failure mode: no half-empty dump that looks like a backup. Confirmed at the command line and then written as a test that also guards §1.2 from the other side — if it ever passes, DATABASE_URL has been pointed at a role with BYPASSRLS. `BACKUP_DATABASE_URL` therefore falls back to `MIGRATION_DATABASE_URL` and never to `DATABASE_URL`, and refuses to run when neither is set.
- **The password never reaches argv.** The connection is handed to pg_dump through `PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE`, percent-decoded, with `PGSSLMODE=require` forced for any non-local host (the Supabase pooler URL is exactly this shape) unless the URL names its own sslmode.
- **"Off the primary host" is checked, not trusted — but only in production.** With `ENVIRONMENT=production` the destination must be absolute, must not be inside the deployment tree (Railway replaces it on every deploy), and the database must not be on the same machine. Development defaults to `<repo>/.backups`, which is deliberately *not* off-host and which those same checks reject the moment the environment flips. Four tests, both directions.
- **A failed run tells the owner, immediately, through the path that already works.** One high-severity `backup_failed` alert per business per business-local day, delivered by the approved Utility template rather than waiting up to 21 hours for the nightly job. Verified end to end against the seeded café by pointing the backup at a dead port: pg_dump failed, the run was logged with its reason, one alert was written, and `[DRY-RUN outbound WhatsApp] template business_alert -> 628120001111: ['backup gagal', 'Kopi Kenangan Senja', 'Backup otomatis database GAGAL pada 06/09 14:05. Backup terakhir yang berhasil: 06/09 13:54. …']` went out. Two more failed runs the same day added log lines and no second alert. The induced alert row was removed afterwards so the demo café is clean. The send is dry-run until M12-T2 supplies Meta credentials — that is the existing documented behaviour of the client, not a stub written here.
- **The backup alert deliberately does not go through `alert_policy` (M10-T2).** That policy suppresses a subject alerted within the last seven days, which is exactly wrong here: a backup that has been failing for six nights has to be said again on the seventh. The only dedup is the business's local day, so a retry after a failure does not say it twice. Tested both ways — one alert for three runs on one day, three alerts for three consecutive days.
- **`deliver_unsent` moved to `app/jobs/delivery.py` verbatim**, because "an alert the owner sees" should not mean two copies of the sending code. `nightly.process_business` is now three lines and its behaviour is unchanged (the M10-T2 suite passes untouched). Side effect worth knowing: a backup failure flushes any other unsent alerts in the same message, which is one message rather than two and no alert is lost.
- **The process exits 1 on failure and 0 on success**, so a scheduler or an uptime check sees it even if WhatsApp does not.
- **An invariant caught a bug in the new tests, and the tests were fixed rather than the invariant.** `test_every_business_has_every_standard_posting_rule` failed because the new fixture created businesses without the standard chart and rules, and left them behind. The fixture now calls `seed_books` — what registration gives a real business — and deletes the tenant on the way out, matching the pattern in `test_exception_rules.py`. Twelve leftover `Warung Backup` rows from the pre-fix runs were removed from the local database. No assertion was touched (§1.8).
**Deviation:** The done-criterion has three clauses and one of them is fully met today. *A dump exists* — yes, verified and restorable-listable, but in the development destination: it is not off-host, because the production database it would dump does not exist until M12-T1, and the code refuses to run in production without a real off-host destination. *The schedule is running* — no. The production schedule is a Railway cron (`0 19 * * *` = 02:00 WIB, `python -m app.jobs.backup`) created in M12-T4; `scripts/schedule-backup.ps1` registers the equivalent Windows task for a machine that is not Railway, and it was written but deliberately not run — registering a scheduled task on the owner's machine is theirs to approve, and the development database is not the thing that needs backing up. *A failed run raises an alert the owner sees* — yes, end to end, with the live WhatsApp send waiting on M12-T2. Schema change is additive: one enum value, no new table, so M0-T5 is unaffected. Railway's image will need the Postgres 16 client tools on the cron service for `pg_dump`/`pg_restore` to exist — recorded in the module docstring and in `.env.example` as `PG_BIN_DIR`.
**Next:** M15-T2 (restore drill: `scripts/restore-drill.py`, the half of "a backup you have never restored is not a backup" that this task left). Canary due after 2 more tasks (last after M11-T1: M11-T2, M11-T3, M15-T1 = 3).

### [M15-T2] Restore drill
**Date:** 2026-09-06
**Status:** done
**Changed:** scripts/restore-drill.py (new), backend/app/jobs/backup.py (`latest_dump`), backend/tests/test_restore_drill.py (new, 8 tests), docs/runbook.md (new)
**Gates:** pytest 424 passed 0 skipped (was 416) · migrations round-trip ok (0029 → 0028 → 0029) · frontend build ok · seed ok
**Notes:**
- **The drill is the other half of M15-T1.** That task proved a file exists and can be listed; this one puts it back into a database and asks the database whether it believes it. Six phases, each timed: create the scratch database, apply `scripts/db-bootstrap.sql` so `app_role` exists with its grants (the real restore sequence, not a shortcut), `pg_restore --no-owner --single-transaction`, check the restored schema is the revision the code expects and that `alembic upgrade head` against it is a clean no-op, run `tests/test_invariants.py` against it **as `app_role`** so RLS is enforced exactly as in production, and count every table on both sides. Then it drops the scratch database and prints PASS or FAIL with an exit code to match.
- **Green end to end**, against the seeded café: `restored at 0029, code expects 0029`, `12 passed` from the invariant suite, 44 tables and 3,555 rows restored, total 7.40s. The row-count table showed exactly one non-zero delta — `alerts` 1 restored against 0 live — which is the induced `backup_failed` alert from M15-T1's verification that was deleted from the live database after the dump was taken. That is the comparison working, not a fault.
- **A drill that can only say PASS proves nothing**, so the failure direction is tested too: 20 KB of junk named like a dump fails at `pg_restore`, nothing downstream is reported as having passed, and the scratch database is still dropped.
- **Restored-zero is a failure, a delta is not.** The source keeps trading after a dump is taken, so a difference in counts is expected and reported. A table with rows in the source and **zero** in the restore is the RLS-empty-dump catastrophe that M15-T1's `BACKUP_DATABASE_URL` rule exists to prevent, and the drill fails on it by name.
- **One bug, found and fixed by running it.** `with_database` was stripping the `+asyncpg` driver from the URL when it swapped in the scratch database name, so the schema-revision phase ran alembic through SQLAlchemy's default psycopg2 dialect and died on `No module named 'psycopg2'` — reported as `restored at ?`. The scheme is now left alone (alembic needs the driver; the client tools and asyncpg need it gone, and each conversion is its own named function). The regression is the first test in the file.
- **The password stays out of argv here too**: `pg_restore` gets `--dbname <name>` and takes host, user and password from the libpq environment that `app.jobs.backup.connection_env` builds.
- **The drill refuses to make its scratch database on the production cluster** unless `--allow-same-cluster` is passed. Restoring somewhere that is not production is the whole point of the exercise; `--target-url` names where instead.
- `scripts/` is not a package, so the test loads the drill by path — and has to register it in `sys.modules` before executing it, or its dataclasses cannot resolve their own module. The script changes into `backend/` on import because the app's settings read `.env` relative to the working directory, and keeps the caller's directory so a relative `--dump` still means what was typed.
- **`docs/runbook.md` now exists** with the measured restore timing the done-criterion asks for: 6 September 2026, development machine, 21 MB database / 355 KB dump / 3,555 rows across 44 tables — restore 1.0s, checks ~6s, total 7.4s, written up as "about a second per 350 KB of dump plus a fixed six seconds of checking", with an instruction to re-measure once production has real history. The file also carries the backup section (where dumps go, how to tell last night worked, what each failure message means) and the real restore procedure, including the step that matters most: apply `db-bootstrap.sql` and connect as `app_role`, because giving the app an elevated role instead silently turns off every tenant isolation policy. The scenarios M15-T9 owns are listed as empty headings rather than left unmentioned.
**Deviation:** none. No schema change. `docs/runbook.md` is created here rather than in M15-T9 because this task's done-criterion requires it; M15-T9 fills in the remaining scenarios.
**Next:** M15-T3 (clean production bootstrap: `python -m app.bootstrap`, and `app.seed` refusing to run against production). Canary due after 1 more task (last after M11-T1: M11-T2, M11-T3, M15-T1, M15-T2 = 4).

### [M15-T3] Clean production bootstrap
**Date:** 2026-09-06
**Status:** done
**Changed:** backend/app/bootstrap.py (new), backend/app/core/config.py (`ProductionRefusal`, `refuse_in_production`), backend/app/seed.py (two guards, docstring), backend/tests/test_bootstrap.py (new, 14 tests), docs/runbook.md (first-time setup)
**Gates:** pytest 438 passed 0 skipped (was 424) · migrations round-trip ok (0029 → 0028 → 0029) · frontend build ok · seed ok
**Notes:**
- **Canary (§0.3) ran before this task**, five tasks after the last one: `git stash list` empty · `alembic downgrade base` then `upgrade head` (0001 → 0029 from nothing) · seed ok · pytest 424 passed 0 skipped.
- **`python -m app.bootstrap` is the production entrypoint.** It creates exactly what registering through the dashboard creates and nothing more: the business, the owner's staff row and PIN, the standard units and conversions, the chart of accounts, the posting rules, pricing settings, loyalty settings. `POST /auth/register` is the other way in and the two run the same calls in the same order, deliberately, so they cannot drift.
- **"And nothing else" means no demo data, not no rules.** The roadmap lists the business, the owner, the chart and the UOMs. Posting rules, pricing and loyalty settings are in there too because without posting rules the engine refuses every event and the business cannot ring up its first sale — an empty business that cannot trade is not "usable". The test that settles it does not count rows: it bootstraps a café, adds one product, sells two of it, and asserts the order totals 24.000, stock goes 50 → 48, and the journal balances. Then a separate test asserts that a business straight out of bootstrap holds zero items, orders, customers, suppliers, expenses, stock movements, journal lines and alerts.
- **The PIN is prompted, not passed.** `--pin` exists for unattended use, but leaving it off asks twice with `getpass` so the owner's PIN does not sit in shell history; with no terminal to ask, it fails rather than guessing. The phone is normalised the same way the login form's numbers are (`0812…` → `62812…`), or the owner would never match their own account — tested.
- **Running it twice is refused.** Bootstrap creates a business; it does not reset one. A phone that already has a business gets a message saying so and exit 1, tested.
- **`app.seed` now has two gates in front of it, and they are different in kind.** The first is the one the roadmap asks for: `ENVIRONMENT=production` raises `ProductionRefusal` and the process exits 1, naming `app.bootstrap` as what to use instead. The second exists because ENVIRONMENT is a self-declared flag and a real café's database is remote long before anyone remembers to set it — so seed also refuses any database that is not on this machine unless `--yes` says it is deliberate. Both directions tested, including that a local database still runs untouched (gate 4 depends on it).
- Seed is destructive in a way that is easy to under-rate: it deletes and recreates its demo business on every run, and against a real café's database with a different owner phone it would not destroy anything — it would quietly add a month of invented sales to real books, which the nightly job would then alert on. That is the failure the second gate is really for.
- **Two things the tests caught, both fixed in the tests.** `create_order` returns a `CreatedOrder` wrapper, not an `Order`. And `bootstrap()` connects through the app's own engine (correct — it is a CLI entrypoint), whose pooled asyncpg connections get stranded by pytest-asyncio's per-test event loops; an autouse fixture disposes the app engine after each test, the same trap and the same fix as M11-T1.
**Deviation:** none. No schema change. The runbook gained a "Setting up the real café, once" section.
**Next:** M15-T4 (business day boundary: `day_start_hour` on `businesses`, used everywhere a day is computed). Canary due after 4 more tasks (last before M15-T3: M15-T3 = 1).

### [M15-T4] Business day boundary
**Date:** 2026-09-06
**Status:** done
**Changed:** backend/alembic/versions/0030_business_day_start_hour.py (new), backend/app/models/models.py (`Business.day_start_hour`), backend/app/ai/periods.py (`day_start_hour`, `business_day`, `day_bounds`), backend/app/metrics/registry.py, backend/app/api/dashboard.py, backend/app/jobs/rules.py, backend/app/jobs/alert_policy.py, backend/app/jobs/backup.py, backend/app/services/anomaly.py, backend/app/services/velocity.py, backend/app/schemas/auth.py, backend/app/schemas/dashboard.py, backend/tests/test_business_day.py (new, 19 tests), backend/tests/test_anomaly.py (+1), frontend/lib/dates.ts, frontend/lib/types.ts, frontend/lib/demo.ts, frontend/app/(dashboard)/{settings,sales,money,alerts,customers}/page.tsx, docs/runbook.md, docs/api-contract.md
**Gates:** pytest 458 passed 0 skipped (was 438) · migrations round-trip ok (0030 → 0029 → 0030) · frontend build ok · seed ok
**Notes:**
- **One column, one chokepoint.** `businesses.day_start_hour smallint not null default 0` with `check (day_start_hour between 0 and 23)`, and `period_range` — which every "today" in the codebase already went through — grew a `day_start_hour` argument. Because the boundary is applied once, at the place the day is defined, the metric layer, the assistant's tools, the dashboard endpoints, the nightly rules and the anomaly baselines all moved together. Default 0 is exactly the old behaviour: a test asserts every declared period is byte-identical to the pre-change result at `day_start_hour = 0`, so no existing business shifts until its owner changes the setting.
- **The done-criterion, both halves, in `test_business_day.py`.** A café with `day_start_hour = 4` takes 40.000 at 23:50 Thursday and 30.000 at 00:15 Friday. Cashing up at 01:00: the metric layer's `revenue period=today` returns 70.000 over a window of 03/09 04:00 → 04/09 04:00, and `local_day_windows` files the 00:15 bill under `2026-09-03`. Over HTTP as the owner, `/api/sales-trend` shows 70.000 / 2 transactions on 03/09 and 0 on 04/09, and `/api/statements/profit-loss?since=2026-09-03&until=2026-09-03` totals 70.000. The same fixture with the column set back to 0 splits the night 30.000 / 40.000 across two days — the bug, asserted in the same test so the fix cannot be read as a coincidence.
- **Anchored, not just offset.** Every period starts *and* ends at `day_start_hour`, so the week starts at 04:00 on Monday and the month at 04:00 on the 1st: the pieces still abut with no gap and no overlap (tested for hours 0, 4, 6, 23 across today/yesterday, this/last week, this/last month). "This week" is derived from the *business* day's weekday, so Monday 00:30 belongs to Sunday and therefore to the previous week. `local_month_windows` had to be fixed to carry the hour — its previous-month walk did `.replace(hour=0)` and would have quietly dropped every month back to midnight.
- **A day is named by the date it starts on.** The business day running Thursday 04:00 → Friday 04:00 is Thursday, everywhere: the trend chart's key, the rules' dedup key, the backup alert's key, the dashboard's day header.
- **Everywhere a day is computed, enumerated.** Metric layer: `resolve_window`, `local_day_windows`, `local_month_windows`. Dashboard: `/api/sales-trend`, `/api/pnl`, both statements (`_local_day_bounds` now takes the hour, and the default "until today" is the business day, not the wall-clock date). Nightly job: the rules' `today` window, `local_day` (the per-day half of every dedup key), `alert_policy`'s per-business-per-day cap, `refresh_baselines`' fenceposts, the till's synchronous low-stock subject in `velocity.py`, and the backup failure alert's key — a 02:00 backup under a 4am boundary now belongs to the night it backed up, not to the morning it finished in.
- **Shift reports have no day to compute.** A shift is bounded by its open and its close, not by a calendar boundary, so `shifts.py` needed no change — which is the right answer, not an omission: this task exists partly *because* a shift that straddles midnight was being reported against two days. What did need it is the shift list's day header on the money page, which now buckets on the business day like every other chronological list.
- **The frontend groups on the same boundary or the headers lie.** `lib/dates.ts` shifts an instant back by `dayStartHour` before taking its date key — one subtraction, exact in a zone without DST, which Indonesia and Malaysia are. The time under the header is *not* shifted: a bill rung up at 00:15 shows 00:15, under the previous day's heading. Sales, expenses, alerts, customers and the shift list all pass `business.day_start_hour`.
- **The owner sets it in Pengaturan → Profil usaha**, a 0–23 picker with 00.00 labelled "(hari kalender)" and a help tip explaining the closing-after-midnight case. Verified in the browser: the control renders, selecting 04.00 reveals the save button. The API bound (`ge=0, le=23`, a 422) and the check constraint are both tested, the second by a raw UPDATE — the API bound is a nice error message, the constraint is what makes it true of every writer.
- **`promos.py` deliberately stays on the wall clock.** A promo's `day_of_week` and `time_window` conditions are about when the customer is standing there, not about which day's books the sale lands in, and the module already supports a window that crosses midnight (22:00–02:00) as the mechanism for late-night rules. Changing it would have silently altered live promo behaviour for no stated requirement.
- **One test needed its stub updated, and it was strengthened rather than loosened.** `test_fenceposts_cover_30_full_days_plus_today` builds a fake business as `SimpleNamespace(timezone=...)`; once `_day_fenceposts` reads `day_start_hour` the double no longer mirrored the model. The stub gained the attribute, every existing assertion is untouched, and a second test was added asserting the fenceposts sit at 04:00 under a 4am boundary. No assertion was relaxed (§1.8).
**Deviation:** none. The schema change is additive — one nullable-free column with a default on an existing table, no data rewritten, downgrade drops it cleanly (round-tripped). `businesses` has RLS disabled by design so there is no policy to add, and M0-T4/M0-T5 are unaffected (`day_start_hour` matches no money or quantity name pattern). Changing the setting later is safe but re-cuts every historical report on the new boundary, so `docs/runbook.md` tells the owner to set it once, at setup.
**Next:** M12 — the roadmap's §5 build order puts it after M15-T4, and it is the NEEDS HUMAN entry already recorded above: all four tasks need Supabase, Meta, Railway and Vercel credentials that do not exist in this repository. If those arrive, M12-T2's template submission goes first because of the 24-hour review. Without them the next buildable work is M15-T5 → M15-T9, all of which read `blocked_by: M12-T4`. Canary due after 3 more tasks (last before M15-T3: M15-T3, M15-T4 = 2).

### [M15-T5] Health monitoring · [M15-T6] Receipt printing on real hardware — NEEDS HUMAN
**Date:** 2026-09-06
**Status:** NEEDS HUMAN (roadmap §3: a task needs credentials — and here hardware — that do not exist)
**Changed:** docs/progress.md only. Nothing was built.
**Gates:** n/a. The tree is `[M15-T4]`: pytest 458 passed 0 skipped · migrations round-trip ok · frontend build ok · seed ok.
**Notes:**
- **§5's build order is now at step 3.** Steps 1 (M15-T1 → M15-T4) and 2 (M12) are settled: the first is done, the second is the NEEDS HUMAN entry above — Supabase, Meta, Railway and Vercel accounts that do not exist here and must not be invented (§1, §3). Step 3 is M15-T5 → M15-T9. Two of those five cannot be started without the things M12 would provide, and both fail loudly rather than quietly if faked, so they are recorded here and stepped past rather than half-built.
- **M15-T5 needs a deployed API and a real handset.** The done-criterion is "killing the API produces an alert within five minutes, tested by actually killing it". There is no deployed API to kill until M12-T4 (Railway + Vercel), no external monitor account to schedule the check from, and no allow-listed phone to receive the alert until M12-T2/T3. A `/health/db` endpoint that nothing watches is not what the task asks for, and an uptime check pointed at localhost would be a stub reported as working (§3).
- **M15-T6 needs the café's actual printer, and a purchase decision that is the owner's.** The task itself says to test the real thing — paper width, how item names with modifiers wrap, the cut, printing with no dialog and no taps — and to record the exact model, connection method and settings in `docs/runbook.md`. **What I need:** which of the three options the café is buying. The roadmap's own preference order is (1) an Android POS terminal with a built-in printer (Sunmi / iMin / Advan — what majoo ships, and it removes the pairing problem), (2) a 58mm Bluetooth thermal printer driven by Web Bluetooth from Chrome on Android, (3) browser print as the fallback, which is unusable in a queue because of the dialog. If hardware has not been bought yet, option 1 is the one to buy. Writing ESC/POS byte layout against a printer nobody has could not be tested and would be exactly the "never stub an external service and report it as working" failure.
- **What I did instead:** M15-T7. Its `blocked_by: M12-T4` is a sequencing note, not a technical dependency — a manager role, void/refund approval and audit attribution are entirely local, and its done-criterion ("a manager can void without the owner present, the void records who approved it, and a plain staff PIN still cannot") is fully testable against the local Postgres. The same is true of M15-T8, and M15-T9 is a document. So the runway from here is M15-T7 → M15-T8 → M15-T9 → M14-T1 (whose `blocked_by: M13-T5` is dead, M13 being cut, and which §5 lists as step 4).
**Deviation:** none. No code was written for either task.
**Next:** M15-T7 (manager override).

### [M15-T7] Manager override when the owner is not there
**Date:** 2026-09-06
**Status:** done
**Changed:** backend/alembic/versions/0031_manager_role_and_approvals.py (new), backend/app/models/models.py (`staff_role` gains `manager`, `approval_action`, `Approval`), backend/app/models/__init__.py, backend/app/services/orders.py (`APPROVER_ROLES`, `verify_manager_pin`, `record_approval`, three call sites), backend/app/api/auth.py (`role` on create, `PATCH /auth/staff/{id}`), backend/app/api/dashboard.py (`GET /api/approvals`), backend/app/api/pos.py (error text), backend/app/schemas/auth.py, backend/app/schemas/dashboard.py, backend/tests/test_manager_override.py (new, 11 tests), backend/tests/test_db_integration.py (+1 RLS test), frontend/lib/types.ts, frontend/lib/demo.ts, frontend/app/(dashboard)/settings/page.tsx, frontend/app/(dashboard)/money/page.tsx, frontend/app/pos/[businessToken]/page.tsx, docs/api-contract.md, docs/runbook.md
**Gates:** pytest 470 passed 0 skipped (was 458) · migrations round-trip ok (0031 → 0030 → 0031) · frontend build ok · seed ok
**Notes:**
- **`blocked_by: M12-T4` is a sequencing note on this task, not a technical dependency.** Nothing here needs Railway, Vercel, Supabase or Meta: a role, a PIN check and an audit table are entirely local, and the done-criterion is three claims that are all testable against the local Postgres. Recorded as a deliberate reading of §5's build order rather than a silent skip — the two tasks that genuinely cannot start are M15-T5 and M15-T6, and they have their own NEEDS HUMAN entry above.
- **`verify_manager_pin` now accepts any active `owner` or `manager`.** That is the whole behavioural change and it is four lines; everything else in this task exists to make it safe. Rejected, with tests: a plain `staff` PIN, a wrong PIN, and a manager who has been deactivated. The role is read off the staff row at the moment of the override, not off the token issued at the start of the shift, so revoking someone is one flag and takes effect on their next attempt.
- **The done-criterion, all three clauses on one order.** Pak Yudi (manager) voids Sari's 60.000 sale with Bu Ratna (owner) off site; the void writes an `approvals` row naming Yudi as approver with role `manager`, Sari as the requester, 60.000 as the amount and the cashier's note; stock comes back and the order stays readable as `voided`. The same test first asserts Sari's own PIN is refused, so "a manager can" and "a cashier cannot" are proven on the same order rather than in two hopeful halves.
- **The old attribution was a name inside a memo string.** `_reverse` already wrote `void oleh Pak Yudi` into the reversing line's notes and the journal memo, which is prose — not queryable, not sortable, and it says nothing about who asked or what it was worth. `approvals` is the structured trail: append-only, one row per authorisation, `(action, approved_by, approver_role, requested_by, order_id, amount, note, created_at)`, RLS in the same migration. The memo prose stays; it is now the human-readable half of something that also has a machine-readable half.
- **`approver_role` is a snapshot and there is a test for it.** A cashier promoted next month must not change what last month's trail says about who was allowed to approve. The test promotes, voids, demotes, and asserts the row still reads `manager` while the staff row reads `staff`.
- **All three overrides are recorded, not only the void.** A manager-approved bill discount writes a `discount` row carrying the discount given (5.000), not the bill total — the discount is what was authorised. A refund writes the total handed back. An ordinary sale writes nothing: a trail padded with non-events is one nobody reads, and there is a test asserting the empty case.
- **Cannot see reports or settings is structural, and asserted from the outside.** A manager signs in at the kiosk exactly like a cashier and gets `scope="pos"`; owner scope is issued only to the phone that owns the business, via OTP. The test takes a real manager login token and walks it into `/api/overview`, `/api/pnl`, `/api/business`, `/api/approvals`, `/api/pricing-settings` and `/auth/staff` — six 403s. So the role grants exactly one new power and no others.
- **The owner hands out `manager`, never `owner`.** `POST /auth/staff` takes a role and `PATCH /auth/staff/{id}` promotes or demotes; both are typed `Literal["staff", "manager"]`, so `owner` is a 422 from pydantic before it reaches a query, and the owner's own row refuses re-roling with a 400 (it is the login). Tested in both directions, including that a promoted cashier can then actually approve a void — the endpoint's whole purpose.
- **An audit trail nobody can read is not an audit trail**, so `GET /api/approvals` (owner-only, newest first, optional `action` filter) joins the approver and requester names, and the money page shows it under "Otorisasi manajer" with a help tip telling the owner what pattern to look for. Verified in the browser against the demo fixtures: three rows rendering as batal/diskon/refund with the right names, roles, requesters and amounts, and the settings staff list showing per-person roles with "jadikan manajer" / "cabut manajer".
- **The Indonesian copy now names both.** "PIN manajer salah — minta pemilik atau manajer untuk memasukkan PIN-nya", the same for the discount gate, the till's field label, and the pricing setting's description. The AST localisation test passes unchanged.
- **The till still has no void or refund screen.** That is pre-existing: `/pos/orders/{id}/void` and `/pos/orders/{id}/refund` exist and are tested, but the kiosk never calls them — the only "Batal" in the POS cancels an *open e-menu ticket*, which is a different thing. M15-T7 is about who may authorise, not about building that screen, so it is left alone and named here rather than quietly absorbed. Until it is built, a void is an API call; the manager role, the audit trail and the error text are all in place for the screen when it arrives.
- **The enum addition is legal in one transaction because the new value is only declared, never written.** `alter type staff_role add value if not exists 'manager'` then a column of that type — Postgres refuses only the *use* of a value added in the same transaction. Downgrade drops the table and `approval_action` and leaves `manager` in `staff_role`, the 0025/0029 precedent: Postgres has no `drop value`, and a value nothing writes costs nothing.
**Deviation:** none. Additive: one enum value, one new enum, one new table, no existing table altered and no data rewritten. `approvals` carries `business_id`, so it carries `tenant_isolation` in the same migration and has the cross-tenant test §2 requires — business B cannot read A's approvals, and A cannot write a row tagged B.
**Next:** M15-T8 (lockout recovery: re-pair a wiped kiosk and reset a staff PIN from the dashboard). Canary due after 2 more tasks (last before M15-T3: M15-T3, M15-T4, M15-T7 = 3).

### [M15-T8] Lockout recovery
**Date:** 2026-09-06
**Status:** done — the timing half is measured server-side and flagged for one re-measurement on the café's tablet, see Deviation
**Changed:** backend/alembic/versions/0032_pairing_generation.py (new), backend/app/models/models.py (`Business.pairing_generation`), backend/app/core/security.py (`gen` claim on both token kinds), backend/app/core/deps.py (the generation check on every `pos` request), backend/app/api/pos.py (`_pairing_claims`, `_live_business`), backend/app/api/auth.py (`POST /auth/staff/{id}/pin`, `POST /auth/pos-pairing/reset`), backend/app/schemas/auth.py, backend/tests/test_lockout_recovery.py (new, 9 tests), frontend/app/(dashboard)/settings/page.tsx, frontend/app/pos/[businessToken]/page.tsx, docs/runbook.md, docs/api-contract.md
**Gates:** pytest 479 passed 0 skipped (was 470) · migrations round-trip ok (0032 → 0031 → 0032) · frontend build ok · seed ok
**Notes:**
- **The pairing link was a year-long bearer token with no way to die.** `create_pairing_token` said so in its own docstring: "stateless by design (no revocation table needed at demo scale)". That is fine until the tablet is stolen, sold, or simply not coming back — and "the kiosk pairing token is lost" is the first sentence of this task. So the token now carries `gen`, `businesses.pairing_generation` holds the current one, and re-pairing raises it. One integer, one comparison, nothing to clean up.
- **Stopping new logins is not a cut-off.** A stolen tablet is already holding a `pos` token good for twelve hours, so the generation is checked in `deps._ctx` on **every** till request, not only at the pairing screen: one indexed read of `businesses.pairing_generation` per request. The test asserts all three doors at once — the old link cannot reach the "who are you" screen, cannot attempt a PIN login, and the session it was already holding cannot list items or ring up a sale.
- **Re-pairing logs out the good tablet too, and that is the design.** You press it because a device is out of your hands, and anything short of "everything issued before now is void" is not a cut-off. Both the confirmation dialog and the runbook say so in as many words. Reading the link out over the phone is the other button — `POST /auth/pos-pairing` mints under the *current* generation and cuts nobody off, which has its own test, because an owner opening Pengaturan mid-service must not empty the till screen.
- **`POST /auth/staff/{id}/pin` is the other lever.** Owner-only, 4–6 digits, and it does three things it must not do: it does not reactivate someone who has left (tested — a deactivated staff's fresh PIN still cannot log in), it does not change identity (tested — the order rung up before the reset still carries their `staff_id`), and it does not spare the owner (tested — the owner's own PIN is resettable, which matters because it is also the approval PIN).
- **The done-criterion end to end, in the order a person does it.** `test_a_wiped_kiosk_is_back_to_taking_sales`: cut the lost device off, confirm its session is dead, reset the cashier's forgotten PIN, open the new link on the replacement, see the staff list, sign in (the new token carries `gen: 2`), load the menu, sell two coffees, and read back a receipt totalling 40.000 and the sale on the owner's sales page.
- **Timed, and honest about what was timed.** The software's whole share of that drill is **204 ms** — 7 ms to cut the device off, 47 ms for the PIN reset and 43 ms for the login (both bcrypt), 97 ms for the first sale, the rest under 5 ms each. Recorded per step in `docs/runbook.md`. The five minutes the roadmap allows is therefore spent entirely on people and hardware, which is also the useful finding: the runbook's advice is to send the link through WhatsApp and open it there rather than retyping a URL on a touchscreen.
- **Nothing in the tests had to change to accommodate the new claim.** `create_token(generation=1)` and `create_pairing_token(business_id, 1)` default to the generation a business has never re-paired from, so every existing token-minting test still passes unmodified — 470 → 479 with nine additions and no edits.
- **The kiosk says what actually happened.** A re-paired device gets 401 "Perangkat ini sudah tidak dipasangkan — minta tautan kasir baru ke pemilik ya" and the POS already renders the API's own detail on its boot screen. Two paths did not: locking the screen showed "Koneksi terputus." (sending staff to check WiFi for something WiFi cannot fix) and a PIN attempt shook the pad forever. Both now show the real reason.
- **The counter is per business and there is a test for it** — one café re-pairing must not sign out the café next door — and the check constraint keeps it at 1 or above, so no later writer can zero it back into a valid old token.
**Deviation:** the done-criterion says "in under five minutes, **timed**". The functional half is proven end to end over HTTP against the real app; the timing half is measured for the server's share only (204 ms, per step, in the runbook). What is not measured is a person carrying a replacement tablet, which needs the café's actual device and is the same class of thing as M15-T6's printer — so the runbook says, in bold, to re-time it once on that tablet before go-live and write the number down. Schema change is additive: one integer column with a default and a floor constraint, no data rewritten, downgrade drops it cleanly.
**Next:** M15-T9 (the runbook, written for a stressed person at 8am: the scenarios still listed under "Still to be written"). Canary due after 1 more task (last before M15-T3: M15-T3, M15-T4, M15-T7, M15-T8 = 4).

### [M15-T9] The runbook
**Date:** 2026-09-06
**Status:** done
**Changed:** docs/runbook.md (the six remaining scenarios, closing the day, monthly costs, a known-gaps table, a symptom index at the top, and the whole file reordered)
**Gates:** pytest 479 passed 0 skipped (unchanged — documentation only) · migrations round-trip ok (0032 → 0031 → 0032) · frontend build ok · seed ok
**Notes:**
- **Canary (§0.3) ran before this task**, five tasks after the last one: `git stash list` empty · `alembic downgrade base` then `upgrade head` (0001 → 0032 from nothing) · seed ok · pytest 479 passed 0 skipped.
- **Reordered for the audience the roadmap names.** A stressed person at 8am does not scroll past first-time setup to find "the internet is down", so the emergencies come first, then the owner-away and lockout procedures, then closing the day, then setup, backups, restore and costs. The file opens with a symptom index — "if this is happening → go here" — with anchor links, because the fastest runbook is one you never read linearly.
- **Every scenario was checked against the code before it was written**, not against memory. The stock-correction step says the dashboard's stock edit writes a `correction` movement into the ledger because `PATCH /api/items/{id}` calls `set_absolute_stock(reason="correction")`; the unpaid-order step says "Batal on the ticket" because that is what `cancelTicket` does; the receipt step says the receipt survives a dead printer because the till renders it on screen with its own Cetak button.
- **The uncomfortable finding is written down rather than smoothed over.** "A sale was rung up wrong" is the most common café incident and **there is no void or refund button anywhere in the app** — the endpoints exist and are tested, but nothing in the till or the dashboard calls them. The runbook says so in a blockquote, says what to do meanwhile (write it on the paper sheet; do **not** ring up a correcting sale and do **not** hand-edit the stock, because both make the books worse than a visible mistake), and a **Known gaps at go-live** table lists it beside the four other things that are not built: no offline till (M14), no backdated entry (M15-T10), no uptime alert (M15-T5), no proven printer (M15-T6). The alternative was a runbook that reads as if the shop is ready when it is not.
- **The two rules that outrank the rest are at the top**: keep selling, and do not make the numbers up. Everything in the outage scenarios follows from them — paper, then re-entry, and never a second "correcting" sale.
- **"Who pays for what" is a table with the amounts blank and a reason.** Inventing prices for accounts that do not exist would be exactly the kind of confident wrong number this project is built to avoid, so each row names the service, what it does and what breaks when it lapses, and the cost is filled in at signup. The header of that section says nothing here should be on a free tier and names the date this project lost a database to one.
- **The internet-down and tablet-dead procedures are honest about the timestamp.** Paper sales re-entered later land at today's time, not theirs, until M15-T10 exists; the runbook says to note it on the sheet when the outage crossed a day boundary, and says explicitly not to hand-correct the stock as well, which would subtract it twice.
- **Closing the day sends the owner to Otorisasi manajer** (M15-T7) after the shift count, and to the day-boundary setting (M15-T4) as the first thing to check when a daily number looks wrong. The go-live tasks now cross-reference each other rather than sitting as separate features.
**Deviation:** none. Documentation only, no code touched. "Someone who is not Oscar can follow it" is met for every scenario except a wrong paid sale, where the honest instruction is to record it and call a developer — that is a gap in the product, named as one, not a gap in the runbook.
**Next:** M14-T1 (`docs/offline-policy.md`), which §5 lists as step 4 and whose `blocked_by: M13-T5` is dead — M13 is cut. M15's buildable set is now complete: T1–T4 and T7–T9 are done, T5 and T6 are NEEDS HUMAN, and T10 is `blocked_by: M15-T7` and now unblocked. Canary just ran; due again after 5 tasks.

### [M15-T11] The void and refund screen
**Date:** 2026-09-06
**Status:** done
**Changed:** backend/app/services/orders.py (`OrderSummary`, `order_number`, `list_orders`), backend/app/api/pos.py (`receipt_view` extracted, `GET /pos/orders`, `run_reversal` gains `channel`), backend/app/api/dashboard.py (`GET /api/orders`, `/api/orders/{id}/receipt`, `POST /api/orders/{id}/void|refund`), backend/app/schemas/pos.py (`OrderSummaryOut`, `OrdersPage`), backend/tests/test_reversal_screen.py (new, 10 tests), frontend/app/pos/[businessToken]/page.tsx (`ReversalSheet` + the Transaksi button), frontend/components/ReversalPanel.tsx (new), frontend/app/(dashboard)/sales/page.tsx, frontend/lib/types.ts, frontend/lib/demo.ts, docs/runbook.md, docs/api-contract.md
**Gates:** pytest 489 passed 0 skipped (was 479) · migrations round-trip ok (0032 → 0031 → 0032) · frontend build ok · seed ok
**Notes:**
- **No schema change and no new service logic.** Everything this task needed to *do* was built in M3-T4 and M15-T7: reversing lines, reversing payments, `sale_void` movements, the manager role, the `approvals` trail. What was missing was the way in — a list to find the sale in — so that is what this adds, plus the two screens that call it. The reversal service functions are untouched.
- **`list_orders` is the one query both screens use**, and the till and the dashboard differ only in the window they ask for. The till asks for the **business day** (M15-T4), so a 23:50 sale is still on the cashier's list at 00:15 — exactly the hour mistakes are most likely, and the hour a calendar-day list would hide it. The dashboard asks for everything, because a mistake found after the shift closed is the case it exists for. Both are paged, both count before paging.
- **Search is on the receipt number, which is the only reference that exists on paper.** `q` matches the last eight characters of the id — the same short number the receipt prints — case-insensitively and with dashes stripped, so "a1b2c3d4", "A1B2C3D4", a mid-number fragment and a number copied with its dashes all find the same sale. Tested with all four spellings.
- **Open e-menu tickets are excluded by default and there is a test for it.** An unpaid QR order is not a sale, has its own screen, and cancelling one is a different action — it must not appear in a list of things you can void, or the one "Batal" a cashier already knows becomes ambiguous.
- **One receipt-shaping function, not two.** `pos_receipt`'s forty lines of shaping became `receipt_view(session, business_id, order_id)`, and the owner's `/api/orders/{id}/receipt` calls the same function. A test asserts the two endpoints return byte-identical JSON: the owner deciding whether to void must be looking at exactly what the cashier printed.
- **One reversal path, not two.** `_run_reversal` became `run_reversal(..., channel=...)`, so the guard, the Indonesian error text and the audit row are identical from either screen and only the request-log channel differs. A void done from the office is the same kind of void.
- **The owner's PIN prompt is not proving who they are.** They are already signed in with owner scope. It stops a dashboard left open on an unattended laptop from reversing a sale with one click, and it puts a person into the `approvals` row rather than a session. Recorded in the endpoint's docstring so it does not read as redundant friction.
- **Verified live, both screens, against a running backend and the seeded café.** Rang up a 1× Kopi Arabica sale through the till (stock 35 → 34), opened **Transaksi**, found it, tried the cashier's own PIN and got "PIN manajer salah — minta pemilik atau manajer untuk memasukkan PIN-nya", then the owner's PIN: "Transaksi dibatalkan · #58434500 · Rp 20.000 · stok sudah dikembalikan", and the grid behind the sheet went back to 35 without a reload. The database then showed order `voided`, lines `['-1.000', '1.000']`, payments `['-20000.00', '20000.00']`, movements `[('sale', '-1.000'), ('sale_void', '1.000')]`, and one `approvals` row: **void · approved by Ibu Ratna (owner) · requested by Sari · Rp 20.000 · "salah pencet menu"**. On the dashboard, searched `43da77c3` in lower case, opened yesterday's 22.000 sale, refunded it with "barang kembali ke stok" **off**, and got a `refund` approval with `channel="dashboard"` in the request log. The reconciliation invariant was then checked across every item: no mismatches. The demo café was reseeded afterwards so the verification data is not left in it.
- **`requested_by` is null on an owner-side reversal, and that is correct.** `OwnerCtx` carries no `staff_id` because the owner acted directly from the dashboard; nobody asked them. The till's rows carry the cashier who asked, which is the case where the distinction matters.
- **The runbook's known gap is now a procedure.** "A sale was rung up wrong" describes the till path and the owner path, when to leave "barang kembali ke stok" on and when to turn it off, and keeps the two instructions that still matter: never ring up a correcting sale, never hand-edit the stock to compensate. The "No void/refund button" row is gone from **Known gaps at go-live**; the other four stay.
- **One arithmetic slip, in the test, fixed in the test.** The first run asserted stock back to 50 after voiding a 3-cup sale, forgetting the fixture's other two sales; 44 + 3 = 47. The assertion was corrected to the right number rather than loosened, and the reconciliation check beside it is independent of the arithmetic either way.
**Deviation:** none. No migration, no model change, no change to any reversal, posting or stock function. The only behavioural change to existing code is that `pos_receipt` now delegates to an extracted function and `run_reversal` takes a channel.
**Next:** M15-T10 (backdated sale entry), which §5's revised build order puts at step 4 and which closes the remaining "paper sales land at today's time" gap. Canary due after 4 more tasks (last before M15-T9: M15-T9, M15-T11 = 2).

### [M15-T10] Backdated sale entry
**Date:** 2026-09-06
**Status:** done
**Changed:** backend/alembic/versions/0033_order_entry_source.py (new), backend/app/models/models.py (`Order.entry_source`), backend/app/services/orders.py (`entry_source` on `create_order`, the shift rule, `OrderSummary.entry_source`), backend/app/api/dashboard.py (`POST /api/backdated-sales`), backend/app/schemas/dashboard.py, backend/app/schemas/pos.py, backend/app/seed.py (the demo business is dated to match its history), backend/tests/test_backdated_sales.py (new, 10 tests), frontend/components/BackdatedSaleForm.tsx (new), frontend/components/ReversalPanel.tsx, frontend/app/(dashboard)/sales/page.tsx, frontend/lib/types.ts, frontend/lib/demo.ts, docs/runbook.md, docs/api-contract.md
**Gates:** pytest 499 passed 0 skipped (was 489) · migrations round-trip ok (0033 → 0032 → 0033) · frontend build ok · seed ok
**Notes:**
- **`entry_source` is a second axis, not a third value on `source`.** `source` says which channel the customer used — the till or the QR menu — and `entry_source` says how it was keyed in: `live` as it happened, `manual_backdated` typed afterwards from paper by somebody who chose the timestamp. Collapsing them would make "a backdated menu order" unsayable, and it is a coherent thing. Default `live`, one check constraint, a partial index on the non-`live` rows so the audit query stays cheap as history grows.
- **It posts through the ordinary engine and nothing about it is a special case.** Same pricing service, same atomic stock guard, same journal entry, same points — the endpoint prices the lines itself and pays exactly that, because a paper sale has no keypad to disagree with and a payment mismatch would be an error the owner cannot act on. The done-criterion test walks a slip from two days ago all the way through: `sold_at` is the slip's time, the stock movement and the journal entry are dated with the sale rather than with the typing, the entry balances, and the reconciliation invariant (M2-T3) holds.
- **It lands on the right business day, which is the reason M15-T4 came first.** The test's café closes late and starts its day at 04:00; the slip is from 00:20, so it belongs to the *previous* date. `revenue period=today` at that moment returns it and `yesterday` returns zero — the same assertion in both directions, so the fix cannot be read as a coincidence.
- **A live check found a real bug that the tests had not thought to ask about.** `create_order` stamps the cashier's currently-open shift on every sale, which is right for one being rung up and wrong for one typed in from paper: money taken two days ago is not in tonight's drawer, so counting it there makes the cashier come up short by exactly the amount somebody typed in to be helpful. A backdated sale now takes no shift, and a test asserts both directions — the open till still expects only its float after a backdated entry, and still picks up an ordinary sale rung up beside it.
- **Four guards, because choosing a sale's timestamp is the power to move takings between days.** Owner-only. Refused: a future date, anything more than 60 days old, anything before the business was registered, and an unknown or deactivated cashier — each with its own Indonesian message. Oversell is refused with the same 409 as the till and leaves nothing behind, and an unknown item is a 404 with no partial order, both tested by counting the orders afterwards.
- **Visible in the audit trail, not just in the database.** `entry_source` rides on `OrderSummary`, so `/api/orders` and the owner's reversal list both carry it, and the list prints "dari nota kertas" next to a typed row. Nothing in the metric layer excludes them — a paper sale is a real sale and revenue must include it — but the tag is now there for anything that wants to, which is what the roadmap asked for ("can be excluded").
- **Verified live through the real screen**, backend and frontend running: opened Penjualan → Catat nota, set the date to two days ago at 23:40, picked 3× Kopi Arabica, added the note "tablet mati, nota no. 12", saved, and got `#9994BBE7 · Rp 60.000 tercatat`. The database then showed `entry_source=manual_backdated`, `source=pos`, `sold_at 2026-09-04`, the line note carried through, a `sale −3.000` movement dated with the sale, a balanced `OrderCompleted` posted at the sale's own moment, and no reconciliation mismatches on any item.
- **The seed had to be fixed for its own story to hold.** `python -m app.seed` invents 30 days of sales but created the business row dated *now*, so on the demo café every backdated entry — including one for its own seeded history — was refused as "before the business was registered". That is the guard working correctly on incoherent demo data, so the demo data was corrected: the business is now dated 31 days back, alongside its `onboarding_completed_at`. Found by trying the screen rather than by reading the code.
- **The item picker offers only what the café sells.** `/api/items` is the whole inventory and includes raw materials priced at zero; offering "Gula Aren · Rp 0" invites a zero-value order. Also found by opening the form.
**Deviation:** none. The schema change is additive — one text column with a default and a check constraint, no data rewritten, downgrade drops it cleanly. `orders` already carries `tenant_isolation`, so no new policy is needed and M0-T5 is unaffected.
**Next:** M14-T1 (`docs/offline-policy.md`), which §5's revised build order puts at step 7 and which is now unblocked: steps 3 and 4 are done, step 5 (M12) is the standing NEEDS HUMAN, and step 6 (M15-T5, M15-T6) depends on it. M15's buildable set is complete — T1–T4 and T7–T11 done, T5 and T6 waiting on credentials and hardware. Canary due after 3 more tasks (last before M15-T9: M15-T9, M15-T11, M15-T10 = 3).

### Run stops here — everything buildable without credentials or hardware is done
**Date:** 2026-09-07
**Status:** NEEDS HUMAN (roadmap §3: the remaining tasks need credentials, hardware, and now live trading time)
**Changed:** docs/progress.md only. Nothing was built.
**Gates:** n/a. The tree is `[M15-T10]`: pytest 499 passed 0 skipped · migrations round-trip ok · frontend build ok · seed ok.
**Notes:**
- **Correcting the previous entry.** [M15-T10] ends "Next: M14-T1 … now unblocked". Roadmap **v9** revised step 7 after that was written: M14 is gated behind roughly two weeks of live trading, because the offline queue is the riskiest code in the system and a sync bug does not crash — it writes a wrong number that the reconciliation invariant surfaces days later. Shipping it as the café's first experience of the product would make a real bug indistinguishable from unfamiliarity. So M14-T1 is **not** the next task, and no work on it has started.
- **Where the build actually stands.** M0–M11 complete and tagged through `checkpoint/M9`. M15: T1, T2, T3, T4, T7, T8, T9, T10, T11 done, one task per commit, four gates green before each. Nothing in the roadmap that can be done from this machine remains undone.
- **What is left, and what each one is waiting for:**
  - **M12-T1** a Supabase project on a **paid** tier — URL, anon key, service key, database password — then the restore sequence in `PROJECT-STATUS.md` §5.
  - **M12-T2** a Meta app: WhatsApp Business account, phone number ID, permanent access token, webhook verify token. **Submit the two templates first** — review takes 24h or more, so it is the long pole and everything WhatsApp-shaped waits behind it.
  - **M12-T3** a real handset on the allow-list, to receive one live nightly alert.
  - **M12-T4** Railway (API + cron) and Vercel accounts and their deploy tokens. The cron entries M15-T1 and M15-T5 need are created here: `0 19 * * *` (02:00 WIB) for `python -m app.jobs.backup`, and the nightly job. Railway's image needs the Postgres 16 client tools on the cron service or `pg_dump`/`pg_restore` do not exist.
  - **M15-T5** an external uptime check against `/health/db`, alerting the owner's phone after two consecutive failures. Needs M12-T4 (something deployed to watch) and M12-T2/T3 (somewhere to send it).
  - **M15-T6** the café's actual receipt printer. **This is a purchase decision, not a task I can do:** the roadmap's preference order is (1) an Android POS terminal with a built-in printer — Sunmi, iMin, Advan, which is what majoo ships and which removes the pairing problem entirely; (2) a 58mm Bluetooth thermal printer over Web Bluetooth from Chrome on Android; (3) browser print, unusable in a queue and a fallback only. If hardware has not been bought, buy option 1.
  - **M14** the offline queue, gated behind ~2 weeks of live use (roadmap v9 step 7).
  - **M1-T3 part 2** the live evaluation of questions 16–30 and the text-to-SQL baseline, once Gemini billing is on. Non-blocking; the existing 15-question evidence stands on its own.
- **Three things to do before the café's first day**, all of them recorded in `docs/runbook.md` rather than only here: set **Hari usaha dimulai jam** to an hour after the last bill is normally settled (04.00 is the usual answer) — it decides what "today" means everywhere; appoint a **manager** so a void does not need the owner on site; and **re-time the lockout drill** on the café's own tablet, which is the one measurement in the runbook taken on a development machine rather than the real device.
- **Two known gaps remain in the runbook's go-live table**, both waiting on the above: no uptime alert (M15-T5) and no printer proven on real hardware (M15-T6). The offline-till row stays until M14, which is now a deliberate wait rather than a backlog item.
**Deviation:** none. No code was written in this entry.
**Next:** nothing, until the M12 accounts exist and the printer is chosen. After M12 lands, the order is M15-T5, then M15-T6, then two weeks of trading, then M14-T1.

### [M15-T12] PIN brute-force protection
**Date:** 2026-09-07
**Status:** done
**Changed:** backend/alembic/versions/0034_pin_attempts.py (new), backend/app/services/pin_guard.py (new), backend/app/models/models.py + __init__.py (`PinAttempt`), backend/app/api/pos.py (`/pos/login` throttled, 429 wording, `ManagerPinThrottled` on the three order paths), backend/app/services/orders.py (`verify_manager_pin` takes `business_id` and is counted; `ManagerPinThrottled`), backend/app/api/dashboard.py (`GET`/`DELETE /pin-lockouts`), backend/app/schemas/dashboard.py (`PinLockoutRow`), backend/tests/test_pin_guard.py (new, 15 tests), backend/tests/conftest.py (autouse engine disposal), backend/tests/test_manager_override.py (call sites), frontend/components/PinLockouts.tsx (new), frontend/app/(dashboard)/settings/page.tsx, frontend/app/pos/[businessToken]/page.tsx, frontend/lib/types.ts, frontend/lib/demo.ts
**Gates:** pytest 514 passed 0 skipped (was 499) · migrations round-trip ok (0034 → 0033 → 0034) · frontend build ok · seed ok
**Notes:**
- **What was unguarded.** `POST /auth/verify-otp` has counted attempts since M0. `POST /pos/login` counted nothing, and neither did `verify_manager_pin` — the gate on voids, refunds and discounts, which are the three ways money leaves. A 4-digit PIN is 10,000 combinations and the threat model is not a stranger on the internet: it is the person holding the tablet all shift, and the pairing link is a bearer URL anyone who has seen it can replay from their own phone.
- **The design is decided by one constraint: a café cannot have its till hard-locked during a rush.** A guard that protects the money by stopping trade is a denial of service the owner switches off within a week, and then nothing is protected. So it is an escalating *cooldown*, never a lock: 5 failures → 30s, 10 → 2 min, 20 → 15 min, and it stops escalating there. The first four failures cost nothing at all, which is the clause that decides whether this survives contact with a real shift — there is a test named after it.
- **Three counters, because they are three different questions.** `pos_login` is one staff member's own PIN. `manager_pin` is one cashier's attempts at somebody *else's* approver PIN, counted against whoever is asking (on a failure there is no way to know which approver was meant) and kept apart because that one guards money. `pos_device` is the whole pairing generation, which catches somebody spraying five guesses each across ten staff and never tripping any single person's counter; its thresholds are four times slacker because one shared tablet carries every cashier's mistakes.
- **A request that arrives during a cooldown is itself another attempt**, so `check` counts and logs it. Without that, hammering sits on the mildest penalty for ever and the log shows five lines instead of the hundred that happened. Somebody who reads "tunggu 30 detik" and waits is never counted. The 100-attempt test lands on the top rung with `failures == 101` and `{pin_failed: 9, pin_locked: 97}` in `request_logs`.
- **`record_failure` deliberately takes no session and opens its own.** Every path that counts a failure then raises, and raising rolls the caller's transaction back — which would discard the count and leave the guard doing nothing whatsoever. `verify_manager_pin` runs deep inside the order transaction, so there is no version of this that works on the caller's session.
- **A correct PIN forgives everything before it** (the row is deleted, tested), a gap longer than the 15-minute window starts the count again, and re-pairing a device (M15-T8) forgives that generation's accumulated failures — the same "everything before now is void" the owner just asked for.
- **The owner can see it and undo it.** `GET /pin-lockouts` lists only subjects still inside the window, with the subject resolved to a person rather than `staff:<uuid>`; `DELETE /pin-lockouts/{id}` lets somebody back in now, because a cashier locked out at the start of a rush cannot wait fifteen minutes and the owner is the one who can tell "forgot their PIN" from "trying everyone else's". Every failure is in `request_logs` either way, so the pattern is readable after the fact even when no threshold was ever reached.
- **The cashier is told how long to wait.** 429 with `Terlalu banyak PIN salah — tunggu N detik/menit lalu coba lagi`, shown on the PIN pad instead of the usual silent shake: the right PIN will not work either until the wait is over, and shaking the pad for the fifth time with no explanation is how a cashier concludes the tablet is broken.
- **`verify_manager_pin` now requires `business_id` as a keyword rather than accepting a default.** A signature that lets a caller quietly opt out of the guard is a guard that will eventually be opted out of. The two existing tests in `test_manager_override.py` were updated at the call site only — no assertion changed, nothing narrowed.
- **One piece of test infrastructure moved to `conftest.py`.** Disposing the app's own engine between tests was already needed by M15-T3 and M11-T1; since a wrong PIN can now be triggered from almost any test — because `record_failure` writes in its own transaction — it is autouse for the whole suite. Finding that out one file at a time is how a suite acquires flakiness nobody can reproduce.
- **RLS on `pin_attempts` in the same migration** (§1.1), with an isolation test proving business A cannot read or write business B's rows (§2).
**Deviation:** none. Schema change is additive: one table with its policy. Worth flagging for the author rather than fixing silently — the roadmap header went from "Version 8" to "Version 10" in this revision, while `docs/progress.md` refers to a "roadmap v9" whose header bump never landed. Nothing depends on the number; the build order is what is read.
**Next:** nothing buildable without credentials or hardware. M15-T12 was the last task in the roadmap's build order that needs neither. The queue is unchanged from the `[stop]` entry above: M12 (Supabase paid tier, Meta templates, Railway, Vercel), then M15-T5 and M15-T6 behind it, then M14 gated behind about two weeks of live use, and M1-T3 part 2 once Gemini billing is on.

### [stop] Run ends again: M15-T12 was the last buildable task
**Date:** 2026-09-07
**Status:** stop condition (roadmap §3: every remaining task needs credentials, hardware, or live use)
**Changed:** docs/progress.md only.
**Gates:** canary (§0.3) from a clean state: `git stash list` empty · `alembic downgrade base` then `upgrade head` (0001 -> 0034 from nothing) · seed ok · pytest 514 passed 0 skipped.
**Notes:**
- **This run resumed mid-task.** M15-T12 was written but uncommitted, and the roadmap's v10 revision defining it was uncommitted too. Both are now in, as two commits: the roadmap revision, then the task with its four gates green. Nothing was rewritten; the tree is what was already there plus the progress entry.
- **The canary above is the handover check**, run at the end rather than at the five-task mark it was due at, so the tree being handed over is verified from an empty database rather than from whatever state the session left behind.
- **The queue is unchanged** from the previous `[stop]` entry, and the reasons are the same:
  - **M12-T1 to M12-T4** need Supabase (paid tier), a Meta app with both templates submitted first, a real handset on the allow-list, and Railway/Vercel deploy tokens. None exist in this repository and none may be invented (§1, §3).
  - **M15-T5** needs something deployed to watch and somewhere to send the alert, so it sits behind M12-T4 and M12-T2.
  - **M15-T6** is a purchase decision before it is a task: the roadmap's preference order is an Android POS terminal with a built-in printer, then a 58mm Bluetooth thermal printer over Web Bluetooth, then browser print as a fallback that is unusable in a queue.
  - **M14** is deliberately gated behind roughly two weeks of live use (roadmap v10, "Why M14 waits"), not blocked by anything technical.
  - **M1-T3 part 2** needs Gemini billing enabled for the live evaluation of questions 16-30.
- **One uncommitted file left alone on purpose:** `AGENTS.md` at the repo root, untracked, a copy of `CLAUDE.md`. It is not part of any task and committing it would batch unrelated work.
**Deviation:** none.
**Next:** nothing, until credentials, a printer, or two weeks of live trading arrive. The build is at `1a414fb`.

### [repo-sync] Prepare the current version for GitHub
**Date:** 2026-09-15
**Status:** done (publication preparation)
**Changed:** docs/BUILD-ROADMAP.md (existing v11 update planning M9-T7), AGENTS.md (existing repository instructions, now tracked), docs/progress.md.
**Gates:** pytest 514 passed 0 skipped (one dependency deprecation warning) · migrations round-trip ok (0034 → 0033 → 0034) · frontend build ok · seed ok.
**Notes:**
- User requested publication of the current version to `Tunezt/FYP`, retaining the earlier requirement that GitHub show no AI contributors. This is a repository publication task; no roadmap feature was implemented or marked complete.
- Includes the 30 existing commits after M8-T2, through M15-T12 and its stop entry, plus the current documentation. M9-T7 remains planned and requires Gemini billing.
- Publish on the clean `main` history by removing AI co-author trailers from copied commit messages while preserving authors, committers, dates, parents' corresponding history, and every file tree. Keep the original local `master` history and all checkpoint tags; do not force-push or upload the original tags.
- Scanned 910 historical file blobs and the pending documentation for credential patterns and known local secrets; no matches. Environment files, local database files, and backups remain ignored.
**Deviation:** User-requested repository publication outside the feature roadmap. The existing roadmap edit and repository guidance are recorded together as this single publication-preparation task.
**Next:** Publish the clean `main` branch; feature work remains gated as described in the preceding stop entry.


### [M12-T1a] Temporary OTP log fallback until Meta exists
**Date:** 2026-09-16
**Status:** done
**Changed:** backend/app/core/otp_fallback.py (new), backend/app/core/config.py (`otp_log_fallback`), backend/app/api/auth.py (request-otp calls the fallback; `is_dev_bypass_code` extracted), backend/app/main.py (startup banner), backend/.env.example, backend/tests/test_otp_fallback.py (new, 8 tests)
**Gates:** pytest 522 passed 0 skipped (was 514) · migrations round-trip ok (0034 -> 0033 -> 0034) · frontend build ok · seed ok
**Notes:**
- **User-directed deviation from the roadmap.** M12 is going live on Supabase, Railway and Vercel before the Meta app exists, because the user's own Facebook account is too new. Owner login is a WhatsApp OTP, so without this the production dashboard cannot be logged into at all.
- **Fenced three ways.** Off unless `OTP_LOG_FALLBACK=true`; ignored the moment a real WhatsApp token is set; and **expires by date, not by memory**: `EXPIRES = 2026-10-16` is hardcoded, after which the flag is refused whatever the environment says and extending it takes a commit.
- **Loud on every boot** while enabled (days remaining), an ERROR if enabled but expired. The risk is stated in the banner itself: anyone with Railway log access can log in as any owner while it is on.
- `000000` stays development-only; a test pins that the fallback never enables it in production.
- **Supabase state found during this run (recorded here, not a code change):** the project is the July one, not a fresh one. It was at alembic 0002 with the demo cafe and two test businesses (`321`, `231`). Migrated 0003 -> 0034 (32 s). `app_role` already existed with the right attributes; its password was reset from `.env.production` without being displayed. Verified live as `app_role`: no BYPASSRLS; a tenant table with no `app.current_business_id` raises rather than returning rows (fails closed); each business sees only its own staff. `tests/test_db_integration.py` against Supabase: 24 passed.
- **Also found:** the server is Postgres 17.6 (the backup cron will need pg_dump 17), and `app/core/db.py` fails TLS verification against the Supabase pooler because Supabase's CA is not in the system store. That fix is the next commit.
- **Deferred by the user, not dropped:** encrypted R2 backups, the age key, the weekly integrity check and the nightly closed-business-day fix. Backups become a blocking gate before the real cafe's first sale, together with the production wipe (downgrade base, upgrade head, clear the receipts bucket, assert zero businesses, prove `app.seed` refuses).
- Tagged `demo/cp2-presentation` at 00a3e98, the code state for the FYP presentation (run locally with `app.seed`).
**Deviation:** yes, user-directed, as above.
**Next:** Supabase CA certificate TLS fix, then Railway API variables, Vercel redeploy, smoke test on demo data.


### [M12-T1c] Database TLS verified against the Supabase CA
**Date:** 2026-09-16
**Status:** done
**Changed:** backend/app/core/db.py (`ssl_context_for`), backend/app/core/config.py (`database_ssl_root_cert`), backend/certs/supabase-ca.crt (new, public CA), backend/tests/test_db_tls.py (new, 8 tests)
**Gates:** pytest 530 passed 0 skipped (was 522) · migrations round-trip ok (0034 -> 0033 -> 0034) · frontend build ok · seed ok
**Notes:**
- **The bug.** `db.py` verified remote TLS against the system trust store. Supabase's pooler chains to *Supabase Root 2021 CA*, which no system store carries, so the first connection failed with `CERTIFICATE_VERIFY_FAILED`. Production would never have reached its database. It went unnoticed because every test connects to localhost, where TLS is skipped.
- **The fix keeps verification on.** The CA (downloaded from the project's Database settings, public, expires 2031-04-26) is committed at `backend/certs/supabase-ca.crt` and used automatically for `*.supabase.com` / `*.supabase.co`; `DATABASE_SSL_ROOT_CERT` overrides it. A missing CA file raises rather than falling back to an unverified connection, and there is a test for exactly that.
- **A second failure behind the first.** Python 3.13 turns on `VERIFY_X509_STRICT`, which rejects Supabase's root for lacking a keyUsage extension. Only that flag is cleared, only for the pinned CA; chain, expiry and hostname are still verified, and other hosts keep Python's default flags (tested).
- **Verified live**: the app engine connected to the Supabase pooler as `app_role` with `CERT_REQUIRED` and hostname checking on.
- Alembic's engine still connects with asyncpg's default (encrypted, unverified). It only runs as a deploy step against a fixed host; noted, not changed here.
**Deviation:** none beyond the user-directed M12 ordering recorded in [M12-T1a].
**Next:** push, Railway deploy with production variables, `/health/db`, Vercel redeploy, smoke test on the demo data.


### [dash-1] Charts: one grouped query per metric instead of one per day
**Date:** 2026-09-16
**Status:** done
**Changed:** backend/app/metrics/registry.py (`series_buckets`, `SERIES_BUCKETS`, `_back_to_back`, fast path in `series`), backend/app/metrics/catalogue.py (bucketed revenue, transaction_count, expense_total), backend/tests/test_metric_series.py (new, 8 tests)
**Gates:** pytest 558 passed 0 skipped (was 530) - migrations round-trip ok - frontend build ok - seed ok
**Notes:**
- **User-directed, outside the roadmap.** The owner reported that the dashboard's 30/90-day and "Tahun Ini" tabs did nothing on the deployed site.
- **The cause was N+1 by design.** `series` called `compute` once per window, so a 90-day chart was 90 round trips per metric, 180 for the trend endpoint. On the laptop, against a local Postgres, that is imperceptible; from Railway's US West region to Supabase in Singapore a single `select 1` measured **1.2-2.4 s**, so the request never returned.
- **Two fixes, one of them not code.** The Railway service was moved to Southeast Asia, which took the same `/health/db` call from ~1400 ms to **20 ms**. This commit is the other half: a metric may register a bucketed form, one grouped query that assigns each row to `floor((column - start) / width)`.
- **The fast path is only ever an optimisation.** It engages only for back-to-back equal windows and only when no dimension is passed; months and `item_id` series still go row by row. `test_bucketed_series_equals_compute_on_every_window` runs both and asserts equality over 1/7/30/90-day windows, with sales placed exactly at 04:00 and one microsecond before it, so an off-by-one on the business-day boundary cannot pass.
- A statement counter proves a 90-day chart is one query per metric, not ninety.
**Deviation:** none. No schema change; the registry's contract (one implementation per metric) is unchanged - the bucketed form is checked against it rather than replacing it.
**Next:** the range bugs the same report surfaced.


### [dash-2] The range tabs actually change the chart
**Date:** 2026-09-16
**Status:** done
**Changed:** backend/app/api/dashboard.py (`/sales-trend` ceiling 365 -> 730), backend/tests/test_trend_range.py (new, 9 tests), frontend/app/(dashboard)/overview/page.tsx, frontend/app/(dashboard)/sales/page.tsx
**Gates:** pytest 558 passed 0 skipped - migrations round-trip ok - frontend build ok - seed ok
**Notes:**
- **"Tahun Ini" was a 422, not an empty shop.** The overview fetches twice the window it draws so it can show "vs the period before"; on the year tab that is 730 days, and the endpoint's ceiling was 365. The request was rejected outright and the tab drew nothing. The ceiling is now two years, and `test_the_dashboard_windows_are_all_served` covers every tab the UI can ask for (1/30/90/365/730), with 731 still refused rather than silently trimmed.
- **The silence was the worse half.** `useOwnerData` keeps the previous data on failure, and both pages rendered on `trend.data` alone. A rejected or slow request therefore left the *old* range's chart on screen with no spinner and no error - which is exactly what "clicking the tabs does nothing" looked like. Both pages now distinguish the three states: loading shows a skeleton (never the previous range's numbers), failure shows the existing `ErrorState` with a retry, and only then the chart.
- Verified in the browser against local demo data: the year tab fetches `days=730` and draws a full year.
**Deviation:** none.
**Next:** the history lists the same report asked for.


### [dash-3] History lists: filter in SQL, fold the old days
**Date:** 2026-09-16
**Status:** done
**Changed:** backend/app/api/dashboard.py (`_day_range_filters`; `since`/`until`/`staff_id` on `/sales`, `since`/`until`/`category` on `/expenses`, `since`/`until`/`severity` on `/alerts`), backend/tests/test_sales_filters.py (new, 11 tests), frontend/components/HistoryFilters.tsx (new), frontend/components/ui.tsx (`DayHeaderToggle`), frontend/components/icons.tsx (`IconChevronDown`), frontend/app/(dashboard)/sales|money|alerts/page.tsx
**Gates:** pytest 558 passed 0 skipped - migrations round-trip ok - frontend build ok - seed ok
**Notes:**
- **User-directed.** The owner asked for the history to open on today and yesterday, fold the older days, and be filterable by person and by date.
- **Filtering belongs in SQL, not the page.** The lists are paginated at 20-40 rows; filtering client-side would answer "what did Sari sell last Tuesday" from whatever page happened to be loaded, and the result count would be a lie. One helper, `_day_range_filters`, gives all three lists the same meaning of "a day": *business* days (M15-T4), both ends inclusive - so the 00:15 bill filters under the night before, and asking for one day means that whole day. Tests pin exactly that, plus that a nonsense date is a 422 rather than a silently ignored filter.
- **Folding is per day and remembered per click.** "Hari ini" and "Kemarin" open, everything older folded; the header carries the day's count and total so a folded day still answers "how much?". The whole header is the hit target, not a 16px chevron, because this is used on a counter tablet.
- **The empty state now tells the truth.** With a filter on it says nothing matched rather than implying the shop has no sales, no expenses, no alerts.
**Deviation:** none. Query parameters only, all optional; an unfiltered call is byte-for-byte the old behaviour, which `test_unfiltered_is_unchanged` holds.
**Next:** the interactive-row polish from the same report.


### [dash-4] A row that does something now looks like it
**Date:** 2026-09-16
**Status:** done
**Changed:** frontend/app/globals.css (`--row-hover`/`--row-press` tokens, `.list-row-action`, reduced-motion branch, `.btn-quiet:hover`), frontend/components/ui.tsx (`RowChevron`), frontend/app/(dashboard)/inventory/page.tsx
**Gates:** pytest 558 passed 0 skipped - migrations round-trip ok - frontend build ok - seed ok
**Notes:**
- **User-directed.** "Give it some element to show it's clickable, and darken/dim on hover."
- **Only rows that actually do something get the affordance.** Stock rows open the edit sheet, so they get a pointer, a trailing chevron that nudges 2px on hover, a hover tint, a darker pressed state and a keyboard focus ring. Alert, receipt and staff rows do nothing when clicked and were deliberately left quiet: a hover state on an inert row is a promise the interface cannot keep, and the design context (`.impeccable.md`) is explicit that rows stay quiet while the day header does the scanning work.
- **The tint darkens toward ink rather than introducing a colour**, at 4.5% light / 6% dark, and runs 8px past the text so the row reads as one band. Radius 12px, inside the existing 24px card grammar.
- `prefers-reduced-motion: reduce` drops both transitions - the hover tint still applies, only the movement goes.
- `.btn-quiet:hover` changed the border only, which is invisible on glass; it now darkens too.
**Deviation:** none.
**Next:** deploy these four to Railway/Vercel when the owner says to push; the OTP fallback still expires 2026-10-16.


### [dash-5] Riwayat transaksi is a list of receipts, and each one opens
**Date:** 2026-09-16
**Status:** done
**Changed:** backend/app/services/orders.py (`list_orders` takes `staff_id`), backend/app/api/dashboard.py (`/orders` takes `since`/`until`/`staff_id`), backend/tests/test_sales_filters.py (+3 tests), frontend/components/TransactionHistory.tsx (new), frontend/app/(dashboard)/sales/page.tsx, frontend/app/globals.css (`.pill-quiet`, `.receipt-lines`)
**Gates:** pytest 561 passed 0 skipped (was 558) - migrations round-trip ok - frontend build ok - seed ok
**Notes:**
- **User-directed.** The owner asked for the history to be "categorized by the transaction number, with the cashier name", opening to show what was ordered.
- **A sale is a receipt, not a line.** The list was rendering `/api/sales`, which is order *lines*: a three-item bill appeared as three separate sales, none of which could be matched against the paper slip in the owner's hand or against the number the till printed. It now renders `/api/orders` - one row per receipt: number, time, cashier, item count, total.
- **Opening a row fetches that receipt**, from the same endpoint the cashier printed from (M15-T11's rule: the owner deciding whether to void must not be reading a different document). Lazily, because forty rows would otherwise be forty requests for something nobody asked to see.
- **Voided bills stay visible but stop counting.** Nothing is deleted, so a cancelled bill still appears - struck through, chipped "dibatalkan", and excluded from the day header's total. A backdated entry keeps its "dari nota kertas" tag (M15-T10).
- `list_orders` already took a time range; it gained `staff_id`, and the endpoint converts a business day to the instants the service wants, so `/orders` and `/sales` now mean exactly the same thing by "10 September".
**Deviation:** none.
**Next:** the controls those filters are made of.


### [dash-6] The controls are ours, not the operating system's
**Date:** 2026-09-16
**Status:** done
**Changed:** frontend/components/Select.tsx (new), frontend/components/DateRangePicker.tsx (new), frontend/components/HistoryFilters.tsx, frontend/components/icons.tsx (`IconChevronLeft`, `IconCalendar`), frontend/app/globals.css (`.control-field`, `.popover-panel`, `.icon-btn`, `.chip-btn`, calendar band), frontend/app/(dashboard)/sales|money|alerts|settings|promos|customers/page.tsx, frontend/app/pos/[businessToken]/page.tsx, frontend/components/BackdatedSaleForm.tsx
**Gates:** pytest 561 passed 0 skipped - migrations round-trip ok - frontend build ok - seed ok
**Notes:**
- **User-directed:** "why is the dropdown ui default ahh looking" and a reference image of a date picker with the range drawn as one connected band.
- **A native `<select>` renders the operating system's widget** - a blue Windows listbox in the middle of a warm-light cafe dashboard - and cannot be styled at all. `Select` is the listbox pattern instead: a trigger that owns the value, a floating panel, and the keyboard the native control had (Up/Down/Home/End, type-ahead, Enter/Escape, focus returned to the trigger). Verified by driving it from the keyboard alone.
- **The panel is `position: fixed` and measured from the trigger**, so a card's own overflow can never clip it, and it flips above when the viewport runs out below.
- **The date range is one calendar, not two `mm/dd/yyyy` boxes.** The browser's native date input shows the *browser's* locale order, which is how an Indonesian cafe ends up reading American dates. One click sets the start, the second the end; the span is drawn as a single band - rounded cap, continuous tint, rounded cap - because two circled dates do not say "everything between these". Today keeps a ring even when it is outside the range. Indonesian month and day names, and "Hari ini / 7 hari / 30 hari" shortcuts.
- **Every native control in the app is replaced**, not only the two the owner pointed at: Pengaturan (jam mulai hari, pembulatan, arah, peran), Promo (jenis, barang, bonus, tanggal), Catat nota (kasir, barang, pembayaran), Pelanggan (ulang tahun), and the POS supplier picker. A `field` variant matches the existing `.field` grammar inside forms; `allowEmpty={false}` is for fields that must have an answer, so a form cannot be emptied into an invalid state.
- Reduced motion drops the panel animation and every transition.
**Deviation:** none.
**Next:** deploy when the owner says to push; nothing here changes the API contract.


### [svc-0] Baseline: the Poernama restyle already in the working tree
**Date:** 2026-09-17
**Status:** done
**Changed:** the 37 modified files and 2 new files that were uncommitted when this session started (frontend/app/globals.css, tailwind.config.ts, components/ui.tsx, icons.tsx, Wordmark.tsx (new), every dashboard page, the POS/kitchen/menu layouts and pages, backend/app/seed.py and bootstrap.py demo naming, scripts/wa-sim.py (new)). No edits made by this session.
**Gates:** pytest 561 passed 0 skipped - migrations round-trip ok (0034 -> 0033 -> 0034) - frontend build ok - seed ok
**Notes:**
- **User-directed sequence, outside the roadmap:** revise the cashier, kitchen and QR ordering experience (tasks `svc-1` onwards). The dashboard is described as "already polished" and must keep its design language - that polish is this uncommitted A1 "Porcelain & ink" restyle from the previous session.
- **Committed first and on its own** so every `svc-*` commit contains only its own change. The POS, kitchen and menu pages are touched by both, and folding a 2,500-line restyle into "[svc-1] product choices" would make either one impossible to review or revert.
- Gates were run against exactly this tree before committing; nothing was changed to make them pass.
**Deviation:** none.
**Next:** svc-1 - deliberate product choices on the till and the QR menu.


### [svc-1] Deliberate product choices: nothing is chosen for the customer
**Date:** 2026-09-17
**Status:** done
**Changed:** backend/app/services/orders.py (`ChoiceMissing`, `require_explicit_choices`), backend/app/services/tickets.py (the menu always asks), backend/app/api/pos.py (`/pos/orders` asks; `choice_missing_message`), backend/app/api/menu.py, backend/tests/test_service_journey.py (new, tests 1-2), frontend/lib/choices.ts (new), frontend/components/ProductPicker.tsx (new), frontend/app/pos/[businessToken]/page.tsx, frontend/app/menu/[token]/page.tsx
**Gates:** pytest 563 passed 0 skipped (was 561) - migrations round-trip ok - frontend build ok - seed ok
**Notes:**
- **User-directed.** Both pickers pre-selected the default size and every `is_default` modifier, so "Americano" silently became "Americano Standar Panas".
- **The server was half of the problem.** Required modifier groups were already enforced (`_resolve_modifiers` never applies defaults), but a line with no `variant_id` was quietly given the default size. The till and the QR menu now call `require_explicit_choices` first: a product with more than one active size must name one, or the answer is a 422 "Ukuran Americano belum dipilih". The service-layer default stays for callers with no customer to ask (WhatsApp tool, backdated slip) - `test_line_without_variant_uses_the_default` still encodes that, and it is right for those callers.
- **One picker, one rule set, both screens.** `lib/choices.ts` decides what to ask; `ProductPicker` shows every question with "wajib"/"opsional" and a tick once answered, and the add button explains the gap ("Pilih ukuran dan suhu") instead of just greying out. Single/multi and min/max are respected; a full multi group disables the rest.
- **A product with nothing to ask stays one tap** (one variant, no modifier groups). Optional extras still open the sheet, with "Tambah" ready immediately.
- **Lines are identities, not items.** Size, every modifier and the preparation note together decide whether two taps merge; editing a line reopens the sheet with that line's answers and merges if the edit makes it equal to another line. Verified in the browser at 1280x800 (till) and 375x812 (menu); option labels wrap instead of truncating on a phone.
**Deviation:** none.
**Next:** svc-2 - unpaid orders on the backend: held drafts, edits under a revision guard, idempotent submission, the active-orders list.


### [svc-2] Unpaid orders live on the server, and one tap is one order
**Date:** 2026-09-17
**Status:** done
**Changed:** backend/alembic/versions/0035_order_client_ref.py (new), backend/app/models/models.py (`Order.client_ref`), backend/app/services/tickets.py (open orders of both channels: `price_cart`, `hold_draft`, `update_open_order`, `open_orders`, `get_open_order`, `find_by_client_ref`, `replay_created`; settle takes `expected_rev`/`client_ref`), backend/app/services/orders.py (`list_orders` leaves out cancelled unpaid carts), backend/app/schemas/menu.py (`DraftIn`, `OpenOrderUpdateIn`, `ActiveOrderOut`), backend/app/schemas/pos.py (`OrderIn.client_ref`), backend/app/api/pos.py (`POST /pos/drafts`, `GET|PUT /pos/open-orders/{id}`, `GET /pos/active-orders`; settle/cancel accept any open order), backend/app/api/menu.py, backend/tests/test_service_journey.py (tests 3, 4, 5, 6, 9)
**Gates:** pytest 568 passed 0 skipped (was 563) - migrations round-trip ok (0035 -> 0034 -> 0035) - frontend build ok - seed ok
**Notes:**
- **User-directed.** A held order must survive switching customers and a refresh without a second, browser-only order system. A held draft is the same thing a QR ticket already was: an `open` row with a `cart`, no lines, no stock, no posting. `source = 'pos'` tells them apart. Settling either runs the one sale path on the row.
- **Edits happen under a revision.** `update_open_order` re-prices from today's catalogue and lands only `where status = 'open' and cart.rev = :expected`. A second tablet's stale edit, or a payment for a version the cashier has not seen, is a 409 that says it was changed elsewhere. Who changed it and what each revision came to is appended to `cart.revisions`.
- **Idempotency is a reference plus a lock.** `client_ref` names the submission that *created* an order (unique per business, migration 0035). `find_by_client_ref` first takes `pg_advisory_xact_lock`, so a second copy arriving mid-flight waits and then finds the first rather than failing on the index. A payment's own reference is kept in `cart.paid_ref`, because a QR order already carries the guest's placement reference in the column. The test caught that the two cannot share it.
- Test 9 races the pairs with `asyncio.gather`. A resubmitted QR order gives one ticket, a double-tapped sale gives one sale and one stock movement, two tablets paying the same QR order give one 200 and one 409 with one payment row, and two edits from the same revision give one 200 and one 409.
- **Cancelling an unpaid cart is not a void.** Nothing was taken, so nothing is reversed, and it no longer appears in the till's or owner's receipt list as a "voided sale". It never was one. That also fixes cancelled QR tickets, which previously showed there with zero lines.
- `test_every_http_exception_detail_is_indonesian` flagged a new message ("Americano sedang habis") whose Indonesian words are not in its marker list. The message was reworded ("Stok ... sedang habis"); the test is unchanged.
- This overlaps M14-T2 (client idempotency keys) only in part: no client-generated order ids, no replay ordering. M14-T2 remains to be built on top when M14 starts.
**Deviation:** none.
**Next:** svc-3 - additions to a paid order as their own linked purchase.


### [svc-3] "Tambahan untuk #1234": an addition after payment is its own purchase
**Date:** 2026-09-17
**Status:** done
**Changed:** backend/alembic/versions/0036_order_parent.py (new), backend/app/models/models.py (`Order.parent_order_id`), backend/app/services/orders.py (`ParentOrderInvalid`, `resolve_parent`, `create_order(parent_order_id=)`), backend/app/services/tickets.py (held additions; the parent is re-checked at settlement), backend/app/services/kitchen.py (`KitchenTicket.parent_code`), backend/app/schemas/pos.py, backend/app/schemas/menu.py, backend/app/api/pos.py (sale response and receipt carry `parent_number`), backend/tests/test_service_journey.py (test 7)
**Gates:** pytest 569 passed 0 skipped (was 568) - migrations round-trip ok (0036 -> 0035 -> 0036) - frontend build ok - seed ok
**Notes:**
- **User-directed, inside the counter-service model** (M13 stays cut: no table sessions, no bill merging). Before payment an order is extended by editing it (svc-2). After payment "Tambah pesanan" is a new sale with its own payment, receipt, posting and kitchen ticket, pointing at the paid order it belongs to.
- **The original is only read.** Test 7 snapshots the first order's lines, payments, journal entry, stock movements and kitchen events, with the kitchen already on "preparing". After the addition all of them are identical. The kitchen holds two tickets: the original still "preparing" with its own two coffees, and the addition "new" with only the roti and `parent_code` naming the original.
- **A family has one head.** An addition to an addition resolves to the original, so three purchases read as one "Tambahan untuk #1234" group rather than a chain.
- **Refused with a reason:** adding to an unpaid order ("tambahkan langsung ke pesanan itu") or to a voided or refunded one. A held addition is re-checked when paid, in case the original was reversed in between.
- **Business decision left as it was, stated plainly:** voiding or refunding the original does not touch its additions. Each is its own receipt with its own payment, and the existing manager-PIN reversal applies to each separately. Reversing a whole family at once would be a new rule.
**Deviation:** none.
**Next:** svc-4 - kitchen backend: per-line progress, expected-state conflicts, cancellation notices, history.


### [svc-4] Kitchen backend: what is left to make, and who got there first
**Date:** 2026-09-17
**Status:** done
**Changed:** backend/alembic/versions/0037_kitchen_line_events.py (new table, RLS in the same migration), backend/app/models/models.py + models/__init__.py (`KitchenLineEvent`), backend/app/services/kitchen.py (line progress, `expected` state, `cancellations`, `history`, sizes written out), backend/app/schemas/pos.py (`KitchenBoardOut`, `KitchenLineIn`, `KitchenStateIn.expected`), backend/app/api/pos.py (`GET /pos/kitchen/board`, `POST /pos/kitchen/{id}/lines/{line_id}`), backend/tests/test_service_journey.py (test 8, test 10, RLS test for the new table)
**Gates:** pytest 572 passed 0 skipped (was 569) - migrations round-trip ok (0037 -> 0036 -> 0037) - frontend build ok - seed ok
**Notes:**
- **User-directed:** Baru -> Disiapkan -> Siap diambil -> Diserahkan, mapped onto the existing `new/preparing/ready/done` with no new state.
- **Checked the existing forward-only tests first.** `test_states_move_forward_only_and_done_bumps_the_ticket_off` pins that ready->preparing and done->ready are refused, and that `done` is accepted straight from `new` at the service level. Nothing here weakens either. **Recall/undo of a kitchen state was not added**: it would contradict that test, so under §1.8 it is a question for the owner, not a change to make. Unticking a *line* while the ticket is still being prepared is allowed. That is a correction to line progress, not a state moving backwards.
- **"Ready" means the whole order.** Ticking any line starts the ticket. Once any line has been ticked, `ready` is refused while one is unfinished ("Masih ada 1 item belum selesai"). A ticket nobody ticked line by line can still be called ready as a whole, which is what M11-T2's API test does and what a single coffee needs. Lines are fixed once the ticket is ready.
- **The expected state stops two tablets disagreeing.** A device sends the state it is showing. The move must be the next step from it and is refused if another device got there first ("sudah siap diambil dari perangkat lain"). The same tap arriving twice is still a no-op 200. With an expected state there is no path from `new` to `done`, so an unprepared order cannot be dismissed by accident. Callers that send no expected state keep the M11-T2 behaviour. The order row is read `for update`, so concurrent moves on one ticket serialise.
- **Cancellations are shown, not silently dropped.** `board()` still excludes a reversed order (the M11-T2 test pins that). `cancellations()` lists paid orders voided or refunded before handover, with who approved it and the reason from the `approvals` row, until someone in the kitchen acknowledges. Acknowledging appends `done` and is the only move a reversed order accepts.
- **Choices are written out.** The kitchen line carries `size` whenever the product has more than one size, including "Standar". It previously hid the default size, so "Americano" could mean either.
- `history()` is the last handovers of the board window, newest first, with who handed over.
**Deviation:** none.
**Next:** svc-5 - QR backend: per-order access key, a real quote before sending, prices re-checked before payment.


### [svc-5] QR backend: a private order, a real total, and no price the customer did not see
**Date:** 2026-09-17
**Status:** done
**Changed:** backend/app/services/tickets.py (`access_key` on placing, `price_changes`, `PricesChanged` at settlement), backend/app/api/menu.py (`POST /menu/{token}/quote`, `expected_total` on placing, `watch?key=` with a redacted view without it), backend/app/api/pos.py (`POST /pos/open-orders/{id}/reprice`, `price_changes` on active orders, `prices_changed_message`), backend/app/schemas/menu.py, backend/tests/test_service_journey.py (2 tests)
**Gates:** pytest 574 passed 0 skipped (was 572) - migrations round-trip ok (no migration in this task; 0037 -> 0036 -> 0037) - frontend build ok - seed ok
**Notes:**
- **Canary (§0.3) ran before this task**, five commits after the session began: `git stash list` empty, `alembic downgrade base` then `upgrade head` (0001 -> 0037), seed ok, pytest 572 passed 0 skipped.
- **Every table shares one menu link, so the order id is not a secret worth a person's name.** Placing a QR order returns a random `access_key` (kept in the cart, returned only to the placing request or its idempotent replay). `GET /menu/{token}/orders/{id}` without the key still shows status, items, total and kitchen progress (the existing M11 tests depend on that). Guest name, phone, table, order note and line notes are withheld. Comparison is `secrets.compare_digest`.
- **The guest confirms the total the till will charge.** `/menu/{token}/quote` runs the same `price_cart` (choices, availability, tax, service, rounding) and writes nothing. Placing with `expected_total` is refused, with nothing written, if the server now prices the cart differently, so the phone can re-quote with the cart intact.
- **Business decision taken, and stated: the catalogue price at payment wins, and the customer must see it first.** Before this, settlement charged the price stored at placing, whatever the catalogue said by then. Now `settle_ticket` compares every unpaid line with today's catalogue and refuses payment with the old and new price named ("Americano · Large Rp 29.000 → Rp 30.000"). The cashier re-prices explicitly (`/reprice`, which keeps every size, option, note and quantity and bumps the revision), the guest's page shows `revised`, and payment goes through at the new total. Out-of-stock stays the sale's own 409 ("Stok tidak cukup"), which `test_a_settlement_that_fails_leaves_the_ticket_open_and_untouched` pins. If the owner prefers to honour the price shown at order time, this check is the one place to change.
- A first attempt normalised cart quantities to `1.000`. `test_placing_a_ticket_writes_one_open_row_and_takes_nothing` pins the stored `"2"`, so that change was reverted, and the new test compares numerically instead.
**Deviation:** none.
**Next:** svc-6 - the cashier workspace: products beside a persistent order, Pesanan baru / aktif / Riwayat transaksi.


### [svc-6] The cashier workspace: Pesanan baru, Pesanan aktif, Riwayat transaksi
**Date:** 2026-09-17
**Status:** done
**Changed:** frontend/app/pos/[businessToken]/page.tsx (sign-in only now), frontend/components/pos/SellScreen.tsx (new), frontend/components/pos/OrderPanel.tsx (new), frontend/components/pos/ActiveOrders.tsx (new), frontend/components/pos/Receipts.tsx (new: the receipt sheet and the former ReversalSheet as an inline history view), frontend/lib/pos.ts (new: shared shapes, `newRef`, wait/clock labels)
**Gates:** pytest 574 passed 0 skipped - migrations round-trip ok - frontend build ok - seed ok
**Notes:**
- **User-directed.** Three views in one segmented control: *Pesanan baru* (the order being built), *Pesanan aktif* (everything the counter still owes someone), *Riwayat transaksi* (today's paid receipts; void and refund are unchanged, M15-T11). Shift, cash, kitchen and lock stay in the header but quieter: the shift shows the expected cash as text, the others are icon buttons that gain labels on wide screens.
- **Products beside a persistent order on a wide till (>= 1024px).** Compact tiles in an auto-fill grid with a search field, and a sticky 390px panel with order type, customer name, table, editable lines (quantity steppers, tap a line to change its choices, line discount), the server-priced total, *Bayar*, and *Simpan, bayar nanti*. Below 1024px the panel becomes a drawer behind a persistent dock showing the count and total.
- **Switching customers loses nothing.** *Simpan* holds the cart on the server (`POST /pos/drafts` with a reference minted once per cart; `PUT` with the revision when editing a held or QR order). Resuming another order first holds whatever unsaved work is on the panel; a failed hold stops the switch and says why.
- **Pesanan aktif** has filters with live counts (Semua, Belum dibayar, Disiapkan, Siap diambil) and rows with code, source, name, service type, time and elapsed, item summary, total, and payment and preparation as separate badges. Opening a row shows a detail panel (a sheet on tablets) whose actions depend on its state: unpaid gets *Bayar*, *Ubah pesanan* or *Batalkan* with a reason; changed prices get *Perbarui ke harga sekarang*; paid gets *Tambah pesanan* and *Struk*; ready gets *Tandai sudah diserahkan*. Polled every 6s while visible; a failed poll keeps the last list and shows "Koneksi terputus · data mungkin tidak terbaru".
- **Paying replays rather than duplicates.** The payment reference survives a failed attempt, so pressing *Bayar* again after a dropped connection returns the first payment (svc-2). An edited open order is saved under its revision and then settled with that revision.
- Verified in the browser against orders created through the API (in preparation with one line ticked, ready, unpaid QR, held draft, add-on) at 1280x800 and 800x1280: held an order and saw it listed; opened the QR order, added a croissant and paid (Rp 87.000 -> Rp 115.000); confirmed a handover and watched the counts update. Fixed during verification: tile prices and order-type labels wrapping, the nav dot overlapping its count, "Buka shift" wrapping in portrait. The impeccable detector found nothing.
**Deviation:** none.
**Next:** svc-7 - the kitchen screen.


### [svc-7] The kitchen screen: Baru, Disiapkan, Siap diambil, and why a ticket left
**Date:** 2026-09-17
**Status:** done
**Changed:** frontend/app/kitchen/[businessToken]/page.tsx (the board rebuilt on `GET /pos/kitchen/board`; sign-in unchanged)
**Gates:** pytest 574 passed 0 skipped - migrations round-trip ok - frontend build ok - seed ok
**Notes:**
- **User-directed.** Three columns in the order the work happens, Baru -> Disiapkan -> Siap diambil (two columns on a portrait tablet, three from 1280px). Each column lists its oldest ticket first. *Diserahkan* is the handover action and the history tab.
- **One obvious action per state, and no dismiss shortcut.** Baru: *Mulai siapkan*. Disiapkan: *Siap diambil*, disabled on a multi-item ticket until every item is ticked ("1 item belum selesai"). Siap diambil: *Sudah diserahkan*. Every move sends the state the screen was showing (svc-4), so there is no path from Baru to handed over, and the old always-visible "Selesai" button is gone.
- **Readable at a distance.** Code at 30px; quantity and item at 19-22px; the size written out ("Americano · Standar"); modifiers on their own line; preparation notes in a tinted band. Takeaway is an ink-filled chip and dine-in a quiet table chip; additions carry "Tambahan untuk #9B08". Elapsed time is measured on the server's clock (the board returns `server_time`). It turns amber from 10 minutes and adds an amber outline from 20. No promised preparation time is shown.
- **A failed refresh never looks like an empty kitchen.** The last board stays; the header switches to "Koneksi terputus · data dari 22.49" with *Coba lagi*; the first load failing shows its own error instead of "Antrean kosong". Polls do not overlap, and tickets are keyed by order id so a refresh does not reset scroll. Verified by stopping the API with the board open.
- **Conflicts explain themselves.** Verified by moving a ticket from a second client while the screen was stale. The tap was refused and "#450E: Pesanan ini sudah siap diambil dari perangkat lain" appeared both on the card and above the columns, because a refused move can take its card off the board. The first verification showed only the card message, which vanished with the card, so the board-level notice was added.
- **Cancellations are shown until someone acknowledges them.** A refunded in-preparation order appears as "#BEE6 dikembalikan — hentikan pembuatan", with its items, approver and reason, and *Oke, mengerti*. The *Sudah diserahkan* tab lists the window's handovers with time and staff, and refunded ones are marked.
- Recall/undo of a ticket state was not added (see svc-4: the M11-T2 forward-only test). The impeccable detector found nothing.
**Deviation:** none.
**Next:** svc-8 - the QR customer journey.


### [svc-8] The QR journey: choose, see the real total, show the code, follow the order
**Date:** 2026-09-17
**Status:** done
**Changed:** frontend/app/menu/[token]/page.tsx (rebuilt), frontend/components/ProductPicker.tsx (functional state updates; "dari" wording)
**Gates:** pytest 574 passed 0 skipped - migrations round-trip ok - frontend build ok - seed ok
**Notes:**
- **User-directed.** The customer's side of the same system, at phone width. The menu is one list with search, "dari Rp ..." for sized items, a "Habis" state, and a count per item. The picker is the till's (svc-1). *Pesanan kamu* lets the guest edit every line (quantity, and tap to change choices or notes), choose *Makan di sini* or *Bawa pulang* (table required for dine-in), add a name and a note for the kitchen, and see **the server's quote** (tax, service, rounding) before *Kirim pesanan*. It says plainly that nothing is paid yet, that they pay at the cashier, and that preparation starts after payment.
- **Retries cannot duplicate.** The submission reference is minted once and stored with the unsent cart, so a retry after a dropped connection, even after a refresh, replays the same order (svc-2). `expected_total` is the quoted total. If a price or availability moved, the guest is told, the menu reloads, unavailable lines are removed by name, and the rest of the cart stays.
- **After sending:** a 56px order code, a four-step tracker (Bayar di kasir -> Masuk dapur -> Disiapkan -> Siap diambil) driven only by the server's `status` and `kitchen_state`, the items and total, and a notice when the cashier revised the order. The phone stores only `{id, access_key}`. A refresh restores the order with its private details, polling pauses while the page is hidden, and a failed poll says the code is still valid rather than looking lost. *Pesan lagi* appears only once the order is handed over or cancelled, so a guest cannot drop tracking of an order in progress.
- Verified at 375x812: Americano needed Large + Dingin before it could be added (note "es sedikit"), Croissant was one tap, the server total was Rp 50.000, and sending gave M-2025 with the pay-at-cashier instructions. The order was then paid and started through the API, and after a refresh the page showed "Sedang disiapkan" with steps 1-2 ticked and table and name visible (keyed view).
- A picker bug found while testing: taps landing before a re-render could overwrite each other (each handler spread a stale `sel`). All picker updates now use functional state updates.
- The impeccable detector found nothing.
**Deviation:** none.
**Next:** svc-9 - end-to-end service run across the three screens, and the closing summary.


### [svc-9] One service, three screens: verification run and open decisions
**Date:** 2026-09-17
**Status:** done
**Changed:** docs/progress.md only
**Gates:** pytest 574 passed 0 skipped - migrations round-trip ok (0037 -> 0036 -> 0037) - frontend build ok - seed ok
**Notes:**
- **The ten journey checks the owner asked for are tests, not screenshots.** They live in `backend/tests/test_service_journey.py` and run against the real API and Postgres: (1) Americano refused without size, then without temperature, on the till and the QR menu; (2) differently customised Americanos stay three lines; (3) held orders survive switching customers and a refresh; (4) a QR order reaches the cashier, is edited, and stale edits and stale payments are refused; (5) unpaid orders never reach the kitchen; (6) payment is one order, one payment, one journal entry, one movement per line and one kitchen ticket, and a replayed tap returns it; (7) an addition charges only the new items and cooks only them, and the original's footprint is unchanged; (8) partial preparation, ready and handover agree across kitchen board, active orders and the guest's page; (9) concurrent resubmits, double taps, two tablets paying and two tablets editing do not duplicate; (10) cancelling unpaid leaves no reversal rows, a refund after preparation keeps both originals and reversals, and the kitchen sees why. Also: RLS on `kitchen_line_events`, QR order privacy, and quote/re-price.
- **Cross-screen run in the browser** (seeded Poernama catalogue; orders created through the API). Kitchen at 1280x800, three columns: ticked the remaining item on #8568 and marked it ready; the till's `/pos/active-orders` showed `paid/ready` with both lines done. The addition #2EB0 stayed "new" beside it. The QR order M-A9E4 was paid at the till and appeared under Disiapkan without a reload. The guest's keyed status showed `preparing` with name and table, and an unkeyed read showed the same progress with both withheld. The refunded #52B1 sat in the cancellation band.
- **Screen sizes checked across svc-1, svc-6, svc-7 and svc-8:** till at 1280x800 (landscape) and 800x1280 (portrait, drawer), kitchen at 1440x900 and 1280x800, QR menu at 375x812 and 390x844.

**Decisions this sequence took that the owner may want to reverse (none blocks use):**
1. **Price at payment.** An unpaid order whose catalogue price changed cannot be paid until the cashier re-prices it in front of the customer (svc-5). Honouring the price the customer saw at order time is the alternative; the check in `settle_ticket` is the one place to change.
2. **Reversing a family.** Voiding or refunding an original order does not reverse its paid additions (svc-3). Each is its own receipt under the existing manager-PIN rules.
3. **Handover from the till.** A cashier can confirm *Sudah diserahkan* for a ready order from Pesanan aktif, as well as the kitchen. This fits counter pickup. If handover must only ever be confirmed in the kitchen, remove the button.

**NEEDS HUMAN (not blocking, not built):**
- **Kitchen recall/undo** (ready back to preparing, or undo a handover) was not added. `tests/test_kitchen.py::test_states_move_forward_only_and_done_bumps_the_ticket_off` requires those moves to be refused, and under §1.8 changing that is the owner's call. What exists instead: unticking a line while a ticket is being prepared, and the expected-state guard that stops accidental skips.
- **A multi-item ticket can still be marked ready as a whole through the API** when nobody ticked any line, because the M11-T2 API test does exactly that on a two-line order. The kitchen screen never offers that path (it requires every line ticked). Making it a server rule needs the same decision as above.

**Known limitations, stated plainly:**
- Everything is polled (till 6s, kitchen 4s, guest 6s). No push; near-instant sync across devices would need websockets or SSE, which the café's wifi was deliberately never trusted with (M11).
- An unsent cart on the till lives only in the page until *Simpan* is tapped. That is deliberate: held orders are server-side, and there is no second browser order system. The QR guest's unsent cart is kept in the phone's localStorage for refresh, and only as a convenience.
- Tickets older than the 12-hour board window drop off the board and the history (unchanged M11-T2 behaviour).
- M14 (offline queue) is untouched and still gated on two weeks of live use. `client_ref` covers retries while online, not queued offline sales.
- Browser screenshots in emulated mobile sizes were sometimes cropped or timed out in the preview pane. Those checks were confirmed by reading layout metrics and page text instead.
**Deviation:** none. Nothing was pushed or deployed.
**Next:** owner review of the three decisions and the NEEDS HUMAN items above; push only when asked.


### [prt-1] Daily service numbers: Pesanan 042, one sequence for till and QR
**Date:** 2026-09-18
**Status:** done
**Changed:** backend/alembic/versions/0038_service_numbers.py (new: `service_number_counters` with RLS; `orders.service_date/service_number/batch_no/external_ref`), backend/app/models/models.py + __init__.py, backend/app/services/service_numbers.py (new), backend/app/services/orders.py, backend/app/services/tickets.py, backend/app/services/kitchen.py, backend/app/api/pos.py, backend/app/api/menu.py, backend/app/schemas/pos.py + menu.py, backend/tests/test_service_numbers.py (new, 8 tests), frontend/lib/pos.ts (`orderHeading`, `orderLabel`), frontend/components/pos/ActiveOrders.tsx, OrderPanel.tsx, SellScreen.tsx, Receipts.tsx, frontend/components/TransactionHistory.tsx, frontend/lib/types.ts, frontend/app/kitchen/[businessToken]/page.tsx, frontend/app/menu/[token]/page.tsx
**Gates:** pytest 582 passed 0 skipped - migrations round-trip ok (0038 -> 0037 -> 0038) - frontend build ok - seed ok
**Notes:**
- **User-directed: the café's real layout** (cashier and barista at the front, kitchen 15-20 m back, chef on paper). This supersedes the svc-sequence assumption that kitchen work runs through a screen. The svc improvements (choices, held orders, QR, Pesanan aktif) are kept.
- **One number people say out loud.** 001, 002 ... per business per *business day* (M15-T4 boundary), shared by till and QR, allocated server-side when an order is first persisted (held draft, QR order, or sale). One `insert ... on conflict do update ... returning` on a counter row, so concurrent requests cannot share a number and no number is derived from a count of orders. A cancelled order keeps its number and the counter never goes back. A request that rolls back takes its increment with it, since nobody ever saw that number. Past 999 it is simply 1000.
- **Kept through everything.** Retries replay the numbered order (svc-2); edits, payment and refresh never renumber. An order carried past the day boundary keeps its number and date, and Pesanan aktif marks it with the date. A paid addition shares its original's number and takes `batch_no` 1, 2 ... under a lock on the original ("Pesanan 042 · Tambahan 1").
- **UUIDs and old references untouched.** The receipt's eight-character reference stays. Historical orders are not renumbered and display `#XXXX`. Additions made before this migration are ordered into batches by creation time (needed for the new unique index), and their numbers stay NULL.
- **Headings:** dine-in with a table shows "MEJA 7" first and "Pesanan 042" second; everything else shows "PESANAN 042". A driver's reference (`external_ref`, typed by hand, no integration) is shown beside the number, never instead of it. Kasir/QR badges stay separate.
- **History disambiguates.** `/api/orders?q=001` matches the service number across days and every row carries `service_date`; the dashboard list is grouped by day already. The number authorises nothing.
- Tests: 12 concurrent mixed till/QR/draft requests get 001-012; the number is identical on the QR page, active orders, receipt, kitchen ticket and after a replayed payment; cancelled numbers are not reused; batches; a 04:00 day start puts 02:30 on the previous day's sequence; carried-over orders are flagged; 999 → 1000; legacy fallback; RLS on the counter table.
- **Clock note:** the full suite was first run after 00:00 WIB, when 8-10 existing tests fail on an unmodified HEAD (verified in a separate worktree; memory updated). Work continued on a side branch and each commit landed only after the gates were re-run after 06:00 WIB.
**Deviation:** none. Additive: one new table (RLS in the same migration, isolation tested), four nullable/defaulted columns, two partial unique indexes.
**Next:** prt-2.


### [prt-2] Every item says where it is made: Bar, Dapur, or nothing
**Date:** 2026-09-18
**Status:** done
**Changed:** backend/alembic/versions/0039_prep_station.py (new: `items.prep_station`, `order_lines.prep_station`), backend/app/models/models.py, backend/app/schemas/dashboard.py + pos.py, backend/app/api/dashboard.py, backend/app/api/pos.py, backend/app/services/orders.py (snapshot on the sold line), backend/app/services/tickets.py (cart lines carry it), backend/app/seed.py (explicit stations for the demo menu), backend/tests/test_prep_routing.py (new, 2 tests), frontend/lib/types.ts, frontend/app/(dashboard)/inventory/page.tsx
**Gates:** pytest 584 passed 0 skipped - migrations round-trip ok (0039 -> 0038 -> 0039) - frontend build ok - seed ok
**Notes:**
- **Set by the owner, never inferred.** `bar` (the front: coffee, tea, and display pastries such as the croissant), `kitchen`, or `none` (nothing to prepare). A new item has none until someone chooses, whatever its name. Stok → edit → *Disiapkan di* offers the three choices, and each row shows its station or "tujuan belum diatur".
- **Snapshotted per sold line**, like `unit_cost_at_sale`: moving the roti to the front display tomorrow does not change where yesterday's order was made. Tested.
- An item with no station set is routed to the front slip with a flag (prt-3). The person reading it stands beside the cashier and can walk it back.
**Deviation:** none. Additive: two nullable columns with check constraints.
**Next:** prt-3.


### [prt-3] Paper the cafe needs: receipts, Bar and Dapur slips, persisted with the payment
**Date:** 2026-09-18
**Status:** done
**Changed:** backend/alembic/versions/0040_print_jobs.py (new, RLS), backend/app/models/models.py + __init__.py (`PrintJob`), backend/app/services/printing.py (new), backend/app/services/orders.py (enqueue in the payment transaction; BATAL on reversal), backend/tests/test_prep_routing.py (+9 tests)
**Gates:** pytest 593 passed 0 skipped - migrations round-trip ok (0040 -> 0039 -> 0040) - frontend build ok - seed ok
**Notes:**
- **Two printers, fixed by the room.** Front: the customer receipt and a *separate* Bar slip. Kitchen: the Dapur slip. A station with nothing to make gets no slip; a drink-only order sends nothing to the kitchen.
- **Written with the payment.** Jobs are inserted in `create_order`'s transaction, so a refresh or dropped connection cannot lose them and a dead printer cannot roll back the sale. `dedupe_key` is unique per business and keyed on the financial order: concurrent replays and triple-tapped settlements produce exactly one receipt and one slip per station (tested). Unpaid orders and backdated paper sales print nothing.
- **Documents are frozen, printer-neutral blocks** (title, label, banner, kv, item ...). Slips carry the table or number, service type, time, a slip reference (`001-D1`), and each item with quantity, explicit size, every modifier and the note. No prices, payment, or customer name. The receipt carries everything with prices and payment.
- **Additions** print their own receipt and slips with only their items, labelled TAMBAHAN and "Tambahan 1". The original's jobs are never produced again (tested: its job count is unchanged).
- **Cancellation cannot un-print.** On void or refund, a slip still `pending` is withdrawn (`cancelled`). One that may be on paper (claimed, printed or failed) gets a BATAL notice to the same printer, listing only that station's items. The receipt job stays as the sale's record; stock and books reverse exactly as before (tested).
- **Queue semantics:** `claim_next` uses `FOR UPDATE SKIP LOCKED`, so two devices never take one job. Failed jobs can be retried. A claimed job with no answer after 90 s *displays* as uncertain and cannot be retried silently, only reprinted (marked CETAK ULANG with a count and the staff name) or confirmed by a person. A late device answer cannot overwrite a person's confirmation.
**Deviation:** none. One new table, RLS in the same migration, isolation tested.
**Next:** prt-4.


### [prt-4] Printers pull their slips; the till sees and recovers the queue honestly
**Date:** 2026-09-18
**Status:** done
**Changed:** backend/app/api/printing.py (new: `/print/agent/*`, `/pos/print-jobs/*`, `/api/printers/{printer}/token`), backend/app/main.py, backend/app/core/security.py (`create_token(extra=)`), backend/app/api/pos.py + schemas/menu.py (print summary on active orders), backend/tests/test_error_localization.py (scans the new module too), backend/tests/test_prep_routing.py (+4 tests), frontend/components/pos/PrintDocument.tsx (new), frontend/components/pos/PrintQueue.tsx (new), frontend/components/PrinterTokens.tsx (new), frontend/components/pos/SellScreen.tsx, ActiveOrders.tsx, frontend/app/(dashboard)/settings/page.tsx, frontend/lib/pos.ts
**Gates:** pytest 597 passed 0 skipped - migrations round-trip ok (0040 -> 0039 -> 0040) - frontend build ok - seed ok
**Notes:**
- **Inspected the constraints first.** The API is on Railway and the pages on Vercel, so the cloud cannot reach a printer on the café's wifi. The till is one Android tablet in Chrome, which cannot open raw TCP to a network printer, and Bluetooth will not reliably reach a kitchen 15-20 m back. So unattended printing must **pull**: a device token (scope `printer`, one printer each, owner-issued, dies with a re-pairing) claims jobs and reports results over HTTPS.
- **The till says only what is known:** Menunggu printer / Sedang dikirim / Belum pasti tercetak / Tercetak (and whether a person confirmed it) / Gagal cetak / Ditarik. The header icon counts what needs checking; *Antrean cetak* and every order's detail offer *Coba lagi* (failed only), *Cetak ulang* (marked), *Kertas sudah ada* (uncertain), and *Cetak manual* (front printer only).
- **The browser fallback never assumes paper.** Preview first; the job is taken only when the dialog opens; afterwards the till asks "Apakah kertasnya keluar?", and only "Ya" marks it printed, recorded as that person's confirmation. Tested through the API; the rendering was checked in the browser (Bar slip for MEJA 7 showed only Bar items with size, modifier and note).
- Tests: a device pulls only its printer's jobs in order (receipt then Bar slip as separate jobs), cannot answer another printer's job, and a repeated answer is idempotent; scope separation in all directions and revocation by re-pairing; failed → retry → uncertain → refused retry → marked reprint → person's confirmation; browser take/decline/retake/confirm.
- **Not done, deliberately:** no printer SDK, bridge or cloud-print adapter. That depends on hardware nobody has chosen, and could not be tested (documented with the bill sequence; NEEDS HUMAN note to follow).
**Deviation:** none. No schema change.
**Next:** bill-1. The owner changed direction (see the decision record at bill-1): dine-in becomes an open bill per table paid at the end, and per-item serving states (drafted as prt-5) are dropped, not landed.


### [prt-7] The receipt comes out before the Bar slip, every time
**Date:** 2026-09-18
**Status:** done
**Changed:** backend/app/services/printing.py (`PAPER_ORDER`; used by `claim_next` and `jobs_for_orders`), backend/app/api/printing.py (queue listing), backend/tests/test_prep_routing.py (+1 test)
**Gates:** canary (§0.3) first: `git stash list` empty, `alembic downgrade base` then `upgrade head` (40 migrations), seed ok, pytest 596 passed 1 failed; that failure is this task. After the fix: pytest 598 passed 0 skipped - migrations round-trip ok (0040 -> 0039 -> 0040) - frontend build ok - seed ok
**Notes:**
- **The canary caught a real bug, not a flaky test.** Every print job one payment writes shares that transaction's `now()`, so the front printer's queue fell back to ordering by a random UUID. Roughly one sale in two printed the Bar slip before the customer's receipt. Now, within one moment, the receipt comes first, then the slips, then BATAL notices, then anything else. The till's queue lists them the same way.
- The new test pays six orders and requires the front printer to pull `receipt, bar_ticket` six times in a row. It failed against the old ordering (checked by reverting the ordering locally) and passed five of five runs with the fix.
- **Local DB housekeeping, stated for the record:** while landing prt-1 the gates first ran pytest before `alembic upgrade`, and 99 fixture businesses were left half-created (no chart or posting rules) and tripped two invariant tests. They were first completed with `ensure_standard_chart`/`ensure_standard_rules` (additive), then removed by this canary's rebuild from base. No test or application code was touched for it. The gate script now upgrades before testing.
**Deviation:** none. No schema change.
**Next:** bill-1.
