"""M10-T1 — exception rules over the registry. Done when each rule has a test
that manufactures its condition and asserts exactly one alert. Each test also
runs the rules a second time on the same data and asserts nothing more is
written (one alert per subject per day), and a quiet shop yields none.

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai.periods import period_range
from app.core.security import hash_pin
from app.jobs.rules import (
    MARGIN_DROP_POINTS, PRICE_MOVE_PCT, rule_margin_drop, rule_stockout_risk, rule_supplier_price, rule_takings_anomaly,
    rule_void_rate, run_rules,
)
from app.models import Alert, Business, Item, ItemVariant, Order, Staff
from app.services.catalog import ensure_default_variant
from app.services.orders import OrderLineSpec, PaymentSpec, create_order, void_order
from app.services.pricing import ensure_pricing_settings
from app.services.receiving import GrLineSpec, receive_goods
from app.services.stock import open_item_stock
from app.services.suppliers import create_supplier

from tests.conftest import seed_books

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")
D = Decimal
NOW = datetime.now(timezone.utc)


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
    """A quiet café: Kopi 20.000 (cost 8.000), Roti 15.000 (cost 6.000), plenty of stock, and 41 steady
    days (today included) of one sale per cashier — 2 or 3 Kopi plus 1 Roti, alternating by day, so
    revenue is 110.000 or 150.000 a day (a real baseline has some spread). No voids, no receipts.
    Every rule must stay silent here."""
    async with session_factory() as s:
        biz = Business(name="Rules", owner_phone=f"62963{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await seed_books(s, bid)
        await ensure_pricing_settings(s, bid)
        owner = Staff(business_id=bid, name="Bu Ratna", role="owner", pin_hash=hash_pin("1234"))
        sari = Staff(business_id=bid, name="Sari", pin_hash=hash_pin("2345"))
        budi = Staff(business_id=bid, name="Budi", pin_hash=hash_pin("3456"))
        kopi = Item(business_id=bid, name="Kopi", unit="cup", current_stock=D(800), cost_price=D(8000), sell_price=D(20000), reorder_threshold=D(5))
        roti = Item(business_id=bid, name="Roti", unit="pcs", current_stock=D(500), cost_price=D(6000), sell_price=D(15000), reorder_threshold=D(5))
        s.add_all([owner, sari, budi, kopi, roti])
        await s.flush()
        for it in (kopi, roti):
            await open_item_stock(s, it, unit_cost=it.cost_price)
            await ensure_default_variant(s, it)
        for day in range(40, -1, -1):
            when = NOW - timedelta(days=day, hours=6 if day else 1)
            qty = D(2) if day % 2 == 0 else D(3)
            for staff in (sari, budi):
                await create_order(s, business_id=bid, staff_id=staff.id,
                                   lines=[OrderLineSpec(item_id=kopi.id, quantity=qty), OrderLineSpec(item_id=roti.id, quantity=D(1))],
                                   payments=[PaymentSpec(method="cash", amount=D(20000) * qty + D(15000))], sold_at=when)
        await s.commit()
        ids = {"bid": bid, "owner": owner.id, "sari": sari.id, "budi": budi.id, "kopi": kopi.id, "roti": roti.id}
    yield ids
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


async def _alerts(s, kind=None):
    stmt = select(Alert).where(Alert.rule_key.is_not(None))
    if kind:
        stmt = stmt.where(Alert.type == kind)
    return (await s.execute(stmt)).scalars().all()


async def test_a_quiet_shop_produces_no_rule_alerts(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        biz = await s.get(Business, c["bid"])
        assert await run_rules(s, biz) == [] and await _alerts(s) == []


async def test_margin_drop_fires_exactly_once(session_factory, shop):
    """The last 7 days sell at a much thinner margin than the 30 before: the
    bean got dearer (cost 8.000 → 17.000, on the item and its default variant,
    which is where a sale snapshots cost from) and 5 more Kopi a day sell at it."""
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        kopi = await s.get(Item, c["kopi"])
        kopi.cost_price = D(17000)
        for v in (await s.execute(select(ItemVariant).where(ItemVariant.item_id == c["kopi"]))).scalars():
            v.cost_price = D(17000)
        await s.flush()
        for day in range(6, -1, -1):
            when = NOW - timedelta(days=day, hours=5 if day else 1, minutes=30)
            await create_order(s, business_id=c["bid"], staff_id=c["sari"], lines=[OrderLineSpec(item_id=c["kopi"], quantity=D(5))],
                               payments=[PaymentSpec(method="cash", amount=D(100000))], sold_at=when)
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        biz = await s.get(Business, c["bid"])
        found = await rule_margin_drop(s, biz)
        assert len(found) == 1 and found[0].type == "margin_drop"
        assert found[0].details["drop_points"] >= float(MARGIN_DROP_POINTS)
        first = await run_rules(s, biz)
        assert [a.type for a in first] == ["margin_drop"]
        assert await run_rules(s, biz) == [] and len(await _alerts(s, "margin_drop")) == 1
        assert "Margin kotor" in first[0].message and first[0].rule_key.startswith("margin_drop:")
        await s.commit()


async def test_stockout_before_the_next_delivery_fires_exactly_once(session_factory, shop):
    """Roti sells 2 a day over the 14-day window; its supplier delivers every
    10 days; drop the stock to 6 → ~3 days left, delivery in 10 → one alert."""
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        jaya = await create_supplier(s, c["bid"], name="Toko Jaya")
        for days_ago in (30, 20, 10):
            await receive_goods(s, c["bid"], supplier_id=jaya.id, lines=[GrLineSpec(item_id=c["roti"], quantity=D(20), unit_cost=D(6000))],
                                received_at=NOW - timedelta(days=days_ago), received_by=c["owner"])
        roti = await s.get(Item, c["roti"])
        roti.current_stock = D(6)
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        biz = await s.get(Business, c["bid"])
        found = await rule_stockout_risk(s, biz)
        assert [(f.related_item_id, f.details["delivery_days"]) for f in found] == [(c["roti"], 10)]
        assert found[0].details["days_remaining"] < 10
        first = await run_rules(s, biz)
        assert [a.type for a in first] == ["stockout_risk"] and first[0].related_item_id == c["roti"]
        assert await run_rules(s, biz) == [] and len(await _alerts(s, "stockout_risk")) == 1
        await s.commit()


async def test_void_rate_outlier_fires_exactly_once(session_factory, shop):
    """Budi voids 8 of his last 41 orders (none of today's, so today's takings
    stay normal); Sari voids none."""
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        biz = await s.get(Business, c["bid"])
        today_start, _, _ = period_range("today", biz.timezone)
        budi_orders = (await s.execute(
            select(Order.id).where(Order.staff_id == c["budi"], Order.sold_at < today_start).order_by(Order.sold_at.desc()).limit(8)
        )).scalars().all()
        for oid in budi_orders:
            await void_order(s, business_id=c["bid"], order_id=oid, staff_id=c["budi"], manager_pin="1234")
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        biz = await s.get(Business, c["bid"])
        found = await rule_void_rate(s, biz)
        assert len(found) == 1 and found[0].details["staff_id"] == str(c["budi"]) and found[0].details["voids"] == 8
        first = await run_rules(s, biz)
        assert [a.type for a in first] == ["void_rate"] and "Budi" in first[0].message
        assert await run_rules(s, biz) == [] and len(await _alerts(s, "void_rate")) == 1
        await s.commit()


async def test_supplier_price_move_fires_exactly_once(session_factory, shop):
    """Kopi was 8.000 from Toko Jaya; today's delivery is 9.600 (+20%)."""
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        jaya = await create_supplier(s, c["bid"], name="Toko Jaya")
        await receive_goods(s, c["bid"], supplier_id=jaya.id, lines=[GrLineSpec(item_id=c["kopi"], quantity=D(10), unit_cost=D(8000))],
                            received_at=NOW - timedelta(days=9), received_by=c["owner"])
        await receive_goods(s, c["bid"], supplier_id=jaya.id, lines=[GrLineSpec(item_id=c["kopi"], quantity=D(10), unit_cost=D(9600))],
                            received_at=NOW - timedelta(hours=2), received_by=c["owner"])
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        biz = await s.get(Business, c["bid"])
        found = await rule_supplier_price(s, biz)
        assert len(found) == 1 and found[0].related_item_id == c["kopi"] and found[0].details["change_pct"] == 20.0
        assert found[0].details["change_pct"] >= PRICE_MOVE_PCT
        first = await run_rules(s, biz)
        assert [a.type for a in first] == ["supplier_price"] and "naik 20%" in first[0].message
        assert await run_rules(s, biz) == [] and len(await _alerts(s, "supplier_price")) == 1
        await s.commit()


async def test_takings_anomaly_fires_exactly_once(session_factory, shop):
    """Forty days around 130.000 a day, then a 2.000.000 spike today: z far beyond 3."""
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        for _ in range(20):
            await create_order(s, business_id=c["bid"], staff_id=c["sari"], lines=[OrderLineSpec(item_id=c["kopi"], quantity=D(5))],
                               payments=[PaymentSpec(method="cash", amount=D(100000))], sold_at=NOW - timedelta(minutes=30))
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        biz = await s.get(Business, c["bid"])
        found = await rule_takings_anomaly(s, biz)
        assert len(found) == 1 and found[0].type == "anomaly" and found[0].details["z"] > 3
        first = await run_rules(s, biz)
        assert [a.type for a in first] == ["anomaly"] and "jauh di atas normal" in first[0].message
        assert await run_rules(s, biz) == [] and len(await _alerts(s, "anomaly")) == 1
        await s.commit()
