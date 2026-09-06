"""customers — who bought (roadmap M8-T1).

Name, phone, address, birthday, notes. Phone is the natural key when it is
known — one customer per phone per business, enforced by a partial unique
index — and it is stored in the same digits-only international form as
`businesses.owner_phone`, because it is also the customer's WhatsApp identity
(points balances and promos will be sent to it later in M8).

`orders.customer_id` has been a nullable forward reference since 0005; it gets
its foreign key here, the same way `order_lines.variant_id` did in 0007. Every
existing order has NULL there, so the constraint is safe to add.

Additive. RLS in this migration.

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-06
"""
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
create table customers (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  name text not null,
  phone text,                                   -- digits only, international (62812...), null when unknown
  address text,
  birthday date,
  notes text,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (length(trim(name)) > 0),
  check (phone is null or phone ~ '^[0-9]{8,15}$')
);
create unique index uq_customers_business_phone on customers(business_id, phone) where phone is not null;
create index idx_customers_business_name on customers(business_id, lower(name));

alter table orders
  add constraint orders_customer_id_fkey foreign key (customer_id) references customers(id);
create index idx_orders_customer on orders(customer_id) where customer_id is not null;
"""

RLS_SQL = """
alter table customers enable row level security;
alter table customers force row level security;
create policy tenant_isolation on customers
  using (business_id = current_setting('app.current_business_id')::uuid)
  with check (business_id = current_setting('app.current_business_id')::uuid);
"""

DOWNGRADE_SQL = """
drop index if exists idx_orders_customer;
alter table orders drop constraint if exists orders_customer_id_fkey;
drop table if exists customers;
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
