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


class ItemOut(BaseModel):
    id: uuid.UUID
    name: str
    unit: str
    current_stock: Decimal
    sell_price: Decimal
    reorder_threshold: Decimal

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
