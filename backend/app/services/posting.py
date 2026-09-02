"""The posting engine — the single writer (roadmap M6-T4, §4.3).

A domain event arrives with its component amounts already computed by the
service that owns the change (a sale knows its payments, discount, tax and
COGS; a goods receipt knows its inventory value; a stock count knows its
variance at cost). The engine:

  1. looks up the business's posting rules for the event (M6-T3) — it holds no
     account codes and no per-event branches of its own,
  2. turns every non-zero component into a Dr / Cr line pair,
  3. writes ONE journal entry in the caller's transaction (M6-T2).

Because it writes in the same transaction as the originating change, the
change and its journal entry commit together or not at all: if posting fails
for any reason — a missing rule, an inactive account, the database's balance
constraint — the exception propagates and the caller's rollback takes the sale,
receipt or count with it. `reversal` components reverse the entry that the
source row originally posted.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import JournalEntry
from app.services.ledger import LineSpec, post_entry, reverse_entry
from app.services.posting_rules import pick_rule, rules_for

MONEY = Decimal("0.01")


class PostingFailed(Exception):
    """`code`: no_rule (an amount had no rule), no_source (reversal target missing)."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}:{detail}")


async def post_event(
    session: AsyncSession,
    business_id: uuid.UUID,
    event_type: str,
    components: dict[str, Decimal],
    *,
    source_type: str | None = None,
    source_id: uuid.UUID | None = None,
    memo: str | None = None,
    posted_at: datetime | None = None,
    created_by: uuid.UUID | None = None,
) -> JournalEntry | None:
    """Post one event. Components with a zero amount are skipped; an event whose
    every component is zero posts nothing and returns None. A component with
    no matching active rule is an error, never silently dropped."""
    rules = await rules_for(session, event_type)
    lines: list[LineSpec] = []
    for component, raw in components.items():
        amount = Decimal(raw or 0).quantize(MONEY)
        if amount == 0:
            continue
        if amount < 0:
            raise PostingFailed("negative", f"{event_type}/{component}={amount}")
        rule = pick_rule(rules, component)
        if rule is None:
            raise PostingFailed("no_rule", f"{event_type}/{component}")
        if rule.debit_code is None:
            raise PostingFailed("no_rule", f"{event_type}/{component} is a reversal rule")
        lines.append(LineSpec(rule.debit_code, debit=amount, memo=component))
        lines.append(LineSpec(rule.credit_code, credit=amount, memo=component))
    if not lines:
        return None
    return await post_entry(
        session, business_id, lines=lines, memo=memo, source_type=source_type, source_id=source_id,
        event_type=event_type, posted_at=posted_at, created_by=created_by,
    )


async def reverse_event(
    session: AsyncSession,
    business_id: uuid.UUID,
    event_type: str,
    *,
    source_type: str,
    source_id: uuid.UUID,
    original_event_type: str,
    memo: str | None = None,
    posted_at: datetime | None = None,
    created_by: uuid.UUID | None = None,
) -> JournalEntry | None:
    """A `reversal` event: flip every entry the source row posted for
    `original_event_type`. Returns None when the source never posted (e.g. a
    sale with an all-zero total)."""
    rules = await rules_for(session, event_type)
    if "reversal" not in rules:
        raise PostingFailed("no_rule", f"{event_type}/reversal")
    originals = (
        await session.execute(
            select(JournalEntry).where(
                JournalEntry.source_type == source_type, JournalEntry.source_id == source_id,
                JournalEntry.event_type == original_event_type,
            ).order_by(JournalEntry.entry_no)
        )
    ).scalars().all()
    last = None
    for original in originals:
        last = await reverse_entry(session, business_id, original, memo=memo, event_type=event_type,
                                   posted_at=posted_at, created_by=created_by)
    return last
