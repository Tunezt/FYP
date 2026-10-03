"""`items.menu_category` — the heading a product sits under on the till and the QR menu (till-10).

The owner's feedback of 2 October 2026 (point 4): the till showed every product
in one alphabetical grid; a café menu is read by section — coffee, non-coffee,
food, snacks, dessert. Each sellable item now carries the name of its section,
free text so the owner can name sections the way the café does (the dashboard
suggests Kopi, Non-kopi, Makanan, Camilan, Dessert). NULL means not sorted yet:
the till and the QR menu list it under "Lainnya", last.

Ingredients (no selling price) never appear on a menu, so they need none.

Additive; `items` already carries the tenant_isolation policy. The check keeps
a name a heading can show (1 to 40 characters, not blank).

Revision ID: 0047
Revises: 0046
Create Date: 2026-10-02
"""
from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
alter table items add column menu_category text;
alter table items add constraint items_menu_category_check
  check (menu_category is null or (length(btrim(menu_category)) between 1 and 40));
"""

DOWNGRADE_SQL = """
alter table items drop constraint if exists items_menu_category_check;
alter table items drop column if exists menu_category;
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
