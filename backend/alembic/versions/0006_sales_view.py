"""`sales` becomes a view over the order model (roadmap M3-T2).

The single authorised structural change (roadmap §1.6). In one migration:

  1. `alter table sales rename to sales_legacy` — the one-row-per-item table is
     kept as an archive, never read by application code again.
  2. Backfill `orders` / `order_lines` / `payments` from `sales_legacy`: one order
     per legacy sale, one line whose **id is the legacy sale id** (so every
     existing reference — `stock_movements.source_id`, receipts, logs — still
     resolves through the view), one payment of the full amount with method
     `other` (the legacy row never recorded how it was paid; `other` is the
     honest value, not a guess). `unit_cost_at_sale` is NULL for legacy lines:
     the cost at the time is unknown. Idempotent: legacy sales whose id already
     exists as a line are skipped, so gate 2's downgrade/upgrade round-trip is
     safe.
  3. `create view sales` shaped exactly like the old table
     (`id, business_id, item_id, quantity, unit_price, total_price, staff_id,
     sold_at`) with `security_invoker = true`, so the underlying tables'
     tenant_isolation policies apply to every read through the view. The eight
     assistant tools, the dashboard, anomaly and velocity keep reading `Sale`
     untouched. `sales` is read-only from here on.
  4. Write paths (services/sales.py, app/seed.py) move to the order model in the
     same commit.

Rollback: drop the view, rename `sales_legacy` back. Orders created after this
migration stay in `orders` (additive) and reappear in the view on re-upgrade.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-03
"""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
alter table sales rename to sales_legacy;

-- 2. Backfill: one order per legacy sale, line id = legacy sale id.
create temp table legacy_to_backfill on commit drop as
select s.*, gen_random_uuid() as new_order_id
from sales_legacy s
where not exists (select 1 from order_lines ol where ol.id = s.id);

insert into orders (id, business_id, staff_id, order_type, status, subtotal, discount_total,
                    tax_total, service_charge, rounding, total, sold_at, created_at)
select new_order_id, business_id, staff_id, 'takeaway', 'completed', total_price, 0,
       0, 0, 0, total_price, sold_at, sold_at
from legacy_to_backfill;

insert into order_lines (id, business_id, order_id, item_id, quantity, unit_price, line_discount,
                         line_total, unit_cost_at_sale, created_at)
select id, business_id, new_order_id, item_id, quantity, unit_price, 0,
       total_price, null, sold_at
from legacy_to_backfill;

insert into payments (business_id, order_id, method, amount, reference, created_at)
select business_id, new_order_id, 'other', total_price, 'legacy', sold_at
from legacy_to_backfill;

-- 3. The compatibility view. security_invoker: reads run as the caller, so RLS on
--    order_lines/orders applies (Postgres 15+).
create view sales with (security_invoker = true) as
select ol.id,
       ol.business_id,
       ol.item_id,
       ol.quantity,
       ol.unit_price,
       ol.line_total as total_price,
       o.staff_id,
       o.sold_at
from order_lines ol
join orders o on o.id = ol.order_id;
"""

DOWNGRADE_SQL = """
drop view if exists sales;
alter table sales_legacy rename to sales;
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
    _execute_statements(UPGRADE_SQL)


def downgrade() -> None:
    _execute_statements(DOWNGRADE_SQL)
