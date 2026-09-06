"""alert rules over the registry (roadmap M10-T1).

Four new alert kinds join `anomaly` and `low_stock`: `margin_drop`,
`stockout_risk`, `void_rate`, `supplier_price`. Each alert also carries a
`rule_key` — the rule plus what it is about plus the local day — so a rule can
tell "I already said this today" from "this is new" (the basis M10-T2's
deduplication builds on), and `details` for the figures behind the message.

Postgres cannot remove a value from an enum, so the downgrade leaves the four
values in place (harmless: nothing writes them once the code is gone) and
drops only the two columns. Additive.

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-06
"""
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

# `alter type … add value` cannot run inside the same transaction that uses the
# value, but adding several in one migration is fine; nothing here uses them.
SCHEMA_SQL = """
alter type alert_type add value if not exists 'margin_drop';
alter type alert_type add value if not exists 'stockout_risk';
alter type alert_type add value if not exists 'void_rate';
alter type alert_type add value if not exists 'supplier_price';

alter table alerts add column rule_key text;
alter table alerts add column details jsonb;
create index idx_alerts_rule_key on alerts(business_id, rule_key) where rule_key is not null;
"""

DOWNGRADE_SQL = """
drop index if exists idx_alerts_rule_key;
alter table alerts drop column if exists details;
alter table alerts drop column if exists rule_key;
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
