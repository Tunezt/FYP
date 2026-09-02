"""orders, order_lines, payments — the order model (roadmap M3-T1).

A sale becomes an order with many lines and many payments. Many-to-one payments
on an order is what makes split payment fall out for free. `order_lines.unit_cost_at_sale`
snapshots the cost at the moment of sale so historical margin never moves when
`items.cost_price` changes later (a real existing bug in the one-row `sales` model).

No outlet or terminal columns (roadmap §4.1). `customer_id` and `variant_id` are
nullable forward references (M8 customers, M4 variants) and get their foreign keys
when those tables exist. `business_id` is denormalised onto lines and payments so
the standard tenant_isolation template applies to all three (roadmap §1.1).

Enum values not fixed by the roadmap, chosen here and extendable additively:
  order_status    open · completed · voided · refunded
  payment_method  cash · qris · transfer · card · ewallet · points · other

Additive only. RLS in this migration. Existing `sales` untouched (M3-T2 turns it
into a view).

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-03
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
create type order_type as enum ('dine_in', 'takeaway', 'delivery', 'pickup');
create type order_status as enum ('open', 'completed', 'voided', 'refunded');
create type payment_method as enum ('cash', 'qris', 'transfer', 'card', 'ewallet', 'points', 'other');

create table orders (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  staff_id uuid references staff(id),          -- null for self-service channels (M11)
  customer_id uuid,                            -- forward reference to customers (M8)
  order_type order_type not null default 'takeaway',
  status order_status not null default 'completed',
  subtotal numeric(12,2) not null default 0,
  discount_total numeric(12,2) not null default 0,
  tax_total numeric(12,2) not null default 0,
  service_charge numeric(12,2) not null default 0,
  rounding numeric(12,2) not null default 0,
  total numeric(12,2) not null default 0,
  sold_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);
create index idx_orders_business_time on orders(business_id, sold_at desc);

create table order_lines (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  order_id uuid not null references orders(id),
  item_id uuid not null references items(id),
  variant_id uuid,                             -- forward reference to item_variants (M4)
  quantity numeric(12,3) not null check (quantity <> 0),  -- negative on a reversing line
  unit_price numeric(12,2) not null,
  line_discount numeric(12,2) not null default 0,
  line_total numeric(12,2) not null,
  unit_cost_at_sale numeric(12,2),             -- cost snapshot; null only for backfilled history
  notes text,
  created_at timestamptz not null default now()
);
create index idx_order_lines_order on order_lines(order_id);
create index idx_order_lines_business_item on order_lines(business_id, item_id);

create table payments (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  order_id uuid not null references orders(id),
  method payment_method not null,
  amount numeric(12,2) not null,               -- negative on a refund
  reference text,                              -- QRIS/transfer reference, card last-4, …
  created_at timestamptz not null default now()
);
create index idx_payments_order on payments(order_id);
"""

RLS_TABLES = ["orders", "order_lines", "payments"]

RLS_SQL_TEMPLATE = """
alter table {table} enable row level security;
alter table {table} force row level security;
create policy tenant_isolation on {table}
  using (business_id = current_setting('app.current_business_id')::uuid)
  with check (business_id = current_setting('app.current_business_id')::uuid);
"""

DOWNGRADE_SQL = """
drop table if exists payments;
drop table if exists order_lines;
drop table if exists orders;
drop type if exists payment_method;
drop type if exists order_status;
drop type if exists order_type;
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
