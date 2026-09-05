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

The expectation (M7-T3) is float + cash payments − cash refunds + cash in
− cash out, over everything attributed to this till. Closing writes it with
the count and the variance, and posts the variance to the ledger in the same
transaction: short is an expense, over is income against the same account.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CashMovement, Payment, Shift, Staff
from app.services.posting import post_event

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
    cash_in: Decimal        # cash movements into the drawer (M7-T2)
    cash_out: Decimal       # petty cash, supplier paid in cash, bank drop, as a positive number
    expected_cash: Decimal  # float + sales − refunds + in − out


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
    """What the drawer should hold: float + cash payments − cash refunds
    + cash in − cash out, over everything stamped with this shift. Only
    movements that went through the drawer carry a shift (M7-T2), so this
    counts all of them."""
    # Imported here, not at module scope: cash.py needs open_shift_id from us.
    from app.services.cash import OUTFLOWS

    taken, handed_back = (
        await session.execute(
            select(
                func.coalesce(func.sum(func.greatest(Payment.amount, 0)), 0),
                func.coalesce(func.sum(func.least(Payment.amount, 0)), 0),
            ).where(Payment.shift_id == shift.id, Payment.method == "cash")
        )
    ).one()
    outflow = CashMovement.kind.in_(OUTFLOWS)
    moved_in, moved_out = (
        await session.execute(
            select(
                func.coalesce(func.sum(case((outflow, 0), else_=CashMovement.amount)), 0),
                func.coalesce(func.sum(case((outflow, CashMovement.amount), else_=0)), 0),
            ).where(CashMovement.shift_id == shift.id)
        )
    ).one()
    cash_sales, cash_refunds = Decimal(taken).quantize(MONEY), (-Decimal(handed_back)).quantize(MONEY)
    cash_in, cash_out = Decimal(moved_in).quantize(MONEY), Decimal(moved_out).quantize(MONEY)
    opening = Decimal(shift.opening_float).quantize(MONEY)
    return CashSummary(
        opening, cash_sales, cash_refunds, cash_in, cash_out,
        opening + cash_sales - cash_refunds + cash_in - cash_out,
    )


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
    await post_variance(session, shift)
    return shift


async def post_variance(session: AsyncSession, shift: Shift) -> None:
    """The variance is real money that left or arrived without a sale, so it
    goes on the books like anything else (M7-T3). Short is an expense, over is
    that same account credited. A shift that counted exactly posts nothing.

    Runs in the caller's transaction: if the ledger refuses, the close goes
    with it and the shift is still open."""
    variance = Decimal(shift.variance or 0).quantize(MONEY)
    if variance == 0:
        return
    component = "variance_over" if variance > 0 else "variance_short"
    await post_event(
        session, shift.business_id, "ShiftClosed", {component: abs(variance)},
        source_type="shift", source_id=shift.id,
        memo="Selisih kas saat tutup shift", posted_at=shift.closed_at, created_by=shift.closed_by,
    )


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
        "cash_in": summary.cash_in, "cash_out": summary.cash_out,
        "expected_cash": shift.expected_cash if shift.status == "closed" else summary.expected_cash,
        "counted_cash": shift.counted_cash, "variance": shift.variance, "notes": shift.notes,
    }
