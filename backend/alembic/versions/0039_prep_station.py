"""`prep_station` — where an item is made: Bar, Dapur, or nowhere (prt-2).

The café's physical layout decides this, not the item's name. The barista works
at the front beside the cashier; the kitchen is 15-20 metres behind them; some
pastries are simply taken from the display at the front. So each sellable item
says where it is prepared, and the owner sets it:

  bar      made or handed out at the front (coffee, tea, display pastries)
  kitchen  made in the back kitchen (cooked food)
  none     nothing to prepare (a bottle of water from the fridge)

NULL means "not decided yet". Nothing guesses: an unassigned item is routed to
the front, where the person reading the slip is standing next to the cashier
and can walk it back, and its slip says the destination is not set.

`order_lines.prep_station` snapshots the item's station at the moment of sale,
the same way `unit_cost_at_sale` snapshots cost: moving a product from the bar
to the kitchen tomorrow must not rewrite where yesterday's order was made.

Additive. Both tables already carry the tenant_isolation policy.

Revision ID: 0039
Revises: 0038
Create Date: 2026-09-18
"""
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
alter table items add column prep_station text;
alter table items add constraint items_prep_station_check check (prep_station is null or prep_station in ('bar', 'kitchen', 'none'));
alter table order_lines add column prep_station text;
alter table order_lines add constraint order_lines_prep_station_check check (prep_station is null or prep_station in ('bar', 'kitchen', 'none'));
"""

DOWNGRADE_SQL = """
alter table order_lines drop constraint if exists order_lines_prep_station_check;
alter table order_lines drop column if exists prep_station;
alter table items drop constraint if exists items_prep_station_check;
alter table items drop column if exists prep_station;
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


def downgrade() -> None:
    _execute_statements(DOWNGRADE_SQL)
