"""Print bridge (prt-8): honest device outcomes, held stale jobs, printer status.

A print bridge on the café network now pulls jobs and drives the printers. That
needs the queue to remember a little more, all of it additive:

print_jobs
  evidence      how a device knows a job printed: `printer_status` (the printer
                answered a status request queued behind the job's cut) or
                `bytes_delivered` (it accepted the bytes, nothing more). Null for
                a person's confirmation (confirmed_by) and older rows.
  uncertain_at  a device reported that paper may or may not exist (connection
                cut mid-job, no confirmation). The job stays `claimed`; the till
                shows it as uncertain at once instead of after 90 seconds.
  released_at / released_by
                a job held back for being old was let through by a person, and
                prints marked TERLAMBAT.
  withdrawn_by  a person decided a held or failed job is not needed; the row
                becomes `cancelled`, nothing is deleted.

print_devices (new, RLS in this migration)
  the last word from each bridge worker: is its printer ready, out of paper,
  cover open, unreachable. One row per (business, printer, device name). The
  `test_*` columns carry the owner's setup test: the dashboard asks for a test
  ticket, the bridge prints it on its next poll and says what happened.

Downgrade drops the added columns and the table, like 0039.

Revision ID: 0042
Revises: 0041
Create Date: 2026-09-19
"""
from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
alter table print_jobs add column evidence text
  check (evidence in ('printer_status', 'bytes_delivered'));
alter table print_jobs add column uncertain_at timestamptz;
alter table print_jobs add column released_at timestamptz;
alter table print_jobs add column released_by uuid references staff(id);
alter table print_jobs add column withdrawn_by uuid references staff(id);

create table print_devices (
  business_id uuid not null references businesses(id) on delete cascade,
  printer text not null check (printer in ('front', 'kitchen')),
  device text not null,
  state text not null check (state in ('ready', 'reachable', 'paper_low', 'paper_out', 'cover_open', 'offline', 'error', 'unknown')),
  detail text,
  version text,
  last_seen_at timestamptz not null default now(),
  test_requested_at timestamptz,
  test_result text check (test_result in ('printed', 'delivered', 'uncertain', 'failed')),
  test_detail text,
  test_result_at timestamptz,
  created_at timestamptz not null default now(),
  primary key (business_id, printer, device)
);
"""

RLS_SQL = """
alter table print_devices enable row level security;
alter table print_devices force row level security;
create policy tenant_isolation on print_devices
  using (business_id = current_setting('app.current_business_id')::uuid)
  with check (business_id = current_setting('app.current_business_id')::uuid);
"""

DOWNGRADE_SQL = """
drop table if exists print_devices;
alter table print_jobs drop column if exists withdrawn_by;
alter table print_jobs drop column if exists released_by;
alter table print_jobs drop column if exists released_at;
alter table print_jobs drop column if exists uncertain_at;
alter table print_jobs drop column if exists evidence;
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
