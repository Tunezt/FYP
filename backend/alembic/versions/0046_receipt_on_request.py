"""The customer's receipt only when asked, and a receipt the customer can scan (till-5b).

The owner's decision of 1 October 2026 (decision 4): after payment the cashier
asks, and taps one of *Kertas* (print), *QR* (a web receipt the customer
scans), *WhatsApp* (route A, till-7) or *Tidak perlu*. Kitchen and Bar slips
always print. The choice is recorded so paper use can be reported later.

* `businesses.receipt_mode`: `always` (print every receipt at payment, as the
  system has since prt-3) or `ask`. On for every café that exists when this
  runs (Poernama) and for new cafés; the column default keeps a business row
  made any other way printing as before.
* `orders.receipt_choice` (`paper` | `qr` | `whatsapp` | `none`) and
  `receipt_choice_at`: what the cashier tapped, last.
* `orders.receipt_code`: the unguessable code a web or WhatsApp receipt is
  found by, made only when one is asked for. It begins with the café's own
  prefix so the one bot number shared by every café can tell whose it is.
  Unique across all cafés.

Additive. The downgrade drops the columns and the index.

Revision ID: 0046
Revises: 0045
Create Date: 2026-10-01
"""
from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
alter table businesses add column receipt_mode text not null default 'always';
alter table businesses add constraint businesses_receipt_mode_check
  check (receipt_mode in ('always', 'ask'));

-- The cafés that already exist are the real one: they get the owner's rule.
update businesses set receipt_mode = 'ask';

alter table orders add column receipt_choice text;
alter table orders add constraint orders_receipt_choice_check
  check (receipt_choice is null or receipt_choice in ('paper', 'qr', 'whatsapp', 'none'));
alter table orders add column receipt_choice_at timestamptz;
alter table orders add column receipt_code text;
create unique index orders_receipt_code_key on orders (receipt_code) where receipt_code is not null;
"""

DOWNGRADE_SQL = """
drop index if exists orders_receipt_code_key;
alter table orders drop column if exists receipt_code;
alter table orders drop column if exists receipt_choice_at;
alter table orders drop constraint if exists orders_receipt_choice_check;
alter table orders drop column if exists receipt_choice;
alter table businesses drop constraint if exists businesses_receipt_mode_check;
alter table businesses drop column if exists receipt_mode;
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
