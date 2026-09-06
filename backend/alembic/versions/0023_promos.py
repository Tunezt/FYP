"""promos — the promo engine (roadmap M8-T3).

`promos` is the reward: a percentage or an amount off an item or the whole
bill, or a bonus item (buy N of A, get M of B — B may be A: a BOGO). Its
`promo_conditions` are ANDed: date range, day of week, time window (in the
business's own timezone), minimum spend, multiples. There is no activate /
expire job: a promo is open exactly when every condition holds at the moment
of the sale, so it starts and stops applying by itself.

`promo_applications` records what applied to which order (and line), for the
receipt, for reversals, and for `get_promo_performance` later. `orders` gets
`promo_total`, kept apart from `discount_total` so the cost of a campaign and
the cost of a cashier's discounts never blur into one number.

The cost is posted to a new contra-revenue account `4250 Diskon promo`,
backfilled into every existing chart, with the rules to match.

Additive. RLS in this migration.

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-06
"""
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
create type promo_kind as enum ('percent_off', 'amount_off', 'bonus_item');
create type promo_condition_kind as enum ('date_range', 'day_of_week', 'time_window', 'min_spend', 'multiples');

create table promos (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  name text not null check (length(trim(name)) > 0),
  kind promo_kind not null,
  value numeric(12,4) not null default 0 check (value >= 0),        -- percent_off: 0.10 = 10% · amount_off: rupiah · bonus_item: unused
  item_id uuid references items(id),                                 -- what it applies to / what must be bought; null = whole bill (percent/amount only)
  bonus_item_id uuid references items(id),                           -- bonus_item: what is given (null = the same item)
  bonus_quantity numeric(12,3) not null default 1 check (bonus_quantity > 0),
  max_per_order integer check (max_per_order is null or max_per_order > 0),
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (kind <> 'bonus_item' or item_id is not null),
  check (kind <> 'percent_off' or value <= 1)
);
create index idx_promos_business_active on promos(business_id, is_active);

create table promo_conditions (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  promo_id uuid not null references promos(id) on delete cascade,
  kind promo_condition_kind not null,
  starts_at timestamptz,                    -- date_range
  ends_at timestamptz,                      -- date_range (exclusive)
  days_of_week integer[],                   -- day_of_week: 0 = Monday … 6 = Sunday, business-local
  time_start time,                          -- time_window, business-local, inclusive
  time_end time,                            -- time_window, exclusive; may be before time_start (crosses midnight)
  amount numeric(12,2),                     -- min_spend: net after cashier discounts must reach this
  quantity numeric(12,3),                   -- multiples: buy this many of item_id per application
  created_at timestamptz not null default now(),
  check (kind <> 'date_range' or starts_at is not null or ends_at is not null),
  check (kind <> 'day_of_week' or (days_of_week is not null and cardinality(days_of_week) > 0)),
  check (kind <> 'time_window' or (time_start is not null and time_end is not null)),
  check (kind <> 'min_spend' or (amount is not null and amount > 0)),
  check (kind <> 'multiples' or (quantity is not null and quantity > 0))
);
create index idx_promo_conditions_promo on promo_conditions(promo_id);

create table promo_applications (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  order_id uuid not null references orders(id),
  promo_id uuid not null references promos(id),
  order_line_id uuid references order_lines(id),   -- the line the reward landed on (null = whole bill)
  amount numeric(12,2) not null check (amount >= 0), -- rupiah given away by this application
  bonus_quantity numeric(12,3) not null default 0,   -- units given for free (bonus_item)
  created_at timestamptz not null default now()
);
create index idx_promo_applications_order on promo_applications(order_id);
create index idx_promo_applications_promo on promo_applications(promo_id, created_at desc);

alter table orders add column promo_total numeric(12,2) not null default 0;
"""

RLS_SQL = """
alter table promos enable row level security;
alter table promos force row level security;
create policy tenant_isolation on promos
  using (business_id = current_setting('app.current_business_id')::uuid)
  with check (business_id = current_setting('app.current_business_id')::uuid);

alter table promo_conditions enable row level security;
alter table promo_conditions force row level security;
create policy tenant_isolation on promo_conditions
  using (business_id = current_setting('app.current_business_id')::uuid)
  with check (business_id = current_setting('app.current_business_id')::uuid);

alter table promo_applications enable row level security;
alter table promo_applications force row level security;
create policy tenant_isolation on promo_applications
  using (business_id = current_setting('app.current_business_id')::uuid)
  with check (business_id = current_setting('app.current_business_id')::uuid);
"""

# Frozen copies (services/accounts and services/posting_rules are the living definitions).
NEW_ACCOUNTS = [("4250", "Diskon promo", "revenue")]
NEW_RULES = [
    ("OrderCompleted", "promo", "4250", "4100", "Biaya promo (kontra pendapatan)"),
    ("OrderRefunded", "promo_reversal", "4100", "4250", "Retur: promo dibatalkan"),
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

DOWNGRADE_SQL = """
delete from posting_rules where is_system and component in ('promo', 'promo_reversal');
alter table orders drop column if exists promo_total;
drop table if exists promo_applications;
drop table if exists promo_conditions;
drop table if exists promos;
drop type if exists promo_condition_kind;
drop type if exists promo_kind;
"""
# The 4250 account is left in place on downgrade: accounts may already carry
# journal lines, and the ledger never deletes.


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
    _execute_statements(BACKFILL_ACCOUNTS_SQL.format(values=_sql_values(NEW_ACCOUNTS)))
    _execute_statements(BACKFILL_RULES_SQL.format(values=_sql_values(NEW_RULES)))


def downgrade() -> None:
    _execute_statements(DOWNGRADE_SQL)
