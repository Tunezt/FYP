"""`businesses.require_shift`: no sale at the till without an open shift (till-4).

The owner's decision of 1 October 2026: the cashier counts the opening cash
into the drawer (*Buka shift*) before the first sale, and the server refuses a
payment taken at the till while that cashier has no open shift. Until now a
sale with no shift was still a sale (`shift_id` NULL, M7-T1).

It is a per-café setting, on for every café that exists when this runs (today
that is Poernama) and for every café created by `app.bootstrap` and
`/auth/register`. The column default stays off so that a business row made any
other way behaves as it always has.

Additive: one column. The downgrade drops it.

Revision ID: 0044
Revises: 0043
Create Date: 2026-10-01
"""
from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
alter table businesses add column require_shift boolean not null default false;

-- The cafés that already exist are the real one: they get the owner's rule.
update businesses set require_shift = true;
"""

DOWNGRADE_SQL = """
alter table businesses drop column if exists require_shift;
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
