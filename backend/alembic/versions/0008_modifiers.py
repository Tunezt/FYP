"""modifier_groups, modifiers, order_line_modifiers (roadmap M4-T2).

"Extra shot, less sugar." A modifier group belongs to an item and is single- or
multi-select, required or optional (min/max). A modifier is priced (price_delta)
or free. What the customer chose is snapshotted onto the order line
(`order_line_modifiers.name` / `price_delta`) so the receipt and the margin of an
old order never change when the catalogue does. The line's `unit_price` already
includes the modifier deltas; the snapshot rows are the itemisation.

Additive only. RLS on all three in this migration.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-03
"""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
create type modifier_selection as enum ('single', 'multi');

create table modifier_groups (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  item_id uuid not null references items(id) on delete cascade,
  name text not null,
  selection modifier_selection not null default 'single',
  is_required boolean not null default false,
  min_select integer not null default 0 check (min_select >= 0),
  max_select integer check (max_select is null or max_select >= 1),
  sort_order integer not null default 0,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index idx_modifier_groups_item on modifier_groups(business_id, item_id);
create unique index uq_modifier_groups_name on modifier_groups(item_id, lower(name));

create table modifiers (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  group_id uuid not null references modifier_groups(id) on delete cascade,
  name text not null,
  price_delta numeric(12,2) not null default 0,
  is_default boolean not null default false,
  sort_order integer not null default 0,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index idx_modifiers_group on modifiers(business_id, group_id);
create unique index uq_modifiers_name on modifiers(group_id, lower(name));

create table order_line_modifiers (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  order_line_id uuid not null references order_lines(id),
  modifier_id uuid references modifiers(id),   -- null once a modifier is deleted? never: nothing is deleted
  name text not null,                           -- snapshot
  price_delta numeric(12,2) not null default 0, -- snapshot
  created_at timestamptz not null default now()
);
create index idx_order_line_modifiers_line on order_line_modifiers(order_line_id);
"""

RLS_TABLES = ["modifier_groups", "modifiers", "order_line_modifiers"]

RLS_SQL_TEMPLATE = """
alter table {table} enable row level security;
alter table {table} force row level security;
create policy tenant_isolation on {table}
  using (business_id = current_setting('app.current_business_id')::uuid)
  with check (business_id = current_setting('app.current_business_id')::uuid);
"""

DOWNGRADE_SQL = """
drop table if exists order_line_modifiers;
drop table if exists modifiers;
drop table if exists modifier_groups;
drop type if exists modifier_selection;
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
