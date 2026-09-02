"""stock_movements — the append-only stock ledger (roadmap M2-T1).

`items.current_stock` stops being the truth and becomes a cached projection over
this table: every change to stock writes one signed `qty_delta` row here in the
same transaction (M2-T2), and SUM(qty_delta) per item must equal
`items.current_stock` (the M2-T3 invariant). Nothing in this table is ever
updated or deleted — voids, refunds and corrections write reversing rows.

`unit_cost` is the cost per unit at the time of the movement when it is known
(a purchase, a sale's cost snapshot); NULL when it is not, never a guess.

Additive only. RLS policy applied in this migration, per roadmap §1.1.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-03
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
create type stock_movement_reason as enum (
  'sale', 'sale_void', 'refund', 'purchase', 'waste',
  'production_in', 'production_out', 'opname', 'correction'
);

create table stock_movements (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  item_id uuid not null references items(id),
  qty_delta numeric(12,3) not null,  -- signed: negative leaves stock, positive enters
  reason stock_movement_reason not null,
  source_type text,                  -- 'sale', 'receipt', 'order', … the originating row's table
  source_id uuid,                    -- that row's id; no FK so history survives the M3 order model
  unit_cost numeric(12,2),           -- cost per unit at movement time; NULL when unknown
  staff_id uuid references staff(id),
  created_at timestamptz not null default now()
);
create index idx_stock_movements_item_time
  on stock_movements(business_id, item_id, created_at desc);
"""

RLS_SQL = """
alter table stock_movements enable row level security;
alter table stock_movements force row level security;
create policy tenant_isolation on stock_movements
  using (business_id = current_setting('app.current_business_id')::uuid)
  with check (business_id = current_setting('app.current_business_id')::uuid);
"""

DOWNGRADE_SQL = """
drop table if exists stock_movements;
drop type if exists stock_movement_reason;
"""


# ── statement helpers, copied from 0001_initial_schema.py (roadmap §1.7) ──────
# The asyncpg dialect executes via PREPARE, which refuses multi-command strings.

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
