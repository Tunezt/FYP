"""Daily service numbers (prt-1): what staff and customers call an order.

"Pesanan 042" is allocated once, server-side, when an order is first
persisted: a held draft, a QR order, or a sale rung straight through. It is
kept through edits, payment, refreshes and replays (a replay returns the
already-numbered order and never reaches this module). A paid addition does
not get a number of its own: it shares its original's and takes the next batch
("Pesanan 042 · Tambahan 1").

The sequence is per business per *business day* (M15-T4), so a bill at 00:15
with a 04:00 day start still belongs to the night's numbers. An order carried
across the boundary keeps the number and date it was given; the date is what
disambiguates "042" in history.

A display number is never a credential. Nothing authorises on it, and every
lookup by number is scoped to a date.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.periods import business_day
from app.models import Business, Order

_NEXT = text(
    """
    insert into service_number_counters (business_id, service_date, last_number)
    values (:bid, :day, 1)
    on conflict (business_id, service_date)
    do update set last_number = service_number_counters.last_number + 1, updated_at = now()
    returning last_number
    """
)


async def service_day(session: AsyncSession, business_id: uuid.UUID, at: datetime | None = None) -> date:
    business = await session.get(Business, business_id)
    moment = at or datetime.now(timezone.utc)
    if business is None:
        return moment.date()
    return business_day(moment, business.timezone, business.day_start_hour)


async def allocate(session: AsyncSession, business_id: uuid.UUID, at: datetime | None = None) -> tuple[date, int]:
    """The next number of the business day `at` falls in. One statement,
    serialised by Postgres on the counter row: concurrent callers never share a
    number, and the number never depends on how many orders exist."""
    day = await service_day(session, business_id, at)
    number = (await session.execute(_NEXT, {"bid": business_id, "day": day})).scalar_one()
    return day, int(number)


async def number_order(session: AsyncSession, order: Order, *, at: datetime | None = None, parent: Order | None = None) -> None:
    """Give a newly persisted order its identity, once. An order that already
    has one keeps it; an addition inherits its original's and takes the next
    batch under a lock on the original, so two additions racing get 1 and 2."""
    if order.service_number is not None:
        return
    if parent is not None:
        locked = (await session.execute(select(Order).where(Order.id == parent.id).with_for_update())).scalar_one()
        if locked.service_number is None:
            locked.service_date, locked.service_number = await allocate(session, order.business_id, locked.created_at)
        last = (await session.execute(
            select(func.coalesce(func.max(Order.batch_no), 0)).where(Order.parent_order_id == locked.id)
        )).scalar_one()
        order.service_date, order.service_number = locked.service_date, locked.service_number
        order.batch_no = int(last) + 1
        return
    order.service_date, order.service_number = await allocate(session, order.business_id, at)


def service_label(order: Order) -> str:
    """"042", "1204", or the pre-prt-1 reference for an order that never had
    a number. Callers add "Pesanan" / "Tambahan" around it."""
    if order.service_number is None:
        return f"#{str(order.id)[-4:].upper()}"
    return f"{order.service_number:03d}"


def batch_label(order: Order) -> str:
    """"Pesanan 042" or "Pesanan 042 · Tambahan 1"."""
    base = f"Pesanan {service_label(order)}"
    return f"{base} · Tambahan {order.batch_no}" if order.batch_no else base
