"""Open bills (bill-1): the front printer also prints a *nota*.

A dine-in table keeps one open bill and pays when it leaves. Each time the
cashier sends new items to be made, the Bar and Dapur slips carry only those
items, and the front printer gives a nota the staff take to the table: the new
items with prices, the running total, and BELUM DIBAYAR. It is a print job like
the others, so the only change is one more allowed `kind`.

The cart's own bookkeeping (which lines were sent in which batch, which sent
lines were cancelled and why) lives in `orders.cart` jsonb, like the rest of an
unpaid order. Nothing is taken from stock and nothing is posted until payment.

Additive: the kind check is widened, nothing else changes. Downgrading puts the
narrower check back as NOT VALID, so nota rows already written stay as they are
(nothing is deleted) while new rows are held to the old list.

Revision ID: 0041
Revises: 0040
Create Date: 2026-09-18
"""
from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
alter table print_jobs drop constraint print_jobs_kind_check;
alter table print_jobs add constraint print_jobs_kind_check
  check (kind in ('receipt', 'nota', 'bar_ticket', 'kitchen_ticket', 'bar_cancel', 'kitchen_cancel'));
"""

DOWNGRADE_SQL = """
alter table print_jobs drop constraint print_jobs_kind_check;
alter table print_jobs add constraint print_jobs_kind_check
  check (kind in ('receipt', 'bar_ticket', 'kitchen_ticket', 'bar_cancel', 'kitchen_cancel')) not valid;
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
        elif ch == "-" and i + 1 < n and sql[i + 1] == "-":
            current.append(ch)
            in_comment = True
        elif ch == "'":
            current.append(ch)
            in_string = True
        elif ch == ";":
            statements.append("".join(current))
            current = []
        else:
            current.append(ch)
        i += 1
    if current:
        statements.append("".join(current))
    return statements


def _has_sql(statement: str) -> bool:
    for line in statement.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("--"):
            return True
    return False


def _execute_statements(sql: str) -> None:
    for statement in _split_statements(sql):
        if _has_sql(statement):
            op.execute(statement)


def upgrade() -> None:
    _execute_statements(UPGRADE_SQL)


def downgrade() -> None:
    _execute_statements(DOWNGRADE_SQL)
