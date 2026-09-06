"""M10-T2 — alert quality, not quantity. Done when a simulated week of stable
data produces zero alerts. Also: a condition that persists is said once, not
every night; a burst of findings is capped at five a night and the rest
follow the next night; the till's low-stock alert and the nightly stock-out
rule are one conversation.

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import hash_pin
from app.jobs.alert_policy import MAX_NEW_PER_NIGHT, SUPPRESS_WINDOW_DAYS, apply_policy, subject_of
from app.jobs.nightly import nightly_pass
from app.jobs.rules import RuleAlert, run_rules
from app.models import Alert, Business, Item, Staff
from app.services.catalog import ensure_default_variant
from app.services.orders import OrderLineSpec, PaymentSpec, create_order
from app.services.pricing import ensure_pricing_settings
from app.services.stock import open_item_stock
from app.services.velocity import check_low_stock_for_item

from tests.conftest import seed_books

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")
D = Decimal
NOW = datetime.now(timezone.utc)


def test_subject_strips_the_day_only_when_there_is_one():
    assert subject_of("stock:abc:2026-09-06") == "stock:abc"
    assert subject_of("margin_drop:2026-09-06") == "margin_drop"
    assert subject_of("anomaly:daily_revenue:2026-09-06") == "anomaly:daily_revenue"
    assert subject_of("legacy-key") == "legacy-key"


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


async def _sell_day(s, c, day_offset: int, hours: int = 6):
    """The café's normal day: one sale per cashier, 2 or 3 Kopi + 1 Roti."""
    when = NOW - timedelta(days=day_offset, hours=hours)
    qty = D(2) if day_offset % 2 == 0 else D(3)
    for staff in (c["sari"], c["budi"]):
        created = await create_order(s, business_id=c["bid"], staff_id=staff,
                                     lines=[OrderLineSpec(item_id=c["kopi"], quantity=qty), OrderLineSpec(item_id=c["roti"], quantity=D(1))],
                                     payments=[PaymentSpec(method="cash", amount=D(20000) * qty + D(15000))], sold_at=when)
        for cl in created.lines:   # what the POS does after every sale
            await check_low_stock_for_item(s, c["bid"], cl.line.item_id)


@pytest.fixture
async def shop(session_factory):
    """A stable café with 34 days of history before the simulated week starts."""
    async with session_factory() as s:
        biz = Business(name="Quiet Week", owner_phone=f"62962{uuid.uuid4().hex[:9]}")
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
        c = {"bid": bid, "owner": owner.id, "sari": sari.id, "budi": budi.id, "kopi": kopi.id, "roti": roti.id}
        for day in range(40, 6, -1):
            await _sell_day(s, c, day)
        await s.commit()
    yield c
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


async def test_a_simulated_stable_week_produces_zero_alerts(session_factory, shop):
    """THE DONE-WHEN. Seven nights of the full pass on seven ordinary days."""
    c = shop
    for day in range(6, -1, -1):
        async with session_factory() as s:
            await _set_tenant(s, c["bid"])
            await _sell_day(s, c, day)
            biz = await s.get(Business, c["bid"])
            night = NOW - timedelta(days=day, hours=1)
            written = await nightly_pass(s, biz, now=night)
            assert written == [], f"night {day} days ago wrote {[a.type for a in written]}"
            await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert (await s.execute(select(func.count(Alert.id)))).scalar_one() == 0


async def test_a_persistent_condition_is_said_once_a_week_not_every_night(session_factory, shop):
    """Roti drops to 3 days of stock and stays there all week (no delivery):
    one alert on the first night, then silence — the same alert as yesterday
    is not an alert. After the window it is said again."""
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        (await s.get(Item, c["roti"])).current_stock = D(6)
        await s.commit()
    kinds = []
    for day in range(6, -1, -1):
        async with session_factory() as s:
            await _set_tenant(s, c["bid"])
            biz = await s.get(Business, c["bid"])
            written = await nightly_pass(s, biz, now=NOW - timedelta(days=day, hours=1))
            kinds.append([a.type for a in written])
            (await s.get(Item, c["roti"])).current_stock = D(6)   # nothing delivered, still 3 days left
            await s.commit()
    assert kinds[0] and kinds[0] != [] and all(k == [] for k in kinds[1:]), kinds
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        alerts = (await s.execute(select(Alert).where(Alert.related_item_id == c["roti"]))).scalars().all()
        assert len(alerts) == 1 and subject_of(alerts[0].rule_key) == f"stock:{c['roti']}"
        # A week and an hour after that first alert the window has passed and the
        # condition still holds (worse: 2 left): once more. (The velocity window
        # still sees the history's sales at that moment.)
        (await s.get(Item, c["roti"])).current_stock = D(2)
        await s.flush()
        biz = await s.get(Business, c["bid"])
        later = await run_rules(s, biz, now=alerts[0].created_at + timedelta(days=SUPPRESS_WINDOW_DAYS, hours=1))
        assert [a.type for a in later] == ["stockout_risk"]
        await s.commit()


async def test_the_tills_low_stock_alert_and_the_nightly_rule_are_one_conversation(session_factory, shop):
    """A sale drops Roti under the threshold at 14:00; the till alerts at once.
    That night the stock-out rule finds the same item and says nothing new."""
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        (await s.get(Item, c["roti"])).current_stock = D(3)
        await s.flush()
        first = await check_low_stock_for_item(s, c["bid"], c["roti"])
        assert first is not None and first.type == "low_stock" and first.rule_key.startswith(f"stock:{c['roti']}:")
        biz = await s.get(Business, c["bid"])
        written = await run_rules(s, biz)
        assert written == []
        assert (await s.execute(select(func.count(Alert.id)).where(Alert.related_item_id == c["roti"]))).scalar_one() == 1
        await s.commit()


async def test_a_burst_is_capped_at_five_a_night_and_the_rest_follow(session_factory, shop):
    c = shop
    findings = [
        RuleAlert(type="supplier_price", rule_key=f"supplier_price:item{i}:sup:2026-09-06", severity="medium" if i % 2 else "high",
                  message=f"harga {i}", metric="supplier_price", details={"i": i})
        for i in range(8)
    ]
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        biz = await s.get(Business, c["bid"])
        night1 = await apply_policy(s, biz, findings, now=NOW)
        assert len(night1.written) == MAX_NEW_PER_NIGHT == 5 and night1.deferred == 3
        assert [a.severity for a in night1.written][:4] == ["high"] * 4        # the loudest first
        again = await apply_policy(s, biz, findings, now=NOW)
        assert again.written == [] and again.duplicates == 5 and again.deferred == 3   # same night: nothing new
        # The next night the three deferred subjects (with tomorrow's key) are written; the five are suppressed.
        tomorrow = [RuleAlert(f.type, f.rule_key.replace("2026-09-06", "2026-09-07"), f.severity, f.message, f.metric, None, f.details) for f in findings]
        night2 = await apply_policy(s, biz, tomorrow, now=NOW + timedelta(days=1))
        assert len(night2.written) == 3 and night2.suppressed == 5
        assert (await s.execute(select(func.count(Alert.id)).where(Alert.type == "supplier_price"))).scalar_one() == 8
        await s.commit()
