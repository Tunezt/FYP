"""The café's logo and its own closing line on the receipt (till-12).

The owner's feedback of 2 October 2026 (point 6): the receipt should open with
the café's logo — the lettering of its signage — not the name in plain capitals,
and read like the receipts of established cafés: the logo, the address, the
order number large between rules, the details as label and value, each item
with its quantity and unit price, the total set apart, and a closing line of
the café's own (the Wi-Fi, a thank-you).

* `businesses.receipt_logo`: the logo as a 1-bit bitmap the printer prints
  dot for dot (`{width, height, bits}`; `bits` is base64, rows of `width/8`
  bytes, most significant bit first, 1 = ink). NULL prints the name in text,
  as before. The dashboard turns the café's wordmark on and off.
* `businesses.receipt_footer`: up to 200 characters printed above the thanks.
  NULL prints nothing extra.

Additive; `businesses` deliberately has no RLS (CLAUDE.md) and gets none here.

Revision ID: 0048
Revises: 0047
Create Date: 2026-10-02
"""
from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
alter table businesses add column receipt_logo jsonb;
alter table businesses add constraint businesses_receipt_logo_check
  check (receipt_logo is null or jsonb_typeof(receipt_logo) = 'object');
alter table businesses add column receipt_footer text;
alter table businesses add constraint businesses_receipt_footer_check
  check (receipt_footer is null or length(receipt_footer) <= 200);
"""

DOWNGRADE_SQL = """
alter table businesses drop constraint if exists businesses_receipt_footer_check;
alter table businesses drop column if exists receipt_footer;
alter table businesses drop constraint if exists businesses_receipt_logo_check;
alter table businesses drop column if exists receipt_logo;
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
