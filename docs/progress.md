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
- **Seed** (`python -m app.seed`): fictional café *Kopi Kenangan Senja*, owner Ibu Ratna (+62 812-0000-1111, PIN 1234) + staff Sari (2345) / Budi (3456), 11 items (sellable drinks/food in servings + raw materials in kg/liter), 30 days of deterministic sales history with weekend bumps and a planted spike yesterday (so anomaly detection has something to find in Phase 5), sample expenses. Idempotent (delete-and-recreate).
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
