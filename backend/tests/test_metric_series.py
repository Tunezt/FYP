"""A chart is one query per metric, and still the registry's own number.

`series` over a 90-day chart used to be 90 round trips per metric. Against a
remote database that turned the dashboard's 30/90-day and year views into
requests that never came back. The bucketed form collapses them into one
grouped query — and is only allowed to exist because it equals `compute` on
every window, which is what this file checks, on sales that sit right on the
business-day boundaries where an off-by-one would hide.

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import hash_pin
from app.metrics import compute, local_day_windows, local_month_windows, series
from app.metrics.registry import SERIES_BUCKETS
from app.models import Business, Item, Staff
from app.services.catalog import ensure_default_variant
from app.services.expenses import record_expense
from app.services.orders import OrderLineSpec, PaymentSpec, create_order, void_order
from app.services.pricing import ensure_pricing_settings
from app.services.stock import open_item_stock

from tests.conftest import seed_books

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")
D = Decimal
WIB = ZoneInfo("Asia/Jakarta")
# 20:00 WIB on 16 Sep: the "today" business day (start hour 04:00) began at 04:00.
NOW = datetime(2026, 9, 16, 20, 0, tzinfo=WIB).astimezone(timezone.utc)
BUCKETED = ("revenue", "transaction_count", "expense_total")


def wib(day, hour, minute=0, micro=0):
    return datetime(2026, 9, day, hour, minute, 0, micro, tzinfo=WIB).astimezone(timezone.utc)


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
    await session.execute(text("select set_config('app.current_business_id', :bid, true)"), {"bid": str(business_id)})


@pytest.fixture
async def shop(session_factory):
    """A 04:00 business day, with sales placed exactly on and one microsecond
    either side of the boundaries, a voided order, and expenses."""
    async with session_factory() as s:
        biz = Business(name="Series Shop", owner_phone=f"62967{uuid.uuid4().hex[:9]}", day_start_hour=4)
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await seed_books(s, bid)
        await ensure_pricing_settings(s, bid)
        owner = Staff(business_id=bid, name="Bu Ratna", role="owner", pin_hash=hash_pin("1234"))
        kopi = Item(business_id=bid, name="Kopi", unit="cup", current_stock=D(500), cost_price=D(8000), sell_price=D(20000))
        s.add_all([owner, kopi])
        await s.flush()
        await open_item_stock(s, kopi, unit_cost=kopi.cost_price)
        await ensure_default_variant(s, kopi)

        async def sell(qty, when):
            return await create_order(s, business_id=bid, staff_id=owner.id,
                                      lines=[OrderLineSpec(item_id=kopi.id, quantity=D(qty))],
                                      payments=[PaymentSpec(method="cash", amount=D(20000 * qty))], sold_at=when)

        await sell(1, wib(10, 12))
        await sell(2, wib(14, 4))                       # exactly the start of the 14th's business day
        await sell(3, wib(14, 3, 59, 999999))           # one microsecond earlier: the 13th's business day
        await sell(1, wib(15, 0, 15))                   # after midnight: still the 14th
        voided = await sell(4, wib(15, 9))
        await void_order(s, business_id=bid, order_id=voided.order.id, staff_id=owner.id, manager_pin="1234")
        await sell(2, wib(16, 19, 30))                  # today
        await sell(5, wib(1, 12) - timedelta(days=200)) # far outside any window
        await record_expense(s, bid, amount=D(12000), description="es batu", category="operasional", source="manual", occurred_at=wib(14, 4))
        await record_expense(s, bid, amount=D(7000), description="gas", category="operasional", source="manual", occurred_at=wib(14, 3, 59, 999999))
        await s.commit()
    yield bid
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


async def _per_window(s, biz, name, windows):
    return [(k, await compute(s, biz, name, since=a, until=b)) for k, a, b in windows]


@pytest.mark.parametrize("days", [1, 7, 30, 90])
async def test_bucketed_series_equals_compute_on_every_window(session_factory, shop, days):
    async with session_factory() as s:
        await _set_tenant(s, shop)
        biz = await s.get(Business, shop)
        windows = local_day_windows(biz, days, now=NOW)
        for name in BUCKETED:
            assert name in SERIES_BUCKETS
            fast = await series(s, biz, name, windows)
            slow = await _per_window(s, biz, name, windows)
            assert [(k, r.value) for k, r in fast] == [(k, r.value) for k, r in slow], name
            assert [(r.since, r.until, r.name, r.unit) for _k, r in fast] == [(r.since, r.until, r.name, r.unit) for _k, r in slow]


async def test_the_boundary_sales_land_on_the_right_business_day(session_factory, shop):
    async with session_factory() as s:
        await _set_tenant(s, shop)
        biz = await s.get(Business, shop)
        by_day = {k: r.value for k, r in await series(s, biz, "revenue", local_day_windows(biz, 7, now=NOW))}
        assert by_day["2026-09-13"] == D("60000.00")    # 03:59:59.999999 on the 14th
        assert by_day["2026-09-14"] == D("60000.00")    # 04:00 on the 14th + 00:15 on the 15th
        assert by_day["2026-09-15"] == D("0.00")        # the void nets to zero
        assert by_day["2026-09-16"] == D("40000.00")
        counts = {k: r.value for k, r in await series(s, biz, "transaction_count", local_day_windows(biz, 7, now=NOW))}
        assert counts["2026-09-14"] == 2 and counts["2026-09-15"] == 0


async def test_a_daily_chart_is_one_query_per_metric(session_factory, shop, engine):
    statements: list[str] = []

    def count(conn, cursor, statement, *args):
        if statement.lstrip().lower().startswith("select") and "set_config" not in statement:
            statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", count)
    try:
        async with session_factory() as s:
            await _set_tenant(s, shop)
            biz = await s.get(Business, shop)
            statements.clear()
            await series(s, biz, "revenue", local_day_windows(biz, 90, now=NOW))
            assert len(statements) == 1
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count)


async def test_month_windows_are_not_back_to_back_equal_and_fall_back_to_compute(session_factory, shop):
    async with session_factory() as s:
        await _set_tenant(s, shop)
        biz = await s.get(Business, shop)
        windows = local_month_windows(biz, 3, now=NOW)
        fast = await series(s, biz, "revenue", windows)
        slow = await _per_window(s, biz, "revenue", windows)
        assert [(k, r.value) for k, r in fast] == [(k, r.value) for k, r in slow]


async def test_a_dimensioned_series_never_takes_the_bucketed_path(session_factory, shop):
    async with session_factory() as s:
        await _set_tenant(s, shop)
        biz = await s.get(Business, shop)
        item_id = (await s.execute(text("select id from items where business_id = :b"), {"b": str(shop)})).scalar_one()
        windows = local_day_windows(biz, 7, now=NOW)
        fast = await series(s, biz, "revenue", windows, item_id=item_id)
        slow = [(k, await compute(s, biz, "revenue", since=a, until=b, item_id=item_id)) for k, a, b in windows]
        assert [(k, r.value) for k, r in fast] == [(k, r.value) for k, r in slow]
