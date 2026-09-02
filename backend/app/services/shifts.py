"""Shifts (roadmap M7-T1): a cashier's till session.

Open with a float, sell, close with a count. While a shift is open the
expectation is live (float + cash payments attributed to the shift); at
close it is written to the row together with the count and the variance,
and never changed again — a wrong count is a note and the next shift's
problem, not an edit.

Attribution: `create_order` stamps the cashier's open shift on the order
and its payments; a reversal stamps the *acting* cashier's open shift on the
reversing payments, because the refund leaves that till. A sale with no open
shift is still a sale (`shift_id` NULL) — M7 discipline is about the count,
not about refusing customers.

M7-T2 adds cash in/out to the expectation; M7-T3 posts the variance.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Payment, Shift, Staff

MONEY = Decimal("0.01")


class ShiftInvalid(Exception):
    """`code`: already_open, float, not_open, closed, counted."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class CashSummary:
    opening_float: Decimal
    cash_sales: Decimal     # cash taken (positive cash payments)
    cash_refunds: Decimal   # cash handed back (reversing cash payments), as a positive number
    expected_cash: Decimal  # float + sales − refunds


async def current_shift(session: AsyncSession, staff_id: uuid.UUID | None) -> Shift | None:
    if staff_id is None:
        return None
    return (
        await session.execute(select(Shift).where(Shift.staff_id == staff_id, Shift.status == "open"))
    ).scalar_one_or_none()


async def open_shift_id(session: AsyncSession, staff_id: uuid.UUID | None) -> uuid.UUID | None:
    shift = await current_shift(session, staff_id)
    return shift.id if shift else None


async def open_shift(
    session: AsyncSession, business_id: uuid.UUID, *, staff_id: uuid.UUID,
    opening_float: Decimal, opened_at: datetime | None = None,
) -> Shift:
    opening_float = Decimal(opening_float).quantize(MONEY)
    if opening_float < 0:
        raise ShiftInvalid("float")
    if await current_shift(session, staff_id) is not None:
        raise ShiftInvalid("already_open")
    shift = Shift(business_id=business_id, staff_id=staff_id, status="open", opening_float=opening_float)
    if opened_at is not None:
        shift.opened_at = opened_at
    session.add(shift)
    await session.flush()
    return shift


async def cash_summary(session: AsyncSession, shift: Shift) -> CashSummary:
    taken, handed_back = (
        await session.execute(
            select(
                func.coalesce(func.sum(func.greatest(Payment.amount, 0)), 0),
                func.coalesce(func.sum(func.least(Payment.amount, 0)), 0),
            ).where(Payment.shift_id == shift.id, Payment.method == "cash")
        )
    ).one()
    cash_sales, cash_refunds = Decimal(taken).quantize(MONEY), (-Decimal(handed_back)).quantize(MONEY)
    opening = Decimal(shift.opening_float).quantize(MONEY)
    return CashSummary(opening, cash_sales, cash_refunds, opening + cash_sales - cash_refunds)


async def close_shift(
    session: AsyncSession, shift: Shift, *, counted_cash: Decimal, closed_by: uuid.UUID | None,
    closed_at: datetime | None = None, notes: str | None = None,
) -> Shift:
    if shift.status != "open":
        raise ShiftInvalid("closed")
    counted_cash = Decimal(counted_cash).quantize(MONEY)
    if counted_cash < 0:
        raise ShiftInvalid("counted")
    summary = await cash_summary(session, shift)
    now = closed_at or datetime.now(timezone.utc)
    shift.expected_cash = summary.expected_cash
    shift.counted_cash = counted_cash
    shift.variance = counted_cash - summary.expected_cash
    shift.status = "closed"
    shift.closed_at = now
    shift.closed_by = closed_by
    shift.notes = notes
    shift.updated_at = now
    await session.flush()
    return shift


async def list_shifts(session: AsyncSession, *, limit: int = 50) -> list[Shift]:
    return (
        await session.execute(select(Shift).order_by(Shift.opened_at.desc(), Shift.id).limit(limit))
    ).scalars().all()


async def shift_view(session: AsyncSession, shift: Shift) -> dict:
    """What both the kiosk and the owner see: the row plus its live cash summary."""
    staff = await session.get(Staff, shift.staff_id)
    summary = await cash_summary(session, shift)
    return {
        "id": shift.id, "staff_id": shift.staff_id, "staff_name": staff.name if staff else "?",
        "status": shift.status, "opening_float": shift.opening_float,
        "opened_at": shift.opened_at, "closed_at": shift.closed_at, "closed_by": shift.closed_by,
        "cash_sales": summary.cash_sales, "cash_refunds": summary.cash_refunds,
        "expected_cash": shift.expected_cash if shift.status == "closed" else summary.expected_cash,
        "counted_cash": shift.counted_cash, "variance": shift.variance, "notes": shift.notes,
    }
