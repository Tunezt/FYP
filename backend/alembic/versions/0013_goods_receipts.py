"""goods_receipts, goods_receipt_lines (roadmap M5-T3).

Goods arriving, from a purchase order or without one. Receiving is the event
that moves stock: each line writes a `purchase` stock movement in the same
transaction and recomputes the item's moving-average cost (M4-T5). Lines carry
the received quantity in a unit and the unit cost in that unit, converted to
the item's own unit and cost for the ledger. A line that belongs to a PO line
advances its `received_quantity`; partial receipts leave the PO
`partially_received`, over-receipt is refused unless explicitly allowed.

Additive only. RLS on both tables in this migration.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-03
"""
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
create table goods_receipts (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  supplier_id uuid references suppliers(id),
  po_id uuid references purchase_orders(id),
  number integer not null,
  received_at timestamptz not null default now(),
  received_by uuid references staff(id),
  notes text,
  subtotal numeric(12,2) not null default 0,
  created_at timestamptz not null default now()
);
create unique index uq_goods_receipts_number on goods_receipts(business_id, number);
create index idx_goods_receipts_supplier on goods_receipts(business_id, supplier_id);
create index idx_goods_receipts_po on goods_receipts(po_id);

create table goods_receipt_lines (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  receipt_id uuid not null references goods_receipts(id),
  po_line_id uuid references po_lines(id),
  item_id uuid not null references items(id),
  quantity numeric(12,3) not null check (quantity > 0),      -- as received, in uom_id
  uom_id uuid references uoms(id),                            -- null = the item's own unit
  quantity_item_unit numeric(12,3) not null,                  -- what went into the ledger
  unit_cost numeric(12,2) not null default 0,                 -- per received unit
  unit_cost_item_unit numeric(12,2) not null default 0,       -- per item unit, what the average uses
  line_total numeric(12,2) not null default 0,
  created_at timestamptz not null default now()
);
create index idx_goods_receipt_lines_receipt on goods_receipt_lines(receipt_id);
create index idx_goods_receipt_lines_item on goods_receipt_lines(business_id, item_id);
create index idx_goods_receipt_lines_po_line on goods_receipt_lines(po_line_id);
"""

RLS_TABLES = ["goods_receipts", "goods_receipt_lines"]

RLS_SQL_TEMPLATE = """
alter table {table} enable row level security;
alter table {table} force row level security;
create policy tenant_isolation on {table}
  using (business_id = current_setting('app.current_business_id')::uuid)
  with check (business_id = current_setting('app.current_business_id')::uuid);
"""

DOWNGRADE_SQL = """
drop table if exists goods_receipt_lines;
drop table if exists goods_receipts;
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
