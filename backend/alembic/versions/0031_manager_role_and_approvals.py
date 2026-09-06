"""Manager role and the override audit trail (roadmap M15-T7).

Void, refund and an over-threshold discount need a manager's PIN. Until now the
only role that could give one was `owner`, and in a real café the owner is not
always on site — a cashier who cannot void a mistake starts doing arithmetic in
their head instead, which is exactly how a ledger loses its connection to
reality.

So `staff_role` gains `manager`: a till role that can approve, and that gets no
more access than a cashier anywhere else (owner scope is issued only to the
phone that owns the business, never from a staff row's role).

`approvals` is the audit trail the override leaves behind. Append-only, one row
per authorisation, naming who approved, what role they held **at the time**,
who asked, what it was worth, and when. A manager signing off a 90% discount at
two in the morning is the thing this table exists to make visible.

Postgres cannot remove a value from an enum, so the downgrade drops the table
and `approval_action` but leaves `manager` in `staff_role` — same precedent as
0025 and 0029. `add value if not exists` keeps the upgrade idempotent. The new
value is only *declared* here, never written, which is what makes it legal in
the same transaction.

Additive. RLS in this migration.

Revision ID: 0031
Revises: 0030
Create Date: 2026-09-06
"""
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
alter type staff_role add value if not exists 'manager';

create type approval_action as enum ('discount', 'void', 'refund');

create table approvals (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  order_id uuid references orders(id) on delete cascade,
  action approval_action not null,
  approved_by uuid not null references staff(id),
  approver_role staff_role not null,
  requested_by uuid references staff(id),
  amount numeric(12,2),
  note text,
  created_at timestamptz not null default now()
);
create index idx_approvals_business_time on approvals(business_id, created_at desc);
create index idx_approvals_order on approvals(business_id, order_id);
"""

RLS_SQL = """
alter table approvals enable row level security;
alter table approvals force row level security;
create policy tenant_isolation on approvals
  using (business_id = current_setting('app.current_business_id')::uuid)
  with check (business_id = current_setting('app.current_business_id')::uuid);
"""

DOWNGRADE_SQL = """
drop table if exists approvals;
drop type if exists approval_action;
-- `manager` stays in staff_role: Postgres has no `alter type ... drop value`,
-- and an enum value nothing writes costs nothing. See the module docstring.
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
        elif ch == "-" and sql[i : i + 2] == "--":
            in_comment = True
            current.append(ch)
        elif ch == "'":
            in_string = True
            current.append(ch)
        elif ch == ";":
            current.append(ch)
            statements.append("".join(current))
            current = []
        else:
            current.append(ch)
        i += 1
    if "".join(current).strip():
        statements.append("".join(current))
    return statements


def _has_sql(statement: str) -> bool:
    return any(line.split("--", 1)[0].strip() for line in statement.splitlines())


def _execute_statements(sql: str) -> None:
    for statement in _split_statements(sql):
        if _has_sql(statement):
            op.execute(statement.strip())


def upgrade() -> None:
    _execute_statements(SCHEMA_SQL)
    _execute_statements(RLS_SQL)


def downgrade() -> None:
    _execute_statements(DOWNGRADE_SQL)
