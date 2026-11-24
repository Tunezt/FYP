"""The old receipt example values become empty (ui-struk-placeholders).

A new cafe used to start with "Jl. Lorem Ipsum No. 1, Kota Dolor",
"0812-0000-0000" and "@poernama.cafe" stored as if they were real, marked
"Contoh" in the settings and printed on the receipt unless the owner replaced
them. The owner (24 Nov 2026): an example belongs in a grey input placeholder,
not in the stored value, and red means error. A new cafe now starts empty and
an empty detail is left off the receipt.

This clears every business whose stored value is still exactly one of the three
old examples. Values the owner typed are never touched. Data only, no schema
change.

The downgrade does nothing, deliberately: it could not tell a value this
migration cleared from one the owner emptied afterwards, and writing "Lorem
Ipsum" back onto a real receipt is exactly the harm this change removes.

Revision ID: 0052
Revises: 0051
Create Date: 2026-11-24
"""
from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None

SCHEMA_SQL = """
update businesses set address = null where trim(address) = 'Jl. Lorem Ipsum No. 1, Kota Dolor';
update businesses set contact_phone = null where trim(contact_phone) = '0812-0000-0000';
update businesses set instagram = null where trim(instagram) = '@poernama.cafe';
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


def downgrade() -> None:
    # See the module docstring: the old example values are not written back.
    pass
