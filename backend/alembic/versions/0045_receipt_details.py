"""What a customer's receipt needs to be a receipt (till-5a).

The first hands-on run printed a receipt headed only "POERNAMA": no address,
no way to reach the café, a tax line called "Pajak", and for a cash payment no
record of the money handed over or the change given back.

* `businesses.address`, `contact_phone`, `instagram`: the café's details, set
  in Pengaturan and printed under its name. Placeholders until the owner types
  the real ones (the settings page marks them).
* `pricing_settings.tax_label`: what the tax is called on paper. Restaurant
  food and drink is the regional PBJT (formerly PB1), not PPN.
* `payments.tendered`: the cash the customer handed over, when the cashier
  typed it. NULL means not recorded (exact money, or a non-cash payment); a
  value is never less than the payment it belongs to.

Additive: columns only, all nullable or defaulted. The downgrade drops them.

Revision ID: 0045
Revises: 0044
Create Date: 2026-10-01
"""
from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
alter table businesses add column address text;
alter table businesses add column contact_phone text;
alter table businesses add column instagram text;

alter table pricing_settings add column tax_label text not null default 'Pajak';
alter table pricing_settings add constraint pricing_settings_tax_label_check
  check (length(btrim(tax_label)) between 1 and 20);

alter table payments add column tendered numeric(12,2);
alter table payments add constraint payments_tendered_check
  check (tendered is null or (method = 'cash' and tendered >= amount));
"""

DOWNGRADE_SQL = """
alter table payments drop constraint if exists payments_tendered_check;
alter table payments drop column if exists tendered;
alter table pricing_settings drop constraint if exists pricing_settings_tax_label_check;
alter table pricing_settings drop column if exists tax_label;
alter table businesses drop column if exists instagram;
alter table businesses drop column if exists contact_phone;
alter table businesses drop column if exists address;
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
