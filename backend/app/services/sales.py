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

from app.models import Item, Order, OrderLine


class InsufficientStock(Exception):
    def __init__(self, item_name: str, available: Decimal):
        self.item_name = item_name
        self.available = available
        super().__init__(f"Insufficient stock for {item_name}: {available} available")


class ItemNotFound(Exception):
    pass


@dataclass
class RecordedSale:
    """A completed single-item sale in the order model (M3-T2): `line` is what
    the `sales` view exposes (its id is the sale id), `order` carries staff,
    time and the total."""

    order: Order
    line: OrderLine
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
    payment_method: str = "cash",
) -> RecordedSale:
    """One item paid in full with one method — a thin wrapper over
    services/orders.create_order, which is the single write path for selling
    (M3-T3). `payment_method` defaults to cash for the legacy /pos/sales call.
    """
    from app.services.orders import OrderLineSpec, PaymentSpec, create_order

    item = await session.get(Item, item_id)
    if item is None:
        raise ItemNotFound()
    price = Decimal(unit_price) if unit_price is not None else Decimal(item.sell_price)
    total = (price * Decimal(quantity)).quantize(Decimal("0.01"))
    created = await create_order(
        session,
        business_id=business_id,
        staff_id=staff_id,
        lines=[OrderLineSpec(item_id=item_id, quantity=Decimal(quantity), unit_price=price)],
        payments=[PaymentSpec(method=payment_method, amount=total)],
    )
    first = created.lines[0]
    return RecordedSale(
        order=created.order, line=first.line, item_name=first.item_name, remaining_stock=first.remaining_stock
    )
