"""`print_jobs` — paper that must come out, remembered by the server (prt-3).

Two printers: the **front** printer beside the cashier and barista prints the
customer's receipt and a separate Bar slip; the **kitchen** printer 15-20 metres
back prints the Dapur slip. A job is written in the same transaction as the
payment it belongs to, so a refreshed page, a dropped connection or a dead
printer cannot lose it, and a printer failing can never roll the sale back.

Each job carries its document frozen at creation (`document` jsonb: printer-
neutral blocks, no ESC/POS), so a reprint an hour later prints what was sold,
not what the catalogue says now.

`dedupe_key` is unique per business: one receipt, one Bar slip and one Dapur
slip per financial order, whatever retries happen. A reprint is a new row
pointing at the job it copies (`reprint_of`) and is marked on paper as a reprint.

States are what the system actually knows:
  pending    waiting for a printer (or a person) to take it
  claimed    a printer device took it and has not reported back — once that is
             old, the screen calls it *uncertain*: paper may or may not exist
  printed    a device reported success, or a person confirmed the paper is in hand
  failed     a device reported it could not print
  cancelled  withdrawn before anything took it (the order was voided first)
Nothing is deleted.

Additive. RLS in this migration.

Revision ID: 0040
Revises: 0039
Create Date: 2026-09-18
"""
from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
create table print_jobs (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  order_id uuid not null references orders(id),
  printer text not null check (printer in ('front', 'kitchen')),
  kind text not null check (kind in ('receipt', 'bar_ticket', 'kitchen_ticket', 'bar_cancel', 'kitchen_cancel')),
  copy text not null default 'original' check (copy in ('original', 'reprint')),
  reprint_of uuid references print_jobs(id),
  dedupe_key text not null,
  document jsonb not null,
  status text not null default 'pending' check (status in ('pending', 'claimed', 'printed', 'failed', 'cancelled')),
  attempts integer not null default 0 check (attempts >= 0),
  claimed_at timestamptz,
  claimed_by text,
  printed_at timestamptz,
  confirmed_by uuid references staff(id),
  failed_at timestamptz,
  error text,
  created_by uuid references staff(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (business_id, dedupe_key)
);
create index idx_print_jobs_queue on print_jobs(business_id, printer, status, created_at);
create index idx_print_jobs_order on print_jobs(business_id, order_id);
"""

RLS_SQL = """
alter table print_jobs enable row level security;
alter table print_jobs force row level security;
create policy tenant_isolation on print_jobs
  using (business_id = current_setting('app.current_business_id')::uuid)
  with check (business_id = current_setting('app.current_business_id')::uuid);
"""

DOWNGRADE_SQL = """
drop table if exists print_jobs;
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
