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


class ItemUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    unit: str | None = None
    current_stock: Decimal | None = Field(default=None, ge=0)
    cost_price: Decimal | None = Field(default=None, ge=0)
    sell_price: Decimal | None = Field(default=None, ge=0)
    reorder_threshold: Decimal | None = Field(default=None, ge=0)


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
