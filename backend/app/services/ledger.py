"""The journal (roadmap M6-T2): write balanced entries, read balances.

`post_entry` writes one entry and its lines in the caller's transaction. It
checks the balance in Python only to give a clear error EARLY; the database's
deferred constraint (migration 0015) is the enforcement, and it fires at commit
for any path that bypasses this function. Nothing here updates or deletes: a
correction is a reversing entry (`reverse_entry`).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, JournalEntry, JournalLine
from app.services.accounts import DEBIT_NORMAL

MONEY = Decimal("0.01")


class LedgerInvalid(Exception):
    """`code`: unbalanced, empty, account, side, amount."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}:{detail}")


@dataclass
class LineSpec:
    account_code: str
    debit: Decimal = Decimal(0)
    credit: Decimal = Decimal(0)
    memo: str | None = None


async def _next_entry_no(session: AsyncSession) -> int:
    current = (await session.execute(select(func.max(JournalEntry.entry_no)))).scalar_one()
    return int(current or 0) + 1


async def post_entry(
    session: AsyncSession,
    business_id: uuid.UUID,
    *,
    lines: list[LineSpec],
    memo: str | None = None,
    source_type: str | None = None,
    source_id: uuid.UUID | None = None,
    event_type: str | None = None,
    posted_at: datetime | None = None,
    created_by: uuid.UUID | None = None,
) -> JournalEntry:
    if not lines:
        raise LedgerInvalid("empty")
    resolved: list[tuple[Account, Decimal, Decimal, str | None]] = []
    total_debit = total_credit = Decimal(0)
    for spec in lines:
        debit, credit = Decimal(spec.debit or 0).quantize(MONEY), Decimal(spec.credit or 0).quantize(MONEY)
        if debit < 0 or credit < 0:
            raise LedgerInvalid("amount", spec.account_code)
        if (debit == 0) == (credit == 0):
            raise LedgerInvalid("side", spec.account_code)  # exactly one side, non-zero
        account = (
            await session.execute(select(Account).where(Account.code == spec.account_code, Account.is_active.is_(True)))
        ).scalar_one_or_none()
        if account is None:
            raise LedgerInvalid("account", spec.account_code)
        resolved.append((account, debit, credit, spec.memo))
        total_debit += debit
        total_credit += credit
    if total_debit != total_credit:
        raise LedgerInvalid("unbalanced", f"debit {total_debit} credit {total_credit}")

    entry = JournalEntry(
        business_id=business_id, entry_no=await _next_entry_no(session), memo=memo,
        source_type=source_type, source_id=source_id, event_type=event_type, created_by=created_by,
    )
    if posted_at is not None:
        entry.posted_at = posted_at
    session.add(entry)
    await session.flush()
    for line_no, (account, debit, credit, line_memo) in enumerate(resolved, start=1):
        session.add(JournalLine(business_id=business_id, entry_id=entry.id, line_no=line_no, account_id=account.id,
                                debit=debit, credit=credit, memo=line_memo))
    await session.flush()
    return entry


async def entry_lines(session: AsyncSession, entry_id: uuid.UUID) -> list[JournalLine]:
    return (
        await session.execute(select(JournalLine).where(JournalLine.entry_id == entry_id).order_by(JournalLine.line_no, JournalLine.id))
    ).scalars().all()


async def reverse_entry(
    session: AsyncSession, business_id: uuid.UUID, entry: JournalEntry, *, memo: str | None = None,
    event_type: str | None = None, posted_at: datetime | None = None, created_by: uuid.UUID | None = None,
) -> JournalEntry:
    """A new entry with every line flipped. The original stays."""
    lines = await entry_lines(session, entry.id)
    codes = {a.id: a.code for a in (await session.execute(select(Account).where(Account.id.in_([l.account_id for l in lines])))).scalars()}
    return await post_entry(
        session, business_id,
        lines=[LineSpec(account_code=codes[l.account_id], debit=l.credit, credit=l.debit, memo=l.memo) for l in lines],
        memo=memo or f"pembalikan #{entry.entry_no}", source_type=entry.source_type, source_id=entry.source_id,
        event_type=event_type or f"reverse:{entry.event_type}", posted_at=posted_at, created_by=created_by,
    )


async def account_balances(
    session: AsyncSession, *, up_to: datetime | None = None, since: datetime | None = None,
) -> dict[str, Decimal]:
    """{account code: balance in its normal direction} over the posted period.
    Debit-normal accounts: debit − credit; credit-normal: credit − debit."""
    stmt = (
        select(Account.code, Account.type, func.coalesce(func.sum(JournalLine.debit), 0), func.coalesce(func.sum(JournalLine.credit), 0))
        .join(JournalLine, JournalLine.account_id == Account.id)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .group_by(Account.code, Account.type)
    )
    if since is not None:
        stmt = stmt.where(JournalEntry.posted_at >= since)
    if up_to is not None:
        stmt = stmt.where(JournalEntry.posted_at < up_to)
    out: dict[str, Decimal] = {}
    for code, type_, debit, credit in (await session.execute(stmt)).all():
        d, c = Decimal(debit), Decimal(credit)
        out[code] = (d - c) if type_ in DEBIT_NORMAL else (c - d)
    return out


async def trial_balance(session: AsyncSession) -> tuple[Decimal, Decimal]:
    """(Σ debit, Σ credit) over every line — must always be equal."""
    d, c = (await session.execute(select(func.coalesce(func.sum(JournalLine.debit), 0), func.coalesce(func.sum(JournalLine.credit), 0)))).one()
    return Decimal(d), Decimal(c)
