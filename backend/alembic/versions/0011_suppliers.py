"""suppliers, receipts.supplier_id (roadmap M5-T1).

Who the business buys from: name, phone, address, notes. Purchase history is
derived, never duplicated — from receipt photos linked to the supplier now, and
from purchase orders / goods receipts in M5-T2/M5-T3. `receipts.supplier_id` is
a new nullable column (additive); the free-text `receipts.supplier` stays as
what the photo said. Existing receipts are linked where their text matches a
supplier name exactly (case-insensitive) — there are none at migration time
unless a business already created suppliers, so this is a no-op today and
idempotent later.

Additive only. RLS in this migration.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-03
"""
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
create table suppliers (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  name text not null,
  phone text,
  address text,
  notes text,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create unique index uq_suppliers_name on suppliers(business_id, lower(name));

alter table receipts add column supplier_id uuid references suppliers(id);
create index idx_receipts_supplier on receipts(supplier_id);
"""

RLS_SQL = """
alter table suppliers enable row level security;
alter table suppliers force row level security;
create policy tenant_isolation on suppliers
  using (business_id = current_setting('app.current_business_id')::uuid)
  with check (business_id = current_setting('app.current_business_id')::uuid);
"""

BACKFILL_SQL = """
update receipts r
set supplier_id = s.id
from suppliers s
where r.supplier_id is null and s.business_id = r.business_id
  and r.supplier is not null and lower(trim(r.supplier)) = lower(s.name);
"""

DOWNGRADE_SQL = """
alter table receipts drop column if exists supplier_id;
drop table if exists suppliers;
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
