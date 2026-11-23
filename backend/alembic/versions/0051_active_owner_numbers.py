"""One WhatsApp number on several cafes: the bot remembers which one (wa-gw-5).

The owner, 23 November 2026: his number stays an extra assistant number on the
family restaurant and also owns the presentation demo. The bot used to pick one
of the two with an unordered LIMIT 1, so a message could be answered from
either cafe's books.

* `businesses.active_owner_numbers`: a JSON list of phone numbers (digits-only,
  international) that reach several cafes and chose this one. The bot asks
  once with a numbered list, then answers from the chosen cafe until the owner
  types "ganti toko".

On `businesses` for the reason 0050 gives: the bot needs it before it knows the
tenant, and `businesses` has no RLS by design. Additive. The downgrade drops it.

Revision ID: 0051
Revises: 0050
Create Date: 2026-11-23
"""
from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
alter table businesses add column active_owner_numbers jsonb not null default '[]'::jsonb;
"""

DOWNGRADE_SQL = """
alter table businesses drop column if exists active_owner_numbers;
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
