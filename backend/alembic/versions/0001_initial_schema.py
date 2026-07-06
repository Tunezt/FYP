"""Initial schema — DDL exactly as specified in PROJECT_BRIEF.md Section 6,
plus RLS policies on every business-scoped table and two additive support
tables (pending_confirmations, login_otps) logged in docs/progress.md.

Revision ID: 0001
Revises:
Create Date: 2026-07-07
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA_SQL = """
create extension if not exists vector;
create extension if not exists pgcrypto;

create type staff_role as enum ('owner', 'staff');
create type expense_source as enum ('manual', 'receipt');
create type alert_type as enum ('anomaly', 'low_stock');
create type alert_severity as enum ('low', 'medium', 'high');

create table businesses (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  business_type text not null default 'cafe',
  owner_phone text not null unique,
  whatsapp_number text,
  language_preference text not null default 'id',
  timezone text not null default 'Asia/Jakarta',
  onboarding_completed_at timestamptz,
  created_at timestamptz not null default now()
);

create table staff (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  name text not null,
  role staff_role not null default 'staff',
  phone text,
  pin_hash text not null,
  is_active boolean not null default true,
  created_at timestamptz not null default now()
);
create index idx_staff_business on staff(business_id);

create table items (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  name text not null,
  unit text not null,
  current_stock numeric(12,3) not null default 0 check (current_stock >= 0),
  cost_price numeric(12,2) not null default 0,
  sell_price numeric(12,2) not null default 0,
  reorder_threshold numeric(12,3) not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index idx_items_business on items(business_id);

create table sales (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  item_id uuid not null references items(id),
  quantity numeric(12,3) not null check (quantity > 0),
  unit_price numeric(12,2) not null,
  total_price numeric(12,2) not null,
  staff_id uuid not null references staff(id),
  sold_at timestamptz not null default now()
);
create index idx_sales_business_time on sales(business_id, sold_at desc);

create table receipts (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  image_url text not null,
  parsed_data jsonb not null default '{}',
  supplier text,
  total_amount numeric(12,2),
  embedding vector(768), -- gemini-embedding-001, truncated to 768 dims for storage/speed at this scale
  occurred_at timestamptz,
  created_at timestamptz not null default now()
);
create index idx_receipts_business on receipts(business_id);
create index idx_receipts_embedding on receipts using hnsw (embedding vector_cosine_ops);

create table expenses (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  amount numeric(12,2) not null,
  category text,
  description text,
  source expense_source not null default 'manual',
  receipt_id uuid,
  occurred_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);
create index idx_expenses_business_time on expenses(business_id, occurred_at desc);

alter table expenses add constraint fk_expenses_receipt
  foreign key (receipt_id) references receipts(id) on delete set null;

create table alerts (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  type alert_type not null,
  related_item_id uuid references items(id),
  metric text,
  severity alert_severity not null default 'medium',
  message text not null,
  is_sent boolean not null default false,
  is_acknowledged boolean not null default false,
  created_at timestamptz not null default now()
);
create index idx_alerts_business_time on alerts(business_id, created_at desc);

-- cache for the 30-day rolling baseline used by anomaly detection
create table metric_baselines (
  business_id uuid not null references businesses(id) on delete cascade,
  metric text not null,
  rolling_mean numeric(14,4) not null,
  rolling_stddev numeric(14,4) not null,
  computed_at timestamptz not null default now(),
  primary key (business_id, metric)
);

-- latency/outcome instrumentation + CP2 evaluation fields
create table request_logs (
  id bigint generated always as identity primary key,
  business_id uuid references businesses(id),
  channel text not null,
  path text,
  raw_query text,
  classified_intent text,
  latency_ms integer not null,
  status text not null,
  created_at timestamptz not null default now()
);
create index idx_request_logs_business_time on request_logs(business_id, created_at desc);

-- ── Support tables added during the build (additive; see docs/progress.md) ──

-- Parsed-but-unconfirmed extractions held for the WhatsApp confirmation gate.
create table pending_confirmations (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  kind text not null,
  payload jsonb not null,
  expires_at timestamptz not null,
  created_at timestamptz not null default now()
);
create index idx_pending_confirmations_business on pending_confirmations(business_id, created_at desc);

-- Dashboard-login OTP state. Keyed by phone (may predate any business row),
-- so deliberately not business-scoped and carries no RLS policy. Hash only.
create table login_otps (
  phone text primary key,
  code_hash text not null,
  attempts integer not null default 0,
  expires_at timestamptz not null,
  created_at timestamptz not null default now()
);
"""

RLS_TABLES = [
    "staff",
    "items",
    "sales",
    "expenses",
    "receipts",
    "alerts",
    "metric_baselines",
    "request_logs",
    "pending_confirmations",
]

RLS_SQL_TEMPLATE = """
alter table {table} enable row level security;
alter table {table} force row level security;
create policy tenant_isolation on {table}
  using (business_id = current_setting('app.current_business_id')::uuid)
  with check (business_id = current_setting('app.current_business_id')::uuid);
"""


def upgrade() -> None:
    op.execute(SCHEMA_SQL)
    for table in RLS_TABLES:
        op.execute(RLS_SQL_TEMPLATE.format(table=table))


def downgrade() -> None:
    op.execute(
        """
        drop table if exists login_otps, pending_confirmations, request_logs,
          metric_baselines, alerts, expenses, receipts, sales, items, staff,
          businesses cascade;
        drop type if exists staff_role, expense_source, alert_type, alert_severity;
        """
    )
