"""backup_failed alert kind (roadmap M15-T1).

A backup that fails silently is the same as no backup, so a failed run raises
an alert through the machinery the owner already receives on WhatsApp. That
needs one more value in the `alert_type` enum.

Postgres cannot remove a value from an enum, so the downgrade is deliberately
a no-op: an unused enum value is harmless, and `add value if not exists` makes
the upgrade idempotent when it is re-applied. Same precedent as 0025.

Revision ID: 0029
Revises: 0028
Create Date: 2026-09-06
"""
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
alter type alert_type add value if not exists 'backup_failed';
"""

DOWNGRADE_SQL = """
-- Nothing to undo: Postgres has no `alter type ... drop value`, and an enum
-- value nothing writes costs nothing. See the module docstring.
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
