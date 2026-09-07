"""`pin_attempts` — brute-force protection for PIN entry (roadmap M15-T12).

`POST /auth/verify-otp` has counted attempts since M0. `POST /pos/login` and
`verify_manager_pin` counted nothing, and the second of those is the gate on
voids, refunds and discounts — the three ways money leaves.

A 4-digit PIN is 10,000 combinations. The threat is not a stranger on the
internet; it is the person holding the tablet all shift, and the pairing link is
a bearer URL that anyone who has seen it can replay from their own phone.

**The design constraint is that a café cannot have its till hard-locked during a
rush.** A lockout that protects the money by stopping trade is a denial of
service the owner disables within a week, and then there is no protection at
all. So this table holds an *escalating cooldown* rather than a lock: a counter
in a rolling window, cleared the moment a correct PIN arrives, and clearable by
the owner from the dashboard.

One row per (scope, subject): a staff member's own PIN, a device's whole
generation, or a cashier's attempts at somebody's manager PIN. `locked_until` is
advisory — the check is done in Python against the row, so changing the
thresholds needs no migration.

Additive. RLS in this migration.

Revision ID: 0034
Revises: 0033
Create Date: 2026-09-07
"""
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
create table pin_attempts (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  scope text not null,
  subject text not null,
  failures integer not null default 0,
  first_failed_at timestamptz not null default now(),
  last_failed_at timestamptz not null default now(),
  locked_until timestamptz,
  constraint pin_attempts_scope_check check (scope in ('pos_login', 'pos_device', 'manager_pin')),
  constraint pin_attempts_failures_positive check (failures >= 0),
  constraint pin_attempts_unique_subject unique (business_id, scope, subject)
);
create index idx_pin_attempts_active on pin_attempts(business_id, last_failed_at desc);
"""

RLS_SQL = """
alter table pin_attempts enable row level security;
alter table pin_attempts force row level security;
create policy tenant_isolation on pin_attempts
  using (business_id = current_setting('app.current_business_id')::uuid)
  with check (business_id = current_setting('app.current_business_id')::uuid);
"""

DOWNGRADE_SQL = """
drop table if exists pin_attempts;
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
    _execute_statements(RLS_SQL)


def downgrade() -> None:
    _execute_statements(DOWNGRADE_SQL)
