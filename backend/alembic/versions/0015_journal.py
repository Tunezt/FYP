"""journal_entries, journal_lines, and the balance constraint (roadmap M6-T2).

Double-entry: an entry is a set of lines, each a debit OR a credit on one
account, and the debits must equal the credits. That rule is enforced BY THE
DATABASE with a deferred constraint trigger — checked when the transaction
commits, so lines can be written one at a time inside a transaction, and an
unbalanced entry can never be committed by any code path, including a bug in
the posting engine or a hand-written SQL fix. Application-level checks get
bypassed eventually; a constraint does not.

Postgres has no multi-row CHECK, so the constraint is a CONSTRAINT TRIGGER
declared DEFERRABLE INITIALLY DEFERRED on both tables:
  * journal_lines  — any insert/update/delete re-checks the affected entry
  * journal_entries — an entry with no lines is refused too

Nothing here is ever updated or deleted by application code; corrections are
reversing entries (roadmap §1.5).

Additive only. RLS on both tables in this migration.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-03
"""
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
create table journal_entries (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  entry_no integer not null,                    -- per-business running number
  posted_at timestamptz not null default now(),
  memo text,
  source_type text,                             -- 'order', 'goods_receipt', 'expense', 'shift', …
  source_id uuid,
  event_type text,                              -- the domain event that produced it (M6-T4)
  created_by uuid references staff(id),
  created_at timestamptz not null default now()
);
create unique index uq_journal_entries_no on journal_entries(business_id, entry_no);
create index idx_journal_entries_time on journal_entries(business_id, posted_at desc);
create index idx_journal_entries_source on journal_entries(source_type, source_id);

create table journal_lines (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  entry_id uuid not null references journal_entries(id),
  line_no integer not null default 0,           -- position within the entry (debits first by convention)
  account_id uuid not null references accounts(id),
  debit numeric(12,2) not null default 0 check (debit >= 0),
  credit numeric(12,2) not null default 0 check (credit >= 0),
  memo text,
  created_at timestamptz not null default now(),
  check ((debit = 0) <> (credit = 0))          -- exactly one side, and not both zero
);
create index idx_journal_lines_entry on journal_lines(entry_id);
create index idx_journal_lines_account on journal_lines(business_id, account_id);

-- SECURITY DEFINER: the check must see every line of the entry whatever tenant
-- context (or none — a business being removed) the caller has; RLS on the
-- tables still governs what application code can read and write.
create or replace function journal_entry_must_balance() returns trigger
language plpgsql security definer set search_path = public as $fn$
declare
  v_entry uuid;
  v_debit numeric(14,2);
  v_credit numeric(14,2);
  v_lines integer;
begin
  if tg_table_name = 'journal_entries' then
    v_entry := new.id;
  elsif tg_op = 'DELETE' then
    v_entry := old.entry_id;
  else
    v_entry := new.entry_id;
  end if;
  -- The entry itself is gone (a business being removed cascades through
  -- entries and lines alike): nothing left to balance.
  if not exists (select 1 from journal_entries where id = v_entry) then
    return null;
  end if;
  select coalesce(sum(debit), 0), coalesce(sum(credit), 0), count(*)
    into v_debit, v_credit, v_lines
    from journal_lines where entry_id = v_entry;
  if v_lines = 0 then
    raise exception 'journal entry % has no lines', v_entry using errcode = 'check_violation';
  end if;
  if v_debit <> v_credit then
    raise exception 'journal entry % is unbalanced: debit % credit %', v_entry, v_debit, v_credit
      using errcode = 'check_violation';
  end if;
  return null;
end
$fn$;

create constraint trigger journal_lines_balance
  after insert or update or delete on journal_lines
  deferrable initially deferred
  for each row execute function journal_entry_must_balance();

create constraint trigger journal_entries_balance
  after insert on journal_entries
  deferrable initially deferred
  for each row execute function journal_entry_must_balance();
"""

RLS_TABLES = ["journal_entries", "journal_lines"]

RLS_SQL_TEMPLATE = """
alter table {table} enable row level security;
alter table {table} force row level security;
create policy tenant_isolation on {table}
  using (business_id = current_setting('app.current_business_id')::uuid)
  with check (business_id = current_setting('app.current_business_id')::uuid);
"""

DOWNGRADE_SQL = """
drop trigger if exists journal_entries_balance on journal_entries;
drop trigger if exists journal_lines_balance on journal_lines;
drop function if exists journal_entry_must_balance();
drop table if exists journal_lines;
drop table if exists journal_entries;
"""


# ── statement helpers, copied from 0001_initial_schema.py (roadmap §1.7) ──────
# The splitter ignores ';' inside single-quoted strings and '--' comments; the
# function body above is delimited with $fn$ and contains ';', so it is split
# by hand: everything before the function, the function, everything after.

def _split_statements(sql: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    in_comment = False
    in_string = False
    in_dollar = False
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if in_dollar:
            current.append(ch)
            if sql[i : i + 4] == "$fn$":
                current.append(sql[i + 1 : i + 4])
                i += 3
                in_dollar = False
        elif in_comment:
            current.append(ch)
            in_comment = ch != "\n"
        elif in_string:
            current.append(ch)
            if ch == "'":
                in_string = False
        elif sql[i : i + 4] == "$fn$":
            current.append("$fn$")
            i += 3
            in_dollar = True
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
    for table in RLS_TABLES:
        _execute_statements(RLS_SQL_TEMPLATE.format(table=table))


def downgrade() -> None:
    _execute_statements(DOWNGRADE_SQL)
