"""`businesses.day_start_hour` — the business day boundary (roadmap M15-T4).

A café that closes at 23:30 settles its last bill after midnight. On a calendar
day those takings land on tomorrow: the owner's daily number is wrong, the
shift reconciliation straddles two days, and the anomaly baseline learns from a
split day. `day_start_hour = 4` makes the business day run 04:00 → 04:00, so
00:15 belongs to the night that produced it.

Default 0 is exactly today's behaviour, so every existing business is
unaffected until its owner changes the setting.

`businesses` has RLS explicitly disabled by design (roadmap §1.1) — it *is* the
tenant — so there is no policy to add here.

Revision ID: 0030
Revises: 0029
Create Date: 2026-09-06
"""
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
alter table businesses
  add column if not exists day_start_hour smallint not null default 0;

alter table businesses
  drop constraint if exists businesses_day_start_hour_range;

alter table businesses
  add constraint businesses_day_start_hour_range check (day_start_hour between 0 and 23);
"""

DOWNGRADE_SQL = """
alter table businesses drop constraint if exists businesses_day_start_hour_range;
alter table businesses drop column if exists day_start_hour;
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


def downgrade() -> None:
    _execute_statements(DOWNGRADE_SQL)
