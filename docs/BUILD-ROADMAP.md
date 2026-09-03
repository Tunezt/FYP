# Warung Pintar — Autonomous Build Roadmap

**Version 2** · 2 September 2026. Supersedes v1 entirely.
**Target:** the core of majoo's SME operating system, plus the things majoo cannot do.

v2 was written after reading the actual schema. Several v1 assumptions were wrong and one of them
would have caused a destructive migration. §4.0 records what is now verified fact rather than
inference. **Read this whole file before touching code.**

This is designed for long unattended runs. You will not finish it in one session. Work until you
hit a stop condition, then stop cleanly.

---

## 0. Operating protocol

### 0.1 The loop

Each session:

1. Read `docs/progress.md` to find the last completed task ID.
2. Find the **first task whose `blocked_by` is satisfied and which is not marked done.**
3. Announce the task ID. Work only on that task.
4. Implement it.
5. Run **every gate in §2**. All must pass.
6. Append a progress entry (§6) to `docs/progress.md`.
7. Commit as `[<task-id>] <short description>`.
8. Go to 2. Keep going until a stop condition in §3.

**One task per commit. Never batch.** If a task is bigger than it looked, split it, record the
split in `docs/progress.md`, and do the first half.

### 0.2 Checkpoints

After the last task of each milestone, tag it:

```bash
git tag checkpoint/M2 && git log --oneline -1
```

Tags make a bad run recoverable with one command instead of unpicking forty commits. Never delete
or move a checkpoint tag.

### 0.3 Canary

**Every 5 tasks, re-run the full gate suite from a clean state** before starting the next task:

```bash
git stash list          # must be empty
alembic downgrade base && alembic upgrade head
python -m app.seed
pytest -q
```

This catches the slow rot that per-task gates miss: a migration that only works when applied on
top of the previous state, a seed that depends on leftover rows, a test that passes only because
another test ran first.

### 0.4 Budget

You are running on metered credit. Do not burn it re-reading the whole repo every session. Read
this file, `docs/progress.md`, and only the files the current task touches. If you find yourself
exploring rather than implementing, you have lost the thread. Go back to the task definition.

---

## 1. Invariants — never violate these

### 1.1 Every business-scoped table gets RLS

The pattern already exists in `alembic/versions/0001_initial_schema.py`:

```python
RLS_SQL_TEMPLATE = """
alter table {table} enable row level security;
alter table {table} force row level security;
create policy tenant_isolation on {table}
  using (business_id = current_setting('app.current_business_id')::uuid)
  with check (business_id = current_setting('app.current_business_id')::uuid);
"""
```

**Every new table with a `business_id` gets exactly this, in the same migration that creates it.**
Not a follow-up migration. Tenant isolation is a graded requirement and it is enforced at the
database, not in the UI. A table without a policy is a silent cross-business data leak.

Note the deliberate exceptions already in place: `businesses` (no `business_id`, it *is* the
tenant) and `login_otps` (keyed by phone, may predate any business). Both have RLS explicitly
disabled so behaviour does not depend on a Supabase dashboard toggle. Do not "fix" either.

### 1.2 `DATABASE_URL` stays on the restricted `app_role`

The `postgres` role has `BYPASSRLS`. Pointing `DATABASE_URL` at it disables every policy in the
system **and every isolation test still passes**, because the test connects as the same role. This
bug has already been found and fixed once. Do not reintroduce it, not even temporarily to debug.

`MIGRATION_DATABASE_URL` is the elevated role and alembic is the only thing that uses it.

### 1.3 Money is exact decimal — and it already is

**Corrected in v2.** This codebase uses `numeric(12,2)` for money and `numeric(12,3)` for
quantities. That is exact fixed-point arithmetic and it is **correct**. Postgres `numeric` does not
have floating-point rounding error.

**Do not convert money columns to `BIGINT`.** v1 of this document said to. That was written before
the schema was read and it was wrong. A mass type migration here would be pure risk for zero gain.

The actual rule: **no money or quantity column may ever be `FLOAT`, `REAL` or `DOUBLE PRECISION`.**
New columns follow the existing convention: `numeric(12,2)` for rupiah, `numeric(12,3)` for
quantities, `numeric(14,4)` for computed statistics. M0-T4 adds a test that enforces this.

### 1.4 No free-form text-to-SQL, ever

The fixed tool set is deliberate and it is the strongest technical argument the project has.
Published benchmarks put raw text-to-SQL near 64% on realistic multi-table schemas, and its failure
mode is a plausible wrong answer rather than an error. A defined tool or metric layer moves that
above 95% and fails loudly instead.

The tool set grows by **adding named tools with fixed signatures**. It never grows by handing the
model a schema and letting it write queries.

### 1.5 Nothing is ever deleted

Voids, refunds, corrections and adjustments write **reversing rows**. `DELETE` does not appear in
application code against business data. This is what makes the ledger auditable.

### 1.6 Schema changes are additive, with one authorised exception

The existing 11 tables keep working. New capability arrives as new tables.

The single authorised structural change is **M3-T2**, converting `sales` from a table into a view
over the new order model. It is planned, has a tested rollback, and is the only one. **If you
believe another is necessary, that is a stop condition (§3), not a judgement call.**

### 1.7 Migration authoring pattern — this will bite you

Migrations in this repo **cannot** use a single `op.execute()` with multiple statements. SQLAlchemy's
asyncpg dialect executes via `PREPARE`, and Postgres refuses to prepare a string containing more
than one command. This was found the hard way.

**Every new migration reuses the helpers already in `0001_initial_schema.py`:** `_split_statements`,
`_has_sql`, `_execute_statements`. Import them or copy them. Write your DDL as one SQL string and
pass it to `_execute_statements`.

Enums follow the same split: created in raw SQL in the migration, declared in `models.py` with
`Enum(..., create_type=False)`. Do not let SQLAlchemy manage enum creation.

`models.py` is a 1:1 mirror of the migration DDL and **must not drift from it**. Every migration
that adds a table adds the matching model in the same commit.

### 1.8 Never weaken a test to make it pass

If a test fails, the code is wrong until proven otherwise. **You may not delete a test, loosen an
assertion, add a skip marker, or narrow a test's scope in order to get a green suite.** Doing that
converts a real bug into a permanent invisible one, and on an unattended run nobody is watching.

If you genuinely believe a test encodes wrong behaviour, that is a stop condition. Write it down
and stop.

An invariant test failure (§2) is never flaky. Treat it as data corruption until you have proven
otherwise.

### 1.9 Proactive WhatsApp messages require an approved template

Free-form replies are legal only inside the 24-hour window opened by an inbound message. The
nightly job runs outside it. Meta's rule, not a design choice.

### 1.10 User-facing error text is Indonesian

An AST-based regression test already enforces this. Keep it passing.

### 1.11 The Gemini key may look wrong and be right

The newer `AQ.<...>` format is valid. Do not "fix" it.

---

## 2. Verification gates

Before **every** commit:

```bash
# 1. Tests — count must never decrease, zero failures, zero skips after M0-T1
cd backend && ./.venv/Scripts/python.exe -m pytest -q

# 2. Migrations apply and reverse
cd backend && alembic upgrade head && alembic downgrade -1 && alembic upgrade head

# 3. Frontend builds and typechecks
cd frontend && npm run build

# 4. Seed works
cd backend && ./.venv/Scripts/python.exe -m app.seed
```

Rules:

- Test count **never decreases**.
- The 4 DB-integration tests must **not** skip after M0-T1. If they skip, local Postgres is down
  and you are flying blind. Fix that before anything else.
- **Any new table with `business_id` needs a test proving business A cannot read business B's rows
  in it.** No RLS test, no commit.
- Any new money-moving path needs a test asserting the resulting numbers, not just a 200 response.

---

## 3. Stop conditions

Stop, append a `NEEDS HUMAN` entry to `docs/progress.md`, end the run:

- A task needs credentials that do not exist (Meta, Supabase, Railway, Vercel).
- An existing table needs a destructive change not authorised in §1.6.
- An invariant test in `test_invariants.py` fails and you cannot explain why in one sentence.
- You believe a test encodes wrong behaviour (§1.8).
- Three consecutive attempts at the same gate failure have not fixed it.
- Behaviour is genuinely ambiguous and guessing wrong is expensive to unwind: rounding rules, tax
  treatment, whether a refund reduces revenue or increases an expense.

**Do not retry in a loop. Do not invent credentials. Do not stub an external service and report it
as working.** Write the question and stop.

---

## 4. Context

### 4.0 Verified repo facts

Read from the source on 2 September 2026. Trust these over your own assumptions.

| Fact | Detail |
|---|---|
| Tables | 11: `businesses`, `staff`, `items`, `sales`, `expenses`, `receipts`, `alerts`, `metric_baselines`, `request_logs`, `pending_confirmations`, `login_otps` |
| Primary keys | `uuid` with `gen_random_uuid()`, except `request_logs` (`bigint identity`) and `login_otps` (`phone`) |
| Money type | `numeric(12,2)`. **Exact. Correct. Leave it.** |
| Quantity type | `numeric(12,3)`, so 1 gram is representable when stocking in kg |
| Stock guard | `items.current_stock` has `check (current_stock >= 0)` **plus** the atomic conditional UPDATE. Both stay. |
| RLS | 9 tables carry `tenant_isolation`. `businesses` and `login_otps` have RLS explicitly disabled, by design. |
| Multi-outlet | **Does not exist.** `items` and `sales` hang directly off `business_id`. |
| `sales` shape | One item per row. No order concept, no multi-line, no multi-payment. |
| Cost snapshot | **Missing.** `sales` has no `unit_cost_at_sale`, so historical margin drifts as `items.cost_price` changes. Real bug, fixed in M3-T1. |
| Raw materials | No separation from sellable products. Both are `items`. Recipes will treat a raw material as an item with `sell_price = 0`. |
| Migrations | 2: `0001_initial_schema`, `0002_request_logs_cascade` |
| Enums | `staff_role`, `expense_source`, `alert_type`, `alert_severity`. Raw SQL in migration, `create_type=False` in models. |

### 4.1 Explicit non-goals

Do not build these. They are scope creep dressed as completeness.

- **Multi-outlet.** One business, one location. No `outlets` table, no `outlet_id` columns.
- **Attendance tracking.** Out of scope by the project brief.
- **Payroll disbursement.** No money movement to staff.
- **Real marketplace integrations.** Shopee, GrabFood and GoFood need signed partner agreements.
- **Payment gateway integration.** Record the payment method; do not process payments.
- **Hardware drivers.** Browser print only.
- **Native mobile apps.** PWA plus WhatsApp.

### 4.2 Two things that change the plan

**The Supabase outage is not blocking.** Development needs a Postgres with pgvector and RLS, not
Supabase specifically. M0-T1 stands one up in Docker. After that the 4 skipped integration tests
come back and the whole roadmap runs without touching the owner's account. Supabase becomes a
deploy-time concern.

**The supplier-invoice photo feature is mostly already built.** The vision path exists: photo, ack,
private storage, Gemini Pro structured parse with a confidence signal, `pending_confirmations` gate,
correction flow. Pointing it at a supplier invoice so it produces a draft **goods receipt** rather
than only an expense is M5-T4, sitting on a large amount of finished work.

### 4.3 Target architecture

```
  WhatsApp ──┐
  POS kiosk ─┼──▶  FastAPI  ·  domain services
  Dashboard ─┘              │  emits domain events
                            ▼
              ┌─────────────────────────────────┐
              │  POSTING ENGINE (single writer) │
              │  one DB transaction, all-or-none│
              └──┬───────────┬──────────────────┘
                 ▼           ▼
          stock_movements  journal_lines
             (append)       (balanced)
                 │           │
                 ▼           ▼
        items.current_stock  account balances
        (cached projection)  (derived, never typed)

              ┌─────────────────────────────────┐
              │  METRIC LAYER (~20 metrics)     │
              │  the ONLY analytical read path  │
              └──┬───────────┬──────────────────┘
                 ▼           ▼
         WhatsApp tools   Dashboard widgets
```

Two rules fall out and they are the whole design:

- **One writer.** Everything moving stock or money goes through the posting engine in one
  transaction. If the sale commits, its stock movement and journal lines committed too, or nothing
  did.
- **One reader.** Everything analytical reads through the metric layer, so the dashboard and the
  assistant cannot disagree about profit.

---

## 5. Milestones

Task IDs are stable. Never renumber.

---

### M0 — Green environment

**M0-T1 · Local Postgres with pgvector** — `blocked_by: none`
`docker-compose.yml` at repo root running `pgvector/pgvector:pg16` with a named volume.
`backend/.env.example` documenting both URLs against it. `scripts/db-bootstrap.sql` creating
`app_role` with grants and default privileges, mirroring the Supabase restore sequence so local and
production behave identically.
**Done when:** `docker compose up -d`, `alembic upgrade head`, `python -m app.seed` succeed and
`pytest -q` reports **71 passed, 0 skipped**.

**M0-T2 · Document the local-first workflow** — `blocked_by: M0-T1`
README and `docs/progress.md`. Local Postgres is the development default; Supabase is a deploy
target. Note `python dev.py` is the Windows entrypoint, not bare uvicorn.
**Done when:** a fresh clone reaches a green suite following only the README.

**M0-T3 · Review and commit the uncommitted tree** — `blocked_by: M0-T1`
Per `PROJECT-STATUS.md` §7: `db_errors.py`, `dev.py`, migration `0002`, `ThemeToggle`,
`lib/itemCategory.ts`, 15 product photos, `bg-ambient.jpg`, `ErrorState`, `ItemIcon`,
`formatCompactRupiah`, the Ringkasan period control, six page edits.
**Done when:** `git status` clean, all gates pass.

**M0-T4 · Numeric type guard** — `blocked_by: M0-T1`
**This is a test, not a migration.** Add `backend/tests/test_invariants.py::test_no_float_money`.
Query `information_schema.columns` for every column whose name matches money or quantity patterns
(`amount`, `price`, `total`, `cost`, `stock`, `quantity`, `threshold`) and assert `data_type` is
`numeric`, never `real` or `double precision`.
**Do not convert anything.** The existing `numeric(12,2)` and `numeric(12,3)` are correct.
**Done when:** the test passes today and would fail if someone added a float column.

**M0-T5 · RLS coverage invariant** — `blocked_by: M0-T4`
Add `test_invariants.py::test_all_scoped_tables_have_rls`. Query `information_schema.columns` for
tables with a `business_id` column, cross-check against `pg_policies` for a `tenant_isolation`
policy. Explicit allowlist for the two by-design exceptions: `businesses`, `login_otps`.
**Done when:** it passes today and fails if you add a policy-less scoped table.

**M0-T6 · Verify `request_logs` under RLS** — `blocked_by: M0-T5`
`request_logs.business_id` is nullable but the table carries `tenant_isolation` with a `with check`
clause. A row with a NULL `business_id` should fail that check. Determine what actually happens when
an unauthenticated request is logged. Either it never happens, or logging uses a different path, or
this is a live bug swallowing instrumentation.
**Done when:** `docs/progress.md` records which of the three it is, with a test covering the
unauthenticated-request logging path.

---

### M1 — Prove the vision path

*The highest-value unknown in the project, and it needs no external credentials.*

**M1-T1 · Baseline on the staged receipts** — `blocked_by: M0-T1`
Run all 3 photos in `docs/vision-test-samples/` through the real vision path. Record raw model
output, parsed structure, confidence signal, whether the confirmation gate triggered.
**Do not tune the prompt before recording the baseline.** The unedited first result is the evidence.
**Done when:** `docs/vision-results.md` exists with per-image results including failures.

**M1-T2 · Expand the sample set** — `blocked_by: M1-T1`
At least 15 images: printed thermal, handwritten, blurred, angled, and one deliberately unreadable
control. Label anything generated as generated.
**Done when:** accuracy reported per category with sample counts stated.

**M1-T3 · Tune, then re-measure** — `blocked_by: M1-T2`
Now improve the prompt and confidence threshold. Re-run the full set.
**Done when:** before and after sit side by side in `docs/vision-results.md`, and **the confirmation
gate triggers on every low-confidence read.** A wrong number written silently is worse than a
question asked.

---

### M2 — Stock movement ledger

*`items.current_stock` stops being the truth and becomes a cache over auditable history.*

**M2-T1 · Create `stock_movements`** — `blocked_by: M0-T6`
Append-only. `id uuid`, `business_id uuid not null`, `item_id uuid not null`,
`qty_delta numeric(12,3) not null` (signed), `reason` (new enum: `sale`, `sale_void`, `refund`,
`purchase`, `waste`, `production_in`, `production_out`, `opname`, `correction`),
`source_type text`, `source_id uuid`, `unit_cost numeric(12,2)`, `staff_id uuid`, `created_at`.
RLS policy in the same migration. Index on `(business_id, item_id, created_at desc)`.
Follow §1.7 for the migration and add the matching model in the same commit.
**Done when:** table, policy and model exist; M0-T5 still passes.

**M2-T2 · Route every stock change through it** — `blocked_by: M2-T1`
Find every write to `items.current_stock`. Each also writes a `stock_movements` row **in the same
transaction**.
**Keep the atomic conditional UPDATE and the `check (current_stock >= 0)` constraint.** They remain
the concurrency guard. The movement row is written alongside, not instead. The existing race test
must pass unchanged.
**Done when:** the race test passes and a new test asserts a movement row for every path.

**M2-T3 · The reconciliation invariant** — `blocked_by: M2-T2`
Add to `test_invariants.py`: for every item,
`SUM(stock_movements.qty_delta) == items.current_stock`.
**Done when:** it passes after seeding and after a simulated day of sales, a correction and a void.
This is your early-warning system for everything downstream. A failure here is a stop condition.

**M2-T4 · Backfill history** — `blocked_by: M2-T3`
Migration generating movement rows from existing `sales` and stock-affecting `receipts`, so the
invariant holds for pre-existing data. Where cost is unknown write `NULL` unit_cost, never a guess.
**Done when:** M2-T3 passes against a database seeded with pre-migration data.

---

### M3 — Order model

**M3-T1 · Create `orders`, `order_lines`, `payments`** — `blocked_by: M2-T4`
**No outlet or terminal columns** (§4.1).
`orders`: `business_id`, `staff_id`, `customer_id` (nullable, forward reference to M8),
`order_type` (new enum: `dine_in`, `takeaway`, `delivery`, `pickup`), `status`, `subtotal`,
`discount_total`, `tax_total`, `service_charge`, `rounding`, `total`, `sold_at`, `created_at`.
`order_lines`: `order_id`, `item_id`, `variant_id` (nullable, forward reference to M4), `quantity
numeric(12,3)`, `unit_price`, `line_discount`, `line_total`, **`unit_cost_at_sale numeric(12,2)`**,
`notes`.
`payments`: `order_id`, `method` (new enum), `amount`, `reference`. **Many-to-one on order**, which
is what makes split payment fall out rather than be a feature.
RLS on all three.
**`unit_cost_at_sale` fixes a real existing bug:** today margin on an old sale changes whenever
`items.cost_price` changes.
**Done when:** tables, policies and models exist; M0-T5 passes.

**M3-T2 · `sales` becomes a view** — `blocked_by: M3-T1`
The one authorised structural change. In one migration:
1. `alter table sales rename to sales_legacy`
2. Backfill `orders` / `order_lines` / `payments` from `sales_legacy`
3. `create view sales as select ... from order_lines join orders ...`, shaped **exactly** like the
   old table (`id, business_id, item_id, quantity, unit_price, total_price, staff_id, sold_at`) so
   every existing read keeps working
4. Point all write paths at the order model

`sales` becomes read-only. The 8 assistant tools that read it keep working untouched, which is the
entire point.
**Note:** `sales` currently carries an RLS policy. A view does not inherit one. Either create the
view with `security_invoker = true` so the underlying tables' policies apply, or verify isolation
another way. **Add an explicit RLS test on the view.**
**Rollback:** downgrade drops the view and renames `sales_legacy` back. Gate 2 will run it, so it
must work.
**Done when:** all gates pass, no tool code changed, downgrade/upgrade round-trips cleanly, and the
view has a passing isolation test.

**M3-T3 · POS writes orders** — `blocked_by: M3-T2`
Kiosk and API build a multi-line order with payments. Atomic stock guard per line.
**Done when:** a two-item sale paid half cash half QRIS produces one order, two lines, two payments,
two stock movements, and the reconciliation invariant holds.

**M3-T4 · Void and refund as reversals** — `blocked_by: M3-T3`
Both write reversing order lines and reversing stock movements, and require a manager PIN. Nothing
deleted, nothing updated in place.
**Done when:** voiding returns stock to its prior level **via a new movement row**, and the original
order is still readable in full.

---

### M4 — Catalogue depth

*Where majoo's real moat is, and where clones usually stop.*

**M4-T1 · Variants** — `blocked_by: M3-T4`
`item_variants` child of `items`: `name`, `sku`, `sell_price numeric(12,2)`,
`cost_price numeric(12,2)`, `is_default`, `is_active`. `items` untouched; single-variant products
keep working through a default variant created by migration for every existing item.
**Done when:** an item with three sizes sells at three prices and reporting rolls up to the parent.

**M4-T2 · Modifiers** — `blocked_by: M4-T1`
`modifier_groups`, `modifiers`, `order_line_modifiers`. Priced or free, single or multi select,
required or optional.
**Done when:** "extra shot, less sugar" persists on the line, prices correctly, prints on the
receipt.

**M4-T3 · Units and conversion** — `blocked_by: M4-T2`
`items.unit` is currently free text. Add `uoms` and `uom_conversions` and a nullable `uom_id` on
items, leaving `unit` in place for compatibility. Stock in kg, consume in g.
**Done when:** consuming 250g from 5kg leaves 4.750, proven by a test crossing the unit boundary,
with `numeric(12,3)` precision respected.

**M4-T4 · Recipes** — `blocked_by: M4-T3`
`recipe_lines` keyed on **variant, not item** (a large latte uses more milk): `variant_id`,
`component_item_id`, `quantity`, `uom_id`.
**Done when:** selling one large latte writes stock movements for beans and milk in the right
quantities and M2-T3 still holds.

**M4-T5 · Moving-average COGS** — `blocked_by: M4-T4`
Recompute average cost on every goods receipt. Snapshot onto `order_lines.unit_cost_at_sale` at
sale time. **Never join to current cost for historical margin.**
**Done when:** a test buys at two prices, sells, asserts exact COGS, and proves gross margin on an
old order does not move when today's cost changes.

**M4-T6 · Bulk catalogue import** — `blocked_by: M4-T5`
Extend the existing openpyxl path to variants, modifiers, uoms and recipes. Validate before
writing, report row-level errors, all or nothing.
**Done when:** a 200-row template with deliberate errors in 5 rows imports nothing and names all 5.

---

### M5 — Purchasing and the camera

**M5-T1 · Suppliers** — `blocked_by: M4-T6`
`suppliers` with contact details and purchase history.

**M5-T2 · Purchase orders** — `blocked_by: M5-T1`
`purchase_orders`, `po_lines`. States: draft, ordered, partially received, received, cancelled.

**M5-T3 · Goods receipt** — `blocked_by: M5-T2`
`goods_receipts` and lines. Receiving emits `purchase` stock movements and updates moving-average
cost. Partial and over-receipt handled explicitly.
**Done when:** receiving 8 of 10 leaves the PO partially received and M2-T3 holds.

**M5-T4 · Supplier invoice photo to draft goods receipt** — `blocked_by: M5-T3`
**The differentiator. Reuses the vision path that already exists.**
Photo, ack, private storage, Gemini Pro parse into supplier plus line items plus quantities plus
prices, fuzzy-match each line to an existing item, park in `pending_confirmations`, owner confirms
over WhatsApp, then create the goods receipt.
**Never write stock from a photo without confirmation.** Unmatched lines are surfaced as questions,
never guesses.
**Done when:** an invoice photo produces a draft confirmed with one reply, with a test covering the
unmatched-line path.

**M5-T5 · Menu photo to draft catalogue** — `blocked_by: M5-T4`
Same machinery pointed at onboarding. Photograph a menu, extract products, variants and prices,
review, confirm, create.
**Done when:** a photographed menu produces a reviewable draft catalogue.

---

### M6 — The ledger

**M6-T1 · Accounts** — `blocked_by: M5-T5`
`accounts` with type (asset, liability, equity, revenue, expense) and a seeded Indonesian SME chart
of accounts. Merchant-extendable.

**M6-T2 · Journal with a balance constraint** — `blocked_by: M6-T1`
`journal_entries`, `journal_lines`. **Enforce debits equal credits with a deferred database
constraint, not application code.** Application-level checks get bypassed eventually; a constraint
does not.
**Done when:** an unbalanced entry raises at the database, proven by a test.

**M6-T3 · Posting rules** — `blocked_by: M6-T2`
A table mapping event type to debit and credit accounts. Seed: sale, discount, tax collected, COGS,
void, refund, goods receipt, supplier payment, waste, opname variance, expense.
**Done when:** rules are data, not `if` statements.

**M6-T4 · The posting engine** — `blocked_by: M6-T3`
Single writer. Consumes domain events, writes stock movements and journal lines **in the same
transaction as the originating change**.
**Done when:** a test forces journal writing to fail and asserts the sale rolled back too. Prove
it, do not assert it by inspection.

**M6-T5 · Statements** — `blocked_by: M6-T4`
Profit and loss, then balance sheet.
**Done when:** a test seeds a known week and asserts exact figures, and "balance sheet balances"
joins `test_invariants.py`.

**M6-T6 · Expenses into the ledger** — `blocked_by: M6-T5`
Route the existing `record_expense` tool through the posting engine.
**Done when:** an expense recorded over WhatsApp appears in the P&L.

---

### M7 — Shift and till discipline

**M7-T1 · Shifts** — `blocked_by: M6-T6`
`shifts`: staff, opening float, open and close times, expected cash, counted cash, variance.

**M7-T2 · Cash in and out** — `blocked_by: M7-T1`
Petty cash, supplier paid in cash, bank drop. Each posts to the ledger.

**M7-T3 · Close with reconciliation** — `blocked_by: M7-T2`
Expected cash = float + cash payments + cash in − cash out. Variance recorded and posted.
**Done when:** a simulated shift closes with a correct variance and a matching journal entry.

**M7-T4 · Discounts, tax, service charge, rounding** — `blocked_by: M7-T3`
Line and bill discounts behind a permission gate. Tax inclusive or exclusive. Service charge before
or after tax, configurable. Rupiah rounding at total.
**Write the test table of 12 combinations first**, then make it pass. This is where being off by
one rupiah compounds into a broken ledger.

---

### M8 — Customers, points, promos

**M8-T1 · Customers** — `blocked_by: M7-T4`
Name, phone, address, birthday. Attach to order. Phone is the natural key and also the WhatsApp
identity, which matters later.

**M8-T2 · Points ledger** — `blocked_by: M8-T1`
Append-only, same pattern as stock. Earn rules, redemption as a payment method, balance is a
derived sum with an invariant test.

**M8-T3 · Promo engine** — `blocked_by: M8-T2`
`promos`, `promo_conditions`. Discount or bonus product. Conditions: date range, day of week, time
window, minimum spend, multiples. Auto activate and expire.
**Done when:** a BOGO promo applies at the till, posts its cost to the ledger, and stops applying
the moment its window closes.

**M8-T4 · Vouchers** — `blocked_by: M8-T3`
Single and bulk codes, expiry, single-use under concurrency.
**Done when:** two simultaneous redemptions of one code produce exactly one success. Same test
shape as the stock race.

---

### M9 — Metric layer

*The assistant and the dashboard become physically incapable of disagreeing.*

**M9-T1 · Metric registry** — `blocked_by: M8-T4`
Declarative. Each metric: name, Indonesian and English description, dimensions, supported time
grains, one implementation. Nothing else in the app computes these numbers.
Seed: `revenue`, `gross_profit`, `gross_margin_pct`, `cogs`, `transaction_count`, `average_ticket`,
`item_units_sold`, `stock_on_hand`, `stock_days_remaining`, `waste_value`, `expense_total`,
`net_profit`, `cash_variance`, `discount_cost`, `promo_cost`, `new_customers`, `repeat_rate`,
`top_items_by_revenue`, `top_items_by_margin`, `peak_hour`.

**M9-T2 · Point the existing 8 tools at the registry** — `blocked_by: M9-T1`
`get_sales_summary`, `get_profit`, `compare_periods` and the rest stop computing and start calling
metrics.
**Done when:** behaviour unchanged, all existing tests pass. Pure refactor, zero behavioural diff.

**M9-T3 · Point the dashboard at the registry** — `blocked_by: M9-T2`
Every dashboard number comes from the same metric endpoint the assistant uses.
**Done when:** a test asserts the WhatsApp answer and the dashboard widget return the identical
figure for the same period. **This test is the thesis.**

**M9-T4 · Grow the tool set** — `blocked_by: M9-T3`
`get_purchase_history`, `get_supplier_prices`, `get_recipe_cost`, `get_shift_summary`,
`get_customer_summary`, `get_promo_performance`, `draft_purchase_order`. Fixed signatures over the
registry. Still no free-form SQL.

**M9-T5 · Explicit refusal** — `blocked_by: M9-T4`
When no metric or tool matches, the assistant says so in the owner's language and offers what it
can answer. It never improvises a number.
**Done when:** a set of out-of-scope questions produces refusals and zero fabricated figures.

**M9-T6 · Evaluation harness** — `blocked_by: M9-T5`
Question set in real code-switched Indonesian, Malay and English with typos. Score answer accuracy,
refusal rate, and **silent-error rate**, which is the one that matters. Implement a naive
text-to-SQL baseline behind a flag purely for comparison.
**Done when:** `docs/evaluation.md` reports both with the silent-error gap stated plainly.

---

### M10 — Proactive intelligence

**M10-T1 · Exception rules over metrics** — `blocked_by: M9-T6`
Rebuild the nightly job on the registry. Margin dropped against baseline; an item will stock out
before the next likely delivery; one staff member's void rate is an outlier; a supplier price moved;
takings anomalous against the 30-day z-score.
**Done when:** each rule has a test manufacturing the condition and asserting exactly one alert.

**M10-T2 · Alert quality, not quantity** — `blocked_by: M10-T1`
Deduplicate, rate limit, suppress an alert that is the same alert as yesterday.
**Done when:** a simulated week of stable data produces **zero** alerts. Silence when nothing is
wrong is the feature. A muted assistant is worth nothing.

---

### M11 — Channels *(optional, only after M0–M10)*

**M11-T1** QR e-menu at `/menu/{token}`, ordering into the same order queue as the POS.
**M11-T2** Kitchen display with ticket states and a bump action.
**M11-T3** Order type routing so delivery and dine-in behave differently at the till.

---

### M12 — Live integration *(human-blocked, never start alone)*

**M12-T1** New Supabase project, then the restore sequence in `PROJECT-STATUS.md` §5.
**M12-T2** Meta app. **Submit both templates first**, review takes 24h+. Then webhook, allow-list
the demo phone, one live round-trip.
**M12-T3** Live nightly alert to a real handset.
**M12-T4** Railway (API + cron) and Vercel.

---

## 6. Progress entry format

Append to `docs/progress.md`. Never rewrite history.

```markdown
### [M2-T3] Stock reconciliation invariant
**Date:** 2026-09-04
**Status:** done
**Changed:** backend/tests/test_invariants.py, backend/app/services/stock.py
**Gates:** pytest 78 passed 0 skipped · migrations round-trip ok · frontend build ok · seed ok
**Notes:** Found and fixed a pre-existing bug where waste writes skipped the movement row.
**Deviation:** none
**Next:** M2-T4
```

Stop condition:

```markdown
### [M12-T2] Meta app setup
**Status:** NEEDS HUMAN
**Blocker:** No Meta Business account credentials available.
**What I need:** app ID, phone number ID, permanent access token, verify token.
**What I did instead:** moved to M10-T1.
```

---

## 7. Appendix A — event catalogue

Add a row here before adding the code.

| Event | Stock effect | Ledger effect |
|---|---|---|
| `OrderCompleted` | negative per line, recipe-expanded | Dr cash/receivable · Cr revenue · Cr tax · Dr COGS · Cr inventory |
| `OrderVoided` | positive reversal | full reversing entry |
| `OrderRefunded` | positive reversal, optional | Dr revenue · Cr cash |
| `DiscountApplied` | none | Dr discount expense · Cr revenue |
| `GoodsReceived` | positive, recompute avg cost | Dr inventory · Cr payable |
| `SupplierPaid` | none | Dr payable · Cr cash |
| `CashIn` | none | Dr cash · Cr owner capital / bank |
| `BankDrop` | none | Dr bank · Cr cash |
| `StockWasted` | negative | Dr waste expense · Cr inventory |
| `StockCounted` | delta either way | Dr or Cr variance against inventory |
| `ProductionRun` | negative inputs, positive output | inventory reclassification only |
| `ExpenseIncurred` | none | Dr expense · Cr cash/payable |
| `ShiftClosed` | none | Dr or Cr cash variance |
| `PointsEarned` | none | Dr marketing expense · Cr points liability |
| `PointsRedeemed` | none | Dr points liability · Cr revenue |

---

## 8. Appendix B — session start prompt

```
Read docs/BUILD-ROADMAP.md in full, then docs/progress.md.
Identify the first unblocked task not marked done.
Announce the task ID, then implement only that task.
Run every gate in section 2 before committing.
Append a progress entry, commit, tag at milestone boundaries, and continue.
Every 5 tasks run the canary in section 0.3.
Stop and write a NEEDS HUMAN entry if you hit any condition in section 3.
Never weaken a test to make it pass.
```

---

## 9. What done looks like

M0 to M6 is a system a real café could run a real day on, where the books are correct because
nobody typed them. That is already past majoo's Starter tier.

M9 is the part that is not majoo: one definition of every number, an assistant that computes rather
than reports, and a refusal instead of a confident wrong answer.

M10 is the part worth paying for: it stays quiet until something is actually wrong.
