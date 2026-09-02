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

from app.models import Item, Order, OrderLine, Payment
from app.services.stock import record_movement


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
    """One item, one order, one line, one payment, one stock movement — all in
    the caller's transaction. `payment_method` defaults to cash because the POS
    kiosk does not yet ask (M3-T3 adds real multi-line orders and split payment).
    """
    item = await session.get(Item, item_id)
    if item is None:
        raise ItemNotFound()

    result = await session.execute(ATOMIC_DECREMENT, {"qty": quantity, "item_id": item_id})
    remaining = result.scalar_one_or_none()
    if remaining is None:
        raise InsufficientStock(item.name, item.current_stock)

    price = unit_price if unit_price is not None else item.sell_price
    total = (price * quantity).quantize(Decimal("0.01"))
    # Set client-side (not left to the server default) so the value is
    # available on the ORM objects right after flush, without a refresh.
    sold_at = datetime.now(timezone.utc)
    order = Order(
        business_id=business_id,
        staff_id=staff_id,
        order_type="takeaway",
        status="completed",
        subtotal=total,
        total=total,
        sold_at=sold_at,
    )
    session.add(order)
    await session.flush()
    line = OrderLine(
        business_id=business_id,
        order_id=order.id,
        item_id=item_id,
        quantity=quantity,
        unit_price=price,
        line_total=total,
        # The cost snapshot that fixes the old "margin drifts with cost_price" bug.
        unit_cost_at_sale=item.cost_price,
    )
    session.add(line)
    session.add(Payment(business_id=business_id, order_id=order.id, method=payment_method, amount=total))
    await session.flush()
    # Ledger row in the same transaction (M2-T2). The atomic UPDATE above stays
    # the concurrency guard; this is the auditable history alongside it.
    await record_movement(
        session,
        business_id=business_id,
        item_id=item_id,
        qty_delta=-quantity,
        reason="sale",
        source_type="sale",
        source_id=line.id,
        unit_cost=item.cost_price,
        staff_id=staff_id,
        created_at=sold_at,
    )
    return RecordedSale(order=order, line=line, item_name=item.name, remaining_stock=remaining)
