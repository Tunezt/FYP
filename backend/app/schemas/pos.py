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


class ItemOut(BaseModel):
    id: uuid.UUID
    name: str
    unit: str
    current_stock: Decimal
    sell_price: Decimal
    reorder_threshold: Decimal
    variants: list[PosVariantOut] = []  # active variants, default first (M4-T1)

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
    quantity: Decimal = Field(gt=0, le=Decimal("999999"))
    unit_price: Decimal | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=200)


class PaymentIn(BaseModel):
    method: PosPaymentMethod
    amount: Decimal = Field(gt=0, le=Decimal("999999999"))
    reference: str | None = Field(default=None, max_length=120)


class OrderIn(BaseModel):
    lines: list[OrderLineIn] = Field(min_length=1, max_length=50)
    payments: list[PaymentIn] = Field(min_length=1, max_length=10)
    order_type: PosOrderType = "takeaway"


class OrderLineOut(BaseModel):
    id: uuid.UUID
    item_id: uuid.UUID
    variant_id: uuid.UUID | None = None
    item_name: str
    quantity: Decimal
    unit_price: Decimal
    line_total: Decimal
    remaining_stock: Decimal


class PaymentOut(BaseModel):
    id: uuid.UUID
    method: str
    amount: Decimal
    reference: str | None


class OrderOut(BaseModel):
    id: uuid.UUID
    order_type: str
    subtotal: Decimal
    total: Decimal
    sold_at: datetime
    lines: list[OrderLineOut]
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
