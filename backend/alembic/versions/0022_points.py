"""points — the loyalty ledger (roadmap M8-T2).

Same pattern as stock: `points_movements` is append-only, one signed row per
change, and `customers.points_balance` is the cached SUM(points_delta) that a
redemption decrements with an atomic conditional UPDATE so two tills cannot
spend the same points twice. Balance is never edited by hand; a correction is
a new row. An invariant test keeps the cache honest.

`loyalty_settings` is one row per business: whether the programme is on, how
many rupiah earn one point, what one point is worth when redeemed, and the
minimum redemption. Off by default; backfilled for existing businesses.

Also backfills the `PointsReversed` posting rule (a void or refund takes back
the points a sale earned, so the liability accrued for them is released).

Additive. RLS in this migration.

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-06
"""
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
create table loyalty_settings (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null unique references businesses(id) on delete cascade,
  is_active boolean not null default false,
  rupiah_per_point numeric(12,2) not null default 1000 check (rupiah_per_point > 0),   -- spend this to earn 1 point
  point_value numeric(12,2) not null default 100 check (point_value >= 0),            -- 1 point pays this much
  min_redeem_points integer not null default 0 check (min_redeem_points >= 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create type points_reason as enum ('earn', 'redeem', 'adjust', 'reversal', 'expire');

create table points_movements (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  customer_id uuid not null references customers(id),
  points_delta integer not null check (points_delta <> 0),
  reason points_reason not null,
  source_type text,                              -- 'order' for earn / redeem / their reversals
  source_id uuid,
  amount numeric(12,2) not null default 0,       -- the rupiah the ledger saw for this row (cost of earn, value redeemed)
  staff_id uuid references staff(id),
  notes text,
  created_at timestamptz not null default now()
);
create index idx_points_movements_customer on points_movements(customer_id, created_at desc);
create index idx_points_movements_source on points_movements(source_type, source_id);

-- The cached balance. No lower bound on purpose: a void that takes back points
-- the customer has already spent leaves a (rare, visible) negative balance
-- rather than a ledger row that could not be written; the redemption guard
-- (balance >= points) means it can never be spent below zero.
alter table customers add column points_balance integer not null default 0;
"""

RLS_SQL = """
alter table loyalty_settings enable row level security;
alter table loyalty_settings force row level security;
create policy tenant_isolation on loyalty_settings
  using (business_id = current_setting('app.current_business_id')::uuid)
  with check (business_id = current_setting('app.current_business_id')::uuid);

alter table points_movements enable row level security;
alter table points_movements force row level security;
create policy tenant_isolation on points_movements
  using (business_id = current_setting('app.current_business_id')::uuid)
  with check (business_id = current_setting('app.current_business_id')::uuid);
"""

BACKFILL_SETTINGS_SQL = """
insert into loyalty_settings (business_id)
select b.id from businesses b
where not exists (select 1 from loyalty_settings l where l.business_id = b.id);
"""

# Frozen copy (services/posting_rules is the living definition).
NEW_RULES = [
    ("PointsReversed", "points", "2300", "5600", "Poin ditarik kembali (void / retur)"),
]

BACKFILL_RULES_SQL = """
insert into posting_rules (business_id, event_type, component, debit_code, credit_code, description, is_system)
select b.id, s.event_type, s.component, s.debit_code, s.credit_code, s.description, true
from businesses b
cross join (values {values}) as s(event_type, component, debit_code, credit_code, description)
where not exists (
  select 1 from posting_rules r where r.business_id = b.id and r.event_type = s.event_type and r.component = s.component
);
"""

DOWNGRADE_SQL = """
delete from posting_rules where is_system and event_type = 'PointsReversed';
alter table customers drop column if exists points_balance;
drop table if exists points_movements;
drop type if exists points_reason;
drop table if exists loyalty_settings;
"""


def _sql_values(rows) -> str:
    return ", ".join(
        "(" + ", ".join("'" + str(v).replace("'", "''") + "'" for v in row) + ")" for row in rows
    )


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
    _execute_statements(BACKFILL_SETTINGS_SQL)
    _execute_statements(BACKFILL_RULES_SQL.format(values=_sql_values(NEW_RULES)))


def downgrade() -> None:
    _execute_statements(DOWNGRADE_SQL)
