"""SQLAlchemy models — a 1:1 mirror of the DDL in alembic/versions/ (0001 onward).

The schema itself (core entities, support tables, roadmap tables, RLS policies,
indexes) is defined in raw SQL in the migrations; these models exist for query
construction and must not drift from it. A migration that adds a table adds its
model here in the same commit (roadmap §1.7).
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


staff_role = Enum("owner", "staff", name="staff_role", create_type=False)
expense_source = Enum("manual", "receipt", name="expense_source", create_type=False)
alert_type = Enum("anomaly", "low_stock", name="alert_type", create_type=False)
alert_severity = Enum("low", "medium", "high", name="alert_severity", create_type=False)
stock_movement_reason = Enum(
    "sale", "sale_void", "refund", "purchase", "waste",
    "production_in", "production_out", "opname", "correction",
    name="stock_movement_reason", create_type=False,
)
order_type = Enum("dine_in", "takeaway", "delivery", "pickup", name="order_type", create_type=False)
order_status = Enum("open", "completed", "voided", "refunded", name="order_status", create_type=False)
payment_method = Enum(
    "cash", "qris", "transfer", "card", "ewallet", "points", "other",
    name="payment_method", create_type=False,
)
modifier_selection = Enum("single", "multi", name="modifier_selection", create_type=False)
po_status = Enum(
    "draft", "ordered", "partially_received", "received", "cancelled", name="po_status", create_type=False
)
account_type = Enum("asset", "liability", "equity", "revenue", "expense", name="account_type", create_type=False)


class Business(Base):
    __tablename__ = "businesses"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    business_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="cafe")
    owner_phone: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    whatsapp_number: Mapped[str | None] = mapped_column(Text)
    language_preference: Mapped[str] = mapped_column(Text, nullable=False, server_default="id")
    timezone: Mapped[str] = mapped_column(Text, nullable=False, server_default="Asia/Jakarta")
    onboarding_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()


class Staff(Base):
    __tablename__ = "staff"

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(staff_role, nullable=False, server_default="staff")
    phone: Mapped[str | None] = mapped_column(Text)
    pin_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = _now()


class Item(Base):
    __tablename__ = "items"

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[str] = mapped_column(Text, nullable=False)
    current_stock: Mapped[Decimal] = mapped_column(
        Numeric(12, 3), nullable=False, server_default="0"
    )
    cost_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    sell_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    reorder_threshold: Mapped[Decimal] = mapped_column(
        Numeric(12, 3), nullable=False, server_default="0"
    )
    uom_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("uoms.id")
    )  # migration 0009 (M4-T3); `unit` stays as the free-text label
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class Sale(Base):
    """READ-ONLY since migration 0006 (roadmap M3-T2): `sales` is a
    `security_invoker` view over order_lines ⨝ orders, shaped exactly like the
    original table so every reader keeps working. `id` is the order line id.
    Writes go through the order model (services/sales.py); an INSERT here fails
    at the database."""

    __tablename__ = "sales"

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id"), nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    total_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    staff_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("staff.id"), nullable=False
    )
    sold_at: Mapped[datetime] = _now()


class SaleLegacy(Base):
    """The original one-row-per-item `sales` table, renamed by migration 0006 and
    kept as an archive. Fully backfilled into orders/order_lines/payments; no
    application code reads or writes it. Mirrored here only so models.py stays a
    1:1 picture of the schema."""

    __tablename__ = "sales_legacy"

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id"), nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    total_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    staff_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("staff.id"), nullable=False
    )
    sold_at: Mapped[datetime] = _now()


class Expense(Base):
    __tablename__ = "expenses"

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    category: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(expense_source, nullable=False, server_default="manual")
    receipt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("receipts.id", ondelete="SET NULL")
    )
    occurred_at: Mapped[datetime] = _now()
    created_at: Mapped[datetime] = _now()


class Receipt(Base):
    __tablename__ = "receipts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    image_url: Mapped[str] = mapped_column(Text, nullable=False)
    parsed_data: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    supplier: Mapped[str | None] = mapped_column(Text)
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suppliers.id")
    )  # migration 0011 (M5-T1); linked when the photo's supplier text matches a known supplier
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(768))
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(alert_type, nullable=False)
    related_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id")
    )
    metric: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(alert_severity, nullable=False, server_default="medium")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    is_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    is_acknowledged: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = _now()


class MetricBaseline(Base):
    __tablename__ = "metric_baselines"

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        primary_key=True,
    )
    metric: Mapped[str] = mapped_column(Text, primary_key=True)
    rolling_mean: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    rolling_stddev: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    computed_at: Mapped[datetime] = _now()


class RequestLog(Base):
    __tablename__ = "request_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    business_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id")
    )
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str | None] = mapped_column(Text)
    raw_query: Mapped[str | None] = mapped_column(Text)
    classified_intent: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _now()


# ── Support tables added during the build (additive; logged in docs/progress.md) ──


class PendingConfirmation(Base):
    """Holds a parsed-but-unconfirmed extraction (vision receipt / stock-book /
    Excel import) while we wait for the owner's WhatsApp YES. Business data is
    only written to real tables after confirmation — this is the persistence
    the Section 4 confirmation gate requires."""

    __tablename__ = "pending_confirmations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # 'receipt' | 'stock_import'
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = _now()


class LoginOtp(Base):
    """Server-side OTP state for dashboard registration/login. Keyed by phone
    (which may not belong to any business yet during registration), so it is
    deliberately not business-scoped and carries no RLS policy. Stores a hash,
    never the code itself."""

    __tablename__ = "login_otps"

    phone: Mapped[str] = mapped_column(Text, primary_key=True)
    code_hash: Mapped[str] = mapped_column(Text, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = _now()


# ── Roadmap v2 tables (docs/BUILD-ROADMAP.md §5) ──────────────────────────────


class StockMovement(Base):
    """Append-only stock ledger (migration 0003, roadmap M2). One signed row per
    stock change; `items.current_stock` is the cached SUM(qty_delta) per item.
    Never updated or deleted — reversals are new rows with the opposite sign."""

    __tablename__ = "stock_movements"

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id"), nullable=False
    )
    qty_delta: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    reason: Mapped[str] = mapped_column(stock_movement_reason, nullable=False)
    source_type: Mapped[str | None] = mapped_column(Text)
    source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    staff_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("staff.id"))
    created_at: Mapped[datetime] = _now()


class Order(Base):
    """One till transaction (migration 0005, roadmap M3). Many lines, many
    payments. Money columns are numeric(12,2); `total` is what the customer paid."""

    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    staff_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("staff.id"))
    customer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))  # FK arrives with M8
    order_type: Mapped[str] = mapped_column(order_type, nullable=False, server_default="takeaway")
    status: Mapped[str] = mapped_column(order_status, nullable=False, server_default="completed")
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    discount_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    tax_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    service_charge: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    rounding: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    sold_at: Mapped[datetime] = _now()
    created_at: Mapped[datetime] = _now()


class OrderLine(Base):
    """One item on an order. `unit_cost_at_sale` is the cost snapshot that keeps
    historical margin fixed; a reversing line (void/refund) carries a negative
    quantity, nothing is updated in place."""

    __tablename__ = "order_lines"

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False)
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("items.id"), nullable=False)
    variant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("item_variants.id")
    )  # FK added by migration 0007 (M4-T1); NULL on lines sold before variants existed
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    line_discount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    line_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    unit_cost_at_sale: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()


class ItemVariant(Base):
    """A size/option of an item with its own prices (migration 0007, roadmap
    M4-T1). Stock lives on the parent item; every item has exactly one default
    variant (partial unique index) so single-variant products stay simple."""

    __tablename__ = "item_variants"

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    sku: Mapped[str | None] = mapped_column(Text)
    sell_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    cost_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class ModifierGroup(Base):
    """A question asked when an item is sold ("Gula?", "Tambahan?"), migration
    0008 / roadmap M4-T2. Single or multi select, required or optional."""

    __tablename__ = "modifier_groups"

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    selection: Mapped[str] = mapped_column(modifier_selection, nullable=False, server_default="single")
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    min_select: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_select: Mapped[int | None] = mapped_column(Integer)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class Modifier(Base):
    """One answer in a group: priced (`price_delta`) or free."""

    __tablename__ = "modifiers"

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("modifier_groups.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    price_delta: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class OrderLineModifier(Base):
    """What was chosen on a line, snapshotted (name and price) at sale time."""

    __tablename__ = "order_line_modifiers"

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    order_line_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("order_lines.id"), nullable=False
    )
    modifier_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("modifiers.id"))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    price_delta: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    created_at: Mapped[datetime] = _now()


class Uom(Base):
    """A unit of measure of one business (migration 0009, roadmap M4-T3)."""

    __tablename__ = "uoms"

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _now()


class UomConversion(Base):
    """qty_to = qty_from × factor. kg→g is 1000, g→kg is 0.001."""

    __tablename__ = "uom_conversions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    from_uom_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("uoms.id", ondelete="CASCADE"), nullable=False
    )
    to_uom_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("uoms.id", ondelete="CASCADE"), nullable=False
    )
    factor: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    created_at: Mapped[datetime] = _now()


class RecipeLine(Base):
    """One component of a variant (migration 0010, roadmap M4-T4): `quantity`
    of `component_item` in `uom` per unit sold. Deactivated, never deleted."""

    __tablename__ = "recipe_lines"

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("item_variants.id", ondelete="CASCADE"), nullable=False
    )
    component_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id"), nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    uom_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("uoms.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class Supplier(Base):
    """Who the business buys from (migration 0011, roadmap M5-T1). Purchase
    history is derived from receipts / goods receipts, never stored here."""

    __tablename__ = "suppliers"

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    phone: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class PurchaseOrder(Base):
    """What was asked of a supplier (migration 0012, roadmap M5-T2). Never
    touches stock; goods receipts (M5-T3) advance its lines."""

    __tablename__ = "purchase_orders"

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=False)
    status: Mapped[str] = mapped_column(po_status, nullable=False, server_default="draft")
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    expected_at: Mapped[date | None] = mapped_column(Date)
    ordered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("staff.id"))
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class PoLine(Base):
    """One item on a purchase order: ordered quantity (in a unit), agreed unit
    cost, and how much has arrived so far."""

    __tablename__ = "po_lines"

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    po_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("purchase_orders.id"), nullable=False)
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("items.id"), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    uom_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("uoms.id"))
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    line_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    received_quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, server_default="0")
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class GoodsReceipt(Base):
    """Goods that arrived (migration 0013, roadmap M5-T3), from a PO or not.
    The event that moves stock in: each line ledgers a `purchase`."""

    __tablename__ = "goods_receipts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.id"))
    po_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("purchase_orders.id"))
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    received_at: Mapped[datetime] = _now()
    received_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("staff.id"))
    notes: Mapped[str | None] = mapped_column(Text)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    created_at: Mapped[datetime] = _now()


class GoodsReceiptLine(Base):
    """One received line: as received (quantity, uom, unit_cost) and as ledgered
    (quantity_item_unit, unit_cost_item_unit)."""

    __tablename__ = "goods_receipt_lines"

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    receipt_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("goods_receipts.id"), nullable=False)
    po_line_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("po_lines.id"))
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("items.id"), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    uom_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("uoms.id"))
    quantity_item_unit: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    unit_cost_item_unit: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    line_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    created_at: Mapped[datetime] = _now()


class Account(Base):
    """One line of the chart of accounts (migration 0014, roadmap M6-T1)."""

    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(account_type, nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class JournalEntry(Base):
    """One double-entry posting (migration 0015, roadmap M6-T2). Debits equal
    credits by a deferred database constraint; never updated or deleted."""

    __tablename__ = "journal_entries"

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    entry_no: Mapped[int] = mapped_column(Integer, nullable=False)
    posted_at: Mapped[datetime] = _now()
    memo: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str | None] = mapped_column(Text)
    source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    event_type: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("staff.id"))
    created_at: Mapped[datetime] = _now()


class JournalLine(Base):
    """One side of a posting: a debit or a credit on one account."""

    __tablename__ = "journal_lines"

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    entry_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("journal_entries.id"), nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False)
    debit: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    credit: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    memo: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()


class PostingRule(Base):
    """Which accounts an event component debits and credits (migration 0016,
    roadmap M6-T3). Rules are data; the posting engine only looks them up."""

    __tablename__ = "posting_rules"

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    component: Mapped[str] = mapped_column(Text, nullable=False)
    debit_code: Mapped[str | None] = mapped_column(Text)
    credit_code: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class Payment(Base):
    """One payment against an order. Many per order — that is split payment."""

    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False)
    method: Mapped[str] = mapped_column(payment_method, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    reference: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()
