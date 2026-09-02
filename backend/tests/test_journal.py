"""M6-T2 — the journal balances by a deferred DATABASE constraint.

Done-when (roadmap): an unbalanced entry raises at the database, proven by a test.

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Business, JournalEntry, JournalLine
from app.services.accounts import ensure_standard_chart
from app.services.ledger import LedgerInvalid, LineSpec, account_balances, entry_lines, post_entry, reverse_entry, trial_balance

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")


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
async def books(session_factory):
    async with session_factory() as s:
        biz = Business(name="Journal Test", owner_phone=f"62981{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        chart = await ensure_standard_chart(s, bid)
        await s.commit()
        accounts = {code: a.id for code, a in chart.items()}
    yield bid, accounts
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


async def test_unbalanced_entry_is_refused_by_the_database_at_commit(session_factory, books):
    """Raw rows, no service code: the constraint alone stops it — and only at
    commit, so lines may be written one at a time inside the transaction."""
    bid, acc = books
    async with session_factory() as s:
        await _set_tenant(s, bid)
        entry = JournalEntry(business_id=bid, entry_no=1, memo="salah")
        s.add(entry)
        await s.flush()
        s.add(JournalLine(business_id=bid, entry_id=entry.id, account_id=acc["1100"], debit=Decimal(50000)))
        await s.flush()                      # unbalanced mid-transaction: allowed (deferred)
        s.add(JournalLine(business_id=bid, entry_id=entry.id, account_id=acc["4100"], credit=Decimal(40000)))
        await s.flush()                      # still unbalanced by 10.000, still allowed …
        with pytest.raises(DBAPIError) as exc:
            await s.commit()                 # … until commit, where the database refuses
        assert "unbalanced" in str(exc.value)
        await s.rollback()
    async with session_factory() as s:
        await _set_tenant(s, bid)
        assert (await s.execute(select(JournalEntry))).scalars().all() == []   # nothing survived


async def test_entry_without_lines_is_refused(session_factory, books):
    bid, acc = books
    async with session_factory() as s:
        await _set_tenant(s, bid)
        s.add(JournalEntry(business_id=bid, entry_no=1, memo="kosong"))
        with pytest.raises(DBAPIError) as exc:
            await s.commit()
        assert "no lines" in str(exc.value)
        await s.rollback()


async def test_a_line_cannot_be_both_sides_or_neither(session_factory, books):
    bid, acc = books
    for debit, credit in ((Decimal(10), Decimal(10)), (Decimal(0), Decimal(0)), (Decimal(-5), Decimal(0))):
        async with session_factory() as s:
            await _set_tenant(s, bid)
            entry = JournalEntry(business_id=bid, entry_no=1)
            s.add(entry)
            await s.flush()
            s.add(JournalLine(business_id=bid, entry_id=entry.id, account_id=acc["1100"], debit=debit, credit=credit))
            with pytest.raises(DBAPIError):
                await s.flush()              # row CHECK constraints are immediate
            await s.rollback()


async def test_balanced_entries_and_reversal(session_factory, books):
    bid, acc = books
    async with session_factory() as s:
        await _set_tenant(s, bid)
        sale = await post_entry(
            s, bid, memo="penjualan tunai", source_type="order", event_type="OrderCompleted",
            lines=[LineSpec("1100", debit=Decimal(22000)), LineSpec("4100", credit=Decimal(22000)),
                   LineSpec("5100", debit=Decimal(8000)), LineSpec("1300", credit=Decimal(8000))],
        )
        assert sale.entry_no == 1
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, bid)
        assert [(l.debit, l.credit) for l in await entry_lines(s, sale.id)] == [
            (Decimal("22000.00"), Decimal("0.00")), (Decimal("0.00"), Decimal("22000.00")),
            (Decimal("8000.00"), Decimal("0.00")), (Decimal("0.00"), Decimal("8000.00"))]
        balances = await account_balances(s)
        assert balances == {"1100": Decimal("22000.00"), "4100": Decimal("22000.00"),
                            "5100": Decimal("8000.00"), "1300": Decimal("-8000.00")}
        assert (await trial_balance(s)) == (Decimal("30000.00"), Decimal("30000.00"))
        rev = await reverse_entry(s, bid, sale, memo="void")
        assert rev.entry_no == 2 and rev.event_type == "reverse:OrderCompleted"
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, bid)
        assert all(v == 0 for v in (await account_balances(s)).values())
        assert (await trial_balance(s)) == (Decimal("60000.00"), Decimal("60000.00"))   # originals untouched


async def test_service_refuses_bad_specs_early_with_clear_codes(session_factory, books):
    bid, acc = books
    async with session_factory() as s:
        await _set_tenant(s, bid)
        for lines, code in (
            ([], "empty"),
            ([LineSpec("1100", debit=Decimal(10)), LineSpec("4100", credit=Decimal(9))], "unbalanced"),
            ([LineSpec("1100", debit=Decimal(10), credit=Decimal(10))], "side"),
            ([LineSpec("9999", debit=Decimal(10)), LineSpec("4100", credit=Decimal(10))], "account"),
            ([LineSpec("1100", debit=Decimal(-1)), LineSpec("4100", credit=Decimal(-1))], "amount"),
        ):
            with pytest.raises(LedgerInvalid) as exc:
                await post_entry(s, bid, lines=lines)
            assert exc.value.code == code
        await s.rollback()
