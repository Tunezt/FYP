"""item_variants — sizes and options of an item (roadmap M4-T1).

A variant is what actually sells: a name ("Regular", "Large"), an optional SKU,
its own sell and cost price. Stock stays on the parent item (a large latte and
a regular latte draw from the same cups until recipes arrive in M4-T4), and
reporting rolls up through `order_lines.item_id`.

`items` is untouched. Every existing item gets one default variant ("Standar")
copying its prices, so single-variant products keep working exactly as before.
`order_lines.variant_id`, a forward reference since 0005, gets its foreign key
now that the table exists (additive constraint; existing NULLs stay NULL).

Additive only. RLS in this migration.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-03
"""
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
create table item_variants (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  item_id uuid not null references items(id) on delete cascade,
  name text not null,
  sku text,
  sell_price numeric(12,2) not null default 0,
  cost_price numeric(12,2) not null default 0,
  is_default boolean not null default false,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index idx_item_variants_business_item on item_variants(business_id, item_id);
create unique index uq_item_variants_one_default on item_variants(item_id) where is_default;
create unique index uq_item_variants_name on item_variants(item_id, lower(name));

alter table order_lines
  add constraint order_lines_variant_id_fkey foreign key (variant_id) references item_variants(id);

-- Backfill: one default variant per item that has none yet (idempotent).
insert into item_variants (business_id, item_id, name, sell_price, cost_price, is_default, is_active, created_at, updated_at)
select i.business_id, i.id, 'Standar', i.sell_price, i.cost_price, true, true, i.created_at, i.updated_at
from items i
where not exists (select 1 from item_variants v where v.item_id = i.id and v.is_default);
"""

RLS_SQL = """
alter table item_variants enable row level security;
alter table item_variants force row level security;
create policy tenant_isolation on item_variants
  using (business_id = current_setting('app.current_business_id')::uuid)
  with check (business_id = current_setting('app.current_business_id')::uuid);
"""

DOWNGRADE_SQL = """
alter table order_lines drop constraint if exists order_lines_variant_id_fkey;
drop table if exists item_variants;
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
