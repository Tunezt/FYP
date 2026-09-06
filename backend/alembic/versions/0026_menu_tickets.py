"""QR e-menu tickets (roadmap M11-T1).

A guest's order from the e-menu lands in the SAME `orders` table the till
writes — as an *open* row: `status = 'open'`, `source = 'menu'`, no lines, no
payments, no stock taken, nothing posted. The guest's cart is kept verbatim in
`cart` (jsonb) so the queue can show it and the till can fulfil exactly what
was asked. When the cashier settles the ticket the ordinary sale path runs
against this same row: lines, cost snapshots, stock movements, payments,
points and the journal are written once, at payment — and the row's status
flips to `completed`. A ticket the shop cannot serve is *cancelled*
(`status = 'voided'`, reason kept in `cart`) — never deleted.

New columns on `orders`: `source` ('pos' | 'menu'), `table_label`,
`guest_name`, `guest_phone`, `cart`. A partial index serves the queue.
Additive; `orders` already carries the tenant_isolation policy.

Revision ID: 0026
Revises: 0025
Create Date: 2026-09-06
"""
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
alter table orders add column source text not null default 'pos';
alter table orders add constraint orders_source_check check (source in ('pos', 'menu'));
alter table orders add column table_label text;
alter table orders add column guest_name text;
alter table orders add column guest_phone text;
alter table orders add column cart jsonb;
create index idx_orders_open_tickets on orders(business_id, created_at) where status = 'open';
"""

DOWNGRADE_SQL = """
drop index if exists idx_orders_open_tickets;
alter table orders drop column if exists cart;
alter table orders drop column if exists guest_phone;
alter table orders drop column if exists guest_name;
alter table orders drop column if exists table_label;
alter table orders drop constraint if exists orders_source_check;
alter table orders drop column if exists source;
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
