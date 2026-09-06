"""`orders.entry_source` — how a sale reached the books (roadmap M15-T10).

One tablet is one point of failure. When it dies, cracks or will not charge
mid-service, staff fall back to pen and paper, and those sales still have to
reach the books — at the time they actually happened, not at the time somebody
finally got round to typing them in.

`entry_source` is not `source`. `source` says which channel the order came
from — the till or the QR menu — and it is about the customer. `entry_source`
says how it was keyed in, and it is about trust: `live` was rung up as it
happened, `manual_backdated` was typed afterwards from a piece of paper by
somebody who chose the timestamp. That distinction is why the column exists
separately rather than as a third value on `source`: a backdated *menu* order
is a coherent thing, and collapsing the two axes would make it unsayable.

Default `live`, so every existing row and every ordinary sale is unchanged.

Revision ID: 0033
Revises: 0032
Create Date: 2026-09-06
"""
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
alter table orders add column if not exists entry_source text not null default 'live';

alter table orders drop constraint if exists orders_entry_source_check;

alter table orders
  add constraint orders_entry_source_check check (entry_source in ('live', 'manual_backdated'));

create index if not exists idx_orders_entry_source
  on orders(business_id, entry_source, sold_at desc)
  where entry_source <> 'live';
"""

DOWNGRADE_SQL = """
drop index if exists idx_orders_entry_source;
alter table orders drop constraint if exists orders_entry_source_check;
alter table orders drop column if exists entry_source;
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
