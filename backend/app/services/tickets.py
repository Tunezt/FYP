"""E-menu tickets (roadmap M11-T1): a guest's order, waiting for the till.

A ticket is a row in `orders` with `status = 'open'` and `source = 'menu'`.
Placing one writes that row and nothing else — no lines, no stock movement,
no payment, no journal. What the guest asked for is kept verbatim in `cart`
(jsonb), priced at the menu prices they saw, with the bill estimated by the
same pure pricing function the till uses (tax, service charge, rounding).
Promos, vouchers, discounts and points belong to the till and are applied
when the ticket is settled.

Settling runs `create_order` against the ticket's own row (its `ticket=`
argument): stock, cost snapshots, payments, points and the journal are written
once, at payment, by the one code path that already does it — the ticket's
status flips to `completed` under an atomic claim so two tills cannot settle
one ticket. Cancelling flips it to `voided` with the reason kept in `cart`;
there is nothing to reverse because nothing was taken. Nothing is deleted.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Item, ItemVariant, Order
from app.services.catalog import default_variant, made_to_order_item_ids
from app.services.orders import (
    TWO_PLACES,
    CreatedOrder,
    ItemNotFound,
    OrderLineSpec,
    PaymentSpec,
    TicketNotOpen,
    VariantNotFound,
    _resolve_modifiers,
    create_order,
)
from app.services.pricing import LineInput, price_order, pricing_config

MAX_OPEN_TICKETS = 50   # a public endpoint: the queue cannot grow without bound
MENU_ORDER_TYPES = ("dine_in", "takeaway")


class TicketUnavailable(Exception):
    """The item is out of stock right now — say so before the guest waits."""

    def __init__(self, item_name: str):
        self.item_name = item_name


class QueueFull(Exception):
    pass


class TicketNotFound(Exception):
    pass


def ticket_code(order_id: uuid.UUID) -> str:
    """What the guest quotes at the counter: the id's tail, short enough to say."""
    return f"M-{str(order_id)[-4:].upper()}"


def is_ticket(order: Order) -> bool:
    return order.source == "menu" and order.cart is not None


async def place_ticket(
    session: AsyncSession,
    *,
    business_id: uuid.UUID,
    lines: list[OrderLineSpec],
    order_type: str = "dine_in",
    table_label: str | None = None,
    guest_name: str | None = None,
    guest_phone: str | None = None,
    note: str | None = None,
    placed_at: datetime | None = None,
) -> Order:
    """Validate the cart against the catalogue, estimate the bill, write the
    open row. Stock is only *checked* here (an item that is out is refused so
    the guest does not wait for nothing); it is taken at settlement."""
    if order_type not in MENU_ORDER_TYPES:
        order_type = "dine_in"
    placed_at = placed_at or datetime.now(timezone.utc)
    waiting = int((await session.execute(
        select(func.count(Order.id)).where(Order.status == "open", Order.source == "menu")
    )).scalar_one())
    if waiting >= MAX_OPEN_TICKETS:
        raise QueueFull()

    config = await pricing_config(session, business_id)
    made_to_order = await made_to_order_item_ids(session)
    cart_lines: list[dict] = []
    inputs: list[LineInput] = []
    for spec in lines:
        item = await session.get(Item, spec.item_id)
        if item is None:
            raise ItemNotFound()
        if spec.variant_id is not None:
            variant = await session.get(ItemVariant, spec.variant_id)
            if variant is None or variant.item_id != item.id or not variant.is_active:
                raise VariantNotFound()
        else:
            variant = await default_variant(session, item.id)
        modifiers = await _resolve_modifiers(session, item, list(spec.modifier_ids))
        quantity = Decimal(spec.quantity)
        if item.id not in made_to_order and Decimal(item.current_stock) < quantity:
            raise TicketUnavailable(item.name)
        base = Decimal(variant.sell_price) if variant is not None else Decimal(item.sell_price)
        unit_price = (base + sum((Decimal(m.price_delta) for m in modifiers), Decimal(0))).quantize(TWO_PLACES)
        line_total = (unit_price * quantity).quantize(TWO_PLACES)
        inputs.append(LineInput(unit_price=unit_price, quantity=quantity))
        cart_lines.append({
            "item_id": str(item.id), "item_name": item.name, "unit": item.unit,
            "variant_id": str(variant.id) if variant is not None else None,
            "variant_name": variant.name if variant is not None and not variant.is_default else None,   # the size only when it is a choice
            "modifier_ids": [str(m.id) for m in modifiers], "modifier_names": [m.name for m in modifiers],
            "quantity": str(quantity), "base_price": str(base), "unit_price": str(unit_price),
            "line_total": str(line_total), "notes": spec.notes,
        })
    bill = price_order(inputs, config, order_type=order_type)
    ticket = Order(
        business_id=business_id,
        staff_id=None,
        source="menu",
        order_type=order_type,
        status="open",
        table_label=table_label or None,
        guest_name=guest_name or None,
        guest_phone=guest_phone or None,
        cart={"lines": cart_lines, "note": note or None, "estimate": True},
        subtotal=bill.subtotal,
        service_charge=bill.service_charge,
        tax_total=bill.tax_total,
        rounding=bill.rounding,
        total=bill.total,
        sold_at=placed_at,
        created_at=placed_at,
    )
    session.add(ticket)
    await session.flush()
    return ticket


async def open_tickets(session: AsyncSession) -> list[Order]:
    """The queue, oldest first."""
    return list((await session.execute(
        select(Order).where(Order.status == "open", Order.source == "menu").order_by(Order.created_at)
    )).scalars().all())


async def get_ticket(session: AsyncSession, order_id: uuid.UUID) -> Order:
    ticket = await session.get(Order, order_id)
    if ticket is None or not is_ticket(ticket):
        raise TicketNotFound()
    return ticket


def ticket_specs(ticket: Order) -> list[OrderLineSpec]:
    """The cart as the sale path wants it — at the prices the guest was shown."""
    return [
        OrderLineSpec(
            item_id=uuid.UUID(l["item_id"]),
            variant_id=uuid.UUID(l["variant_id"]) if l.get("variant_id") else None,
            modifier_ids=[uuid.UUID(m) for m in l.get("modifier_ids", [])],
            quantity=Decimal(l["quantity"]),
            unit_price=Decimal(l["base_price"]),
            notes=l.get("notes"),
        )
        for l in ticket.cart["lines"]
    ]


async def settle_ticket(
    session: AsyncSession,
    *,
    business_id: uuid.UUID,
    ticket: Order,
    staff_id: uuid.UUID | None,
    payments: list[PaymentSpec],
    bill_discount: Decimal = Decimal(0),
    manager_pin: str | None = None,
    customer_id: uuid.UUID | None = None,
    voucher_code: str | None = None,
) -> CreatedOrder:
    """The till takes payment: the ordinary sale, on the ticket's row."""
    if ticket.status != "open":
        raise TicketNotOpen(ticket.status)
    return await create_order(
        session,
        business_id=business_id,
        staff_id=staff_id,
        lines=ticket_specs(ticket),
        payments=payments,
        order_type=ticket.order_type,
        bill_discount=bill_discount,
        manager_pin=manager_pin,
        customer_id=customer_id,
        voucher_code=voucher_code,
        ticket=ticket,
    )


async def cancel_ticket(session: AsyncSession, *, ticket: Order, reason: str | None, staff_id: uuid.UUID | None) -> Order:
    """The shop cannot serve it: the row stays, its status says so, the cart
    remembers why. Nothing was taken, so there is nothing to reverse."""
    if ticket.status != "open":
        raise TicketNotOpen(ticket.status)
    ticket.status = "voided"
    ticket.cart = {
        **ticket.cart,
        "cancelled": {"reason": reason or None, "at": datetime.now(timezone.utc).isoformat(), "staff_id": str(staff_id) if staff_id else None},
    }
    await session.flush()
    return ticket
