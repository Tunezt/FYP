"""QR e-menu (M11-T1): what a guest sees and sends, and what the till sees of it."""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.pos import PaymentIn, PosModifierGroupOut, PosVariantOut

MenuOrderType = Literal["dine_in", "takeaway"]


class MenuItemOut(BaseModel):
    """An item as the guest sees it: no stock figures, no costs — only whether
    it can be ordered right now."""

    id: uuid.UUID
    name: str
    unit: str
    sell_price: Decimal
    available: bool
    made_to_order: bool = False
    variants: list[PosVariantOut] = []
    modifier_groups: list[PosModifierGroupOut] = []


class MenuOut(BaseModel):
    business_name: str
    items: list[MenuItemOut]


class TicketLineIn(BaseModel):
    item_id: uuid.UUID
    variant_id: uuid.UUID | None = None
    modifier_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    quantity: Decimal = Field(gt=0, le=Decimal("99"))
    notes: str | None = Field(default=None, max_length=200)


class TicketIn(BaseModel):
    lines: list[TicketLineIn] = Field(min_length=1, max_length=30)
    order_type: MenuOrderType = "dine_in"
    table_label: str | None = Field(default=None, max_length=20)
    guest_name: str | None = Field(default=None, max_length=60)
    guest_phone: str | None = Field(default=None, max_length=32)
    note: str | None = Field(default=None, max_length=200)
    # svc-2: the phone names this submission once; a retry after a dropped
    # connection returns the same ticket instead of a second one.
    client_ref: str | None = Field(default=None, min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    # svc-5: the total the guest was shown (from /quote). If the server now
    # prices the same cart differently the order is refused and re-quoted.
    expected_total: Decimal | None = Field(default=None, ge=0)


class MenuQuoteIn(BaseModel):
    lines: list[TicketLineIn] = Field(min_length=1, max_length=30)
    order_type: MenuOrderType = "dine_in"


class MenuQuoteLineOut(BaseModel):
    item_id: uuid.UUID
    unit_price: Decimal
    line_total: Decimal


class MenuQuoteOut(BaseModel):
    """The estimate a guest confirms before sending (svc-5): the same pricing
    the cashier will run, tax and service included."""

    lines: list[MenuQuoteLineOut]
    subtotal: Decimal
    service_charge: Decimal
    tax_total: Decimal
    tax_inclusive: bool
    rounding: Decimal
    total: Decimal


class TicketLineOut(BaseModel):
    item_id: uuid.UUID
    name: str                      # item, with its size when it has one
    modifiers: list[str] = []
    quantity: Decimal
    unit_price: Decimal
    line_total: Decimal
    notes: str | None = None
    # What the till needs to reopen the line with the same answers (svc-2).
    variant_id: uuid.UUID | None = None
    size: str | None = None
    modifier_ids: list[uuid.UUID] = []


class TicketOut(BaseModel):
    """A ticket as the guest (and the till) sees it. Totals are the estimate at
    placing time until the ticket is settled; after that, the sale's figures."""

    id: uuid.UUID
    code: str
    status: Literal["open", "completed", "voided", "refunded"]
    order_type: str
    table_label: str | None
    guest_name: str | None
    note: str | None = None
    placed_at: datetime
    lines: list[TicketLineOut]
    subtotal: Decimal
    service_charge: Decimal
    tax_total: Decimal
    rounding: Decimal
    total: Decimal
    is_estimate: bool
    order_no: str | None = None         # prt-1: "042", the number said at the counter
    batch_no: int = 0
    kitchen_state: str | None = None   # once paid: new · preparing · ready · done (M11-T2)
    access_key: str | None = None      # svc-5: returned to the device that placed it, nobody else
    revised: bool = False              # svc-5: the cashier changed it after it was sent


class PosTicketOut(TicketOut):
    guest_phone: str | None = None


class TicketSettleIn(BaseModel):
    """What the till adds when it takes payment — the same knobs as a sale."""

    payments: list[PaymentIn] = Field(min_length=1, max_length=10)
    bill_discount: Decimal = Field(default=Decimal(0), ge=0, le=Decimal("999999999"))
    manager_pin: str | None = Field(default=None, min_length=4, max_length=6)
    customer_id: uuid.UUID | None = None
    voucher_code: str | None = Field(default=None, max_length=40)
    rev: int | None = Field(default=None, ge=0)   # svc-2: the version the cashier is looking at
    client_ref: str | None = Field(default=None, min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class TicketCancelIn(BaseModel):
    reason: str | None = Field(default=None, max_length=200)


class MenuLinkOut(BaseModel):
    menu_token: str
    menu_path: str


# ── Open orders at the till (svc-2) ──────────────────────────────────────────

DraftOrderType = Literal["dine_in", "takeaway", "pickup"]


class DraftIn(BaseModel):
    """A cashier parks an unpaid order. Prices are never sent: the server prices it."""

    lines: list[TicketLineIn] = Field(min_length=1, max_length=50)
    order_type: DraftOrderType = "takeaway"
    table_label: str | None = Field(default=None, max_length=20)
    guest_name: str | None = Field(default=None, max_length=60)
    note: str | None = Field(default=None, max_length=200)
    client_ref: str | None = Field(default=None, min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    parent_order_id: uuid.UUID | None = None   # svc-3: held addition to a paid order
    external_ref: str | None = Field(default=None, max_length=40)   # prt-1: a driver's or platform's reference, typed by hand


class OpenOrderUpdateIn(BaseModel):
    """Replace an unpaid order's cart. `rev` is the version the device loaded."""

    rev: int = Field(ge=0)
    lines: list[TicketLineIn] = Field(min_length=1, max_length=50)
    order_type: DraftOrderType | None = None
    table_label: str | None = Field(default=None, max_length=20)
    guest_name: str | None = Field(default=None, max_length=60)
    note: str | None = Field(default=None, max_length=200)
    external_ref: str | None = Field(default=None, max_length=40)


class ActiveLineOut(BaseModel):
    name: str
    size: str | None = None
    quantity: Decimal
    modifiers: list[str] = []
    notes: str | None = None
    line_total: Decimal | None = None
    item_id: uuid.UUID | None = None
    variant_id: uuid.UUID | None = None
    modifier_ids: list[uuid.UUID] = []
    done: bool = False


class ActiveOrderOut(BaseModel):
    """One order the counter is still responsible for: unpaid, or paid and not
    yet handed over. Payment and preparation are separate facts (svc-2)."""

    id: uuid.UUID
    code: str                       # what is called out: M-1A2B for QR, #1A2B at the till
    number: str                     # the receipt number, last 8 of the id
    source: Literal["pos", "menu"]
    status: str                     # open · completed · voided · refunded
    payment: Literal["unpaid", "paid", "cancelled", "reversed"]
    prep: Literal["new", "preparing", "ready", "done"] | None = None   # None until paid
    order_type: str
    table_label: str | None = None
    guest_name: str | None = None
    note: str | None = None
    placed_at: datetime
    paid_at: datetime | None = None
    prep_since: datetime | None = None
    total: Decimal
    is_estimate: bool
    rev: int = 0
    staff_name: str | None = None
    lines: list[ActiveLineOut]
    parent_id: uuid.UUID | None = None
    parent_code: str | None = None
    # prt-1: the daily service number, its day, and which batch of the family
    order_no: str = ""
    service_date: date | None = None
    batch_no: int = 0
    external_ref: str | None = None
    previous_day: bool = False       # numbered on an earlier business day than today
    # svc-5: unpaid lines whose catalogue price or availability moved since
    # they were priced. Payment is refused until the order is re-priced.
    price_changes: list["PriceChangeOut"] = []


class PriceChangeOut(BaseModel):
    name: str
    was: Decimal | None
    now: Decimal | None


class RepriceIn(BaseModel):
    rev: int = Field(ge=0)


ActiveOrderOut.model_rebuild()
