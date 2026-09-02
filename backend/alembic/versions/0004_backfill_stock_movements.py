"""Backfill stock_movements from pre-ledger sales and receipts (roadmap M2-T4).

Data only — no schema change. The statements live in
app/services/stock_backfill.py so the test that proves them runs the identical
SQL. Items that already have any ledger row are left alone, so applying this on
a database that was seeded after M2-T2 is a no-op, and re-applying is safe.

Runs as the migration role, which bypasses RLS: the backfill is per business by
construction (every row copies business_id from the item it belongs to).

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-03
"""
from alembic import op

from app.services.stock_backfill import BACKFILL_STATEMENTS, ROLLBACK_STATEMENTS

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in BACKFILL_STATEMENTS:
        op.execute(statement.strip())


def downgrade() -> None:
    # Removes exactly the rows this migration created (source_type backfill_*).
    # Rows written live through services/stock.py are untouched.
    for statement in ROLLBACK_STATEMENTS:
        op.execute(statement.strip())
