"""Order creation — the one write path for selling (roadmap M3-T3).

A sale is one order, N lines, M payments, N stock movements, all in the caller's
transaction: if anything fails — an item out of stock on the third line, payments
that do not add up — the caller rolls back and nothing was sold.

Per line the stock guard is the same atomic conditional UPDATE the one-item path
has always used (`current_stock >= qty`), so two kiosks racing for the last unit
still resolve at the database, line by line. `unit_cost_at_sale` snapshots the
item's cost at this moment so historical margin never moves.

No tax, discount, service charge or rounding yet (M7-T4): subtotal == total.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Item, Order, OrderLine, Payment
from app.services.sales import ATOMIC_DECREMENT, InsufficientStock, ItemNotFound
from app.services.stock import record_movement

TWO_PLACES = Decimal("0.01")


class PaymentMismatch(Exception):
    def __init__(self, total: Decimal, paid: Decimal):
        self.total = total
        self.paid = paid
        super().__init__(f"payments {paid} do not match order total {total}")


class EmptyOrder(Exception):
    pass


@dataclass
class OrderLineSpec:
    item_id: uuid.UUID
    quantity: Decimal
    unit_price: Decimal | None = None  # None → the item's current sell_price
    notes: str | None = None


@dataclass
class PaymentSpec:
    method: str
    amount: Decimal
    reference: str | None = None


@dataclass
class CreatedLine:
    line: OrderLine
    item_name: str
    remaining_stock: Decimal


@dataclass
class CreatedOrder:
    order: Order
    lines: list[CreatedLine] = field(default_factory=list)
    payments: list[Payment] = field(default_factory=list)


async def create_order(
    session: AsyncSession,
    *,
    business_id: uuid.UUID,
    staff_id: uuid.UUID | None,
    lines: list[OrderLineSpec],
    payments: list[PaymentSpec],
    order_type: str = "takeaway",
    sold_at: datetime | None = None,
) -> CreatedOrder:
    if not lines:
        raise EmptyOrder()
    if not payments:
        raise PaymentMismatch(Decimal(0), Decimal(0))
    sold_at = sold_at or datetime.now(timezone.utc)

    # 1. Price every line and take its stock, atomically, before any row exists.
    priced: list[tuple[OrderLineSpec, Item, Decimal, Decimal, Decimal]] = []
    for spec in lines:
        item = await session.get(Item, spec.item_id)
        if item is None:
            raise ItemNotFound()
        quantity = Decimal(spec.quantity)
        result = await session.execute(ATOMIC_DECREMENT, {"qty": quantity, "item_id": item.id})
        remaining = result.scalar_one_or_none()
        if remaining is None:
            raise InsufficientStock(item.name, item.current_stock)
        price = Decimal(spec.unit_price) if spec.unit_price is not None else Decimal(item.sell_price)
        line_total = (price * quantity).quantize(TWO_PLACES)
        priced.append((spec, item, price, line_total, Decimal(remaining)))

    subtotal = sum((lt for _, _, _, lt, _ in priced), Decimal(0)).quantize(TWO_PLACES)
    total = subtotal  # discounts, tax, service charge, rounding: M7-T4

    # 2. Payments must cover the total exactly — a till does not close on a guess.
    paid = sum((Decimal(p.amount) for p in payments), Decimal(0)).quantize(TWO_PLACES)
    if paid != total:
        raise PaymentMismatch(total, paid)

    # 3. Write the order, its lines, payments and ledger rows.
    order = Order(
        business_id=business_id,
        staff_id=staff_id,
        order_type=order_type,
        status="completed",
        subtotal=subtotal,
        total=total,
        sold_at=sold_at,
    )
    session.add(order)
    await session.flush()

    created = CreatedOrder(order=order)
    for spec, item, price, line_total, remaining in priced:
        line = OrderLine(
            business_id=business_id,
            order_id=order.id,
            item_id=item.id,
            quantity=Decimal(spec.quantity),
            unit_price=price,
            line_total=line_total,
            unit_cost_at_sale=item.cost_price,
            notes=spec.notes,
        )
        session.add(line)
        await session.flush()
        await record_movement(
            session,
            business_id=business_id,
            item_id=item.id,
            qty_delta=-Decimal(spec.quantity),
            reason="sale",
            source_type="sale",
            source_id=line.id,
            unit_cost=item.cost_price,
            staff_id=staff_id,
            created_at=sold_at,
        )
        created.lines.append(CreatedLine(line=line, item_name=item.name, remaining_stock=remaining))

    for p in payments:
        payment = Payment(
            business_id=business_id,
            order_id=order.id,
            method=p.method,
            amount=Decimal(p.amount).quantize(TWO_PLACES),
            reference=p.reference,
        )
        session.add(payment)
        created.payments.append(payment)
    await session.flush()
    return created
