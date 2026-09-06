"""vouchers — codes with an expiry, single-use under concurrency (roadmap M8-T4).

A voucher is a code that takes a percentage or an amount off a bill, valid
between `starts_at` and `expires_at`, `max_uses` times. Bulk creation makes
many codes under one `batch_id`; each is its own row, so each is its own
single-use guard. `uses` is the cached count of redemptions and the thing the
guard decrements: `update … set uses = uses + 1 where id = … and uses <
max_uses returning uses` — the stock race, again — so two simultaneous
redemptions of one code produce exactly one success.

`voucher_redemptions` is append-only: one row per redemption, a negative row
when a refund or void gives the use back. The cost is posted to `4250 Diskon
promo` under its own component so it can be told apart from campaigns.

Additive. RLS in this migration.

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-06
"""
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
create type voucher_kind as enum ('percent_off', 'amount_off');

create table vouchers (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  code text not null check (code ~ '^[A-Z0-9][A-Z0-9-]{2,31}$'),
  kind voucher_kind not null,
  value numeric(12,4) not null check (value > 0),          -- percent_off: 0.10 = 10% (<= 1) · amount_off: rupiah
  max_discount numeric(12,2) check (max_discount is null or max_discount > 0),   -- cap for percent_off
  min_spend numeric(12,2) not null default 0 check (min_spend >= 0),
  starts_at timestamptz,
  expires_at timestamptz,
  max_uses integer not null default 1 check (max_uses > 0),
  uses integer not null default 0 check (uses >= 0),
  batch_id uuid,
  batch_name text,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (kind <> 'percent_off' or value <= 1),
  check (expires_at is null or starts_at is null or expires_at > starts_at)
);
create unique index uq_vouchers_business_code on vouchers(business_id, code);
create index idx_vouchers_batch on vouchers(batch_id) where batch_id is not null;

create table voucher_redemptions (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  voucher_id uuid not null references vouchers(id),
  order_id uuid not null references orders(id),
  customer_id uuid references customers(id),
  amount numeric(12,2) not null,                           -- what the voucher took off; negative on a reversal
  reversal_of uuid references voucher_redemptions(id),     -- set on the row a refund/void writes
  created_at timestamptz not null default now()
);
create index idx_voucher_redemptions_voucher on voucher_redemptions(voucher_id, created_at desc);
create index idx_voucher_redemptions_order on voucher_redemptions(order_id);

alter table orders add column voucher_total numeric(12,2) not null default 0;
"""

RLS_SQL = """
alter table vouchers enable row level security;
alter table vouchers force row level security;
create policy tenant_isolation on vouchers
  using (business_id = current_setting('app.current_business_id')::uuid)
  with check (business_id = current_setting('app.current_business_id')::uuid);

alter table voucher_redemptions enable row level security;
alter table voucher_redemptions force row level security;
create policy tenant_isolation on voucher_redemptions
  using (business_id = current_setting('app.current_business_id')::uuid)
  with check (business_id = current_setting('app.current_business_id')::uuid);
"""

NEW_RULES = [
    ("OrderCompleted", "voucher", "4250", "4100", "Voucher dipakai (kontra pendapatan)"),
    ("OrderRefunded", "voucher_reversal", "4100", "4250", "Retur: voucher dibatalkan"),
]

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
delete from posting_rules where is_system and component in ('voucher', 'voucher_reversal');
alter table orders drop column if exists voucher_total;
drop table if exists voucher_redemptions;
drop table if exists vouchers;
drop type if exists voucher_kind;
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
    _execute_statements(BACKFILL_RULES_SQL.format(values=_sql_values(NEW_RULES)))


def downgrade() -> None:
    _execute_statements(DOWNGRADE_SQL)
