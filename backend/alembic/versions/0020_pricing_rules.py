"""posting rules for rounding and for undoing a sale's reclassifications (roadmap M7-T4b).

The pricing engine (M7-T4a) can now put a rupiah rounding on the total and a
discount, tax and service charge on the bill. Rounding needs a rule of its own
each way (up is other income, down is a small give-away), and a refund must
undo what the sale moved out of revenue — tax, service charge, discount and
rounding — or a refunded bill would leave a tax liability and service income
standing for money that was handed back.

No table change: this only backfills the new standard rules into every
business that already exists. Registration seeds them from
services/posting_rules for new ones. Additive.

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-06
"""
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

# Frozen copy of the rules this migration introduces (services/posting_rules
# is the living definition; a migration must not change when it does).
NEW_RULES = [
    ("OrderCompleted", "rounding_up", "4100", "4900", "Pembulatan ke atas (pendapatan lain)"),
    ("OrderCompleted", "rounding_down", "4900", "4100", "Pembulatan ke bawah"),
    ("OrderRefunded", "discount_reversal", "4100", "4200", "Retur: diskon penjualan dibatalkan"),
    ("OrderRefunded", "tax_reversal", "2200", "4100", "Retur: pajak yang dipungut dikembalikan"),
    ("OrderRefunded", "service_charge_reversal", "4900", "4100", "Retur: service charge dibatalkan"),
    ("OrderRefunded", "rounding_up_reversal", "4900", "4100", "Retur: pembulatan ke atas dibatalkan"),
    ("OrderRefunded", "rounding_down_reversal", "4100", "4900", "Retur: pembulatan ke bawah dibatalkan"),
]

BACKFILL_SQL = """
insert into posting_rules (business_id, event_type, component, debit_code, credit_code, description, is_system)
select b.id, s.event_type, s.component, s.debit_code, s.credit_code, s.description, true
from businesses b
cross join (values {values}) as s(event_type, component, debit_code, credit_code, description)
where not exists (
  select 1 from posting_rules r where r.business_id = b.id and r.event_type = s.event_type and r.component = s.component
);
"""

DOWNGRADE_SQL = """
delete from posting_rules where is_system and component in (
  'rounding_up', 'rounding_down', 'discount_reversal', 'tax_reversal',
  'service_charge_reversal', 'rounding_up_reversal', 'rounding_down_reversal'
);
"""


def _sql_values(rows) -> str:
    return ", ".join(
        "(" + ", ".join("'" + str(v).replace("'", "''") + "'" for v in row) + ")" for row in rows
    )


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
    _execute_statements(BACKFILL_SQL.format(values=_sql_values(NEW_RULES)))


def downgrade() -> None:
    _execute_statements(DOWNGRADE_SQL)
