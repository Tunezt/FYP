"""Fix: request_logs.business_id didn't cascade-delete like every other
business-scoped table, contradicting app/seed.py's own docstring ("Idempotent:
re-running deletes and recreates the demo business (cascade)") — re-running the
seed against a DB with any accumulated request_logs rows (e.g. from live
WhatsApp/dashboard/POS traffic) fails with a ForeignKeyViolationError instead of
cascading. Every other table (staff, items, sales, expenses, receipts, alerts,
pending_confirmations) already uses `on delete cascade`; this brings
request_logs in line. business_id stays nullable (a log row can exist for a
sender that never resolved to a business), so cascade only fires for rows that
do reference a real business.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-07
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "alter table request_logs drop constraint request_logs_business_id_fkey;"
    )
    op.execute(
        "alter table request_logs "
        "add constraint request_logs_business_id_fkey "
        "foreign key (business_id) references businesses(id) on delete cascade;"
    )


def downgrade() -> None:
    op.execute(
        "alter table request_logs drop constraint request_logs_business_id_fkey;"
    )
    op.execute(
        "alter table request_logs "
        "add constraint request_logs_business_id_fkey "
        "foreign key (business_id) references businesses(id);"
    )
