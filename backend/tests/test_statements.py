"""M6-T5 — statements: profit and loss, then balance sheet.

Done-when (roadmap): a test seeds a known week and asserts exact figures;
"balance sheet balances" joins tests/test_invariants.py.

The week is 24–30 August 2026 (Mon–Sun). One sale falls the day before it,
one waste is posted "now" (after it), so window edges are exercised, not
just totals.

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import hash_pin
from app.models import Business, Item, Staff
from app.services.accounts import ensure_standard_chart
from app.services.catalog import ensure_default_variant
from app.services.orders import OrderLineSpec, PaymentSpec, create_order
from app.services.posting import post_event
from app.services.posting_rules import ensure_standard_rules
from app.services.receiving import GrLineSpec, receive_goods
from app.services.statements import balance_sheet, profit_and_loss
from app.services.stock import open_item_stock
from app.services.suppliers import create_supplier
from app.services.units import consume_stock, ensure_standard_uoms

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")


def D(day: int, hour: int = 10) -> datetime:
    return datetime(2026, 8, day, hour, tzinfo=timezone.utc)


WEEK_START, WEEK_END = D(24, 0), D(31, 0)   # [Mon 24 Aug, Mon 31 Aug)


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
async def week(session_factory):
    """A known week in the books of a fresh business.

    23 Aug (before)  sale 1 kopi, cash 22.000            → 4100 22.000 / 5100 8.000
    24 Aug           sale 2 kopi, cash 24.000 + QRIS 20.000 → 4100 44.000 / 5100 16.000
    26 Aug           1 kg beans received @ 100.000 on credit → 1300 +100.000 / 2100 100.000
    27 Aug           sewa 20.000 paid in cash             → 5400 20.000 / 1100 −20.000
    now (after)      1 kopi wasted                        → 5700 8.000 / 1300 −8.000
    """
    async with session_factory() as s:
        biz = Business(name="Statements Week", owner_phone=f"62978{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        uoms = await ensure_standard_uoms(s, bid)
        await ensure_standard_chart(s, bid)
        await ensure_standard_rules(s, bid)
        staff = Staff(business_id=bid, name="Kasir", pin_hash=hash_pin("1111"))
        kopi = Item(business_id=bid, name="Kopi", unit="cup", current_stock=Decimal(10), cost_price=Decimal(8000), sell_price=Decimal(22000), uom_id=uoms["cup"].id)
        beans = Item(business_id=bid, name="Biji", unit="kg", current_stock=Decimal(0), cost_price=Decimal(0), uom_id=uoms["kg"].id)
        s.add_all([staff, kopi, beans])
        await s.flush()
        for it in (kopi, beans):
            await open_item_stock(s, it, unit_cost=it.cost_price)
            await ensure_default_variant(s, it)
        supplier = await create_supplier(s, bid, name="Grosir")
        await s.flush()

        await create_order(s, business_id=bid, staff_id=staff.id, sold_at=D(23),
                           lines=[OrderLineSpec(item_id=kopi.id, quantity=Decimal(1))],
                           payments=[PaymentSpec(method="cash", amount=Decimal(22000))])
        await create_order(s, business_id=bid, staff_id=staff.id, sold_at=D(24),
                           lines=[OrderLineSpec(item_id=kopi.id, quantity=Decimal(2))],
                           payments=[PaymentSpec(method="cash", amount=Decimal(24000)), PaymentSpec(method="qris", amount=Decimal(20000))])
        await receive_goods(s, bid, supplier_id=supplier.id, received_at=D(26),
                            lines=[GrLineSpec(item_id=beans.id, quantity=Decimal(1), unit_cost=Decimal(100000))])
        await post_event(s, bid, "ExpenseIncurred", {"expense:sewa": Decimal(20000)}, source_type="test", posted_at=D(27))
        await consume_stock(s, kopi, Decimal(1), reason="waste", source_type="test")
        await s.commit()
    yield bid
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


def _amounts(lines) -> list[tuple[str, Decimal]]:
    return [(l.code, l.amount) for l in lines]


async def test_week_profit_and_loss_has_exact_figures(session_factory, week):
    async with session_factory() as s:
        await _set_tenant(s, week)
        pnl = await profit_and_loss(s, since=WEEK_START, until=WEEK_END)
    assert _amounts(pnl.revenue) == [("4100", Decimal("44000.00"))]
    assert _amounts(pnl.cogs) == [("5100", Decimal("16000.00"))]
    assert _amounts(pnl.expenses) == [("5400", Decimal("20000.00"))]
    assert pnl.revenue_total == Decimal("44000.00")
    assert pnl.cogs_total == Decimal("16000.00")
    assert pnl.gross_profit == Decimal("28000.00")
    assert pnl.expenses_total == Decimal("20000.00")
    assert pnl.net_profit == Decimal("8000.00")
    assert pnl.revenue[0].name == "Penjualan" and pnl.expenses[0].name == "Sewa tempat"


async def test_profit_and_loss_window_edges(session_factory, week):
    async with session_factory() as s:
        await _set_tenant(s, week)
        wider = await profit_and_loss(s, since=D(23, 0), until=WEEK_END)      # takes in the 23 Aug sale
        receipt_day = await profit_and_loss(s, since=D(26, 0), until=D(27, 0))  # only the goods receipt
        after = await profit_and_loss(s, since=WEEK_END, until=datetime(2100, 1, 1, tzinfo=timezone.utc))
    assert wider.revenue_total == Decimal("66000.00") and wider.cogs_total == Decimal("24000.00")
    assert wider.net_profit == Decimal("22000.00")
    # A goods receipt is a balance-sheet event: nothing on the P&L.
    assert receipt_day.revenue == [] and receipt_day.cogs == [] and receipt_day.expenses == []
    assert receipt_day.net_profit == Decimal("0.00")
    # The waste posted after the week is the only thing after it.
    assert _amounts(after.expenses) == [("5700", Decimal("8000.00"))] and after.net_profit == Decimal("-8000.00")


async def test_balance_sheet_has_exact_figures_and_balances(session_factory, week):
    async with session_factory() as s:
        await _set_tenant(s, week)
        end_of_week = await balance_sheet(s, until=WEEK_END)
        before_week = await balance_sheet(s, until=WEEK_START)
        now = await balance_sheet(s)
    # End of the week: cash 22+24−20, QRIS receivable 20, inventory −8−16+100.
    assert _amounts(end_of_week.assets) == [
        ("1100", Decimal("26000.00")), ("1120", Decimal("20000.00")), ("1300", Decimal("76000.00")),
    ]
    assert end_of_week.assets_total == Decimal("122000.00")
    assert _amounts(end_of_week.liabilities) == [("2100", Decimal("100000.00"))]
    assert end_of_week.equity == [] and end_of_week.equity_total == Decimal("0.00")
    assert end_of_week.current_earnings == Decimal("22000.00")
    assert end_of_week.liabilities_and_equity_total == Decimal("122000.00")
    assert end_of_week.balances is True
    # Before the week only the 23 Aug sale exists: inventory is negative because
    # opening stock was never capitalised, and the sheet still balances.
    assert _amounts(before_week.assets) == [("1100", Decimal("22000.00")), ("1300", Decimal("-8000.00"))]
    assert before_week.assets_total == Decimal("14000.00") and before_week.current_earnings == Decimal("14000.00")
    assert before_week.balances is True
    # Now: the waste took 8.000 off inventory and off earnings.
    assert _amounts(now.assets) == [
        ("1100", Decimal("26000.00")), ("1120", Decimal("20000.00")), ("1300", Decimal("68000.00")),
    ]
    assert now.assets_total == Decimal("114000.00") and now.current_earnings == Decimal("14000.00")
    assert now.balances is True


async def test_current_earnings_equal_profit_and_loss_to_date(session_factory, week):
    async with session_factory() as s:
        await _set_tenant(s, week)
        sheet = await balance_sheet(s, until=WEEK_END)
        pnl = await profit_and_loss(s, since=datetime(2000, 1, 1, tzinfo=timezone.utc), until=WEEK_END)
    assert sheet.current_earnings == pnl.net_profit == Decimal("22000.00")


async def test_statements_are_tenant_scoped(session_factory, week):
    """Another business sees none of the week."""
    async with session_factory() as s:
        other = Business(name="Statements Other", owner_phone=f"62978{uuid.uuid4().hex[:9]}")
        s.add(other)
        await s.commit()
        other_id = other.id
    try:
        async with session_factory() as s:
            await _set_tenant(s, other_id)
            pnl = await profit_and_loss(s, since=datetime(2000, 1, 1, tzinfo=timezone.utc), until=datetime(2100, 1, 1, tzinfo=timezone.utc))
            sheet = await balance_sheet(s)
        assert pnl.revenue == [] and pnl.net_profit == Decimal("0.00")
        assert sheet.assets == [] and sheet.balances is True
    finally:
        async with session_factory() as s:
            row = await s.get(Business, other_id)
            if row:
                await s.delete(row)
            await s.commit()


async def test_api_statements_use_business_local_days(session_factory, week):
    """The endpoints take inclusive business-local dates (Asia/Jakarta by default):
    the 10:00 UTC postings are 17:00 WIB on the same calendar day."""
    from datetime import date
    from types import SimpleNamespace

    from fastapi import HTTPException

    from app.api.dashboard import statement_balance_sheet, statement_profit_loss

    async with session_factory() as s:
        await _set_tenant(s, week)
        ctx = SimpleNamespace(session=s, business_id=week, staff_id=None)
        pnl = await statement_profit_loss(ctx, since=date(2026, 8, 24), until=date(2026, 8, 30))
        sheet = await statement_balance_sheet(ctx, as_of=date(2026, 8, 30))
        with pytest.raises(HTTPException) as exc:
            await statement_profit_loss(ctx, since=date(2026, 8, 30), until=date(2026, 8, 24))
    assert pnl.since == date(2026, 8, 24) and pnl.until == date(2026, 8, 30)
    assert pnl.revenue_total == Decimal("44000.00") and pnl.net_profit == Decimal("8000.00")
    assert [(l.code, l.amount) for l in pnl.expenses] == [("5400", Decimal("20000.00"))]
    assert sheet.as_of == date(2026, 8, 30) and sheet.balances is True
    assert sheet.assets_total == Decimal("122000.00") and sheet.current_earnings == Decimal("22000.00")
    assert exc.value.status_code == 422 and "Tanggal" in exc.value.detail
