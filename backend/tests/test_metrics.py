"""M9-T1 — the metric registry: declarative, one implementation each, and the
seeded set the roadmap names. Every figure below is computed by hand from one
known day so the implementations are pinned, not recorded.

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import hash_pin
from app.metrics import REGISTRY, MetricNotFound, compute, get_metric, list_metrics
from app.metrics.catalogue import STANDARD_METRIC_NAMES
from app.metrics.registry import GRAINS, UNITS, MetricArgumentInvalid, MetricSpec
from app.models import Business, Customer, Item, Staff
from app.services.catalog import ensure_default_variant
from app.services.customers import create_customer
from app.services.expenses import record_expense
from app.services.orders import OrderLineSpec, PaymentSpec, create_order, void_order
from app.services.pricing import ensure_pricing_settings
from app.services.shifts import close_shift, open_shift
from app.services.stock import open_item_stock
from app.services.units import consume_stock

from tests.conftest import seed_books

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")
D = Decimal
WIB = ZoneInfo("Asia/Jakarta")
DAY = datetime(2026, 9, 3, tzinfo=WIB)                 # a Thursday, business-local
NOW = datetime(2026, 9, 3, 22, 0, tzinfo=WIB).astimezone(timezone.utc)


def at(h, m=0):
    return DAY.replace(hour=h, minute=m).astimezone(timezone.utc)


# ── the registry itself (no database) ───────────────────────────────────────


def test_the_seeded_set_is_declared_once_each_with_both_descriptions():
    names = {m.name for m in list_metrics()}
    assert set(STANDARD_METRIC_NAMES) <= names and len(STANDARD_METRIC_NAMES) == 20
    for m in list_metrics():
        assert m.implementation is not None and callable(m.implementation)
        assert m.description_id.strip() and m.description_en.strip() and m.description_id != m.description_en
        assert m.unit in UNITS and all(g in GRAINS for g in m.grains)
    assert get_metric("revenue").dimensions == ("item",)
    assert get_metric("stock_on_hand").is_instant and get_metric("stock_days_remaining").is_instant
    assert not get_metric("revenue").is_instant
    with pytest.raises(MetricNotFound):
        get_metric("profit_margin_by_moon_phase")
    with pytest.raises(RuntimeError):            # declaring a name twice is an error, not an override
        from app.metrics.registry import metric

        @metric("revenue", description_id="x", description_en="y", unit="rupiah")
        async def _dup(session, ctx):  # pragma: no cover
            raise AssertionError
    with pytest.raises(AssertionError):          # a spec must be well-formed
        MetricSpec(name="bad", description_id="a", description_en="b", unit="lightyears")


# ── one known day (needs the local Postgres) ────────────────────────────────


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
async def day(session_factory):
    """Thursday 3 Sept 2026 in Jakarta:
      08:00  Andi (new that morning): 2 Kopi @20.000 (cost 8.000) cash            40.000
      10:00  walk-in: 1 Roti @15.000 (cost 6.000) + 1 Kopi, bill discount 5.000   30.000
      12:30  Rina (a customer since August, bought before): 3 Roti                45.000
      15:00  a 1-Kopi sale that is voided at 15:10                                 (nets to 0)
      09:00  waste: 2 Kopi written off                                             16.000 at cost
      an expense of 12.000; a shift closed 4.000 short; Andi created at 07:30; Rina created 1 Aug with a sale on 20 Aug.
    """
    async with session_factory() as s:
        biz = Business(name="Metric Day", owner_phone=f"62968{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await seed_books(s, bid)
        await ensure_pricing_settings(s, bid)
        owner = Staff(business_id=bid, name="Bu Ratna", role="owner", pin_hash=hash_pin("1234"))
        sari = Staff(business_id=bid, name="Sari", pin_hash=hash_pin("2345"))
        kopi = Item(business_id=bid, name="Kopi", unit="cup", current_stock=D(30), cost_price=D(8000), sell_price=D(20000))
        roti = Item(business_id=bid, name="Roti", unit="pcs", current_stock=D(30), cost_price=D(6000), sell_price=D(15000))
        s.add_all([owner, sari, kopi, roti])
        await s.flush()
        for it in (kopi, roti):
            await open_item_stock(s, it, unit_cost=it.cost_price)
            await ensure_default_variant(s, it)
        rina = await create_customer(s, bid, name="Rina", phone="081300001111")
        rina.created_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
        andi = await create_customer(s, bid, name="Andi", phone="081200001111")
        andi.created_at = DAY.replace(hour=7, minute=30).astimezone(timezone.utc)
        await s.flush()

        async def sell(lines, paid, when, **kw):
            return await create_order(s, business_id=bid, staff_id=sari.id, lines=lines,
                                      payments=[PaymentSpec(method="cash", amount=D(paid))], sold_at=when, **kw)

        await sell([OrderLineSpec(item_id=roti.id, quantity=D(1))], 15000, datetime(2026, 8, 20, 5, tzinfo=timezone.utc), customer_id=rina.id)
        # Sari's till opens at 07:00; every sale of the day is attributed to it (M7-T1).
        shift = await open_shift(s, bid, staff_id=sari.id, opening_float=D(100000), opened_at=at(7))
        await sell([OrderLineSpec(item_id=kopi.id, quantity=D(2))], 40000, at(8), customer_id=andi.id)
        row = await ensure_pricing_settings(s, bid)
        row.discount_requires_pin = False
        await s.flush()
        await sell([OrderLineSpec(item_id=roti.id, quantity=D(1)), OrderLineSpec(item_id=kopi.id, quantity=D(1))], 30000, at(10), bill_discount=D(5000))
        await sell([OrderLineSpec(item_id=roti.id, quantity=D(3))], 45000, at(12, 30), customer_id=rina.id)
        voided = await sell([OrderLineSpec(item_id=kopi.id, quantity=D(1))], 20000, at(15))
        await void_order(s, business_id=bid, order_id=voided.order.id, staff_id=sari.id, manager_pin="1234")
        await consume_stock(s, kopi, D(2), reason="waste", staff_id=sari.id)
        # The write-off happened at 09:00 on the known day (the service stamps "now").
        await s.execute(text("update stock_movements set created_at = :t where reason = 'waste'"), {"t": at(9)})
        await record_expense(s, bid, amount=D(12000), description="es batu", category="operasional", source="manual", occurred_at=at(11))
        # Expected = 100.000 float + 115.000 cash (the void nets out); counted 4.000 short.
        await close_shift(s, shift, counted_cash=D(100000) + D(40000 + 30000 + 45000) - D(4000), closed_by=owner.id, closed_at=at(21))
        await s.commit()
        ids = {"bid": bid, "kopi": kopi.id, "roti": roti.id, "andi": andi.id, "rina": rina.id}
    yield ids
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


async def _value(s, c, name, **kw):
    biz = await s.get(Business, c["bid"])
    kw.setdefault("since", at(0))
    kw.setdefault("until", at(0) + timedelta(days=1))
    return await compute(s, biz, name, **kw)


async def test_every_sales_metric_on_the_known_day(session_factory, day):
    c = day
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        # Revenue: 40.000 + (15.000 + 20.000) + 45.000 + (20.000 − 20.000 void) = 120.000 (the bill discount is not in line totals)
        assert (await _value(s, c, "revenue")).value == D("120000.00")
        assert (await _value(s, c, "revenue", item_id=c["kopi"])).value == D("60000.00")   # 2 + 1 + (1 − 1) Kopi
        # COGS: Kopi 3 × 8.000 + Roti 4 × 6.000 = 48.000 (the void nets out)
        assert (await _value(s, c, "cogs")).value == D("48000.00")
        assert (await _value(s, c, "gross_profit")).value == D("72000.00")
        assert (await _value(s, c, "gross_margin_pct")).value == D("60.0")
        # Orders: three, the void excluded (and the voided order's lines are not a fourth)
        assert (await _value(s, c, "transaction_count")).value == 3
        assert (await _value(s, c, "average_ticket")).value == D("40000.00")
        units = await _value(s, c, "item_units_sold")
        assert units.value == D("7.000") and [(r["name"], r["quantity"]) for r in units.rows] == [("Roti", 4.0), ("Kopi", 3.0)]
        assert (await _value(s, c, "item_units_sold", item_id=c["roti"])).value == D("4.000")
        top = await _value(s, c, "top_items_by_revenue")
        assert [(r["name"], r["revenue"]) for r in top.rows] == [("Kopi", 60000.0), ("Roti", 60000.0)] or \
               [(r["name"], r["revenue"]) for r in top.rows] == [("Roti", 60000.0), ("Kopi", 60000.0)]
        margin = await _value(s, c, "top_items_by_margin")
        assert [(r["name"], r["gross_profit"]) for r in margin.rows] == [("Kopi", 36000.0), ("Roti", 36000.0)] or \
               [(r["name"], r["gross_profit"]) for r in margin.rows] == [("Roti", 36000.0), ("Kopi", 36000.0)]
        peak = await _value(s, c, "peak_hour")
        assert peak.value == 12 and {r["hour"]: r["revenue"] for r in peak.rows}[15] == 0.0   # the void nets 15:00 to zero
        assert (await _value(s, c, "discount_cost")).value == D("5000.00")
        assert (await _value(s, c, "promo_cost")).value == D("0.00")


async def test_costs_customers_and_the_till_on_the_known_day(session_factory, day):
    c = day
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert (await _value(s, c, "waste_value")).value == D("16000.00")
        assert (await _value(s, c, "waste_value", item_id=c["roti"])).value == D("0.00")
        assert (await _value(s, c, "expense_total")).value == D("12000.00")
        assert (await _value(s, c, "net_profit")).value == D("60000.00")            # 120.000 − 48.000 − 12.000
        variance = await _value(s, c, "cash_variance")
        assert variance.value == D("-4000.00") and variance.rows == [{"shifts_closed": 1}]
        assert (await _value(s, c, "new_customers")).value == 1                       # Andi; Rina is from August
        rate = await _value(s, c, "repeat_rate")
        assert rate.value == D("50.0") and rate.rows == [{"buyers": 2, "repeat": 1}]   # Rina had bought before, Andi had not
        # Instant metrics describe now and ignore the window.
        stock = await _value(s, c, "stock_on_hand")
        assert {r["name"]: r["stock"] for r in stock.rows} == {"Kopi": 25.0, "Roti": 25.0}   # 30 − 3 sold − 2 waste; 30 − 5 sold
        assert (await _value(s, c, "stock_on_hand", item_id=c["kopi"])).value == D("25.000")
        days = await _value(s, c, "stock_days_remaining", item_id=c["kopi"])
        assert days.unit == "days" and days.period_label == "sekarang"
        # Outside the day: nothing.
        empty = await compute(s, await s.get(Business, c["bid"]), "revenue", since=at(0) + timedelta(days=1), until=at(0) + timedelta(days=2))
        assert empty.value == D("0.00")
        assert (await compute(s, await s.get(Business, c["bid"]), "average_ticket", since=at(0) + timedelta(days=1), until=at(0) + timedelta(days=2))).value is None


async def test_named_periods_use_the_business_timezone_and_bad_arguments_are_refused(session_factory, day):
    c = day
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        biz = await s.get(Business, c["bid"])
        today = await compute(s, biz, "revenue", period="today", now=NOW)
        assert today.value == D("120000.00") and today.period == "today" and today.period_label == "hari ini"
        assert today.since == at(0) and today.until == at(0) + timedelta(days=1)
        # 01:00 UTC on the 4th is still the 3rd... no: 08:00 WIB on the 4th. Yesterday from there is the whole known day.
        tomorrow_morning = datetime(2026, 9, 4, 1, 0, tzinfo=timezone.utc)
        assert (await compute(s, biz, "revenue", period="yesterday", now=tomorrow_morning)).value == D("120000.00")
        assert (await compute(s, biz, "revenue", period="today", now=tomorrow_morning)).value == D("0.00")
        with pytest.raises(MetricArgumentInvalid) as exc:
            await compute(s, biz, "revenue", period="fortnight")
        assert exc.value.code == "period"
        with pytest.raises(MetricArgumentInvalid) as exc:
            await compute(s, biz, "revenue")
        assert exc.value.code == "range"
        with pytest.raises(MetricArgumentInvalid) as exc:
            await compute(s, biz, "revenue", since=at(5), until=at(4))
        assert exc.value.code == "range"
        with pytest.raises(MetricArgumentInvalid) as exc:
            await compute(s, biz, "transaction_count", period="today", item_id=c["kopi"])
        assert exc.value.code == "dimension"
        with pytest.raises(MetricNotFound):
            await compute(s, biz, "vibes", period="today")


async def test_the_registry_agrees_with_the_existing_tool_and_the_api_serves_it(session_factory, day):
    """The bridge for M9-T2: the assistant's get_sales_summary and the metric
    layer already give the same revenue for the same period."""
    from app.ai.tools import get_sales_summary
    from app.api.dashboard import metric_catalogue, metric_value

    c = day
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        biz = await s.get(Business, c["bid"])
        tool = await get_sales_summary(s, biz, {"period": "last_30_days"})
        metric_now = await compute(s, biz, "revenue", period="last_30_days")
        assert D(str(tool["revenue"])) == metric_now.value
        ctx = SimpleNamespace(session=s, business_id=c["bid"], staff_id=None)
        catalogue = await metric_catalogue(ctx)
        assert {m.name for m in catalogue} >= set(STANDARD_METRIC_NAMES) and all(m.description_id for m in catalogue)
        out = await metric_value("revenue", ctx, period=None, since=at(0), until=at(0) + timedelta(days=1), item_id=None, limit=5)
        assert (out.name, out.unit, out.value) == ("revenue", "rupiah", 120000.0)
        top = await metric_value("top_items_by_revenue", ctx, period=None, since=at(0), until=at(0) + timedelta(days=1), item_id=None, limit=1)
        assert len(top.rows) == 1
        with pytest.raises(HTTPException) as exc:
            await metric_value("vibes", ctx, period="today", since=None, until=None, item_id=None, limit=5)
        assert exc.value.status_code == 404 and "tidak dikenali" in exc.value.detail
        with pytest.raises(HTTPException) as exc:
            await metric_value("revenue", ctx, period="fortnight", since=None, until=None, item_id=None, limit=5)
        assert exc.value.status_code == 422
        with pytest.raises(HTTPException) as exc:
            await metric_value("revenue", ctx, period=None, since=None, until=None, item_id=None, limit=5)
        assert exc.value.status_code == 422
