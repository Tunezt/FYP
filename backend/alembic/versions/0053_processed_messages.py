"""A WhatsApp message is answered once, even across a restart (p2-12).

WhatsApp (Meta, and the gateway) redelivers a webhook it thinks failed. The bot
kept the ids it had seen in memory, so a redelivery after a restart or a deploy
ran again: a stock correction applied twice, a question answered twice. Each
message id is now claimed in this table before the message is handled; a second
claim of the same id finds it taken and the message is skipped.

Not business-scoped, like `login_otps`: the claim happens before the sender is
matched to a cafe (a stranger's message is claimed too, then ignored). It holds
only the provider's message id and when it arrived, no content and no phone.
RLS is disabled explicitly, as for `login_otps`.

Revision ID: 0053
Revises: 0052
Create Date: 2026-11-25
"""
from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
create table processed_messages (
    message_id text primary key,
    received_at timestamptz not null default now()
);
alter table processed_messages disable row level security;
"""

DOWNGRADE_SQL = """
drop table if exists processed_messages;
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
