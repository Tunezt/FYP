"""`businesses.pairing_generation` — cutting a lost kiosk off (roadmap M15-T8).

The kiosk pairing link is a signed token with a one-year life and no revocation:
whoever holds it can reach the "who are you" screen and attempt PIN logins for a
year. That is fine until the tablet is stolen, sold, or left in a becak.

A generation counter fixes it without a revocation table. Every pairing link and
every till session carries the generation it was issued under; when the owner
re-pairs, the counter goes up and everything issued before stops working on the
next request. One integer, one comparison, no state to clean up.

Re-pairing is deliberately blunt — it logs out the good tablet too. That is the
right trade for the case it exists for: you re-pair because a device is gone,
and "everything issued before now is void" is the only version of that which is
actually safe. `docs/runbook.md` says so in as many words.

Default 1, so every existing business and every already-issued token stays valid
until the owner chooses otherwise.

Revision ID: 0032
Revises: 0031
Create Date: 2026-09-06
"""
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
alter table businesses
  add column if not exists pairing_generation integer not null default 1;

alter table businesses
  drop constraint if exists businesses_pairing_generation_positive;

alter table businesses
  add constraint businesses_pairing_generation_positive check (pairing_generation >= 1);
"""

DOWNGRADE_SQL = """
alter table businesses drop constraint if exists businesses_pairing_generation_positive;
alter table businesses drop column if exists pairing_generation;
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
