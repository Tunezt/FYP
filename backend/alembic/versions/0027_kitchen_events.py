"""Kitchen display (roadmap M11-T2): ticket states, append-only.

A paid order is a kitchen ticket. Its state — `new` (paid, not started),
`preparing`, `ready`, `done` (bumped off the board) — is the latest row in
`kitchen_events`; nothing is updated, a state change appends a row with who
made it and when. `new` is the absence of rows. The board is every completed
order of the last hours whose latest event is not `done`.

Additive. RLS in this migration.

Revision ID: 0027
Revises: 0026
Create Date: 2026-09-06
"""
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
create type kitchen_state as enum ('new', 'preparing', 'ready', 'done');

create table kitchen_events (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  order_id uuid not null references orders(id),
  state kitchen_state not null,
  staff_id uuid references staff(id),
  created_at timestamptz not null default now()
);
create index idx_kitchen_events_order on kitchen_events(business_id, order_id, created_at desc);
"""

RLS_SQL = """
alter table kitchen_events enable row level security;
alter table kitchen_events force row level security;
create policy tenant_isolation on kitchen_events
  using (business_id = current_setting('app.current_business_id')::uuid)
  with check (business_id = current_setting('app.current_business_id')::uuid);
"""

DOWNGRADE_SQL = """
drop table if exists kitchen_events;
drop type if exists kitchen_state;
"""


# ── statement helpers, copied from 0001_initial_schema.py (roadmap §1.7) ──────

def _split_statements(sql: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    in_comment = False
    in_string = False
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if in_comment:
            current.append(ch)
            in_comment = ch != "\n"
        elif in_string:
            current.append(ch)
            if ch == "'":
                in_string = False
        elif ch == "-" and i + 1 < n and sql[i + 1] == "-":
            current.append(ch)
            in_comment = True
        elif ch == "'":
            current.append(ch)
            in_string = True
        elif ch == ";":
            statements.append("".join(current))
            current = []
        else:
            current.append(ch)
        i += 1
    if current:
        statements.append("".join(current))
    return statements


def _has_sql(statement: str) -> bool:
    for line in statement.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("--"):
            return True
    return False


def _execute_statements(sql: str) -> None:
    for statement in _split_statements(sql):
        if _has_sql(statement):
            op.execute(statement)


def upgrade() -> None:
    _execute_statements(SCHEMA_SQL)
    _execute_statements(RLS_SQL)


def downgrade() -> None:
    _execute_statements(DOWNGRADE_SQL)
