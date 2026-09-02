"""uoms, uom_conversions, items.uom_id (roadmap M4-T3).

`items.unit` is free text and stays for compatibility. `uoms` gives each business
a proper unit list (kg, g, liter, ml, pcs, …) and `uom_conversions` the factors
between them, so stock can be held in kg and consumed in g: 250 g out of 5 kg
leaves 4.750 kg, exact in numeric(12,3).

Every existing business gets the standard Indonesian set and the two obvious
conversion pairs; every item whose free-text `unit` matches a standard code gets
its `uom_id` set. New businesses get the same set at registration
(services/units.ensure_standard_uoms).

Additive only: a new nullable column on `items`, two new tables with RLS.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-03
"""
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
create table uoms (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  code text not null,                 -- 'kg', 'g', 'liter', 'ml', 'pcs', …
  name text not null,                 -- 'kilogram', 'gram', …
  created_at timestamptz not null default now()
);
create unique index uq_uoms_code on uoms(business_id, lower(code));

create table uom_conversions (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  from_uom_id uuid not null references uoms(id) on delete cascade,
  to_uom_id uuid not null references uoms(id) on delete cascade,
  factor numeric(18,6) not null check (factor > 0),   -- qty_to = qty_from * factor
  created_at timestamptz not null default now(),
  check (from_uom_id <> to_uom_id)
);
create unique index uq_uom_conversions_pair on uom_conversions(from_uom_id, to_uom_id);
create index idx_uom_conversions_business on uom_conversions(business_id);

alter table items add column uom_id uuid references uoms(id);
create index idx_items_uom on items(uom_id);
"""

RLS_TABLES = ["uoms", "uom_conversions"]

RLS_SQL_TEMPLATE = """
alter table {table} enable row level security;
alter table {table} force row level security;
create policy tenant_isolation on {table}
  using (business_id = current_setting('app.current_business_id')::uuid)
  with check (business_id = current_setting('app.current_business_id')::uuid);
"""

# Standard set per business (code, name) and conversions (from, to, factor).
STANDARD_UOMS = [
    ("kg", "kilogram"), ("g", "gram"), ("liter", "liter"), ("ml", "mililiter"),
    ("pcs", "pcs"), ("cup", "cup"), ("porsi", "porsi"), ("botol", "botol"), ("bungkus", "bungkus"),
    ("dus", "dus"), ("kaleng", "kaleng"), ("pouch", "pouch"), ("sachet", "sachet"), ("tray", "tray"),
    ("ikat", "ikat"), ("pack", "pack"),
]
STANDARD_CONVERSIONS = [("kg", "g", "1000"), ("g", "kg", "0.001"), ("liter", "ml", "1000"), ("ml", "liter", "0.001")]

BACKFILL_SQL = """
insert into uoms (business_id, code, name)
select b.id, s.code, s.name
from businesses b
cross join (values {uom_values}) as s(code, name)
where not exists (select 1 from uoms u where u.business_id = b.id and lower(u.code) = s.code);

insert into uom_conversions (business_id, from_uom_id, to_uom_id, factor)
select f.business_id, f.id, t.id, c.factor::numeric
from (values {conversion_values}) as c(from_code, to_code, factor)
join uoms f on lower(f.code) = c.from_code
join uoms t on lower(t.code) = c.to_code and t.business_id = f.business_id
where not exists (select 1 from uom_conversions x where x.from_uom_id = f.id and x.to_uom_id = t.id);

update items i
set uom_id = u.id
from uoms u
where i.uom_id is null and u.business_id = i.business_id
  and lower(u.code) = case lower(trim(i.unit)) when 'l' then 'liter' when 'ltr' then 'liter' when 'gram' then 'g' else lower(trim(i.unit)) end;
"""

DOWNGRADE_SQL = """
alter table items drop column if exists uom_id;
drop table if exists uom_conversions;
drop table if exists uoms;
"""


def _sql_values(rows) -> str:
    return ", ".join("(" + ", ".join(f"'{v}'" for v in row) + ")" for row in rows)


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
    _execute_statements(
        BACKFILL_SQL.format(
            uom_values=_sql_values(STANDARD_UOMS),
            conversion_values=_sql_values(STANDARD_CONVERSIONS),
        )
    )


def downgrade() -> None:
    _execute_statements(DOWNGRADE_SQL)
