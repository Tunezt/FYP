"""Sale recording — the one write path shared by POS and (potential) WhatsApp.

Stock is decremented with the brief's atomic conditional UPDATE: the row-level
lock Postgres takes on UPDATE serializes two simultaneous sales of the last
unit; whichever loses the race sees `current_stock >= qty` fail, gets zero rows
back, and is rejected as insufficient stock. No application-level locking.
"""
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Item, Sale


class InsufficientStock(Exception):
    def __init__(self, item_name: str, available: Decimal):
        self.item_name = item_name
        self.available = available
        super().__init__(f"Insufficient stock for {item_name}: {available} available")


class ItemNotFound(Exception):
    pass


@dataclass
class RecordedSale:
    sale: Sale
    item_name: str
    remaining_stock: Decimal


ATOMIC_DECREMENT = text(
    """
    update items
    set current_stock = current_stock - :qty, updated_at = now()
    where id = :item_id and current_stock >= :qty
    returning current_stock
    """
)


async def record_sale(
    session: AsyncSession,
    *,
    business_id: uuid.UUID,
    staff_id: uuid.UUID,
    item_id: uuid.UUID,
    quantity: Decimal,
    unit_price: Decimal | None = None,
) -> RecordedSale:
    item = await session.get(Item, item_id)
    if item is None:
        raise ItemNotFound()

    result = await session.execute(ATOMIC_DECREMENT, {"qty": quantity, "item_id": item_id})
    remaining = result.scalar_one_or_none()
    if remaining is None:
        raise InsufficientStock(item.name, item.current_stock)

    price = unit_price if unit_price is not None else item.sell_price
    sale = Sale(
        business_id=business_id,
        item_id=item_id,
        quantity=quantity,
        unit_price=price,
        total_price=(price * quantity).quantize(Decimal("0.01")),
        staff_id=staff_id,
        # Set client-side (not left to the server default) so the value is
        # available on the ORM object right after flush, without a refresh.
        sold_at=datetime.now(timezone.utc),
    )
    session.add(sale)
    await session.flush()
    return RecordedSale(sale=sale, item_name=item.name, remaining_stock=remaining)
