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
