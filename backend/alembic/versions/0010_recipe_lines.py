"""recipe_lines — what a variant is made of (roadmap M4-T4).

Keyed on the VARIANT, not the item: a large latte uses more milk than a regular
one. Each line says how much of a component item one unit of the variant
consumes, in a unit of measure (NULL = the component's own unit). Selling a
variant that has active recipe lines consumes the components — converted across
the unit boundary, guarded per component by the atomic conditional UPDATE — and
leaves the sold item's own stock alone (it is made to order).

Additive only. RLS in this migration. Recipe lines are catalogue configuration;
they are deactivated, never deleted, so old sales keep their context.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-03
"""
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
create table recipe_lines (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  variant_id uuid not null references item_variants(id) on delete cascade,
  component_item_id uuid not null references items(id),
  quantity numeric(12,3) not null check (quantity > 0),   -- per one unit of the variant
  uom_id uuid references uoms(id),                        -- null = the component's own unit
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create unique index uq_recipe_lines_variant_component on recipe_lines(variant_id, component_item_id);
create index idx_recipe_lines_business_variant on recipe_lines(business_id, variant_id);
create index idx_recipe_lines_component on recipe_lines(component_item_id);
"""

RLS_SQL = """
alter table recipe_lines enable row level security;
alter table recipe_lines force row level security;
create policy tenant_isolation on recipe_lines
  using (business_id = current_setting('app.current_business_id')::uuid)
  with check (business_id = current_setting('app.current_business_id')::uuid);
"""

DOWNGRADE_SQL = """
drop table if exists recipe_lines;
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
