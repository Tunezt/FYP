"""QR e-menu (M11-T1): what a guest sees and sends, and what the till sees of it."""
import uuid
from datetime import datetime
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


class TicketLineOut(BaseModel):
    item_id: uuid.UUID
    name: str                      # item, with its size when it has one
    modifiers: list[str] = []
    quantity: Decimal
    unit_price: Decimal
    line_total: Decimal
    notes: str | None = None


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


class PosTicketOut(TicketOut):
    guest_phone: str | None = None


class TicketSettleIn(BaseModel):
    """What the till adds when it takes payment — the same knobs as a sale."""

    payments: list[PaymentIn] = Field(min_length=1, max_length=10)
    bill_discount: Decimal = Field(default=Decimal(0), ge=0, le=Decimal("999999999"))
    manager_pin: str | None = Field(default=None, min_length=4, max_length=6)
    customer_id: uuid.UUID | None = None
    voucher_code: str | None = Field(default=None, max_length=40)


class TicketCancelIn(BaseModel):
    reason: str | None = Field(default=None, max_length=200)


class MenuLinkOut(BaseModel):
    menu_token: str
    menu_path: str
