import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class PosStaffOut(BaseModel):
    id: uuid.UUID
    name: str
    role: str

    model_config = {"from_attributes": True}


class PosBusinessOut(BaseModel):
    business_name: str
    staff: list[PosStaffOut]


class PosLoginIn(BaseModel):
    pairing_token: str
    staff_id: uuid.UUID
    pin: str = Field(min_length=4, max_length=6, pattern=r"^\d{4,6}$")


class PosLoginOut(BaseModel):
    token: str
    staff_name: str
    business_name: str


class PosVariantOut(BaseModel):
    id: uuid.UUID
    name: str
    sell_price: Decimal
    is_default: bool

    model_config = {"from_attributes": True}


class PosModifierOut(BaseModel):
    id: uuid.UUID
    name: str
    price_delta: Decimal
    is_default: bool

    model_config = {"from_attributes": True}


class PosModifierGroupOut(BaseModel):
    id: uuid.UUID
    name: str
    selection: str          # single | multi
    is_required: bool
    min_select: int
    max_select: int | None
    modifiers: list[PosModifierOut]


class ItemOut(BaseModel):
    id: uuid.UUID
    name: str
    unit: str
    current_stock: Decimal
    sell_price: Decimal
    reorder_threshold: Decimal
    variants: list[PosVariantOut] = []  # active variants, default first (M4-T1)
    modifier_groups: list[PosModifierGroupOut] = []  # active groups (M4-T2)
    made_to_order: bool = False  # has a recipe: components are consumed, not this stock (M4-T4)

    model_config = {"from_attributes": True}


class SaleIn(BaseModel):
    item_id: uuid.UUID
    quantity: Decimal = Field(gt=0, le=Decimal("999999"))
    # Price is prefilled from the item but editable at the counter (discounts,
    # rounding); always validated server-side as non-negative.
    unit_price: Decimal | None = Field(default=None, ge=0)


class SaleOut(BaseModel):
    id: uuid.UUID
    item_id: uuid.UUID
    item_name: str
    quantity: Decimal
    unit_price: Decimal
    total_price: Decimal
    remaining_stock: Decimal
    sold_at: datetime


# ── Multi-line orders with split payment (M3-T3) ─────────────────────────────

from typing import Literal  # noqa: E402

PosPaymentMethod = Literal["cash", "qris", "transfer", "card", "ewallet", "other"]
PosOrderType = Literal["dine_in", "takeaway", "delivery", "pickup"]


class OrderLineIn(BaseModel):
    item_id: uuid.UUID
    variant_id: uuid.UUID | None = None  # None → the item's default variant
    modifier_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    quantity: Decimal = Field(gt=0, le=Decimal("999999"))
    unit_price: Decimal | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=200)
    line_discount: Decimal = Field(default=Decimal(0), ge=0, le=Decimal("999999999"))  # rupiah off (M7-T4b)


class LineModifierOut(BaseModel):
    name: str
    price_delta: Decimal


class PaymentIn(BaseModel):
    method: PosPaymentMethod
    amount: Decimal = Field(gt=0, le=Decimal("999999999"))
    reference: str | None = Field(default=None, max_length=120)


class OrderIn(BaseModel):
    lines: list[OrderLineIn] = Field(min_length=1, max_length=50)
    payments: list[PaymentIn] = Field(min_length=1, max_length=10)
    order_type: PosOrderType = "takeaway"
    bill_discount: Decimal = Field(default=Decimal(0), ge=0, le=Decimal("999999999"))  # M7-T4b
    manager_pin: str | None = Field(default=None, min_length=4, max_length=6)  # required for a discount when the settings say so
    customer_id: uuid.UUID | None = None  # M8-T1: attach the customer


# ── Customers at the till (M8-T1) ───────────────────────────────────────────


class PosCustomerOut(BaseModel):
    id: uuid.UUID
    name: str
    phone: str | None
    visits: int
    last_visit: datetime | None


class PosCustomerIn(BaseModel):
    """Quick add from the kiosk: a name and, usually, a phone."""

    name: str = Field(min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=32)


class QuoteIn(BaseModel):
    """The cart, priced but not sold (M7-T4b): what the kiosk shows before payment."""

    lines: list[OrderLineIn] = Field(min_length=1, max_length=50)
    bill_discount: Decimal = Field(default=Decimal(0), ge=0, le=Decimal("999999999"))


class QuoteLineOut(BaseModel):
    item_id: uuid.UUID
    unit_price: Decimal
    quantity: Decimal
    gross: Decimal
    line_discount: Decimal
    line_total: Decimal


class QuoteOut(BaseModel):
    subtotal: Decimal
    discount_total: Decimal
    service_charge: Decimal
    tax_total: Decimal
    tax_inclusive: bool
    rounding: Decimal
    total: Decimal
    discount_requires_pin: bool
    lines: list[QuoteLineOut]


class OrderLineOut(BaseModel):
    id: uuid.UUID
    item_id: uuid.UUID
    variant_id: uuid.UUID | None = None
    item_name: str
    quantity: Decimal
    unit_price: Decimal
    line_discount: Decimal = Decimal(0)
    line_total: Decimal
    remaining_stock: Decimal
    modifiers: list[LineModifierOut] = []


class PaymentOut(BaseModel):
    id: uuid.UUID
    method: str
    amount: Decimal
    reference: str | None


class OrderOut(BaseModel):
    id: uuid.UUID
    order_type: str
    customer_id: uuid.UUID | None = None
    subtotal: Decimal
    discount_total: Decimal = Decimal(0)
    service_charge: Decimal = Decimal(0)
    tax_total: Decimal = Decimal(0)
    rounding: Decimal = Decimal(0)
    total: Decimal
    sold_at: datetime
    lines: list[OrderLineOut]
    payments: list[PaymentOut]


class ReceiptLineOut(BaseModel):
    name: str                       # item name
    variant: str | None             # size, when the item has one
    quantity: Decimal
    unit_price: Decimal
    line_total: Decimal
    modifiers: list[LineModifierOut]
    notes: str | None


class ReceiptOut(BaseModel):
    order_id: uuid.UUID
    number: str                     # short human reference, last 8 of the id
    business_name: str
    staff_name: str | None
    customer_name: str | None = None
    status: str
    order_type: str
    sold_at: datetime
    lines: list[ReceiptLineOut]
    subtotal: Decimal
    discount_total: Decimal = Decimal(0)
    service_charge: Decimal = Decimal(0)
    tax_total: Decimal = Decimal(0)
    tax_inclusive: bool = True        # true → tax_total is contained in the prices ("termasuk pajak")
    rounding: Decimal = Decimal(0)
    total: Decimal
    payments: list[PaymentOut]


# ── Void / refund (M3-T4): reversals authorised by the manager (owner) PIN ────


class ReversalIn(BaseModel):
    manager_pin: str = Field(min_length=4, max_length=6, pattern=r"^\d{4,6}$")
    note: str | None = Field(default=None, max_length=200)


class RefundIn(ReversalIn):
    restock: bool = True  # False when the goods are not coming back


class ReversalLineOut(BaseModel):
    id: uuid.UUID
    item_id: uuid.UUID
    quantity: Decimal      # negative: this is the reversing line
    line_total: Decimal    # negative
    stock_after: Decimal | None  # None when not restocked


class ReversalOut(BaseModel):
    order_id: uuid.UUID
    status: str            # voided | refunded
    reversing_lines: list[ReversalLineOut]
    reversing_payments: list[PaymentOut]


# ── Shifts (M7-T1) ──────────────────────────────────────────────────────────


class ShiftOpenIn(BaseModel):
    opening_float: Decimal = Field(default=Decimal(0), ge=0)


class ShiftCloseIn(BaseModel):
    counted_cash: Decimal = Field(ge=0)
    notes: str | None = Field(default=None, max_length=300)


class ShiftOut(BaseModel):
    id: uuid.UUID
    staff_id: uuid.UUID
    staff_name: str
    status: str
    opening_float: Decimal
    opened_at: datetime
    closed_at: datetime | None
    closed_by: uuid.UUID | None
    cash_sales: Decimal
    cash_refunds: Decimal
    cash_in: Decimal
    cash_out: Decimal
    expected_cash: Decimal | None
    counted_cash: Decimal | None
    variance: Decimal | None
    notes: str | None


# ── Cash in and out (M7-T2) ─────────────────────────────────────────────────


class PosSupplierOut(BaseModel):
    id: uuid.UUID
    name: str

    model_config = {"from_attributes": True}


class CashMovementIn(BaseModel):
    kind: Literal["cash_in", "petty_cash", "supplier_payment", "bank_drop"]
    amount: Decimal = Field(gt=0)
    reason: str = Field(min_length=1, max_length=200)
    via: Literal["owner", "bank", "cash", "transfer"] | None = None
    category: str | None = Field(default=None, max_length=60)
    supplier_id: uuid.UUID | None = None


class CashMovementOut(BaseModel):
    id: uuid.UUID
    shift_id: uuid.UUID | None
    staff_id: uuid.UUID | None
    staff_name: str | None
    kind: str
    via: str
    direction: str
    amount: Decimal
    reason: str
    category: str | None
    supplier_id: uuid.UUID | None
    supplier_name: str | None
    expense_id: uuid.UUID | None
    occurred_at: datetime
