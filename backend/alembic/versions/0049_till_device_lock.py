"""The till on one tablet only: a switch, off (kasir-1).

The owner, 4 October 2026: the till should open at a short address (`/kasir`)
and on one device only, so that a cashier who knows the link and a PIN cannot
open it on their own phone. "For later, don't apply now, but build it so we can
simply activate it later."

* `businesses.till_device_lock`: the switch. False for every café, existing and
  new, until the owner turns it on in Pengaturan.
* `businesses.till_device_hash`: SHA-256 of the key the bound tablet made for
  itself. NULL means nothing is bound yet: with the switch on, the first device
  to log in with a correct PIN becomes the till.
* `businesses.till_device_bound_at`: when that happened, for the dashboard.

`businesses` has no `business_id` and no RLS by design (CLAUDE.md). Additive.
The downgrade drops the three columns.

Revision ID: 0049
Revises: 0048
Create Date: 2026-10-04
"""
from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
alter table businesses add column till_device_lock boolean not null default false;
alter table businesses add column till_device_hash text;
alter table businesses add column till_device_bound_at timestamptz;
"""

DOWNGRADE_SQL = """
alter table businesses drop column if exists till_device_bound_at;
alter table businesses drop column if exists till_device_hash;
alter table businesses drop column if exists till_device_lock;
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
