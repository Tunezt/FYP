"""Order type routing (roadmap M11-T3): the type decides how the bill is built.

Two knobs on `pricing_settings`:
  service_applies_to   which order types carry the service charge — default
                       all four, i.e. exactly today's behaviour; a café sets it
                       to {dine_in}. Named for what it holds, not for the charge
                       it governs: a column named "charge" must hold rupiah.
  delivery_fee         a flat fee added to `delivery` orders, default 0

Two columns on `orders`: `delivery_fee` (what this order was charged) and
`delivery_address` (where it went). The fee is its own posting component,
reclassified out of sales into a new revenue account `4910 Pendapatan ongkos
kirim`; a refund gives it back. The account and both rules are backfilled for
every existing business, the same way 0023 did for promos.

Additive. `pricing_settings` and `orders` already carry the tenant policy.

Revision ID: 0028
Revises: 0027
Create Date: 2026-09-06
"""
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
alter table pricing_settings add column service_applies_to text[] not null default '{dine_in,takeaway,delivery,pickup}';
alter table pricing_settings add column delivery_fee numeric(12,2) not null default 0;
alter table pricing_settings add constraint pricing_settings_delivery_fee_check check (delivery_fee >= 0);
alter table orders add column delivery_fee numeric(12,2) not null default 0;
alter table orders add column delivery_address text;
"""

# Frozen copies (services/accounts and services/posting_rules are the living definitions).
NEW_ACCOUNTS = [("4910", "Pendapatan ongkos kirim", "revenue")]
NEW_RULES = [
    ("OrderCompleted", "delivery_fee", "4100", "4910", "Ongkos kirim diakui sebagai pendapatan lain"),
    ("OrderRefunded", "delivery_fee_reversal", "4910", "4100", "Retur: ongkos kirim dikembalikan"),
]

BACKFILL_ACCOUNTS_SQL = """
insert into accounts (business_id, code, name, type, is_system)
select b.id, s.code, s.name, s.type::account_type, true
from businesses b
cross join (values {values}) as s(code, name, type)
where not exists (select 1 from accounts a where a.business_id = b.id and a.code = s.code);
"""

BACKFILL_RULES_SQL = """
insert into posting_rules (business_id, event_type, component, debit_code, credit_code, description, is_system)
select b.id, s.event_type, s.component, s.debit_code, s.credit_code, s.description, true
from businesses b
cross join (values {values}) as s(event_type, component, debit_code, credit_code, description)
where not exists (
  select 1 from posting_rules r where r.business_id = b.id and r.event_type = s.event_type and r.component = s.component
);
"""

# Accounts stay (a journal line may reference 4910); the rules and columns go.
DOWNGRADE_SQL = """
delete from posting_rules where is_system and component in ('delivery_fee', 'delivery_fee_reversal');
alter table orders drop column if exists delivery_address;
alter table orders drop column if exists delivery_fee;
alter table pricing_settings drop constraint if exists pricing_settings_delivery_fee_check;
alter table pricing_settings drop column if exists delivery_fee;
alter table pricing_settings drop column if exists service_applies_to;
"""


def _sql_values(rows) -> str:
    def lit(v: str) -> str:
        return "'" + v.replace("'", "''") + "'"

    return ", ".join("(" + ", ".join(lit(v) for v in row) + ")" for row in rows)


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
    _execute_statements(BACKFILL_ACCOUNTS_SQL.format(values=_sql_values(NEW_ACCOUNTS)))
    _execute_statements(BACKFILL_RULES_SQL.format(values=_sql_values(NEW_RULES)))


def downgrade() -> None:
    _execute_statements(DOWNGRADE_SQL)
