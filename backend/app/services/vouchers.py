"""Vouchers (roadmap M8-T4): codes with an expiry, single-use under concurrency.

The thing that matters here is the guard. `redeem` takes the use with one
atomic conditional UPDATE — `uses = uses + 1 where uses < max_uses` — in the
sale's transaction, so two tills typing the same single-use code at the same
moment get exactly one success, and a sale that fails afterwards for any other
reason gives the use back with its rollback. Everything else is validation
with an Indonesian message the cashier can read out.

Bulk creation makes N codes under one batch; every code is its own row and
therefore its own guard. `voucher_redemptions` is append-only: a refund or void
writes a negative row and hands the use back.
"""
from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Voucher, VoucherRedemption

MONEY = Decimal("0.01")
VOUCHER_KINDS = ("percent_off", "amount_off")
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no 0/O, 1/I: read out loud at a till


def q(amount: Decimal) -> Decimal:
    return Decimal(amount).quantize(MONEY, rounding=ROUND_HALF_UP)


class VoucherInvalid(Exception):
    """`code`: code, kind, value, max_discount, min_spend, dates, max_uses, count, duplicate, not_found,
    inactive, not_started, expired, used_up, min_spend_not_met."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}:{detail}")


def normalize_code(code: str | None) -> str:
    return (code or "").strip().upper().replace(" ", "")


def generate_code(prefix: str = "", length: int = 8) -> str:
    body = "".join(secrets.choice(_ALPHABET) for _ in range(length))
    prefix = normalize_code(prefix)
    return f"{prefix}-{body}" if prefix else body


# ── creation ────────────────────────────────────────────────────────────────


async def voucher_by_code(session: AsyncSession, code: str | None) -> Voucher | None:
    code = normalize_code(code)
    if not code:
        return None
    return (await session.execute(select(Voucher).where(Voucher.code == code))).scalar_one_or_none()


def _validate_reward(kind: str, value: Decimal, max_discount: Decimal | None, min_spend: Decimal,
                     starts_at: datetime | None, expires_at: datetime | None, max_uses: int) -> None:
    if kind not in VOUCHER_KINDS:
        raise VoucherInvalid("kind")
    value = Decimal(value or 0)
    if value <= 0 or (kind == "percent_off" and value > 1):
        raise VoucherInvalid("value")
    if max_discount is not None and Decimal(max_discount) <= 0:
        raise VoucherInvalid("max_discount")
    if Decimal(min_spend or 0) < 0:
        raise VoucherInvalid("min_spend")
    if starts_at is not None and expires_at is not None and expires_at <= starts_at:
        raise VoucherInvalid("dates")
    if max_uses <= 0:
        raise VoucherInvalid("max_uses")


async def create_vouchers(
    session: AsyncSession, business_id: uuid.UUID, *, kind: str, value: Decimal, code: str | None = None,
    count: int = 1, prefix: str = "", batch_name: str | None = None, max_discount: Decimal | None = None,
    min_spend: Decimal = Decimal(0), starts_at: datetime | None = None, expires_at: datetime | None = None,
    max_uses: int = 1,
) -> list[Voucher]:
    """One voucher with the given code, or `count` generated codes in a batch.
    Every code is unique per business; a clash with an existing code is a
    `duplicate`, a generated clash is simply re-rolled."""
    _validate_reward(kind, value, max_discount, min_spend, starts_at, expires_at, max_uses)
    if count < 1 or count > 1000:
        raise VoucherInvalid("count")
    batch_id = uuid.uuid4() if count > 1 or code is None else None
    existing = set((await session.execute(select(Voucher.code))).scalars().all())
    codes: list[str] = []
    if code is not None:
        if count != 1:
            raise VoucherInvalid("count")
        code = normalize_code(code)
        if not code or len(code) < 3:
            raise VoucherInvalid("code")
        if code in existing:
            raise VoucherInvalid("duplicate", code)
        codes = [code]
    else:
        while len(codes) < count:
            candidate = generate_code(prefix)
            if candidate not in existing and candidate not in codes:
                codes.append(candidate)
    rows = [
        Voucher(
            business_id=business_id, code=c, kind=kind, value=Decimal(value), max_discount=max_discount,
            min_spend=q(min_spend or 0), starts_at=starts_at, expires_at=expires_at, max_uses=max_uses,
            batch_id=batch_id, batch_name=(batch_name or "").strip() or None,
        )
        for c in codes
    ]
    session.add_all(rows)
    await session.flush()
    return rows


async def update_voucher(session: AsyncSession, voucher: Voucher, **changes) -> Voucher:
    if changes.get("is_active") is not None:
        voucher.is_active = bool(changes["is_active"])
    if "expires_at" in changes:
        if changes["expires_at"] is not None and voucher.starts_at is not None and changes["expires_at"] <= voucher.starts_at:
            raise VoucherInvalid("dates")
        voucher.expires_at = changes["expires_at"]
    if changes.get("max_uses") is not None:
        if changes["max_uses"] <= 0 or changes["max_uses"] < voucher.uses:
            raise VoucherInvalid("max_uses")
        voucher.max_uses = changes["max_uses"]
    voucher.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return voucher


# ── at the till ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class VoucherQuote:
    voucher: Voucher
    amount: Decimal


def voucher_amount(voucher: Voucher, base: Decimal) -> Decimal:
    """What this voucher takes off `base` (the bill net of discounts and promos)."""
    base = q(base)
    if base <= 0:
        return Decimal("0.00")
    if voucher.kind == "percent_off":
        amount = q(base * Decimal(voucher.value))
        if voucher.max_discount is not None:
            amount = min(amount, q(voucher.max_discount))
    else:
        amount = q(voucher.value)
    return min(amount, base)


def check_voucher(voucher: Voucher | None, *, base: Decimal, at: datetime) -> VoucherQuote:
    """Everything but the use count, which only the guarded UPDATE can decide."""
    if voucher is None:
        raise VoucherInvalid("not_found")
    if not voucher.is_active:
        raise VoucherInvalid("inactive")
    if voucher.starts_at is not None and at < voucher.starts_at:
        raise VoucherInvalid("not_started")
    if voucher.expires_at is not None and at >= voucher.expires_at:
        raise VoucherInvalid("expired")
    if voucher.uses >= voucher.max_uses:
        raise VoucherInvalid("used_up")
    if q(base) < Decimal(voucher.min_spend):
        raise VoucherInvalid("min_spend_not_met", str(voucher.min_spend))
    return VoucherQuote(voucher, voucher_amount(voucher, base))


_TAKE_USE = text(
    """
    update vouchers
    set uses = uses + 1, updated_at = now()
    where id = :vid and is_active and uses < max_uses
      and (starts_at is null or starts_at <= :at)
      and (expires_at is null or expires_at > :at)
    returning uses
    """
)
_GIVE_BACK_USE = text(
    """
    update vouchers
    set uses = greatest(uses - 1, 0), updated_at = now()
    where id = :vid
    returning uses
    """
)


async def redeem(
    session: AsyncSession, business_id: uuid.UUID, voucher: Voucher, *, order_id: uuid.UUID, amount: Decimal,
    at: datetime, customer_id: uuid.UUID | None = None,
) -> VoucherRedemption:
    """Take the use atomically — this is the single-use guarantee — then record
    it. Raises `used_up` when another till got there first."""
    after = (await session.execute(_TAKE_USE, {"vid": voucher.id, "at": at})).scalar_one_or_none()
    if after is None:
        raise VoucherInvalid("used_up")
    voucher.uses = int(after)
    row = VoucherRedemption(
        business_id=business_id, voucher_id=voucher.id, order_id=order_id, customer_id=customer_id, amount=q(amount),
    )
    session.add(row)
    await session.flush()
    return row


async def reverse_for_order(session: AsyncSession, business_id: uuid.UUID, order_id: uuid.UUID) -> list[VoucherRedemption]:
    """Void or refund: the use goes back and a negative row says so."""
    originals = (
        await session.execute(
            select(VoucherRedemption).where(
                VoucherRedemption.order_id == order_id, VoucherRedemption.reversal_of.is_(None), VoucherRedemption.amount > 0,
            )
        )
    ).scalars().all()
    written: list[VoucherRedemption] = []
    for r in originals:
        already = (
            await session.execute(select(VoucherRedemption).where(VoucherRedemption.reversal_of == r.id))
        ).scalar_one_or_none()
        if already is not None:
            continue
        after = (await session.execute(_GIVE_BACK_USE, {"vid": r.voucher_id})).scalar_one()
        voucher = await session.get(Voucher, r.voucher_id)
        if voucher is not None:
            voucher.uses = int(after)
        row = VoucherRedemption(
            business_id=business_id, voucher_id=r.voucher_id, order_id=order_id, customer_id=r.customer_id,
            amount=-Decimal(r.amount), reversal_of=r.id,
        )
        session.add(row)
        written.append(row)
    await session.flush()
    return written


async def redemptions_of_order(session: AsyncSession, order_id: uuid.UUID) -> list[VoucherRedemption]:
    return (
        await session.execute(select(VoucherRedemption).where(VoucherRedemption.order_id == order_id).order_by(VoucherRedemption.created_at))
    ).scalars().all()


async def list_vouchers(
    session: AsyncSession, *, q_text: str | None = None, batch_id: uuid.UUID | None = None, include_inactive: bool = True,
    limit: int = 200,
) -> list[Voucher]:
    stmt = select(Voucher).order_by(Voucher.created_at.desc(), Voucher.code).limit(limit)
    if q_text:
        stmt = stmt.where(Voucher.code.contains(normalize_code(q_text)))
    if batch_id is not None:
        stmt = stmt.where(Voucher.batch_id == batch_id)
    if not include_inactive:
        stmt = stmt.where(Voucher.is_active.is_(True))
    return (await session.execute(stmt)).scalars().all()
