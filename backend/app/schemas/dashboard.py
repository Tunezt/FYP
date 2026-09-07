import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class OverviewOut(BaseModel):
    business_name: str
    today_revenue: float
    today_transactions: int
    yesterday_revenue: float
    month_revenue: float
    month_expenses: float
    month_net: float
    unacknowledged_alerts: int
    low_stock_items: int
    onboarding_completed: bool


class TrendPoint(BaseModel):
    date: str  # YYYY-MM-DD (business-local day)
    revenue: float
    transactions: int


class SaleRow(BaseModel):
    id: uuid.UUID
    item_name: str
    staff_name: str
    quantity: Decimal
    unit_price: Decimal
    total_price: Decimal
    sold_at: datetime


class Paginated(BaseModel):
    total: int
    page: int
    page_size: int


class SalesPage(Paginated):
    rows: list[SaleRow]


class InventoryItem(BaseModel):
    id: uuid.UUID
    name: str
    unit: str
    current_stock: Decimal
    cost_price: Decimal
    sell_price: Decimal
    reorder_threshold: Decimal
    avg_daily_usage: float | None
    days_remaining: float | None
    below_reorder_threshold: bool


class ItemCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    unit: str = Field(min_length=1, max_length=20)
    current_stock: Decimal = Field(ge=0, default=Decimal(0))
    cost_price: Decimal = Field(ge=0, default=Decimal(0))
    sell_price: Decimal = Field(ge=0, default=Decimal(0))
    reorder_threshold: Decimal = Field(ge=0, default=Decimal(0))
    uom_id: uuid.UUID | None = None  # M4-T3; resolved from `unit` when omitted


class ItemUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    unit: str | None = None
    current_stock: Decimal | None = Field(default=None, ge=0)
    cost_price: Decimal | None = Field(default=None, ge=0)
    sell_price: Decimal | None = Field(default=None, ge=0)
    reorder_threshold: Decimal | None = Field(default=None, ge=0)
    uom_id: uuid.UUID | None = None


# ── Units of measure (M4-T3) ─────────────────────────────────────────────────


class UomOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str

    model_config = {"from_attributes": True}


class UomCreateIn(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str | None = Field(default=None, max_length=60)


class UomConversionOut(BaseModel):
    id: uuid.UUID
    from_uom_id: uuid.UUID
    to_uom_id: uuid.UUID
    factor: Decimal

    model_config = {"from_attributes": True}


class UomConversionCreateIn(BaseModel):
    from_uom_id: uuid.UUID
    to_uom_id: uuid.UUID
    factor: Decimal = Field(gt=0)      # qty_to = qty_from × factor
    both_ways: bool = True


# ── Suppliers (M5-T1) ───────────────────────────────────────────────────────


class SupplierOut(BaseModel):
    id: uuid.UUID
    name: str
    phone: str | None
    address: str | None
    notes: str | None
    is_active: bool

    model_config = {"from_attributes": True}


class SupplierCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=30)
    address: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=500)


class SupplierUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=30)
    address: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=500)
    is_active: bool | None = None


class SupplierReceiptOut(BaseModel):
    id: uuid.UUID
    kind: str = "photo"            # photo | goods_receipt
    occurred_at: datetime
    total_amount: Decimal | None
    item_count: int


class SupplierHistoryOut(BaseModel):
    supplier: SupplierOut
    purchase_count: int
    total_spent: Decimal
    last_purchase_at: datetime | None
    receipts: list[SupplierReceiptOut]


# ── Purchase orders (M5-T2) ─────────────────────────────────────────────────

from datetime import date  # noqa: E402


class PoLineIn(BaseModel):
    item_id: uuid.UUID
    quantity: Decimal = Field(gt=0, le=Decimal("999999"))
    unit_cost: Decimal = Field(default=Decimal(0), ge=0)
    uom_id: uuid.UUID | None = None


class PoLineUpdateIn(BaseModel):
    item_id: uuid.UUID | None = None
    quantity: Decimal | None = Field(default=None, gt=0, le=Decimal("999999"))
    unit_cost: Decimal | None = Field(default=None, ge=0)
    uom_id: uuid.UUID | None = None


class PurchaseOrderCreateIn(BaseModel):
    supplier_id: uuid.UUID
    lines: list[PoLineIn] = Field(default_factory=list, max_length=100)
    notes: str | None = Field(default=None, max_length=500)
    expected_at: date | None = None


class PurchaseOrderUpdateIn(BaseModel):
    notes: str | None = Field(default=None, max_length=500)
    expected_at: date | None = None


class PoLineOut(BaseModel):
    id: uuid.UUID
    item_id: uuid.UUID
    item_name: str
    quantity: Decimal
    uom_id: uuid.UUID | None
    uom_code: str | None
    unit_cost: Decimal
    line_total: Decimal
    received_quantity: Decimal


class PurchaseOrderOut(BaseModel):
    id: uuid.UUID
    number: int
    supplier_id: uuid.UUID
    supplier_name: str
    status: str
    notes: str | None
    expected_at: date | None
    ordered_at: datetime | None
    cancelled_at: datetime | None
    subtotal: Decimal
    created_at: datetime
    lines: list[PoLineOut]


# ── Goods receipts (M5-T3) ──────────────────────────────────────────────────


class GrLineIn(BaseModel):
    item_id: uuid.UUID
    quantity: Decimal = Field(gt=0, le=Decimal("999999"))
    unit_cost: Decimal = Field(default=Decimal(0), ge=0)   # per received unit
    uom_id: uuid.UUID | None = None
    po_line_id: uuid.UUID | None = None


class GoodsReceiptCreateIn(BaseModel):
    supplier_id: uuid.UUID | None = None
    po_id: uuid.UUID | None = None
    lines: list[GrLineIn] = Field(min_length=1, max_length=100)
    notes: str | None = Field(default=None, max_length=500)
    allow_over_receipt: bool = False


class GrLineOut(BaseModel):
    id: uuid.UUID
    item_id: uuid.UUID
    item_name: str
    po_line_id: uuid.UUID | None
    quantity: Decimal
    uom_code: str | None
    quantity_item_unit: Decimal
    item_unit: str
    unit_cost: Decimal
    unit_cost_item_unit: Decimal
    line_total: Decimal
    stock_after: Decimal
    avg_cost_after: Decimal


class GoodsReceiptOut(BaseModel):
    id: uuid.UUID
    number: int
    supplier_id: uuid.UUID | None
    supplier_name: str | None
    po_id: uuid.UUID | None
    po_number: int | None
    po_status: str | None
    received_at: datetime
    notes: str | None
    subtotal: Decimal
    lines: list[GrLineOut]


# ── Chart of accounts (M6-T1) ───────────────────────────────────────────────


class AccountOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    type: str
    is_system: bool
    is_active: bool

    model_config = {"from_attributes": True}


class AccountCreateIn(BaseModel):
    code: str = Field(min_length=1, max_length=8)
    name: str = Field(min_length=1, max_length=120)
    type: Literal["asset", "liability", "equity", "revenue", "expense"]


class AccountUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    is_active: bool | None = None


# ── Posting rules (M6-T3) ───────────────────────────────────────────────────


class PostingRuleOut(BaseModel):
    id: uuid.UUID
    event_type: str
    component: str
    debit_code: str | None
    credit_code: str | None
    description: str | None
    is_system: bool
    is_active: bool

    model_config = {"from_attributes": True}


class PostingRuleUpdateIn(BaseModel):
    debit_code: str | None = Field(default=None, max_length=8)
    credit_code: str | None = Field(default=None, max_length=8)
    description: str | None = Field(default=None, max_length=200)
    is_active: bool | None = None


# ── Statements (M6-T5) ──────────────────────────────────────────────────────


class StatementLineOut(BaseModel):
    code: str
    name: str
    amount: Decimal


class ProfitAndLossOut(BaseModel):
    since: date  # inclusive, business-local
    until: date  # inclusive, business-local
    revenue: list[StatementLineOut]
    revenue_total: Decimal
    cogs: list[StatementLineOut]
    cogs_total: Decimal
    gross_profit: Decimal
    expenses: list[StatementLineOut]
    expenses_total: Decimal
    net_profit: Decimal


class BalanceSheetOut(BaseModel):
    as_of: date  # end of this business-local day
    assets: list[StatementLineOut]
    assets_total: Decimal
    liabilities: list[StatementLineOut]
    liabilities_total: Decimal
    equity: list[StatementLineOut]
    equity_total: Decimal
    current_earnings: Decimal
    liabilities_and_equity_total: Decimal
    balances: bool


# ── Recipes (M4-T4) ─────────────────────────────────────────────────────────


class RecipeLineOut(BaseModel):
    id: uuid.UUID
    variant_id: uuid.UUID
    component_item_id: uuid.UUID
    component_name: str
    quantity: Decimal
    uom_id: uuid.UUID | None
    uom_code: str | None
    is_active: bool


class RecipeLineIn(BaseModel):
    component_item_id: uuid.UUID
    quantity: Decimal = Field(gt=0, le=Decimal("999999"))
    uom_id: uuid.UUID | None = None   # None = the component's own unit


class RecipeLineUpdateIn(BaseModel):
    quantity: Decimal | None = Field(default=None, gt=0, le=Decimal("999999"))
    uom_id: uuid.UUID | None = None
    is_active: bool | None = None


# ── Variants (M4-T1) ─────────────────────────────────────────────────────────


class VariantOut(BaseModel):
    id: uuid.UUID
    item_id: uuid.UUID
    name: str
    sku: str | None
    sell_price: Decimal
    cost_price: Decimal
    is_default: bool
    is_active: bool

    model_config = {"from_attributes": True}


class VariantCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    sku: str | None = Field(default=None, max_length=60)
    sell_price: Decimal = Field(ge=0)
    cost_price: Decimal = Field(ge=0, default=Decimal(0))
    is_default: bool = False


class VariantUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    sku: str | None = Field(default=None, max_length=60)
    sell_price: Decimal | None = Field(default=None, ge=0)
    cost_price: Decimal | None = Field(default=None, ge=0)
    is_default: bool | None = None
    is_active: bool | None = None


# ── Modifiers (M4-T2) ────────────────────────────────────────────────────────

from typing import Literal  # noqa: E402


class ModifierOut(BaseModel):
    id: uuid.UUID
    group_id: uuid.UUID
    name: str
    price_delta: Decimal
    is_default: bool
    sort_order: int
    is_active: bool

    model_config = {"from_attributes": True}


class ModifierGroupOut(BaseModel):
    id: uuid.UUID
    item_id: uuid.UUID
    name: str
    selection: str
    is_required: bool
    min_select: int
    max_select: int | None
    sort_order: int
    is_active: bool
    modifiers: list[ModifierOut] = []


class ModifierGroupCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    selection: Literal["single", "multi"] = "single"
    is_required: bool = False
    min_select: int = Field(default=0, ge=0, le=20)
    max_select: int | None = Field(default=None, ge=1, le=20)
    sort_order: int = 0


class ModifierGroupUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    selection: Literal["single", "multi"] | None = None
    is_required: bool | None = None
    min_select: int | None = Field(default=None, ge=0, le=20)
    max_select: int | None = Field(default=None, ge=1, le=20)
    sort_order: int | None = None
    is_active: bool | None = None


class ModifierCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    price_delta: Decimal = Field(default=Decimal(0), ge=0)
    is_default: bool = False
    sort_order: int = 0


class ModifierUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    price_delta: Decimal | None = Field(default=None, ge=0)
    is_default: bool | None = None
    sort_order: int | None = None
    is_active: bool | None = None


class ExpenseRow(BaseModel):
    id: uuid.UUID
    amount: Decimal
    category: str | None
    description: str | None
    source: str
    receipt_id: uuid.UUID | None
    occurred_at: datetime

    model_config = {"from_attributes": True}


class ExpensesPage(Paginated):
    rows: list[ExpenseRow]


class PnlMonth(BaseModel):
    month: str  # YYYY-MM
    revenue: float
    expenses: float
    net: float


class AlertRow(BaseModel):
    id: uuid.UUID
    type: str
    metric: str | None
    severity: str
    message: str
    related_item_name: str | None
    is_acknowledged: bool
    created_at: datetime


class ReceiptRow(BaseModel):
    id: uuid.UUID
    supplier: str | None
    total_amount: Decimal | None
    occurred_at: datetime | None
    created_at: datetime
    image_signed_url: str | None
    item_count: int


class ReceiptsPage(Paginated):
    rows: list[ReceiptRow]


class BusinessUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    business_type: str | None = None
    language_preference: str | None = None
    timezone: str | None = None
    # M15-T4. 0 is the plain calendar day; 4 puts a bill settled at 00:15 on the
    # night before. Bounded here as well as by the DDL check constraint, so a
    # bad value is a 422 in Indonesian rather than a database error.
    day_start_hour: int | None = Field(default=None, ge=0, le=23)


# ── Pricing settings (M7-T4b): tax, service charge, rounding, discount gate ──


class PricingSettingsOut(BaseModel):
    tax_rate: Decimal
    tax_inclusive: bool
    service_charge_rate: Decimal
    service_before_tax: bool
    rounding_unit: Decimal
    rounding_mode: Literal["nearest", "up", "down"]
    discount_requires_pin: bool
    service_applies_to: list[str] = ["dine_in", "takeaway", "delivery", "pickup"]   # M11-T3
    delivery_fee: Decimal = Decimal(0)

    model_config = {"from_attributes": True}


class PricingSettingsPatch(BaseModel):
    """Rates are fractions (0.11 = 11%), never percentages; the kiosk and the
    ledger read them as-is."""

    tax_rate: Decimal | None = Field(default=None, ge=0, lt=1)
    tax_inclusive: bool | None = None
    service_charge_rate: Decimal | None = Field(default=None, ge=0, lt=1)
    service_before_tax: bool | None = None
    rounding_unit: Decimal | None = Field(default=None, ge=0, le=Decimal("100000"))
    rounding_mode: Literal["nearest", "up", "down"] | None = None
    discount_requires_pin: bool | None = None
    service_applies_to: list[Literal["dine_in", "takeaway", "delivery", "pickup"]] | None = Field(default=None, max_length=4)   # M11-T3
    delivery_fee: Decimal | None = Field(default=None, ge=0, le=Decimal("9999999"))


# ── Customers (M8-T1) ───────────────────────────────────────────────────────


class CustomerOut(BaseModel):
    id: uuid.UUID
    name: str
    phone: str | None
    address: str | None
    birthday: date | None
    notes: str | None
    is_active: bool
    visits: int
    total_spent: Decimal
    last_visit: datetime | None
    points_balance: int = 0
    created_at: datetime


class CustomerCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=32)
    address: str | None = Field(default=None, max_length=300)
    birthday: date | None = None
    notes: str | None = Field(default=None, max_length=500)


class CustomerUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=32)   # "" clears it
    address: str | None = Field(default=None, max_length=300)
    birthday: date | None = None
    notes: str | None = Field(default=None, max_length=500)
    is_active: bool | None = None


class CustomersPage(BaseModel):
    total: int
    page: int
    page_size: int
    rows: list[CustomerOut]


# ── Points (M8-T2) ──────────────────────────────────────────────────────────


class LoyaltySettingsOut(BaseModel):
    is_active: bool
    rupiah_per_point: Decimal
    point_value: Decimal
    min_redeem_points: int

    model_config = {"from_attributes": True}


class LoyaltySettingsPatch(BaseModel):
    is_active: bool | None = None
    rupiah_per_point: Decimal | None = Field(default=None, gt=0, le=Decimal("100000000"))
    point_value: Decimal | None = Field(default=None, ge=0, le=Decimal("100000000"))
    min_redeem_points: int | None = Field(default=None, ge=0, le=1000000)


class PointsMovementOut(BaseModel):
    id: uuid.UUID
    points_delta: int
    reason: str
    source_type: str | None
    source_id: uuid.UUID | None
    amount: Decimal
    notes: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class PointsAdjustIn(BaseModel):
    points_delta: int = Field(ge=-1000000, le=1000000)
    notes: str | None = Field(default=None, max_length=300)


# ── Promos (M8-T3) ──────────────────────────────────────────────────────────


class PromoConditionIn(BaseModel):
    kind: Literal["date_range", "day_of_week", "time_window", "min_spend", "multiples"]
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    days_of_week: list[int] | None = None
    time_start: time | None = None
    time_end: time | None = None
    amount: Decimal | None = Field(default=None, gt=0)
    quantity: Decimal | None = Field(default=None, gt=0)


class PromoConditionOut(PromoConditionIn):
    pass


class PromoCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["percent_off", "amount_off", "bonus_item"]
    value: Decimal = Field(default=Decimal(0), ge=0)
    item_id: uuid.UUID | None = None
    bonus_item_id: uuid.UUID | None = None
    bonus_quantity: Decimal = Field(default=Decimal(1), gt=0)
    max_per_order: int | None = Field(default=None, gt=0)
    is_active: bool = True
    conditions: list[PromoConditionIn] = Field(default_factory=list, max_length=10)


class PromoUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    value: Decimal | None = Field(default=None, ge=0)
    bonus_quantity: Decimal | None = Field(default=None, gt=0)
    max_per_order: int | None = Field(default=None, gt=0)
    is_active: bool | None = None
    conditions: list[PromoConditionIn] | None = None   # given → replaces the whole set


class PromoOut(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    value: Decimal
    item_id: uuid.UUID | None
    bonus_item_id: uuid.UUID | None
    bonus_quantity: Decimal
    max_per_order: int | None
    is_active: bool
    created_at: datetime
    conditions: list[PromoConditionOut]
    applications: int = 0
    given_away: Decimal = Decimal(0)


# ── Vouchers (M8-T4) ────────────────────────────────────────────────────────


class VoucherCreateIn(BaseModel):
    """One code (`code`) or a batch (`count` > 1, generated with `prefix`)."""

    kind: Literal["percent_off", "amount_off"]
    value: Decimal = Field(gt=0)
    code: str | None = Field(default=None, min_length=3, max_length=32)
    count: int = Field(default=1, ge=1, le=1000)
    prefix: str = Field(default="", max_length=8)
    batch_name: str | None = Field(default=None, max_length=120)
    max_discount: Decimal | None = Field(default=None, gt=0)
    min_spend: Decimal = Field(default=Decimal(0), ge=0)
    starts_at: datetime | None = None
    expires_at: datetime | None = None
    max_uses: int = Field(default=1, ge=1, le=1000000)


class VoucherUpdateIn(BaseModel):
    is_active: bool | None = None
    expires_at: datetime | None = None
    max_uses: int | None = Field(default=None, ge=1)


class VoucherOut(BaseModel):
    id: uuid.UUID
    code: str
    kind: str
    value: Decimal
    max_discount: Decimal | None
    min_spend: Decimal
    starts_at: datetime | None
    expires_at: datetime | None
    max_uses: int
    uses: int
    batch_id: uuid.UUID | None
    batch_name: str | None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Metric layer (M9-T1) ────────────────────────────────────────────────────


class MetricSpecOut(BaseModel):
    name: str
    description_id: str
    description_en: str
    unit: str
    grains: list[str]
    dimensions: list[str]


class MetricValueOut(BaseModel):
    name: str
    unit: str
    value: float | int | None
    rows: list[dict]
    since: datetime | None
    until: datetime | None
    period: str | None
    period_label: str
    note: str | None


# ── Override audit trail (M15-T7) ───────────────────────────────────────────


class ApprovalRow(BaseModel):
    """One manager authorisation, as the owner reads it back. `approver_role`
    is the snapshot the row was written with, not the approver's role today."""

    id: uuid.UUID
    order_id: uuid.UUID | None
    action: Literal["discount", "void", "refund"]
    approved_by: uuid.UUID
    approver_name: str
    approver_role: str
    requested_by: uuid.UUID | None
    requested_by_name: str | None
    amount: Decimal | None
    note: str | None
    created_at: datetime


# ── Backdated sale entry (M15-T10) ──────────────────────────────────────────


class BackdatedLineIn(BaseModel):
    item_id: uuid.UUID
    variant_id: uuid.UUID | None = None
    quantity: Decimal = Field(gt=0, le=Decimal("9999"))


class BackdatedSaleIn(BaseModel):
    """A sale that happened on paper, entered afterwards at the time it actually
    happened. Everything else is an ordinary sale: it prices, moves stock, posts
    to the ledger and earns points exactly as the till would have."""

    sold_at: datetime
    staff_id: uuid.UUID                       # who actually took the money
    lines: list[BackdatedLineIn] = Field(min_length=1, max_length=50)
    payment_method: Literal["cash", "qris", "transfer", "card", "ewallet", "other"] = "cash"
    customer_id: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=200)


# ── PIN brute-force protection (M15-T12) ────────────────────────────────────


class PinLockoutRow(BaseModel):
    """One subject currently being counted. `who` is resolved for the owner:
    a staff name, "perangkat kasir", or the cashier who was asking."""

    id: uuid.UUID
    scope: Literal["pos_login", "pos_device", "manager_pin"]
    who: str
    failures: int
    first_failed_at: datetime
    last_failed_at: datetime
    locked_until: datetime | None
    locked_now: bool
