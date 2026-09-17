"""`kitchen_line_events` — what is still left to make on a ticket (svc-4).

A ticket with a flat white, two croissants and a toastie is not "preparing" as
one thing: the croissants are out, the toastie is in the press, the flat white
has not been started. `kitchen_events` (M11-T2) records the ticket's state; this
records each line's, so the pass can see what remains and a ticket cannot be
called ready while something on it is unfinished.

Same shape as `kitchen_events`: append-only, the latest row per line wins, who
and when on every row. `done = false` is a correction ("I ticked the wrong
line"), written as a new row, never an update. Only paid lines have rows — the
service refuses anything else.

Additive. RLS in this migration.

Revision ID: 0037
Revises: 0036
Create Date: 2026-09-17
"""
from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
create table kitchen_line_events (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  order_id uuid not null references orders(id),
  order_line_id uuid not null references order_lines(id),
  done boolean not null,
  staff_id uuid references staff(id),
  created_at timestamptz not null default now()
);
create index idx_kitchen_line_events_order on kitchen_line_events(business_id, order_id, created_at);
"""

RLS_SQL = """
alter table kitchen_line_events enable row level security;
alter table kitchen_line_events force row level security;
create policy tenant_isolation on kitchen_line_events
  using (business_id = current_setting('app.current_business_id')::uuid)
  with check (business_id = current_setting('app.current_business_id')::uuid);
"""

DOWNGRADE_SQL = """
drop table if exists kitchen_line_events;
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
