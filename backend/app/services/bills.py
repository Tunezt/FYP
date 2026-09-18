"""Open bills per table (bill-1).

A dine-in table orders, eats, orders again and pays when it leaves. The bill is
an ordinary open order (`status = 'open'`, a `cart`, services/tickets.py); what
this module adds is the idea of *sending*:

  * Every cart line has a `uid`. Lines are *unsent* until the cashier sends
    them; sending stamps them with the batch number (`sent_batch`), writes the
    batch to `cart.batches`, and owes paper for exactly those lines — a Bar
    slip, a Dapur slip and the table's nota (services/printing.py).
  * A sent line is locked. It cannot be edited through the cart; it can only be
    cancelled, with a reason, and the station that may already be making it is
    told on paper (BATAL). The cancellation is kept in `cart.voids`.
  * A dine-in table has at most one open bill. Table labels are compared the
    way people say them ("Meja 7", "meja7" and "7" are one table).
  * Paying a bill that was sent sends whatever is still unsent first, then the
    ordinary sale runs (create_order): stock, costs, payments and the journal
    are written at payment, exactly as for every other order.

Nothing is deleted: a cancelled line leaves the cart's lines and is kept in
`cart.voids` with who, when and why.
"""
from __future__ import annotations

import re
import secrets
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Item, Order
from app.services.orders import OrderLineSpec, TicketNotOpen

# Keys a cart line keeps across re-pricing: who it is and whether it went out.
LINE_META = ("uid", "sent_batch", "sent_at")


class TableBusy(Exception):
    """The table already has an open bill; add to that one instead."""

    def __init__(self, order: Order):
        self.order = order


class NotSendable(Exception):
    """code: not_dine_in · no_table · addition · nothing"""

    def __init__(self, code: str):
        self.code = code


class SentLineLocked(Exception):
    """A sent line cannot be changed through the cart, only cancelled."""

    def __init__(self, name: str):
        self.name = name


class LineNotFound(Exception):
    pass


class ReasonRequired(Exception):
    pass


class StockShort(Exception):
    """Sending would promise more of a counted item than the books hold, once
    the other open bills' sent items are counted too. Payment would then be
    refused for food already served, so the send is refused instead."""

    def __init__(self, item_name: str, available: Decimal):
        self.item_name = item_name
        self.available = available


def new_uid() -> str:
    return secrets.token_hex(6)


def table_key(label: str | None) -> str | None:
    """How people say a table: "Meja 7", "meja 7", "MEJA7" and "7" are one."""
    s = re.sub(r"\s+", " ", (label or "").strip().lower())
    s = re.sub(r"^meja\s*[:.#-]?\s*", "", s).strip()
    return s or None


def lines_of(order: Order) -> list[dict]:
    return list((order.cart or {}).get("lines", []))


def sent_lines(order: Order) -> list[dict]:
    return [l for l in lines_of(order) if l.get("sent_batch")]


def unsent_lines(order: Order) -> list[dict]:
    return [l for l in lines_of(order) if not l.get("sent_batch")]


def has_batches(order: Order) -> bool:
    return bool((order.cart or {}).get("batches"))


def spec_of(line: dict) -> OrderLineSpec:
    return OrderLineSpec(
        item_id=uuid.UUID(line["item_id"]),
        variant_id=uuid.UUID(line["variant_id"]) if line.get("variant_id") else None,
        modifier_ids=[uuid.UUID(m) for m in line.get("modifier_ids", [])],
        quantity=Decimal(line["quantity"]),
        notes=line.get("notes"),
    )


def same_line(line: dict, spec: OrderLineSpec) -> bool:
    """Whether a device's copy of a line says what the stored line says."""
    return (
        uuid.UUID(line["item_id"]) == spec.item_id
        and (spec.variant_id is None or (line.get("variant_id") and uuid.UUID(line["variant_id"]) == spec.variant_id))
        and sorted(line.get("modifier_ids", [])) == sorted(str(m) for m in spec.modifier_ids)
        and Decimal(line["quantity"]) == Decimal(spec.quantity)
        and (line.get("notes") or None) == ((spec.notes or "").strip() or None)
    )


def carry_meta(priced: list[dict], metas: list[dict]) -> list[dict]:
    """price_cart returns fresh line dicts in input order; put back each line's
    identity and send state."""
    out = []
    for line, meta in zip(priced, metas):
        kept = {k: meta[k] for k in LINE_META if meta.get(k) is not None}
        out.append({**line, **kept})
    return out


async def lock_order(session: AsyncSession, order: Order) -> Order:
    """Take the row for the rest of the transaction, and read it fresh."""
    locked = (await session.execute(
        select(Order).where(Order.id == order.id).with_for_update().execution_options(populate_existing=True)
    )).scalar_one()
    return locked


async def open_bill_for_table(session: AsyncSession, label: str | None, *, exclude_id: uuid.UUID | None = None) -> Order | None:
    key = table_key(label)
    if key is None:
        return None
    rows = (await session.execute(
        select(Order).where(Order.status == "open", Order.cart.is_not(None), Order.order_type == "dine_in",
                            Order.parent_order_id.is_(None), Order.table_label.is_not(None))
        .order_by(Order.created_at, Order.id)
    )).scalars().all()
    for o in rows:
        if o.id != exclude_id and table_key(o.table_label) == key:
            return o
    return None


async def ensure_table_free(session: AsyncSession, business_id: uuid.UUID, *, order_type: str, table_label: str | None,
                            parent_order_id: uuid.UUID | None = None, exclude_id: uuid.UUID | None = None) -> None:
    """One open dine-in bill per table. Serialised per table with an advisory
    lock, so two tablets opening Meja 7 at once cannot both succeed."""
    key = table_key(table_label)
    if order_type != "dine_in" or key is None or parent_order_id is not None:
        return
    await session.execute(text("select pg_advisory_xact_lock(hashtext(:k))"), {"k": f"table:{business_id}:{key}"})
    other = await open_bill_for_table(session, table_label, exclude_id=exclude_id)
    if other is not None:
        raise TableBusy(other)


async def _check_stock(session: AsyncSession, order: Order, lines: list[dict]) -> None:
    """Counted items only (a made-to-order drink takes its components at
    payment, as it always has). What this batch needs, plus what every open
    bill has already sent and not yet paid, must be on the books."""
    from app.services.catalog import made_to_order_item_ids

    made_to_order = await made_to_order_item_ids(session)
    wanted: dict[str, Decimal] = {}
    for l in lines:
        if uuid.UUID(l["item_id"]) not in made_to_order:
            wanted[l["item_id"]] = wanted.get(l["item_id"], Decimal(0)) + Decimal(l["quantity"])
    if not wanted:
        return
    promised: dict[str, Decimal] = {}
    others = (await session.execute(select(Order).where(Order.status == "open", Order.cart.is_not(None)))).scalars().all()
    for o in others:
        for l in (o.cart or {}).get("lines", []):
            if l.get("sent_batch") and l["item_id"] in wanted:
                promised[l["item_id"]] = promised.get(l["item_id"], Decimal(0)) + Decimal(l["quantity"])
    for item_id, qty in wanted.items():
        item = await session.get(Item, uuid.UUID(item_id))
        on_books = Decimal(item.current_stock) if item is not None else Decimal(0)
        left = on_books - promised.get(item_id, Decimal(0))
        if left < qty:
            raise StockShort(item.name if item is not None else "", max(left, Decimal(0)))


async def send(session: AsyncSession, *, order: Order, expected_rev: int | None, staff_id: uuid.UUID | None,
               nota: bool = True, now: datetime | None = None) -> Order:
    """Send the bill's unsent lines to be made: batch n, its slips and its nota.
    Under the row lock, so two taps or two tablets cannot send one batch twice."""
    from app.services.tickets import OrderChanged, cart_rev, is_open_order

    order = await lock_order(session, order)
    if order.status != "open" or not is_open_order(order):
        raise TicketNotOpen(order.status)
    if expected_rev is not None and cart_rev(order) != int(expected_rev):
        raise OrderChanged(order.status, cart_rev(order))
    if order.order_type != "dine_in":
        raise NotSendable("not_dine_in")
    if order.parent_order_id is not None:
        raise NotSendable("addition")
    if table_key(order.table_label) is None:
        raise NotSendable("no_table")
    going = unsent_lines(order)
    if not going:
        raise NotSendable("nothing")
    await _check_stock(session, order, going)
    return await _stamp_batch(session, order, going, staff_id=staff_id, nota=nota, now=now)


async def _stamp_batch(session: AsyncSession, order: Order, going: list[dict], *, staff_id, nota: bool,
                       now: datetime | None) -> Order:
    from app.services.printing import enqueue_for_send

    moment = now or datetime.now(timezone.utc)
    cart = dict(order.cart or {})
    batches = list(cart.get("batches", []))
    n = len(batches) + 1
    going_uids = {l["uid"] for l in going}
    lines = [
        {**l, "sent_batch": n, "sent_at": moment.isoformat()} if l.get("uid") in going_uids else l
        for l in cart.get("lines", [])
    ]
    batches.append({"n": n, "at": moment.isoformat(), "staff_id": str(staff_id) if staff_id else None,
                    "uids": sorted(going_uids), "nota": nota})
    order.cart = {**cart, "lines": lines, "batches": batches, "rev": int(cart.get("rev", 0)) + 1}
    await session.flush()
    await enqueue_for_send(session, order, n=n, lines=[l for l in lines if l.get("uid") in going_uids],
                           staff_id=staff_id, nota=nota)
    return order


async def send_remainder_before_payment(session: AsyncSession, order: Order, *, staff_id: uuid.UUID | None) -> Order:
    """Paying a bill that was already sent: anything added since goes out as a
    last batch of slips. No nota, the receipt is about to print."""
    if not has_batches(order):
        return order
    going = unsent_lines(order)
    if not going:
        return order
    return await _stamp_batch(session, order, going, staff_id=staff_id, nota=False, now=None)


async def cancel_sent_line(session: AsyncSession, *, business_id: uuid.UUID, order: Order, uid: str,
                           quantity: Decimal | None, reason: str | None, expected_rev: int | None,
                           staff_id: uuid.UUID | None, now: datetime | None = None) -> Order:
    """Take a sent item (or some of its quantity) off the bill. The reason is
    required and kept; the station is told, or its unprinted slip withdrawn."""
    from app.services.printing import cancel_cart_lines
    from app.services.tickets import OrderChanged, _apply_bill, cart_rev, is_open_order, price_cart

    reason = (reason or "").strip()
    if not reason:
        raise ReasonRequired()
    order = await lock_order(session, order)
    if order.status != "open" or not is_open_order(order):
        raise TicketNotOpen(order.status)
    if expected_rev is not None and cart_rev(order) != int(expected_rev):
        raise OrderChanged(order.status, cart_rev(order))
    cart = dict(order.cart or {})
    lines = list(cart.get("lines", []))
    target = next((l for l in lines if l.get("uid") == uid and l.get("sent_batch")), None)
    if target is None:
        raise LineNotFound()
    have = Decimal(target["quantity"])
    take = have if quantity is None else Decimal(quantity)
    if take <= 0 or take > have:
        raise LineNotFound()
    moment = now or datetime.now(timezone.utc)
    kept = []
    for l in lines:
        if l is target:
            if take < have:
                kept.append({**l, "quantity": str(have - take)})
        else:
            kept.append(l)
    priced, bill = await price_cart(session, business_id=business_id, lines=[spec_of(l) for l in kept],
                                    order_type=order.order_type, check_stock=False)
    kept = carry_meta(priced, kept)
    voids = list(cart.get("voids", []))
    voids.append({
        "uid": uid, "item_id": target["item_id"], "item_name": target["item_name"], "variant_name": target.get("variant_name"),
        "modifier_names": list(target.get("modifier_names", [])), "notes": target.get("notes"),
        "prep_station": target.get("prep_station"), "quantity": str(take), "sent_batch": target["sent_batch"],
        "unit_price": target.get("unit_price"), "reason": reason[:200], "at": moment.isoformat(),
        "staff_id": str(staff_id) if staff_id else None,
    })
    order.cart = {**cart, "lines": kept, "voids": voids, "rev": int(cart.get("rev", 0)) + 1}
    _apply_bill(order, bill)
    await session.flush()
    await cancel_cart_lines(session, order, [(target, take)], kept, key=f"void:{len(voids)}", staff_id=staff_id, reason=reason)
    return order


async def cancel_bill_prints(session: AsyncSession, order: Order, *, reason: str | None, staff_id: uuid.UUID | None) -> None:
    """The whole unpaid bill is cancelled: every sent item that may be on a
    slip is cancelled on paper, per station."""
    from app.services.printing import cancel_cart_lines

    going = [(l, Decimal(l["quantity"])) for l in sent_lines(order)]
    if going:
        await cancel_cart_lines(session, order, going, [], key="cancel", staff_id=staff_id, reason=reason)
