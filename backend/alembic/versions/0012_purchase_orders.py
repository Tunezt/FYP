"""purchase_orders, po_lines (roadmap M5-T2).

What the business has asked a supplier for. States: draft → ordered →
partially_received → received, or cancelled (only while nothing has been
received). Lines carry the ordered quantity in a unit, the agreed unit cost, and
`received_quantity`, which M5-T3's goods receipts advance — the PO itself never
touches stock. Money numeric(12,2), quantities numeric(12,3).

Additive only. RLS on both tables in this migration.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-03
"""
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
create type po_status as enum ('draft', 'ordered', 'partially_received', 'received', 'cancelled');

create table purchase_orders (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  supplier_id uuid not null references suppliers(id),
  status po_status not null default 'draft',
  number integer not null,                         -- per-business running number, shown to humans
  notes text,
  expected_at date,
  ordered_at timestamptz,
  cancelled_at timestamptz,
  created_by uuid references staff(id),
  subtotal numeric(12,2) not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create unique index uq_purchase_orders_number on purchase_orders(business_id, number);
create index idx_purchase_orders_supplier on purchase_orders(business_id, supplier_id);
create index idx_purchase_orders_status on purchase_orders(business_id, status);

create table po_lines (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  po_id uuid not null references purchase_orders(id),
  item_id uuid not null references items(id),
  quantity numeric(12,3) not null check (quantity > 0),
  uom_id uuid references uoms(id),                 -- null = the item's own unit
  unit_cost numeric(12,2) not null default 0,
  line_total numeric(12,2) not null default 0,
  received_quantity numeric(12,3) not null default 0 check (received_quantity >= 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index idx_po_lines_po on po_lines(po_id);
create index idx_po_lines_business_item on po_lines(business_id, item_id);
"""

RLS_TABLES = ["purchase_orders", "po_lines"]

RLS_SQL_TEMPLATE = """
alter table {table} enable row level security;
alter table {table} force row level security;
create policy tenant_isolation on {table}
  using (business_id = current_setting('app.current_business_id')::uuid)
  with check (business_id = current_setting('app.current_business_id')::uuid);
"""

DOWNGRADE_SQL = """
drop table if exists po_lines;
drop table if exists purchase_orders;
drop type if exists po_status;
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
    for table in RLS_TABLES:
        _execute_statements(RLS_SQL_TEMPLATE.format(table=table))


def downgrade() -> None:
    _execute_statements(DOWNGRADE_SQL)
