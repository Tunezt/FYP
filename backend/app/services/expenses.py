"""Expenses (roadmap M6-T6): one writer, and it keeps the books.

Every expense row is written here, and the same transaction posts an
`ExpenseIncurred` event through the posting engine (M6-T4) with the
component `expense:<category>` — the rules (M6-T3) decide the account
(`expense:gaji` → 5300, anything unknown → the `expense:*` fallback, 5900).
So an expense the owner states over WhatsApp lands on the P&L (M6-T5)
without a second code path knowing any account code.

`ledger_amount` is what actually hits the P&L. It defaults to the expense
amount. A receipt whose matched lines were already capitalised as a goods
receipt (GoodsReceived → Persediaan) passes only the *remainder* — the part
the ledger has not seen — so the same rupiah is never both inventory and
expense. The expense row still carries the full receipt total: that is what
the owner paid, and what the spend history shows.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Expense
from app.services.posting import post_event

MONEY = Decimal("0.01")
DEFAULT_CATEGORY = "lainnya"


class ExpenseInvalid(Exception):
    """`code`: amount."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def normalize_category(category: str | None) -> str:
    text = (category or "").strip().lower()
    return text or DEFAULT_CATEGORY


async def record_expense(
    session: AsyncSession,
    business_id: uuid.UUID,
    *,
    amount: Decimal,
    description: str | None,
    category: str | None = None,
    source: str = "manual",
    receipt_id: uuid.UUID | None = None,
    occurred_at: datetime | None = None,
    created_by: uuid.UUID | None = None,
    ledger_amount: Decimal | None = None,
) -> Expense:
    amount = Decimal(amount).quantize(MONEY)
    if amount <= 0:
        raise ExpenseInvalid("amount")
    posted = amount if ledger_amount is None else Decimal(ledger_amount).quantize(MONEY)
    if posted < 0 or posted > amount:
        raise ExpenseInvalid("amount")
    occurred_at = occurred_at or datetime.now(timezone.utc)
    category = normalize_category(category)

    expense = Expense(
        business_id=business_id, amount=amount, category=category, description=description,
        source=source, receipt_id=receipt_id, occurred_at=occurred_at,
    )
    session.add(expense)
    await session.flush()
    # Same transaction: if the books refuse, the expense row goes with them.
    await post_event(
        session, business_id, "ExpenseIncurred", {f"expense:{category}": posted},
        source_type="expense", source_id=expense.id, memo=description or f"pengeluaran {category}",
        posted_at=occurred_at, created_by=created_by,
    )
    return expense
