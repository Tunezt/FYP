"""shifts — one cashier's till session (roadmap M7-T1).

A shift is opened with a float and closed with a count. `expected_cash`,
`counted_cash` and `variance` are written once, at close (M7-T3 adds cash
in/out to the expectation and posts the variance). Orders and payments carry
the shift they happened in — payments because a refund paid out during a
later shift leaves *that* till, not the one the sale was made in.

Additive: a new table plus two nullable columns. RLS in this migration.

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-03
"""
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
create type shift_status as enum ('open', 'closed');

create table shifts (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  staff_id uuid not null references staff(id),
  status shift_status not null default 'open',
  opening_float numeric(12,2) not null default 0,
  opened_at timestamptz not null default now(),
  closed_at timestamptz,
  closed_by uuid references staff(id),
  expected_cash numeric(12,2),        -- written at close: float + cash payments (M7-T3: + cash in − cash out)
  counted_cash numeric(12,2),
  variance numeric(12,2),             -- counted − expected
  notes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (opening_float >= 0),
  check (counted_cash is null or counted_cash >= 0),
  check ((status = 'closed') = (closed_at is not null)),
  check (status = 'open' or (expected_cash is not null and counted_cash is not null and variance is not null))
);
create unique index uq_shifts_one_open_per_staff on shifts(business_id, staff_id) where status = 'open';
create index idx_shifts_business_opened on shifts(business_id, opened_at desc);

alter table orders add column shift_id uuid references shifts(id);
alter table payments add column shift_id uuid references shifts(id);
create index idx_orders_shift on orders(shift_id);
create index idx_payments_shift on payments(shift_id);
"""

RLS_SQL = """
alter table shifts enable row level security;
alter table shifts force row level security;
create policy tenant_isolation on shifts
  using (business_id = current_setting('app.current_business_id')::uuid)
  with check (business_id = current_setting('app.current_business_id')::uuid);
"""

DOWNGRADE_SQL = """
drop index if exists idx_payments_shift;
drop index if exists idx_orders_shift;
alter table payments drop column if exists shift_id;
alter table orders drop column if exists shift_id;
drop table if exists shifts;
drop type if exists shift_status;
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
