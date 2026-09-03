"""cash_movements — money moving through the till that is not a sale (roadmap M7-T2).

Petty cash out, supplier paid from the drawer, a bank drop, or cash put in.
Each row posts to the ledger in the same transaction (petty cash through the
expense writer, the rest through the posting engine), and carries the shift
it happened in so the close (M7-T3) can expect it.

Also backfills the posting rules these events use for businesses that
already exist; registration seeds them from services/posting_rules.

Additive. RLS in this migration.

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-03
"""
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
create type cash_movement_kind as enum ('cash_in', 'petty_cash', 'supplier_payment', 'bank_drop');

create table cash_movements (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  shift_id uuid references shifts(id),          -- the till it moved through, when one was open
  staff_id uuid references staff(id),
  kind cash_movement_kind not null,
  via text not null,                            -- cash_in: owner|bank · supplier_payment: cash|transfer · others: cash
  amount numeric(12,2) not null check (amount > 0),
  reason text not null,
  category text,                                -- petty cash: expense category
  supplier_id uuid references suppliers(id),
  expense_id uuid references expenses(id),      -- petty cash: the expense row it wrote
  occurred_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  check (kind <> 'supplier_payment' or supplier_id is not null),
  check (kind <> 'petty_cash' or expense_id is not null)
);
create index idx_cash_movements_shift on cash_movements(shift_id);
create index idx_cash_movements_business_time on cash_movements(business_id, occurred_at desc);
"""

RLS_SQL = """
alter table cash_movements enable row level security;
alter table cash_movements force row level security;
create policy tenant_isolation on cash_movements
  using (business_id = current_setting('app.current_business_id')::uuid)
  with check (business_id = current_setting('app.current_business_id')::uuid);
"""

# Frozen copy of the rules this migration introduces (services/posting_rules
# is the living definition; a migration must not change when it does).
NEW_RULES = [
    ("CashIn", "owner", "1100", "3100", "Kas masuk dari pemilik"),
    ("CashIn", "bank", "1100", "1110", "Kas masuk dari bank"),
    ("BankDrop", "cash", "1110", "1100", "Setor kas ke bank"),
    ("SupplierPaid", "cash", "2100", "1100", "Bayar supplier tunai dari laci"),
    ("SupplierPaid", "transfer", "2100", "1110", "Bayar supplier lewat transfer"),
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
drop table if exists cash_movements;
drop type if exists cash_movement_kind;
delete from posting_rules where is_system and event_type in ('CashIn', 'BankDrop', 'SupplierPaid');
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
    _execute_statements(SCHEMA_SQL)
    _execute_statements(RLS_SQL)
    _execute_statements(BACKFILL_SQL.format(values=_sql_values(NEW_RULES)))


def downgrade() -> None:
    _execute_statements(DOWNGRADE_SQL)
