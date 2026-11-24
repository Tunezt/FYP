"""fin-fair-compare — "vs before" sets a running period against the SAME stretch
of the one before, on a fixed date so the test does not depend on the clock.

Now = Tue 24 Nov 2026 10:00 WIB. October is whole (5th, 20th, 28th); November
so far 450.000. Against all of October (500.000) the café "falls" 10 %;
against 1-24 Oct (200.000) it is up 125 % — the number the owner should read. The bot's tool, the
daily summary and the metric layer all use the one rule.

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import hash_pin
from app.jobs.summary import build_summary
from app.metrics import compare_with_previous
from app.models import Business, Item, Staff
from app.services.catalog import ensure_default_variant
from app.services.expenses import record_expense
from app.services.orders import OrderLineSpec, PaymentSpec, create_order
from app.services.pricing import ensure_pricing_settings
from app.services.stock import open_item_stock

from tests.conftest import seed_books

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")
D = Decimal
WIB = ZoneInfo("Asia/Jakarta")
NOW = datetime(2026, 11, 24, 10, 0, tzinfo=WIB).astimezone(timezone.utc)


def at(month, day, hour=10):
    return datetime(2026, month, day, hour, 0, tzinfo=WIB).astimezone(timezone.utc)


@pytest.fixture
async def engine():
    if not DB_URL:
        pytest.fail("INTEGRATION_DATABASE_URL unset — local Postgres is not configured (roadmap §2)")
    engine = create_async_engine(DB_URL, connect_args={"statement_cache_size": 0})
    yield engine
    await engine.dispose()


@pytest.fixture
async def shop(engine):
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        biz = Business(name="Fair Shop", owner_phone=f"62968{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with factory() as s:
        await s.execute(text("select set_config('app.current_business_id', :b, true)"), {"b": str(bid)})
        await seed_books(s, bid)
        await ensure_pricing_settings(s, bid)
        sari = Staff(business_id=bid, name="Sari", pin_hash=hash_pin("2345"))
        kopi = Item(business_id=bid, name="Kopi", unit="cup", current_stock=D(5000), cost_price=D(8000), sell_price=D(10000))
        s.add_all([sari, kopi])
        await s.flush()
        await open_item_stock(s, kopi, unit_cost=kopi.cost_price)
        await ensure_default_variant(s, kopi)

        async def sell(when, cups):
            await create_order(s, business_id=bid, staff_id=sari.id,
                               lines=[OrderLineSpec(item_id=kopi.id, quantity=D(cups))],
                               payments=[PaymentSpec(method="cash", amount=D(10000) * cups)], sold_at=when)

        # October: 100.000 + 100.000 by the 24th, and 300.000 on the 28th (after the cut).
        await sell(at(10, 5), 10); await sell(at(10, 20), 10); await sell(at(10, 28), 30)
        # November so far: 150.000 + 150.000, then yesterday 40.000 (09:00) + 60.000 (14:00), today 50.000 (09:00).
        await sell(at(11, 3), 15); await sell(at(11, 20), 15)
        await sell(at(11, 23, 9), 4); await sell(at(11, 23, 14), 6); await sell(at(11, 24, 9), 5)
        await record_expense(s, bid, amount=D(20000), description="x", occurred_at=at(10, 10))
        await record_expense(s, bid, amount=D(60000), description="y", occurred_at=at(11, 10))
        await s.commit()
        biz = await s.get(Business, bid)
    yield factory, biz
    async with factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


async def _scoped(factory, biz):
    s = factory()
    sess = await s.__aenter__()
    await sess.execute(text("select set_config('app.current_business_id', :b, true)"), {"b": str(biz.id)})
    return s, sess


async def test_month_so_far_meets_the_same_days_of_last_month_not_all_of_it(shop):
    factory, biz = shop
    cm, s = await _scoped(factory, biz)
    try:
        cmp = await compare_with_previous(s, biz, "revenue", period="this_month", now=NOW)
        assert cmp.current.value == D(450000)       # 150.000 + 150.000 + 40.000 + 60.000 + 50.000
        # 1-24 Oct = 200.000 (the 28th is excluded) -> +125 %, where all of October (500.000) would read -10 %
        assert cmp.previous.value == D(200000)
        assert cmp.pct == 125.0
        assert "periode sama" in cmp.label
        whole = await compare_with_previous(s, biz, "revenue", period="last_month", now=NOW)
        assert whole.current.value == D(500000)     # a finished month is read whole, and meets September
    finally:
        await cm.__aexit__(None, None, None)


async def test_profit_comparison_is_net_of_expenses_over_the_same_stretch(shop):
    factory, biz = shop
    cm, s = await _scoped(factory, biz)
    try:
        rev = await compare_with_previous(s, biz, "revenue", period="this_month", now=NOW)
        exp = await compare_with_previous(s, biz, "expense_total", period="this_month", now=NOW)
        assert exp.current.value == D(60000) and exp.previous.value == D(20000)
        assert rev.current.value - exp.current.value == D(390000)
        assert rev.previous.value - exp.previous.value == D(180000)
    finally:
        await cm.__aexit__(None, None, None)


async def test_today_meets_yesterday_until_the_same_hour(shop):
    factory, biz = shop
    cm, s = await _scoped(factory, biz)
    try:
        cmp = await compare_with_previous(s, biz, "revenue", period="today", now=NOW)
        assert cmp.current.value == D(50000)
        assert cmp.previous.value == D(40000)       # the 14:00 bill (60.000) has not happened yet "yesterday"
        assert cmp.pct == 25.0
    finally:
        await cm.__aexit__(None, None, None)


async def test_daily_summary_uses_the_same_rule(shop):
    factory, biz = shop
    cm, s = await _scoped(factory, biz)
    try:
        msg = await build_summary(s, biz, now=NOW)
    finally:
        await cm.__aexit__(None, None, None)
    assert "Dibanding kemarin jam yang sama: naik 25% (Rp 40.000)" in msg


async def test_the_assistants_compare_tool_cuts_last_month_to_the_same_days(shop):
    import time_machine

    from app.ai.tools import compare_periods

    factory, biz = shop
    cm, s = await _scoped(factory, biz)
    try:
        with time_machine.travel(NOW, tick=False):
            both = await compare_periods(s, biz, {"period_a": "this_month", "period_b": "last_month"})
            swapped = await compare_periods(s, biz, {"period_a": "last_month", "period_b": "this_month"})
            whole = await compare_periods(s, biz, {"period_a": "this_month", "period_b": "last_30_days"})
    finally:
        await cm.__aexit__(None, None, None)
    assert both["period_a"]["revenue"] == 450000.0 and both["period_b"]["revenue"] == 200000.0
    assert both["revenue_delta_pct"] == 125.0 and "same stretch" in both["note"]
    assert swapped["period_a"]["revenue"] == 200000.0 and swapped["period_b"]["revenue"] == 450000.0
    assert "note" not in whole        # not a this/last pair: nothing is cut
