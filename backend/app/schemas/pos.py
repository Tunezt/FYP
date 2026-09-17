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

PosPaymentMethod = Literal["cash", "qris", "transfer", "card", "ewallet", "points", "other"]  # points: M8-T2
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
    voucher_code: str | None = Field(default=None, max_length=40)  # M8-T4
    # Order type routing (M11-T3): a table for dine-in; an address and a phone for delivery.
    table_label: str | None = Field(default=None, max_length=20)
    delivery_address: str | None = Field(default=None, max_length=300)
    guest_name: str | None = Field(default=None, max_length=60)
    guest_phone: str | None = Field(default=None, max_length=32)
    # svc-2: a double tap on "Bayar" or a retried request is the same sale.
    client_ref: str | None = Field(default=None, min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


# ── Customers at the till (M8-T1) ───────────────────────────────────────────


class PosCustomerOut(BaseModel):
    id: uuid.UUID
    name: str
    phone: str | None
    visits: int
    last_visit: datetime | None
    points_balance: int = 0
    points_value: Decimal = Decimal(0)   # what the balance pays for, at the programme's point value (M8-T2)


class PosLoyaltyOut(BaseModel):
    is_active: bool
    rupiah_per_point: Decimal
    point_value: Decimal
    min_redeem_points: int


class PosCustomerIn(BaseModel):
    """Quick add from the kiosk: a name and, usually, a phone."""

    name: str = Field(min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=32)


class QuoteIn(BaseModel):
    """The cart, priced but not sold (M7-T4b): what the kiosk shows before payment."""

    lines: list[OrderLineIn] = Field(min_length=1, max_length=50)
    bill_discount: Decimal = Field(default=Decimal(0), ge=0, le=Decimal("999999999"))
    voucher_code: str | None = Field(default=None, max_length=40)  # M8-T4
    order_type: PosOrderType = "takeaway"  # M11-T3: the type routes service charge and delivery fee


class QuoteLineOut(BaseModel):
    item_id: uuid.UUID
    unit_price: Decimal
    quantity: Decimal
    gross: Decimal
    line_discount: Decimal
    line_total: Decimal
    promo_discount: Decimal = Decimal(0)
    is_bonus: bool = False          # added by a promo, not rung up by the cashier (M8-T3)
    promo_name: str | None = None


class QuotePromoOut(BaseModel):
    promo_id: uuid.UUID
    name: str
    amount: Decimal
    bonus_quantity: Decimal


class QuoteOut(BaseModel):
    subtotal: Decimal
    discount_total: Decimal
    promo_total: Decimal = Decimal(0)
    promos: list[QuotePromoOut] = []
    voucher_total: Decimal = Decimal(0)
    voucher_code: str | None = None
    voucher_error: str | None = None     # the code was given but cannot be used: why, in Indonesian
    service_charge: Decimal
    delivery_fee: Decimal = Decimal(0)   # M11-T3
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
    table_label: str | None = None        # M11-T3
    delivery_address: str | None = None
    delivery_fee: Decimal = Decimal(0)
    customer_id: uuid.UUID | None = None
    points_earned: int = 0
    points_redeemed: int = 0
    subtotal: Decimal
    discount_total: Decimal = Decimal(0)
    promo_total: Decimal = Decimal(0)
    voucher_total: Decimal = Decimal(0)
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
    points_earned: int = 0
    points_redeemed: int = 0
    status: str
    order_type: str
    table_label: str | None = None        # M11-T3
    delivery_address: str | None = None
    delivery_fee: Decimal = Decimal(0)
    sold_at: datetime
    lines: list[ReceiptLineOut]
    subtotal: Decimal
    discount_total: Decimal = Decimal(0)
    promo_total: Decimal = Decimal(0)
    promo_names: list[str] = []
    voucher_total: Decimal = Decimal(0)
    voucher_code: str | None = None
    service_charge: Decimal = Decimal(0)
    tax_total: Decimal = Decimal(0)
    tax_inclusive: bool = True        # true → tax_total is contained in the prices ("termasuk pajak")
    rounding: Decimal = Decimal(0)
    total: Decimal
    payments: list[PaymentOut]


# ── Void / refund (M3-T4): reversals authorised by the manager (owner) PIN ────


class OrderSummaryOut(BaseModel):
    """One row of the recent-sales list (M15-T11). Enough to recognise the sale
    across the counter: the receipt number, the time, who rang it up, what it
    came to, and whether it has already been reversed."""

    id: uuid.UUID
    number: str
    sold_at: datetime
    status: str
    order_type: str
    total: Decimal
    line_count: int
    staff_name: str | None = None
    customer_name: str | None = None
    table_label: str | None = None
    entry_source: str = "live"        # M15-T10: 'manual_backdated' was typed from paper


class OrdersPage(BaseModel):
    total: int
    rows: list[OrderSummaryOut]


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


# ── Kitchen display (M11-T2) ─────────────────────────────────────────────────

KitchenState = Literal["new", "preparing", "ready", "done"]


class KitchenLineOut(BaseModel):
    name: str
    quantity: Decimal
    modifiers: list[str] = []
    notes: str | None = None


class KitchenTicketOut(BaseModel):
    order_id: uuid.UUID
    code: str
    source: str
    order_type: str
    table_label: str | None
    guest_name: str | None
    note: str | None = None
    delivery_address: str | None = None   # M11-T3
    sold_at: datetime
    state: KitchenState
    state_since: datetime | None
    lines: list[KitchenLineOut]


class KitchenStateIn(BaseModel):
    state: KitchenState
