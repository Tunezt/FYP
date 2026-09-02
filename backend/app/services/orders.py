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


# ── Reversals (roadmap M3-T4) ───────────────────────────────────────────────
#
# A void (wrong order, never handed over) and a refund (customer brought it
# back) both write REVERSING rows: a negative-quantity line per original line,
# a negative payment per original payment, and a positive stock movement per
# line. The original rows are never touched; the order's `status` is the only
# field that changes, and it is a state transition the enum exists for. In the
# `sales` view the reversal nets the original to zero, which is the accounting
# effect readers should see.

from sqlalchemy import select, text as sql_text  # noqa: E402

from app.core.security import verify_pin  # noqa: E402
from app.models import Staff  # noqa: E402


class OrderNotFound(Exception):
    pass


class OrderNotReversible(Exception):
    def __init__(self, status: str):
        self.status = status
        super().__init__(f"order is {status}")


class ManagerPinRejected(Exception):
    pass


RESTOCK = sql_text(
    """
    update items
    set current_stock = current_stock + :qty, updated_at = now()
    where id = :item_id
    returning current_stock
    """
)


async def verify_manager_pin(session: AsyncSession, pin: str) -> Staff:
    """The owner's PIN is the manager PIN (the schema has owner/staff roles
    only). Any active owner-role staff of the pinned business may authorise."""
    owners = (
        await session.execute(select(Staff).where(Staff.role == "owner", Staff.is_active.is_(True)))
    ).scalars().all()
    for owner in owners:
        if verify_pin(pin, owner.pin_hash):
            return owner
    raise ManagerPinRejected()


@dataclass
class Reversal:
    order: Order
    reversing_lines: list[OrderLine]
    reversing_payments: list[Payment]
    restocked: dict[uuid.UUID, Decimal]  # item_id -> stock after restock


async def _reverse(
    session: AsyncSession,
    *,
    business_id: uuid.UUID,
    order_id: uuid.UUID,
    staff_id: uuid.UUID | None,
    manager: Staff,
    kind: str,            # "void" | "refund"
    restock: bool,
    note: str | None,
) -> Reversal:
    order = await session.get(Order, order_id)
    if order is None or order.business_id != business_id:
        raise OrderNotFound()
    if order.status != "completed":
        raise OrderNotReversible(order.status)

    originals = (
        await session.execute(
            select(OrderLine).where(OrderLine.order_id == order_id, OrderLine.quantity > 0).order_by(OrderLine.created_at)
        )
    ).scalars().all()
    payments = (await session.execute(select(Payment).where(Payment.order_id == order_id))).scalars().all()

    now = datetime.now(timezone.utc)
    reason = "sale_void" if kind == "void" else "refund"
    tag = f"{kind} oleh {manager.name}" + (f": {note}" if note else "")
    reversal = Reversal(order=order, reversing_lines=[], reversing_payments=[], restocked={})

    for line in originals:
        reversing = OrderLine(
            business_id=business_id,
            order_id=order_id,
            item_id=line.item_id,
            variant_id=line.variant_id,
            quantity=-line.quantity,
            unit_price=line.unit_price,
            line_discount=-line.line_discount,
            line_total=-line.line_total,
            unit_cost_at_sale=line.unit_cost_at_sale,
            notes=tag,
            created_at=now,
        )
        session.add(reversing)
        await session.flush()
        reversal.reversing_lines.append(reversing)
        if restock:
            after = (await session.execute(RESTOCK, {"qty": line.quantity, "item_id": line.item_id})).scalar_one()
            reversal.restocked[line.item_id] = Decimal(after)
            await record_movement(
                session,
                business_id=business_id,
                item_id=line.item_id,
                qty_delta=line.quantity,
                reason=reason,
                source_type="sale",
                source_id=reversing.id,
                unit_cost=line.unit_cost_at_sale,
                staff_id=staff_id,
                created_at=now,
            )

    for p in payments:
        if p.amount <= 0:
            continue
        reversing_payment = Payment(
            business_id=business_id,
            order_id=order_id,
            method=p.method,
            amount=-p.amount,
            reference=f"{kind}:{p.id}",
            created_at=now,
        )
        session.add(reversing_payment)
        reversal.reversing_payments.append(reversing_payment)

    order.status = "voided" if kind == "void" else "refunded"
    await session.flush()
    return reversal


async def void_order(
    session: AsyncSession, *, business_id: uuid.UUID, order_id: uuid.UUID,
    staff_id: uuid.UUID | None, manager_pin: str, note: str | None = None,
) -> Reversal:
    """The order never happened: everything reversed, stock back on the shelf."""
    manager = await verify_manager_pin(session, manager_pin)
    return await _reverse(session, business_id=business_id, order_id=order_id, staff_id=staff_id,
                          manager=manager, kind="void", restock=True, note=note)


async def refund_order(
    session: AsyncSession, *, business_id: uuid.UUID, order_id: uuid.UUID,
    staff_id: uuid.UUID | None, manager_pin: str, restock: bool = True, note: str | None = None,
) -> Reversal:
    """Money back. `restock=False` when the goods are not coming back (eaten,
    spoiled): revenue and payments reverse, stock does not — no movement row."""
    manager = await verify_manager_pin(session, manager_pin)
    return await _reverse(session, business_id=business_id, order_id=order_id, staff_id=staff_id,
                          manager=manager, kind="refund", restock=restock, note=note)
