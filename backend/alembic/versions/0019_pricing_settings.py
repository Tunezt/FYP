"""pricing_settings — how a bill is built (roadmap M7-T4).

One row per business: tax rate and whether menu prices already include it,
service charge rate and whether it sits inside the taxable base, the rupiah
rounding applied at the total, and whether a discount needs a manager PIN.

Defaults are the plain warung: no tax, no service charge, no rounding, and a
manager PIN required before anyone discounts anything. A business that charges
PB1 turns it on; nothing changes for the ones that do not.

Backfilled for existing businesses; registration seeds it from
services/pricing for new ones.

Additive. RLS in this migration.

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-06
"""
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
create table pricing_settings (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null unique references businesses(id) on delete cascade,
  tax_rate numeric(6,4) not null default 0 check (tax_rate >= 0 and tax_rate < 1),
  tax_inclusive boolean not null default true,          -- menu prices already contain the tax
  service_charge_rate numeric(6,4) not null default 0 check (service_charge_rate >= 0 and service_charge_rate < 1),
  service_before_tax boolean not null default true,     -- service charge is inside the taxable base
  rounding_unit numeric(12,2) not null default 0 check (rounding_unit >= 0),
  rounding_mode text not null default 'nearest' check (rounding_mode in ('nearest', 'up', 'down')),
  discount_requires_pin boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
"""

RLS_SQL = """
alter table pricing_settings enable row level security;
alter table pricing_settings force row level security;
create policy tenant_isolation on pricing_settings
  using (business_id = current_setting('app.current_business_id')::uuid)
  with check (business_id = current_setting('app.current_business_id')::uuid);
"""

# Every existing business gets the defaults, so pricing never has to cope with
# a missing row.
BACKFILL_SQL = """
insert into pricing_settings (business_id)
select b.id from businesses b
where not exists (select 1 from pricing_settings p where p.business_id = b.id);
"""

DOWNGRADE_SQL = """
drop table if exists pricing_settings;
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
    _execute_statements(BACKFILL_SQL)


def downgrade() -> None:
    _execute_statements(DOWNGRADE_SQL)
