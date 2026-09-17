"""Kitchen display (roadmap M11-T2): tickets, states, the bump.

Every *paid* order is a kitchen ticket — a till sale or a settled e-menu
ticket alike; an unpaid e-menu ticket is not (the kitchen starts when the
money is in). A ticket's state is the latest row in `kitchen_events`:

    new ──→ preparing ──→ ready ──→ done
     └──────────┴───────────┘  (bump: done from anywhere)

`new` is the absence of rows. States only move forward; a repeat of the
current state is a no-op (a double tap is not an error). Nothing is updated:
every change appends a row with who made it and when, so the history is the
data. The board is the completed orders of the last BOARD_WINDOW_HOURS whose
latest state is not `done` — a ticket nobody bumped does not haunt tomorrow.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Item, ItemVariant, KitchenEvent, Order, OrderLine, OrderLineModifier
from app.services.tickets import ticket_code

STATES = ("new", "preparing", "ready", "done")
TRANSITIONS = {
    "new": {"preparing", "ready", "done"},
    "preparing": {"ready", "done"},
    "ready": {"done"},
    "done": set(),
}
STATE_LABEL_ID = {"new": "baru", "preparing": "sedang disiapkan", "ready": "siap", "done": "selesai"}
BOARD_WINDOW_HOURS = 12


class KitchenInvalid(Exception):
    """code: not_found · not_paid · transition (with the current state)."""

    def __init__(self, code: str, current: str | None = None, wanted: str | None = None):
        self.code, self.current, self.wanted = code, current, wanted


@dataclass
class KitchenLine:
    name: str
    quantity: Decimal
    modifiers: list[str] = field(default_factory=list)
    notes: str | None = None


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


async def _lines_of(session: AsyncSession, order: Order) -> list[KitchenLine]:
    # By item name, not by insertion: `order_lines.created_at` is the
    # transaction's clock, identical for every line of one sale, so any
    # "insertion order" would really be the random uuid order. A cook reading
    # the same ticket twice must see the same list.
    rows = (await session.execute(
        select(OrderLine, Item.name).join(Item, Item.id == OrderLine.item_id)
        .where(OrderLine.order_id == order.id).order_by(Item.name, OrderLine.id)
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
    out: list[KitchenLine] = []
    for l in lines:
        name = names[l.id]
        if l.variant_id is not None:
            variant = await session.get(ItemVariant, l.variant_id)
            if variant is not None and not variant.is_default:
                name = f"{name} · {variant.name}"
        out.append(KitchenLine(name=name, quantity=Decimal(l.quantity), modifiers=mods_by_line.get(l.id, []), notes=l.notes))
    return out


async def ticket_view(session: AsyncSession, order: Order, event: KitchenEvent | None) -> KitchenTicket:
    cart = order.cart or {}
    parent = await session.get(Order, order.parent_order_id) if order.parent_order_id else None
    return KitchenTicket(
        parent_code=order_code(parent) if parent is not None else None,
        order_id=order.id, code=order_code(order), source=order.source, order_type=order.order_type,
        table_label=order.table_label, guest_name=order.guest_name, note=cart.get("note"),
        delivery_address=order.delivery_address,
        sold_at=order.sold_at,
        state=event.state if event is not None else "new",
        state_since=event.created_at if event is not None else None,
        lines=await _lines_of(session, order),
    )


async def board(session: AsyncSession, now: datetime | None = None) -> list[KitchenTicket]:
    """Paid orders of the last BOARD_WINDOW_HOURS not yet bumped, oldest first."""
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


async def set_state(
    session: AsyncSession, *, business_id: uuid.UUID, order_id: uuid.UUID, state: str, staff_id: uuid.UUID | None,
    now: datetime | None = None,
) -> KitchenTicket:
    """Move a ticket forward. Appends one event; never rewrites one."""
    if state not in STATES:
        raise KitchenInvalid("transition", wanted=state)
    order = await session.get(Order, order_id)
    if order is None:
        raise KitchenInvalid("not_found")
    if order.status != "completed":
        raise KitchenInvalid("not_paid", current=order.status)
    current, _since = await current_state(session, order_id)
    if state == current:
        return await ticket_view(session, order, (await latest_events(session, [order_id])).get(order_id))
    if state not in TRANSITIONS[current]:
        raise KitchenInvalid("transition", current=current, wanted=state)
    event = KitchenEvent(business_id=business_id, order_id=order_id, state=state, staff_id=staff_id,
                         created_at=now or datetime.now(timezone.utc))
    session.add(event)
    await session.flush()
    return await ticket_view(session, order, event)
