"""Open orders: unpaid carts the shop is holding (M11-T1, svc-2).

An open order is a row in `orders` with `status = 'open'` and a `cart`. Two
channels write them:

  * `source = 'menu'` — a guest's order from the QR menu (M11-T1), a *ticket*;
  * `source = 'pos'`  — a cashier's held draft (svc-2): "Americano for the
    lady in blue, she's finding her wallet", parked while the next customer
    orders.

Writing one takes nothing: no lines, no stock movement, no payment, no journal.
The cart is kept verbatim (jsonb), priced at the catalogue prices of the moment
with the bill estimated by the same pure pricing function the till uses. Promos,
vouchers, discounts and points belong to the till and are applied when the
order is settled.

An open order can be edited until it is paid (`update_open_order`): the cart is
re-priced from the catalogue and its revision number goes up, under a
conditional update — two devices editing the same order cannot both win, and a
cashier cannot pay for a version of the order they have not seen.

Settling runs `create_order` against the order's own row (its `ticket=`
argument): stock, cost snapshots, payments, points and the journal are written
once, at payment, by the one code path that already does it — the status flips
to `completed` under an atomic claim so two tills cannot settle one order.
Cancelling flips it to `voided` with the reason kept in `cart`; there is nothing
to reverse because nothing was taken. Nothing is deleted.
"""
from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import Integer, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Item, ItemVariant, Order, OrderLine, OrderLineModifier, Payment
from app.services.catalog import default_variant, made_to_order_item_ids
from app.services.orders import (
    TWO_PLACES,
    CreatedLine,
    CreatedOrder,
    ItemNotFound,
    OrderLineSpec,
    PaymentSpec,
    TicketNotOpen,
    VariantNotFound,
    _resolve_modifiers,
    create_order,
    require_explicit_choices,
)
from app.services.pricing import LineInput, price_order, pricing_config

MAX_OPEN_TICKETS = 50   # a public endpoint: the queue cannot grow without bound
MAX_OPEN_DRAFTS = 40    # a till holding more unpaid orders than this has a different problem
MENU_ORDER_TYPES = ("dine_in", "takeaway")
POS_DRAFT_ORDER_TYPES = ("dine_in", "takeaway", "pickup")


class TicketUnavailable(Exception):
    """The item is out of stock right now — say so before the guest waits."""

    def __init__(self, item_name: str):
        self.item_name = item_name


class QueueFull(Exception):
    pass


class TicketNotFound(Exception):
    pass


class PricesChanged(Exception):
    """The catalogue moved under an unpaid order (svc-5): a price is not what the
    customer was shown, or a product, size or option is gone. Payment waits until
    the cashier re-prices it in front of the customer."""

    def __init__(self, changes: list["PriceChange"]):
        self.changes = changes


@dataclass
class PriceChange:
    name: str
    was: Decimal | None
    now: Decimal | None     # None: no longer sold as ordered


class OrderChanged(Exception):
    """Somebody else edited, paid or cancelled this order since it was loaded.
    Carries the current status and revision so the device can reload."""

    def __init__(self, status: str, rev: int):
        self.status = status
        self.rev = rev


def ticket_code(order_id: uuid.UUID) -> str:
    """What the guest quotes at the counter: the id's tail, short enough to say."""
    return f"M-{str(order_id)[-4:].upper()}"


def is_ticket(order: Order) -> bool:
    return order.source == "menu" and order.cart is not None


def is_open_order(order: Order) -> bool:
    """A held cart of either channel — a QR ticket or a cashier's draft."""
    return order.cart is not None and order.source in ("menu", "pos")


def cart_rev(order: Order) -> int:
    return int((order.cart or {}).get("rev", 0))


async def find_by_client_ref(session: AsyncSession, business_id: uuid.UUID, client_ref: str | None) -> Order | None:
    """The order a device already created with this reference, if any (svc-2).

    Takes a transaction-scoped advisory lock on the reference first, so a
    second copy of the same submission arriving mid-flight waits for the first
    to commit and then finds its order, instead of racing it to a duplicate."""
    if not client_ref:
        return None
    await session.execute(
        text("select pg_advisory_xact_lock(hashtext(:k))"), {"k": f"order-ref:{business_id}:{client_ref}"}
    )
    return (await session.execute(
        select(Order).where(Order.client_ref == client_ref)
    )).scalar_one_or_none()


async def price_cart(
    session: AsyncSession,
    *,
    business_id: uuid.UUID,
    lines: list[OrderLineSpec],
    order_type: str,
    check_stock: bool = True,
) -> tuple[list[dict], object]:
    """Validate a cart against the catalogue and price it: (cart lines, bill).

    Every choice must be the customer's own (svc-1). Stock is only *checked*
    (an item that is out is refused so nobody waits for nothing); it is taken
    at settlement. Prices are always today's catalogue prices — the server
    decides what things cost, never the device."""
    await require_explicit_choices(session, lines)
    config = await pricing_config(session, business_id)
    made_to_order = await made_to_order_item_ids(session)
    cart_lines: list[dict] = []
    inputs: list[LineInput] = []
    wanted: dict[uuid.UUID, Decimal] = {}
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
        wanted[item.id] = wanted.get(item.id, Decimal(0)) + quantity
        if check_stock and item.id not in made_to_order and Decimal(item.current_stock) < wanted[item.id]:
            raise TicketUnavailable(item.name)
        base = Decimal(variant.sell_price) if variant is not None else Decimal(item.sell_price)
        unit_price = (base + sum((Decimal(m.price_delta) for m in modifiers), Decimal(0))).quantize(TWO_PLACES)
        line_total = (unit_price * quantity).quantize(TWO_PLACES)
        inputs.append(LineInput(unit_price=unit_price, quantity=quantity))
        sizes = (await session.execute(
            select(func.count(ItemVariant.id)).where(ItemVariant.item_id == item.id, ItemVariant.is_active.is_(True))
        )).scalar_one()
        cart_lines.append({
            "item_id": str(item.id), "item_name": item.name, "unit": item.unit,
            "variant_id": str(variant.id) if variant is not None else None,
            # The size is written whenever the product has sizes, the default
            # one included: "Standar" was a choice too (svc-1).
            "variant_name": variant.name if variant is not None and sizes > 1 else None,
            "modifier_ids": [str(m.id) for m in modifiers], "modifier_names": [m.name for m in modifiers],
            "modifier_prices": [str(Decimal(m.price_delta)) for m in modifiers],
            "quantity": str(quantity), "base_price": str(base), "unit_price": str(unit_price),
            "line_total": str(line_total), "notes": (spec.notes or "").strip() or None,
        })
    bill = price_order(inputs, config, order_type=order_type)
    return cart_lines, bill


def _apply_bill(order: Order, bill) -> None:
    order.subtotal = bill.subtotal
    order.service_charge = bill.service_charge
    order.tax_total = bill.tax_total
    order.rounding = bill.rounding
    order.total = bill.total


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
    client_ref: str | None = None,
) -> Order:
    """A guest's order: validate, estimate, write the open row."""
    if order_type not in MENU_ORDER_TYPES:
        order_type = "dine_in"
    existing = await find_by_client_ref(session, business_id, client_ref)
    if existing is not None:
        return existing
    placed_at = placed_at or datetime.now(timezone.utc)
    waiting = int((await session.execute(
        select(func.count(Order.id)).where(Order.status == "open", Order.source == "menu")
    )).scalar_one())
    if waiting >= MAX_OPEN_TICKETS:
        raise QueueFull()
    cart_lines, bill = await price_cart(session, business_id=business_id, lines=lines, order_type=order_type)
    ticket = Order(
        business_id=business_id,
        staff_id=None,
        source="menu",
        order_type=order_type,
        status="open",
        table_label=table_label or None,
        guest_name=guest_name or None,
        guest_phone=guest_phone or None,
        # The guest's own key to this order (svc-5). The menu link is shared by
        # every table; the order id alone must not show a stranger's name.
        cart={"lines": cart_lines, "note": note or None, "estimate": True, "rev": 0,
              "access_key": secrets.token_urlsafe(18)},
        sold_at=placed_at,
        created_at=placed_at,
        client_ref=client_ref,
    )
    _apply_bill(ticket, bill)
    session.add(ticket)
    await session.flush()
    return ticket


async def hold_draft(
    session: AsyncSession,
    *,
    business_id: uuid.UUID,
    staff_id: uuid.UUID | None,
    lines: list[OrderLineSpec],
    order_type: str = "takeaway",
    table_label: str | None = None,
    guest_name: str | None = None,
    note: str | None = None,
    client_ref: str | None = None,
    now: datetime | None = None,
    parent_order_id: uuid.UUID | None = None,
) -> Order:
    """A cashier parks an unpaid order (svc-2). Same shape as a ticket, on the
    backend so a refresh, a second tablet or a crashed browser loses nothing."""
    if order_type not in POS_DRAFT_ORDER_TYPES:
        order_type = "takeaway"
    existing = await find_by_client_ref(session, business_id, client_ref)
    if existing is not None:
        return existing
    held = int((await session.execute(
        select(func.count(Order.id)).where(Order.status == "open", Order.source == "pos")
    )).scalar_one())
    if held >= MAX_OPEN_DRAFTS:
        raise QueueFull()
    moment = now or datetime.now(timezone.utc)
    from app.services.orders import resolve_parent

    parent = await resolve_parent(session, business_id, parent_order_id)
    cart_lines, bill = await price_cart(session, business_id=business_id, lines=lines, order_type=order_type)
    draft = Order(
        parent_order_id=parent.id if parent is not None else None,
        business_id=business_id,
        staff_id=staff_id,
        source="pos",
        order_type=order_type,
        status="open",
        table_label=(table_label or "").strip() or None,
        guest_name=(guest_name or "").strip() or None,
        cart={"lines": cart_lines, "note": (note or "").strip() or None, "estimate": True, "rev": 0,
              "held_by": str(staff_id) if staff_id else None},
        sold_at=moment,
        created_at=moment,
        client_ref=client_ref,
    )
    _apply_bill(draft, bill)
    session.add(draft)
    await session.flush()
    return draft


async def update_open_order(
    session: AsyncSession,
    *,
    business_id: uuid.UUID,
    order: Order,
    expected_rev: int,
    staff_id: uuid.UUID | None,
    lines: list[OrderLineSpec],
    order_type: str | None = None,
    table_label: str | None = None,
    guest_name: str | None = None,
    note: str | None = None,
    now: datetime | None = None,
) -> Order:
    """Replace an unpaid order's cart (svc-2): add items, change quantities or
    options before payment. Re-priced from today's catalogue.

    Guarded by the revision the device loaded: the UPDATE only lands when the
    row is still open *and* still at that revision, so a second tablet's edit,
    a payment or a cancellation in between is a conflict, not a silent overwrite.
    What each revision came to is appended to `cart.revisions` — the cart of an
    unpaid order is not ledger data, but who changed it and when is kept."""
    if order.status != "open" or not is_open_order(order):
        raise TicketNotOpen(order.status)
    allowed = MENU_ORDER_TYPES if order.source == "menu" else POS_DRAFT_ORDER_TYPES
    kind = order_type if order_type in allowed else order.order_type
    cart_lines, bill = await price_cart(session, business_id=business_id, lines=lines, order_type=kind)
    moment = now or datetime.now(timezone.utc)
    old = dict(order.cart or {})
    revisions = list(old.get("revisions", []))
    revisions.append({"rev": int(old.get("rev", 0)), "at": moment.isoformat(), "staff_id": str(staff_id) if staff_id else None,
                      "total": str(order.total), "items": str(sum((Decimal(l["quantity"]) for l in old.get("lines", [])), Decimal(0)))})
    new_cart = {
        **old,
        "lines": cart_lines,
        "note": (note if note is not None else old.get("note")) or None,
        "rev": int(expected_rev) + 1,
        "revisions": revisions[-20:],
    }
    values = dict(
        cart=new_cart, order_type=kind,
        subtotal=bill.subtotal, service_charge=bill.service_charge, tax_total=bill.tax_total,
        rounding=bill.rounding, total=bill.total,
    )
    if table_label is not None:
        values["table_label"] = table_label.strip() or None
    if guest_name is not None:
        values["guest_name"] = guest_name.strip() or None
    rev_now = func.coalesce(Order.cart["rev"].astext.cast(Integer), 0)
    result = await session.execute(
        update(Order)
        .where(Order.id == order.id, Order.business_id == business_id, Order.status == "open", rev_now == int(expected_rev))
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        await session.refresh(order)
        raise OrderChanged(order.status, cart_rev(order))
    await session.refresh(order)
    return order


async def open_tickets(session: AsyncSession) -> list[Order]:
    """The QR queue, oldest first."""
    return list((await session.execute(
        select(Order).where(Order.status == "open", Order.source == "menu").order_by(Order.created_at)
    )).scalars().all())


async def open_orders(session: AsyncSession) -> list[Order]:
    """Every unpaid held cart, QR and till alike, oldest first (svc-2)."""
    return list((await session.execute(
        select(Order).where(Order.status == "open", Order.cart.is_not(None)).order_by(Order.created_at, Order.id)
    )).scalars().all())


async def get_ticket(session: AsyncSession, order_id: uuid.UUID) -> Order:
    ticket = await session.get(Order, order_id)
    if ticket is None or not is_ticket(ticket):
        raise TicketNotFound()
    return ticket


async def get_open_order(session: AsyncSession, order_id: uuid.UUID) -> Order:
    """A QR ticket or a till draft, in any status (the caller decides what a
    paid or cancelled one means). A plain till sale is not one."""
    order = await session.get(Order, order_id)
    if order is None or not is_open_order(order):
        raise TicketNotFound()
    return order


async def price_changes(session: AsyncSession, order: Order) -> list[PriceChange]:
    """What today's catalogue says about an unpaid cart, line by line (svc-5).
    Stock is not checked here — running out is the sale's own refusal, with its
    own message — only whether each line still exists at the price it shows."""
    from app.models import Modifier

    changes: list[PriceChange] = []
    for l in (order.cart or {}).get("lines", []):
        label = f"{l['item_name']} · {l['variant_name']}" if l.get("variant_name") else l["item_name"]
        was = Decimal(l["unit_price"])
        item = await session.get(Item, uuid.UUID(l["item_id"]))
        variant = await session.get(ItemVariant, uuid.UUID(l["variant_id"])) if l.get("variant_id") else None
        if item is None or (l.get("variant_id") and (variant is None or not variant.is_active)):
            changes.append(PriceChange(name=label, was=was, now=None))
            continue
        base = Decimal(variant.sell_price) if variant is not None else Decimal(item.sell_price)
        now = base
        gone = False
        for mid in l.get("modifier_ids", []):
            m = await session.get(Modifier, uuid.UUID(mid))
            if m is None or not m.is_active:
                gone = True
                break
            now += Decimal(m.price_delta)
        if gone:
            changes.append(PriceChange(name=label, was=was, now=None))
        elif now.quantize(TWO_PLACES) != was.quantize(TWO_PLACES):
            changes.append(PriceChange(name=label, was=was, now=now.quantize(TWO_PLACES)))
    return changes


def ticket_specs(ticket: Order) -> list[OrderLineSpec]:
    """The cart as the sale path wants it — at the prices it was priced at."""
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


async def replay_created(session: AsyncSession, order: Order) -> CreatedOrder:
    """An already-paid order in the shape a fresh sale returns, for a replayed
    submission (svc-2): the device gets its receipt, nothing is written."""
    created = CreatedOrder(order=order)
    lines = (await session.execute(
        select(OrderLine, Item).join(Item, Item.id == OrderLine.item_id)
        .where(OrderLine.order_id == order.id, OrderLine.quantity > 0).order_by(OrderLine.created_at, OrderLine.id)
    )).all()
    for line, item in lines:
        mods = (await session.execute(
            select(OrderLineModifier).where(OrderLineModifier.order_line_id == line.id).order_by(OrderLineModifier.created_at)
        )).scalars().all()
        created.lines.append(CreatedLine(line=line, item_name=item.name, remaining_stock=Decimal(item.current_stock), modifiers=list(mods)))
    created.payments = list((await session.execute(
        select(Payment).where(Payment.order_id == order.id, Payment.amount > 0).order_by(Payment.created_at)
    )).scalars().all())
    return created


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
    expected_rev: int | None = None,
    client_ref: str | None = None,
) -> CreatedOrder:
    """The till takes payment: the ordinary sale, on the open order's own row.

    `client_ref` makes a repeated tap on "Bayar" return the first payment's
    result instead of a 409; `expected_rev` refuses to charge for a version of
    the order the cashier has not seen (svc-2)."""
    if client_ref and ticket.status == "completed" and (ticket.cart or {}).get("paid_ref") == client_ref:
        return await replay_created(session, ticket)
    if ticket.status != "open":
        raise TicketNotOpen(ticket.status)
    if expected_rev is not None and cart_rev(ticket) != int(expected_rev):
        raise OrderChanged(ticket.status, cart_rev(ticket))
    # Server-authoritative prices (svc-5): nobody is charged a price that is no
    # longer the catalogue's, and nobody is charged a new one without seeing it.
    changes = await price_changes(session, ticket)
    if changes:
        raise PricesChanged(changes)
    if ticket.parent_order_id is not None:
        from app.services.orders import resolve_parent

        await resolve_parent(session, business_id, ticket.parent_order_id)
    if client_ref:
        # `client_ref` on the row already names the submission that *created*
        # it (the guest's phone); the payment's own reference lives in the cart.
        ticket.cart = {**ticket.cart, "paid_ref": client_ref}
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
    """The order will not be paid: the row stays, its status says so, the cart
    remembers why. Nothing was taken, so there is nothing to reverse — which is
    exactly what makes this different from voiding a paid sale."""
    if ticket.status != "open":
        raise TicketNotOpen(ticket.status)
    ticket.status = "voided"
    ticket.cart = {
        **ticket.cart,
        "cancelled": {"reason": reason or None, "at": datetime.now(timezone.utc).isoformat(), "staff_id": str(staff_id) if staff_id else None},
    }
    await session.flush()
    return ticket
