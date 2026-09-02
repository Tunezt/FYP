"""M6-T6 — expenses into the ledger.

Done-when (roadmap): an expense recorded over WhatsApp appears in the P&L.
The WhatsApp path is driven for real from the router's tool dispatch with
only the model call faked (a forced `record_expense` function call); the
expense row, the journal entry and the statement are all real.

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai import tools
from app.ai.router import handle_text
from app.models import Business, Expense, JournalEntry, JournalLine
from app.services.expenses import ExpenseInvalid, record_expense
from app.services.ledger import account_balances
from app.services.posting import PostingFailed
from app.services.statements import balance_sheet, profit_and_loss

from tests.conftest import seed_books

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")
LONG_AGO, FAR_AHEAD = datetime(2000, 1, 1, tzinfo=timezone.utc), datetime(2100, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
async def engine():
    if not DB_URL:
        pytest.fail("INTEGRATION_DATABASE_URL unset — local Postgres is not configured (roadmap §2)")
    engine = create_async_engine(DB_URL, connect_args={"statement_cache_size": 0})
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _set_tenant(session, business_id):
    await session.execute(
        text("select set_config('app.current_business_id', :bid, true)"), {"bid": str(business_id)}
    )


@pytest.fixture
async def shop(session_factory):
    async with session_factory() as s:
        biz = Business(name="Expense Test", owner_phone=f"62977{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await seed_books(s, bid)
        await s.commit()
    yield bid
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


def _forced_call(name: str, args: dict):
    return AsyncMock(return_value=SimpleNamespace(name=name, args=args))


async def test_expense_recorded_over_whatsapp_appears_on_the_pnl(session_factory, shop):
    """'tadi beli gas 88 ribu' → the router dispatches record_expense → the
    expense is on this month's P&L under Listrik, air & gas, and cash is down."""
    async with session_factory() as s:
        await _set_tenant(s, shop)
        biz = await s.get(Business, shop)
        with (
            patch("app.ai.router.force_tool_call", new=_forced_call(
                "record_expense", {"amount": 88000, "category": "operasional", "description": "gas 3kg x4"},
            )),
            patch("app.ai.router.compose_reply", new=AsyncMock(return_value="Oke, dicatat.")),
        ):
            routed = await handle_text(s, biz, "tadi beli gas 88 ribu")
        await s.commit()
    assert routed.intent == "record_expense" and routed.reply == "Oke, dicatat."

    async with session_factory() as s:
        await _set_tenant(s, shop)
        expense = (await s.execute(select(Expense))).scalar_one()
        assert expense.amount == Decimal("88000.00") and expense.category == "operasional" and expense.source == "manual"
        entry = (await s.execute(select(JournalEntry).where(JournalEntry.source_type == "expense", JournalEntry.source_id == expense.id))).scalar_one()
        assert entry.event_type == "ExpenseIncurred" and entry.posted_at == expense.occurred_at
        lines = (await s.execute(select(JournalLine).where(JournalLine.entry_id == entry.id).order_by(JournalLine.line_no))).scalars().all()
        assert [(l.debit, l.credit) for l in lines] == [(Decimal("88000.00"), Decimal("0.00")), (Decimal("0.00"), Decimal("88000.00"))]

        pnl = await profit_and_loss(s, since=LONG_AGO, until=FAR_AHEAD)
        assert [(l.code, l.name, l.amount) for l in pnl.expenses] == [("5500", "Listrik, air & gas", Decimal("88000.00"))]
        assert pnl.revenue == [] and pnl.cogs == [] and pnl.net_profit == Decimal("-88000.00")
        sheet = await balance_sheet(s)
        assert [(l.code, l.amount) for l in sheet.assets] == [("1100", Decimal("-88000.00"))]
        assert sheet.current_earnings == Decimal("-88000.00") and sheet.balances


async def test_categories_follow_the_rules_and_unknown_ones_fall_to_other(session_factory, shop):
    async with session_factory() as s:
        await _set_tenant(s, shop)
        biz = await s.get(Business, shop)
        for args in (
            {"amount": 1500000, "category": "gaji", "description": "gaji Sari"},
            {"amount": 2000000, "category": "Sewa ", "description": "sewa kios"},   # case / whitespace
            {"amount": 10000, "category": "parkir", "description": "parkir"},        # no rule → expense:*
            {"amount": 5000, "description": "tanpa kategori"},                       # no category → lainnya
        ):
            facts = await tools.record_expense(s, biz, args)
            assert facts["ok"] is True
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, shop)
        categories = sorted((await s.execute(select(Expense.category))).scalars().all())
        assert categories == ["gaji", "lainnya", "parkir", "sewa"]
        balances = await account_balances(s)
        assert balances["5300"] == Decimal("1500000.00") and balances["5400"] == Decimal("2000000.00")
        assert balances["5900"] == Decimal("15000.00")
        assert balances["1100"] == Decimal("-3515000.00")
        pnl = await profit_and_loss(s, since=LONG_AGO, until=FAR_AHEAD)
        assert pnl.expenses_total == Decimal("3515000.00") and pnl.net_profit == Decimal("-3515000.00")


async def test_invalid_amount_writes_nothing(session_factory, shop):
    async with session_factory() as s:
        await _set_tenant(s, shop)
        biz = await s.get(Business, shop)
        assert await tools.record_expense(s, biz, {"amount": 0, "description": "x"}) == {"ok": False, "error": "invalid_amount"}
        assert await tools.record_expense(s, biz, {"amount": -5, "description": "x"}) == {"ok": False, "error": "invalid_amount"}
        with pytest.raises(ExpenseInvalid):
            await record_expense(s, shop, amount=Decimal(100), description="x", ledger_amount=Decimal(101))
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, shop)
        assert (await s.execute(select(func.count(Expense.id)))).scalar_one() == 0
        assert (await s.execute(select(func.count(JournalEntry.id)))).scalar_one() == 0


async def test_books_refusing_rolls_the_expense_back(session_factory, shop):
    """One transaction: if the posting engine raises, the expense row is gone too."""
    async with session_factory() as s:
        await _set_tenant(s, shop)
        biz = await s.get(Business, shop)
        with patch("app.services.expenses.post_event", new=AsyncMock(side_effect=PostingFailed("no_rule", "expense:x"))):
            with pytest.raises(PostingFailed):
                await tools.record_expense(s, biz, {"amount": 50000, "category": "operasional", "description": "x"})
        await s.rollback()
    async with session_factory() as s:
        await _set_tenant(s, shop)
        assert (await s.execute(select(func.count(Expense.id)))).scalar_one() == 0
        assert (await s.execute(select(func.count(JournalEntry.id)))).scalar_one() == 0


async def test_ledger_amount_is_only_the_uncapitalised_remainder(session_factory, shop):
    """A receipt whose goods were already received into inventory expenses only
    the remainder; the row still shows what was paid."""
    async with session_factory() as s:
        await _set_tenant(s, shop)
        expense = await record_expense(
            s, shop, amount=Decimal(181000), category="bahan baku", description="Nota Toko Manis",
            source="receipt", ledger_amount=Decimal(17000),
        )
        nothing_left = await record_expense(
            s, shop, amount=Decimal(50000), category="bahan baku", description="Nota lain",
            source="receipt", ledger_amount=Decimal(0),
        )
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, shop)
        assert (await s.get(Expense, expense.id)).amount == Decimal("181000.00")
        assert (await s.get(Expense, nothing_left.id)).amount == Decimal("50000.00")
        balances = await account_balances(s)
        assert balances.get("5200") == Decimal("17000.00") and balances.get("1100") == Decimal("-17000.00")
        # Zero remainder: no journal entry at all (the engine posts nothing for zero).
        assert (await s.execute(select(func.count(JournalEntry.id)))).scalar_one() == 1
