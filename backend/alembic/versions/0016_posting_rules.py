"""posting_rules — event type → debit / credit accounts, as data (roadmap M6-T3).

Seeded per business from services/posting_rules.STANDARD_RULES (the single
definition), for existing businesses here and for new ones at registration.
Account references are by CODE so a merchant's renamed accounts keep working.

Additive only. RLS in this migration.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-03
"""
from alembic import op

from app.services.posting_rules import STANDARD_RULES

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
create table posting_rules (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  event_type text not null,        -- 'OrderCompleted', 'GoodsReceived', … (roadmap appendix A)
  component text not null,         -- 'payment:cash', 'cogs', 'expense:*', 'reversal', …
  debit_code text,                 -- account code; null only for 'reversal'
  credit_code text,
  description text,
  is_system boolean not null default false,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check ((debit_code is null) = (credit_code is null)),
  check (debit_code is null or debit_code <> credit_code)
);
create unique index uq_posting_rules_event_component on posting_rules(business_id, event_type, component);
create index idx_posting_rules_event on posting_rules(business_id, event_type);
"""

RLS_SQL = """
alter table posting_rules enable row level security;
alter table posting_rules force row level security;
create policy tenant_isolation on posting_rules
  using (business_id = current_setting('app.current_business_id')::uuid)
  with check (business_id = current_setting('app.current_business_id')::uuid);
"""

BACKFILL_SQL = """
insert into posting_rules (business_id, event_type, component, debit_code, credit_code, description, is_system)
select b.id, s.event_type, s.component, nullif(s.debit_code, ''), nullif(s.credit_code, ''), s.description, true
from businesses b
cross join (values {values}) as s(event_type, component, debit_code, credit_code, description)
where not exists (
  select 1 from posting_rules r where r.business_id = b.id and r.event_type = s.event_type and r.component = s.component
);
"""

DOWNGRADE_SQL = """
drop table if exists posting_rules;
"""


def _sql_values(rows) -> str:
    return ", ".join(
        "(" + ", ".join("'" + str(v if v is not None else "").replace("'", "''") + "'" for v in row) + ")" for row in rows
    )


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
    _execute_statements(BACKFILL_SQL.format(values=_sql_values(STANDARD_RULES)))


def downgrade() -> None:
    _execute_statements(DOWNGRADE_SQL)
