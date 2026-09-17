"""Kitchen display (roadmap M11-T2, svc-4): tickets, states, per-line progress.

Every *paid* order is a kitchen ticket — a till sale, a settled QR order, or an
addition to a paid order alike; an unpaid one is not (counter service: the
kitchen starts when the money is in). A ticket's state is the latest row in
`kitchen_events`:

    new ──→ preparing ──→ ready ──→ done
    Baru    Disiapkan     Siap diambil   Diserahkan

`new` is the absence of rows. States only move forward; a repeat of the
current state is a no-op (a double tap is not an error). Nothing is updated:
every change appends a row with who made it and when, so the history is the
data. The board is the completed orders of the last BOARD_WINDOW_HOURS whose
latest state is not `done` — a ticket nobody handed over does not haunt
tomorrow.

svc-4 adds three things without changing those rules:

* **Line progress** (`kitchen_line_events`). Ticking a line on a `new` ticket
  starts it. Once any line has been ticked, `ready` is refused while a line is
  unfinished — "ready" means the whole order is made. A ticket nobody ticked
  line by line can still be called ready as a whole (the M11-T2 behaviour, and
  the only sensible one for a single coffee); calling it ready completes every
  line.
* **The expected state.** A device sends the state it is looking at. If another
  device moved the ticket in between, the move is refused with the current
  state instead of silently skipping a step — two tablets tapping "Siap" and
  "Diserahkan" on a ticket they both saw as `preparing` cannot hand over an
  order nobody called. With an expected state the move must also be exactly
  one step, so there is no shortcut from `new` to `done`.
* **Cancellations the cooks need to see.** A paid order voided or refunded
  before it was handed over disappears from the board (it is no longer a sale)
  and reappears in `cancellations()` with who approved it and why, until
  somebody in the kitchen acknowledges it. Acknowledging appends `done`.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Approval, Item, ItemVariant, KitchenEvent, KitchenLineEvent, Order, OrderLine, OrderLineModifier, Staff,
)
from app.services.tickets import ticket_code

STATES = ("new", "preparing", "ready", "done")
TRANSITIONS = {
    "new": {"preparing", "ready", "done"},
    "preparing": {"ready", "done"},
    "ready": {"done"},
    "done": set(),
}
NEXT_STEP = {"new": "preparing", "preparing": "ready", "ready": "done"}
STATE_LABEL_ID = {"new": "baru", "preparing": "sedang disiapkan", "ready": "siap diambil", "done": "sudah diserahkan"}
BOARD_WINDOW_HOURS = 12
HISTORY_LIMIT = 30


class KitchenInvalid(Exception):
    """code: not_found · not_paid · transition · conflict (another device moved
    it) · lines_pending (with how many) · line_not_found · closed (the ticket is
    past the point where lines change)."""

    def __init__(self, code: str, current: str | None = None, wanted: str | None = None, pending: int = 0):
        self.code, self.current, self.wanted, self.pending = code, current, wanted, pending


@dataclass
class KitchenLine:
    name: str
    quantity: Decimal
    modifiers: list[str] = field(default_factory=list)
    notes: str | None = None
    line_id: uuid.UUID | None = None
    item_name: str = ""
    size: str | None = None
    done: bool = False


@dataclass
class KitchenTicket:
    order_id: uuid.UUID
    code: str
    source: str
    order_type: str
    table_label: str | None
    guest_name: str | None
    note: str | None
    delivery_address: str | None
    sold_at: datetime
    state: str
    state_since: datetime | None
    lines: list[KitchenLine] = field(default_factory=list)
    parent_code: str | None = None   # svc-3: "Tambahan untuk #1234"
    status: str = "completed"        # svc-4: voided / refunded for a cancellation notice
    reversal_reason: str | None = None
    reversed_by: str | None = None
    reversed_at: datetime | None = None
    state_by: str | None = None


def order_code(order: Order) -> str:
    """What the kitchen shouts: the e-menu code for a guest's ticket, the
    receipt's short number for a till sale."""
    return ticket_code(order.id) if order.source == "menu" else f"#{str(order.id)[-4:].upper()}"


async def latest_events(session: AsyncSession, order_ids: list[uuid.UUID]) -> dict[uuid.UUID, KitchenEvent]:
    if not order_ids:
        return {}
    rows = (await session.execute(
        select(KitchenEvent).where(KitchenEvent.order_id.in_(order_ids)).order_by(KitchenEvent.created_at, KitchenEvent.id)
    )).scalars().all()
    latest: dict[uuid.UUID, KitchenEvent] = {}
    for ev in rows:          # ordered oldest → newest, so the last write wins
        latest[ev.order_id] = ev
    return latest


async def current_state(session: AsyncSession, order_id: uuid.UUID) -> tuple[str, datetime | None]:
    ev = (await latest_events(session, [order_id])).get(order_id)
    return (ev.state, ev.created_at) if ev is not None else ("new", None)


async def _line_progress(session: AsyncSession, order_id: uuid.UUID) -> dict[uuid.UUID, bool]:
    rows = (await session.execute(
        select(KitchenLineEvent).where(KitchenLineEvent.order_id == order_id)
        .order_by(KitchenLineEvent.created_at, KitchenLineEvent.id)
    )).scalars().all()
    progress: dict[uuid.UUID, bool] = {}
    for ev in rows:
        progress[ev.order_line_id] = ev.done
    return progress


async def _paid_lines(session: AsyncSession, order_id: uuid.UUID) -> list[OrderLine]:
    return list((await session.execute(
        select(OrderLine).where(OrderLine.order_id == order_id, OrderLine.quantity > 0)
    )).scalars().all())


async def _lines_of(session: AsyncSession, order: Order, state: str = "new") -> list[KitchenLine]:
    # By item name, not by insertion: `order_lines.created_at` is the
    # transaction's clock, identical for every line of one sale, so any
    # "insertion order" would really be the random uuid order. A cook reading
    # the same ticket twice must see the same list.
    rows = (await session.execute(
        select(OrderLine, Item.name).join(Item, Item.id == OrderLine.item_id)
        .where(OrderLine.order_id == order.id, OrderLine.quantity > 0).order_by(Item.name, OrderLine.id)
    )).all()
    lines = [row[0] for row in rows]
    names = {row[0].id: row[1] for row in rows}
    if not lines:
        return []
    mods = (await session.execute(
        select(OrderLineModifier).where(OrderLineModifier.order_line_id.in_([l.id for l in lines])).order_by(OrderLineModifier.name)
    )).scalars().all()
    mods_by_line: dict[uuid.UUID, list[str]] = {}
    for m in mods:
        mods_by_line.setdefault(m.order_line_id, []).append(m.name)
    progress = await _line_progress(session, order.id)
    finished = state in ("ready", "done")
    sizes_of: dict[uuid.UUID, int] = {}
    out: list[KitchenLine] = []
    for l in lines:
        item_name = names[l.id]
        name, size = item_name, None
        if l.variant_id is not None:
            variant = await session.get(ItemVariant, l.variant_id)
            if l.item_id not in sizes_of:
                sizes_of[l.item_id] = (await session.execute(
                    select(func.count(ItemVariant.id)).where(ItemVariant.item_id == l.item_id)
                )).scalar_one()
            # The size the customer chose is written out whenever the product
            # has sizes — "Standar" included (svc-4). A product with one size
            # has nothing to read.
            if variant is not None and (not variant.is_default or sizes_of[l.item_id] > 1):
                size = variant.name
                name = f"{item_name} · {variant.name}"
        out.append(KitchenLine(
            name=name, quantity=Decimal(l.quantity), modifiers=mods_by_line.get(l.id, []), notes=l.notes,
            line_id=l.id, item_name=item_name, size=size, done=finished or progress.get(l.id, False),
        ))
    return out


async def _staff_name(session: AsyncSession, staff_id: uuid.UUID | None) -> str | None:
    if staff_id is None:
        return None
    staff = await session.get(Staff, staff_id)
    return staff.name if staff is not None else None


async def ticket_view(session: AsyncSession, order: Order, event: KitchenEvent | None) -> KitchenTicket:
    cart = order.cart or {}
    parent = await session.get(Order, order.parent_order_id) if order.parent_order_id else None
    state = event.state if event is not None else "new"
    return KitchenTicket(
        parent_code=order_code(parent) if parent is not None else None,
        order_id=order.id, code=order_code(order), source=order.source, order_type=order.order_type,
        table_label=order.table_label, guest_name=order.guest_name, note=cart.get("note"),
        delivery_address=order.delivery_address,
        sold_at=order.sold_at,
        state=state,
        state_since=event.created_at if event is not None else None,
        lines=await _lines_of(session, order, state),
        status=order.status,
        state_by=await _staff_name(session, event.staff_id) if event is not None else None,
    )


async def board(session: AsyncSession, now: datetime | None = None) -> list[KitchenTicket]:
    """Paid orders of the last BOARD_WINDOW_HOURS not yet handed over, oldest first."""
    moment = now or datetime.now(timezone.utc)
    since = moment - timedelta(hours=BOARD_WINDOW_HOURS)
    orders = (await session.execute(
        select(Order).where(Order.status == "completed", Order.sold_at >= since, Order.sold_at <= moment + timedelta(minutes=5))
        .order_by(Order.sold_at, Order.id)
    )).scalars().all()
    latest = await latest_events(session, [o.id for o in orders])
    tickets: list[KitchenTicket] = []
    for o in orders:
        ev = latest.get(o.id)
        if ev is not None and ev.state == "done":
            continue
        tickets.append(await ticket_view(session, o, ev))
    return tickets


async def _reversal_of(session: AsyncSession, order: Order) -> tuple[str | None, str | None, datetime | None]:
    row = (await session.execute(
        select(Approval).where(Approval.order_id == order.id, Approval.action.in_(("void", "refund")))
        .order_by(Approval.created_at.desc()).limit(1)
    )).scalar_one_or_none()
    if row is None:
        return None, None, None
    return row.note, await _staff_name(session, row.approved_by), row.created_at


async def cancellations(session: AsyncSession, now: datetime | None = None) -> list[KitchenTicket]:
    """Paid orders voided or refunded before anyone in the kitchen handed them
    over or acknowledged the change: the cooks must see why a ticket went away."""
    moment = now or datetime.now(timezone.utc)
    since = moment - timedelta(hours=BOARD_WINDOW_HOURS)
    orders = (await session.execute(
        select(Order).where(Order.status.in_(("voided", "refunded")), Order.sold_at >= since, Order.cart.is_(None) | ~Order.cart.has_key("cancelled"))
        .order_by(Order.sold_at, Order.id)
    )).scalars().all()
    latest = await latest_events(session, [o.id for o in orders])
    out: list[KitchenTicket] = []
    for o in orders:
        ev = latest.get(o.id)
        if ev is not None and ev.state == "done":
            continue
        if not await _paid_lines(session, o.id):
            continue
        view = await ticket_view(session, o, ev)
        view.reversal_reason, view.reversed_by, view.reversed_at = await _reversal_of(session, o)
        out.append(view)
    return out


async def history(session: AsyncSession, now: datetime | None = None, limit: int = HISTORY_LIMIT) -> list[KitchenTicket]:
    """What left the pass recently, newest handover first — for "did table 4
    get their toastie?"."""
    moment = now or datetime.now(timezone.utc)
    since = moment - timedelta(hours=BOARD_WINDOW_HOURS)
    done_rows = (await session.execute(
        select(KitchenEvent).where(KitchenEvent.state == "done", KitchenEvent.created_at >= since)
        .order_by(KitchenEvent.created_at.desc(), KitchenEvent.id).limit(limit)
    )).scalars().all()
    out: list[KitchenTicket] = []
    for ev in done_rows:
        order = await session.get(Order, ev.order_id)
        if order is None:
            continue
        view = await ticket_view(session, order, ev)
        if order.status in ("voided", "refunded"):
            view.reversal_reason, view.reversed_by, view.reversed_at = await _reversal_of(session, order)
        out.append(view)
    return out


async def set_state(
    session: AsyncSession, *, business_id: uuid.UUID, order_id: uuid.UUID, state: str, staff_id: uuid.UUID | None,
    now: datetime | None = None, expected: str | None = None,
) -> KitchenTicket:
    """Move a ticket forward. Appends one event; never rewrites one.

    `expected` (svc-4) is the state the device was showing. When it is given
    the move must be exactly the next step from it, and it is refused with
    `conflict` if the ticket is no longer there — unless it already reached the
    wanted state, which is the same tap arriving twice."""
    if state not in STATES:
        raise KitchenInvalid("transition", wanted=state)
    order = await session.get(Order, order_id, with_for_update=True)
    if order is None:
        raise KitchenInvalid("not_found")
    if order.status in ("voided", "refunded") and state == "done" and await _paid_lines(session, order_id):
        # Acknowledging a cancellation: the cooks have seen it, clear it.
        pass
    elif order.status != "completed":
        raise KitchenInvalid("not_paid", current=order.status)
    current, _since = await current_state(session, order_id)
    if state == current:
        return await ticket_view(session, order, (await latest_events(session, [order_id])).get(order_id))
    if expected is not None:
        if current != expected:
            raise KitchenInvalid("conflict", current=current, wanted=state)
        if order.status == "completed" and NEXT_STEP.get(current) != state:
            raise KitchenInvalid("transition", current=current, wanted=state)
    if state not in TRANSITIONS[current]:
        raise KitchenInvalid("transition", current=current, wanted=state)
    if state == "ready" and order.status == "completed":
        progress = await _line_progress(session, order_id)
        if progress:
            pending = [l for l in await _paid_lines(session, order_id) if not progress.get(l.id, False)]
            if pending:
                raise KitchenInvalid("lines_pending", current=current, wanted=state, pending=len(pending))
    event = KitchenEvent(business_id=business_id, order_id=order_id, state=state, staff_id=staff_id,
                         created_at=now or datetime.now(timezone.utc))
    session.add(event)
    await session.flush()
    return await ticket_view(session, order, event)


async def set_line_done(
    session: AsyncSession, *, business_id: uuid.UUID, order_id: uuid.UUID, line_id: uuid.UUID, done: bool,
    staff_id: uuid.UUID | None, now: datetime | None = None,
) -> KitchenTicket:
    """Tick (or untick) one line. Ticking a line on a `new` ticket starts it;
    lines are fixed once the ticket is ready."""
    order = await session.get(Order, order_id, with_for_update=True)
    if order is None:
        raise KitchenInvalid("not_found")
    if order.status != "completed":
        raise KitchenInvalid("not_paid", current=order.status)
    line = await session.get(OrderLine, line_id)
    if line is None or line.order_id != order_id or line.quantity <= 0:
        raise KitchenInvalid("line_not_found")
    current, _ = await current_state(session, order_id)
    if current in ("ready", "done"):
        raise KitchenInvalid("closed", current=current)
    moment = now or datetime.now(timezone.utc)
    progress = await _line_progress(session, order_id)
    if progress.get(line_id, False) != done:
        if current == "new" and done:
            session.add(KitchenEvent(business_id=business_id, order_id=order_id, state="preparing", staff_id=staff_id, created_at=moment))
        session.add(KitchenLineEvent(business_id=business_id, order_id=order_id, order_line_id=line_id, done=done,
                                     staff_id=staff_id, created_at=moment))
        await session.flush()
    return await ticket_view(session, order, (await latest_events(session, [order_id])).get(order_id))
