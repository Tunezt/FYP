# Warung Pintar

AI business management system for small Indonesian F&B businesses. Three interfaces on one
backend: WhatsApp assistant (owner), POS kiosk (staff), web dashboard (owner).

## How to work in this repo

**Every session, before anything else:**

1. Read `docs/BUILD-ROADMAP.md` in full.
2. Read `docs/progress.md` to find the last completed task ID.
3. Find the first task whose `blocked_by` is satisfied and which is not marked done.
4. Announce that task ID. Work on **only** that task.
5. Run every gate in roadmap §2. All must pass.
6. Append a progress entry (roadmap §6), then commit as `[<task-id>] <description>`.
7. Continue to the next task. Tag `checkpoint/<milestone>` at milestone boundaries.
8. Every 5 tasks, run the canary in roadmap §0.3.

**One task per commit. Never batch. A failing gate means you do not commit.**

Stop and write a `NEEDS HUMAN` entry in `docs/progress.md` if you hit any condition in roadmap §3.

## Hard rules (full detail in roadmap §1)

- **Never weaken a test to make it pass.** No deleting tests, no loosening assertions, no skip
  markers, no narrowing scope. If a test fails the code is wrong until proven otherwise. If you
  believe a test encodes wrong behaviour, that is a stop condition, not a judgement call.
- Every new table with `business_id` gets the `tenant_isolation` RLS policy **in the same migration
  that creates it**, using the template in `alembic/versions/0001_initial_schema.py`. Tenant
  isolation is a graded requirement. `businesses` and `login_otps` are deliberate exceptions with
  RLS disabled by design — do not "fix" them.
- `DATABASE_URL` stays on the restricted `app_role`. The `postgres` role has BYPASSRLS and using it
  silently disables every policy while every test still passes. `MIGRATION_DATABASE_URL` is the
  elevated role, alembic only.
- **Money is `numeric(12,2)`, quantities are `numeric(12,3)`. This is correct — do not convert
  anything to BIGINT or integer.** The only rule is that no money or quantity column may ever be
  `real` or `double precision`.
- **Migrations cannot use a single `op.execute()` with multiple statements.** The asyncpg dialect
  executes via PREPARE and Postgres refuses multi-command prepares. Reuse `_split_statements`,
  `_has_sql` and `_execute_statements` from `0001_initial_schema.py`. Enums go in raw SQL in the
  migration and are declared `Enum(..., create_type=False)` in `models.py`.
- `models.py` is a 1:1 mirror of the migration DDL and must not drift. A migration that adds a
  table adds its model in the same commit.
- **No free-form text-to-SQL.** The fixed tool set is deliberate. Growth means adding named tools
  with fixed signatures, never handing the model a schema.
- Nothing is deleted. Voids, refunds and corrections write reversing rows.
- Schema changes are additive. The single authorised structural change is M3-T2. If you think you
  need another, stop and ask.
- User-facing error text is Indonesian. An AST test enforces this.
- The Gemini key may be in `AQ.<...>` format. That is valid. Do not "fix" it.

## Not in scope

No multi-outlet. No attendance tracking. No payroll disbursement. No real marketplace integrations.
No payment gateway processing. No hardware drivers. No native mobile apps.

## Commands

```bash
# backend (Windows entrypoint — not bare uvicorn)
cd backend && ./.venv/Scripts/python.exe dev.py

# frontend
cd frontend && npm run dev

# gates — all four must pass before any commit
cd backend && ./.venv/Scripts/python.exe -m pytest -q
cd backend && alembic upgrade head && alembic downgrade -1 && alembic upgrade head
cd frontend && npm run build
cd backend && ./.venv/Scripts/python.exe -m app.seed
```

Test count must never decrease. The 4 DB-integration tests must not skip; if they do, local
Postgres is not running and you are flying blind.

## Never

- Never point `DATABASE_URL` at an elevated role, even temporarily, even to debug.
- Never delete or rewrite `docs/progress.md`. Append only.
- Never `git push --force`, rewrite pushed history, or `git checkout .` on a dirty tree.
- Never delete or move a `checkpoint/*` tag.
- Never stub an external service and report it as working.
- Never invent credentials. Meta, Supabase, Railway and Vercel access are stop conditions.
- Never mark a task done with a failing or skipped gate.
