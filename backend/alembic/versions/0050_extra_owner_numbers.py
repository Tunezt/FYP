"""More WhatsApp numbers for the owner's assistant (wa-gw-4).

The owner, 23 November 2026: the bot answers businesses.owner_phone only
(wa-gw-3), but more people need the assistant too, one of them from a
Malaysian (+60) number.

* `businesses.extra_owner_numbers`: a JSON list of {name, phone, added_at}, the
  phone digits-only and international like owner_phone. Every number on it gets
  the owner's assistant on WhatsApp; nothing else (login codes, nightly alerts)
  goes to it.

On `businesses`, not a table of its own, because the bot has to find the cafe
from the sender's number before it knows the tenant, and `businesses` has no
`business_id` and no RLS by design (CLAUDE.md). Additive. The downgrade drops it.

Revision ID: 0050
Revises: 0049
Create Date: 2026-11-23
"""
from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
alter table businesses add column extra_owner_numbers jsonb not null default '[]'::jsonb;
"""

DOWNGRADE_SQL = """
alter table businesses drop column if exists extra_owner_numbers;
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
