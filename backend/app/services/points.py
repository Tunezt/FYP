"""Points (roadmap M8-T2): an append-only loyalty ledger, same pattern as stock.

  points_movements          one signed row per change, never edited
  customers.points_balance  the cached SUM(points_delta), kept in the same
                            transaction as every row, reconciled by an
                            invariant test

Earning: a sale attached to a customer earns floor(paid / rupiah_per_point),
on what was paid with money — the part paid with points earns nothing. The
cost of the points (points × point_value) is posted `PointsEarned`
(Dr marketing expense / Cr points liability).

Redeeming is a payment method. `points` on a payment is a rupiah amount that
must be a whole number of points at `point_value`; the balance is decremented
with an atomic conditional UPDATE — the stock race, again — so two tills cannot
spend the same points. The sale's own `payment:points` component posts it
(Dr points liability / Cr revenue), so nothing is posted here.

Reversing: a void or refund writes the opposite rows for every earn and redeem
the order caused. Redeemed points come back (the void flips the sale's entry,
the refund posts `refund:points`); earned points are taken back and their
accrued cost released (`PointsReversed`). Taking back points the customer has
already spent can leave a negative balance — visible, rare, and never spendable
because the redemption guard requires balance >= points.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Customer, LoyaltySettings, PointsMovement
from app.services.posting import post_event

MONEY = Decimal("0.01")


class PointsInvalid(Exception):
    """`code`: inactive, customer, whole, min, points, delta."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}:{detail}")


class InsufficientPoints(Exception):
    def __init__(self, needed: int, available: int):
        self.needed = needed
        self.available = available
        super().__init__(f"needed {needed} points, has {available}")


@dataclass(frozen=True)
class LoyaltyConfig:
    is_active: bool = False
    rupiah_per_point: Decimal = Decimal(1000)
    point_value: Decimal = Decimal(100)
    min_redeem_points: int = 0

    @classmethod
    def from_row(cls, row: LoyaltySettings) -> "LoyaltyConfig":
        return cls(
            is_active=bool(row.is_active), rupiah_per_point=Decimal(row.rupiah_per_point),
            point_value=Decimal(row.point_value), min_redeem_points=int(row.min_redeem_points),
        )


async def ensure_loyalty_settings(session: AsyncSession, business_id: uuid.UUID) -> LoyaltySettings:
    row = (await session.execute(select(LoyaltySettings))).scalar_one_or_none()
    if row is None:
        row = LoyaltySettings(business_id=business_id)
        session.add(row)
        await session.flush()
    return row


async def loyalty_config(session: AsyncSession, business_id: uuid.UUID) -> LoyaltyConfig:
    """Read-only on the sale path (see pricing_config for why)."""
    row = (await session.execute(select(LoyaltySettings))).scalar_one_or_none()
    return LoyaltyConfig.from_row(row) if row is not None else LoyaltyConfig()


def points_for_amount(amount: Decimal, config: LoyaltyConfig) -> int:
    if not config.is_active or config.rupiah_per_point <= 0 or amount <= 0:
        return 0
    return int((Decimal(amount) / config.rupiah_per_point).to_integral_value(rounding=ROUND_FLOOR))


def rupiah_for_points(points: int, config: LoyaltyConfig) -> Decimal:
    return (Decimal(points) * config.point_value).quantize(MONEY)


def points_for_rupiah(amount: Decimal, config: LoyaltyConfig) -> int:
    """The whole number of points a rupiah payment stands for; refuses a
    fraction — a till never rounds points."""
    if config.point_value <= 0:
        raise PointsInvalid("inactive")
    ratio = Decimal(amount) / config.point_value
    if ratio != ratio.to_integral_value():
        raise PointsInvalid("whole", str(amount))
    return int(ratio)


# ── the ledger ──────────────────────────────────────────────────────────────

_GUARDED_SPEND = text(
    """
    update customers
    set points_balance = points_balance - :n, updated_at = now()
    where id = :cid and points_balance >= :n
    returning points_balance
    """
)
_UNGUARDED_DELTA = text(
    """
    update customers
    set points_balance = points_balance + :n, updated_at = now()
    where id = :cid
    returning points_balance
    """
)


async def record_points(
    session: AsyncSession,
    business_id: uuid.UUID,
    customer: Customer,
    delta: int,
    reason: str,
    *,
    source_type: str | None = None,
    source_id: uuid.UUID | None = None,
    amount: Decimal = Decimal(0),
    staff_id: uuid.UUID | None = None,
    notes: str | None = None,
    created_at: datetime | None = None,
    guarded: bool = True,
) -> PointsMovement:
    """Append one row and move the cache in the same statement pair. A negative
    delta is guarded (balance must cover it) unless the caller is reversing —
    a reversal must always be writable."""
    delta = int(delta)
    if delta == 0:
        raise PointsInvalid("delta")
    if delta < 0 and guarded:
        after = (await session.execute(_GUARDED_SPEND, {"n": -delta, "cid": customer.id})).scalar_one_or_none()
        if after is None:
            raise InsufficientPoints(-delta, int(customer.points_balance or 0))
    else:
        after = (await session.execute(_UNGUARDED_DELTA, {"n": delta, "cid": customer.id})).scalar_one()
    customer.points_balance = int(after)
    row = PointsMovement(
        business_id=business_id, customer_id=customer.id, points_delta=delta, reason=reason,
        source_type=source_type, source_id=source_id, amount=Decimal(amount).quantize(MONEY),
        staff_id=staff_id, notes=notes,
    )
    if created_at is not None:
        row.created_at = created_at
    session.add(row)
    await session.flush()
    return row


async def award_points_for_order(
    session: AsyncSession, business_id: uuid.UUID, customer: Customer, order_id: uuid.UUID, *,
    eligible_amount: Decimal, staff_id: uuid.UUID | None = None, created_at: datetime | None = None,
    config: LoyaltyConfig | None = None,
) -> PointsMovement | None:
    """Earn on what was paid with money. Posts the cost. None when nothing is earned."""
    config = config or await loyalty_config(session, business_id)
    points = points_for_amount(eligible_amount, config)
    if points <= 0:
        return None
    cost = rupiah_for_points(points, config)
    row = await record_points(
        session, business_id, customer, points, "earn", source_type="order", source_id=order_id,
        amount=cost, staff_id=staff_id, created_at=created_at,
    )
    await post_event(
        session, business_id, "PointsEarned", {"points": cost}, source_type="order", source_id=order_id,
        memo=f"{points} poin untuk {customer.name}", posted_at=created_at, created_by=staff_id,
    )
    return row


async def redeem_points_for_payment(
    session: AsyncSession, business_id: uuid.UUID, customer: Customer | None, order_id: uuid.UUID, *,
    rupiah: Decimal, staff_id: uuid.UUID | None = None, created_at: datetime | None = None,
    config: LoyaltyConfig | None = None,
) -> PointsMovement:
    """A `points` payment on a sale. Guarded: raises InsufficientPoints and the
    caller's rollback takes the whole sale with it."""
    config = config or await loyalty_config(session, business_id)
    if not config.is_active:
        raise PointsInvalid("inactive")
    if customer is None:
        raise PointsInvalid("customer")
    points = points_for_rupiah(rupiah, config)
    if points <= 0:
        raise PointsInvalid("points")
    if points < config.min_redeem_points:
        raise PointsInvalid("min", str(config.min_redeem_points))
    return await record_points(
        session, business_id, customer, -points, "redeem", source_type="order", source_id=order_id,
        amount=Decimal(rupiah), staff_id=staff_id, created_at=created_at,
    )


async def reverse_points_for_order(
    session: AsyncSession, business_id: uuid.UUID, order_id: uuid.UUID, *,
    staff_id: uuid.UUID | None = None, created_at: datetime | None = None, memo: str | None = None,
) -> list[PointsMovement]:
    """Void or refund: the opposite row for every earn and redeem of this order.
    Earned points are taken back and their accrued cost released; redeemed points
    return (the sale's own reversal / the refund's `refund:points` posts that)."""
    originals = (
        await session.execute(
            select(PointsMovement).where(
                PointsMovement.source_type == "order", PointsMovement.source_id == order_id,
                PointsMovement.reason.in_(("earn", "redeem")),
            ).order_by(PointsMovement.created_at)
        )
    ).scalars().all()
    if not originals:
        return []
    written: list[PointsMovement] = []
    released = Decimal(0)
    for m in originals:
        customer = await session.get(Customer, m.customer_id)
        written.append(
            await record_points(
                session, business_id, customer, -int(m.points_delta), "reversal", source_type="order",
                source_id=order_id, amount=Decimal(m.amount), staff_id=staff_id, notes=memo,
                created_at=created_at, guarded=False,
            )
        )
        if m.reason == "earn":
            released += Decimal(m.amount)
    if released > 0:
        await post_event(
            session, business_id, "PointsReversed", {"points": released}, source_type="order", source_id=order_id,
            memo=memo or "poin ditarik kembali", posted_at=created_at, created_by=staff_id,
        )
    return written


async def adjust_points(
    session: AsyncSession, business_id: uuid.UUID, customer: Customer, delta: int, *,
    staff_id: uuid.UUID | None = None, notes: str | None = None,
) -> PointsMovement:
    """A manual correction by the owner. Up is a gift (cost accrued like an
    earn); down releases the accrued cost and cannot go below zero."""
    delta = int(delta)
    if delta == 0:
        raise PointsInvalid("delta")
    config = await loyalty_config(session, business_id)
    amount = rupiah_for_points(abs(delta), config)
    row = await record_points(
        session, business_id, customer, delta, "adjust", source_type="adjustment", source_id=None,
        amount=amount, staff_id=staff_id, notes=(notes or "").strip() or None,
    )
    if amount > 0:
        await post_event(
            session, business_id, "PointsEarned" if delta > 0 else "PointsReversed", {"points": amount},
            source_type="points_movement", source_id=row.id, memo=notes or "penyesuaian poin", created_by=staff_id,
        )
    return row


async def list_points(session: AsyncSession, customer_id: uuid.UUID, *, limit: int = 50) -> list[PointsMovement]:
    return (
        await session.execute(
            select(PointsMovement).where(PointsMovement.customer_id == customer_id)
            .order_by(PointsMovement.created_at.desc(), PointsMovement.id).limit(limit)
        )
    ).scalars().all()


async def points_of_order(session: AsyncSession, order_id: uuid.UUID) -> tuple[int, int]:
    """(earned, redeemed) for a receipt — net of reversals."""
    rows = (
        await session.execute(
            select(PointsMovement.reason, PointsMovement.points_delta).where(
                PointsMovement.source_type == "order", PointsMovement.source_id == order_id
            )
        )
    ).all()
    earned = sum(int(d) for r, d in rows if r == "earn")
    redeemed = sum(-int(d) for r, d in rows if r == "redeem")
    return earned, redeemed


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
