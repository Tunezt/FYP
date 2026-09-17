"""`orders.parent_order_id` — "Tambahan untuk #1234" (svc-3).

The café is counter service and customers pay before anything is made. So when
a customer who has already paid comes back for a croissant, that is a new
purchase: its own payment, its own receipt, its own kitchen ticket. What was
missing was the relationship. Without it the cashier and the kitchen see two
unrelated orders, and nobody can tell that the croissant belongs with the flat
white already on the pass.

The addition points at the paid order it belongs to (always the original, never
another addition, so a chain of three reads as one family). The original is
never touched: its lines, payments, receipt and preparation progress stay
exactly as they were. Voiding or refunding either one stays a decision about
that one receipt.

Additive. `orders` already carries the tenant_isolation policy.

Revision ID: 0036
Revises: 0035
Create Date: 2026-09-17
"""
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
alter table orders add column parent_order_id uuid references orders(id);
alter table orders add constraint orders_parent_not_self check (parent_order_id is null or parent_order_id <> id);
create index idx_orders_parent on orders(business_id, parent_order_id) where parent_order_id is not null;
"""

DOWNGRADE_SQL = """
drop index if exists idx_orders_parent;
alter table orders drop constraint if exists orders_parent_not_self;
alter table orders drop column if exists parent_order_id;
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
    _execute_statements(SCHEMA_SQL)


def downgrade() -> None:
    _execute_statements(DOWNGRADE_SQL)
