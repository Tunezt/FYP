"""Daily service numbers: "Pesanan 042" (prt-1).

A customer at the counter, a guest at table 7 with a QR order, the barista
and the chef 20 metres away all need to say the same short thing about one
order. The UUID is the order's identity and stays that; the receipt's
eight-character reference stays what old slips print. This adds the number
people say out loud: 001, 002, 003 ... one sequence per business per business
day (the M15-T4 boundary), shared by the till and the QR menu, running past 999
when a day is busy.

`service_number_counters` holds the last number handed out per (business, day).
Allocation is one `insert ... on conflict do update ... returning`, which
Postgres serialises on the row, so two tills and a phone asking at the same
instant get three different numbers, and a number is never derived from how
many orders exist. A cancelled order keeps its number and the counter never
goes back, so a number is never reused within its day. A request that fails
and rolls back takes its increment with it: that number was never shown to
anyone.

On `orders`:
  service_date / service_number  set when the order is first persisted (a held
                                 draft, a QR order, or a sale); kept through
                                 edits, payment, refreshes and retries
  batch_no                       0 for the original; 1, 2 ... for paid additions,
                                 which share the original's number
                                 ("Pesanan 042 · Tambahan 1")
  external_ref                   a driver's or platform's reference typed in by
                                 hand; not an integration

Historical orders are not renumbered: they keep NULL and display their old
reference. A display number is never a credential: nothing authorises on it.

Additive. RLS on the new table in this migration; `orders` already has it.

Revision ID: 0038
Revises: 0037
Create Date: 2026-09-18
"""
from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
create table service_number_counters (
  business_id uuid not null references businesses(id) on delete cascade,
  service_date date not null,
  last_number integer not null check (last_number > 0),
  updated_at timestamptz not null default now(),
  primary key (business_id, service_date)
);
alter table orders add column service_date date;
alter table orders add column service_number integer;
alter table orders add constraint orders_service_number_positive check (service_number is null or service_number > 0);
alter table orders add constraint orders_service_number_dated check ((service_number is null) = (service_date is null));
alter table orders add column batch_no smallint not null default 0;
alter table orders add constraint orders_batch_no_nonnegative check (batch_no >= 0);
alter table orders add column external_ref text;
alter table orders add constraint orders_external_ref_length check (external_ref is null or length(external_ref) <= 40);
create unique index uq_orders_service_number on orders(business_id, service_date, service_number) where service_number is not null and parent_order_id is null;
-- Additions made before this migration are ordered into batches by when they
-- were created; their service numbers stay NULL (history is not renumbered).
update orders o set batch_no = r.rn
from (select id, row_number() over (partition by business_id, parent_order_id order by created_at, id) as rn
      from orders where parent_order_id is not null) r
where o.id = r.id;
create unique index uq_orders_addition_batch on orders(business_id, parent_order_id, batch_no) where parent_order_id is not null;
"""

RLS_SQL = """
alter table service_number_counters enable row level security;
alter table service_number_counters force row level security;
create policy tenant_isolation on service_number_counters
  using (business_id = current_setting('app.current_business_id')::uuid)
  with check (business_id = current_setting('app.current_business_id')::uuid);
"""

DOWNGRADE_SQL = """
drop index if exists uq_orders_addition_batch;
drop index if exists uq_orders_service_number;
alter table orders drop constraint if exists orders_external_ref_length;
alter table orders drop column if exists external_ref;
alter table orders drop constraint if exists orders_batch_no_nonnegative;
alter table orders drop column if exists batch_no;
alter table orders drop constraint if exists orders_service_number_dated;
alter table orders drop constraint if exists orders_service_number_positive;
alter table orders drop column if exists service_number;
alter table orders drop column if exists service_date;
drop table if exists service_number_counters;
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
    _execute_statements(RLS_SQL)


def downgrade() -> None:
    _execute_statements(DOWNGRADE_SQL)
