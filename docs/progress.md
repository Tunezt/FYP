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
