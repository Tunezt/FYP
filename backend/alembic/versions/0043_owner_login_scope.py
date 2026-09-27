"""`pin_attempts.scope` accepts `owner_login` — the dashboard PIN door.

Meta gates WhatsApp Authentication templates behind business verification
(docs/whatsapp-templates.md), so the owner cannot always receive a login code.
`POST /auth/login-pin` is the second door: the owner's phone and the owner's
own PIN. It faces the open internet, unlike the till, which needs a pairing
link — so it is throttled on its own stricter ladder (3 failures → 30s,
5 → 2 min, 10 → 15 min) using the counter M15-T12 already built.

That counter's check constraint listed three scopes. This widens it to four.
Nothing else changes: same table, same policy, same rows.

Revision ID: 0043
Revises: 0042
Create Date: 2026-09-24
"""
from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
alter table pin_attempts drop constraint if exists pin_attempts_scope_check;

alter table pin_attempts
  add constraint pin_attempts_scope_check
  check (scope in ('pos_login', 'pos_device', 'manager_pin', 'owner_login'));
"""

DOWNGRADE_SQL = """
-- Rows in the new scope would violate the narrower constraint, so they go
-- first: they are failed-PIN counters in a 15-minute window, not business data.
delete from pin_attempts where scope = 'owner_login';

alter table pin_attempts drop constraint if exists pin_attempts_scope_check;

alter table pin_attempts
  add constraint pin_attempts_scope_check
  check (scope in ('pos_login', 'pos_device', 'manager_pin'));
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
