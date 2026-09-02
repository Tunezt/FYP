"""accounts — the chart of accounts (roadmap M6-T1).

Every business gets the same small Indonesian SME chart, seeded here for existing
businesses and at registration for new ones (services/accounts.py is the single
definition). Merchants may add their own accounts; the seeded ones are
`is_system` and can be renamed but never deactivated, because posting rules
(M6-T3) point at them by code.

Additive only. RLS in this migration.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-03
"""
from alembic import op

from app.services.accounts import STANDARD_CHART

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
create type account_type as enum ('asset', 'liability', 'equity', 'revenue', 'expense');

create table accounts (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  code text not null,                       -- '1100', '4100' … sorts the chart
  name text not null,
  type account_type not null,
  is_system boolean not null default false, -- seeded; posting rules reference it
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create unique index uq_accounts_code on accounts(business_id, code);
create index idx_accounts_type on accounts(business_id, type);
"""

RLS_SQL = """
alter table accounts enable row level security;
alter table accounts force row level security;
create policy tenant_isolation on accounts
  using (business_id = current_setting('app.current_business_id')::uuid)
  with check (business_id = current_setting('app.current_business_id')::uuid);
"""

BACKFILL_SQL = """
insert into accounts (business_id, code, name, type, is_system)
select b.id, s.code, s.name, s.type::account_type, true
from businesses b
cross join (values {values}) as s(code, name, type)
where not exists (select 1 from accounts a where a.business_id = b.id and a.code = s.code);
"""

DOWNGRADE_SQL = """
drop table if exists accounts;
drop type if exists account_type;
"""


def _sql_values(rows) -> str:
    return ", ".join("(" + ", ".join("'" + str(v).replace("'", "''") + "'" for v in row) + ")" for row in rows)


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
    _execute_statements(BACKFILL_SQL.format(values=_sql_values([(c, n, t) for c, n, t in STANDARD_CHART])))


def downgrade() -> None:
    _execute_statements(DOWNGRADE_SQL)
