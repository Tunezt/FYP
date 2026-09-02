import uuid
from datetime import datetime
from decimal import Decimal

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
