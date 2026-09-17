"""`orders.client_ref` — one tap is one order, however many times it arrives (svc-2).

A till on café wifi retries. A cashier double-taps "Bayar". A guest's phone
loses signal after the request left and sends it again. Before this, each of
those wrote a second order, and a second order is a second sale: stock taken
twice, money counted twice, a ticket the kitchen makes twice.

The device now names each submission with a reference it generates once, and
the server keeps it on the order it created. Replaying the same reference
returns that order and writes nothing. The unique index is the backstop for two
copies racing; the service takes a transaction-scoped advisory lock on the
reference first, so the second copy waits and then finds the first instead of
failing on the index.

Scoped per business, nullable (every existing order, the WhatsApp tool and the
backdated slip have none). This is deliberately narrower than M14-T2's offline
idempotency key, which also needs a client-generated order id and replay
ordering; nothing here prevents building that on top.

Additive. `orders` already carries the tenant_isolation policy.

Revision ID: 0035
Revises: 0034
Create Date: 2026-09-17
"""
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
alter table orders add column client_ref text;
alter table orders add constraint orders_client_ref_length check (client_ref is null or length(client_ref) between 8 and 64);
create unique index uq_orders_client_ref on orders(business_id, client_ref) where client_ref is not null;
"""

DOWNGRADE_SQL = """
drop index if exists uq_orders_client_ref;
alter table orders drop constraint if exists orders_client_ref_length;
alter table orders drop column if exists client_ref;
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
