"""fin-p0-8 — a day header on the Penjualan page is the whole day's figure.

The list is paged (40 rows), and a busy day spills over pages. The header used
to sum only the loaded rows, so it disagreed with "Hari terbaik" on the same
page. The server now returns each shown day's count and takings (voided bills
left out of the takings) next to the rows.

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import create_token, hash_pin
from app.main import app
from app.models import Business, Item, Staff
from app.services.catalog import ensure_default_variant
from app.services.orders import OrderLineSpec, PaymentSpec, create_order
from app.services.pricing import ensure_pricing_settings
from app.services.stock import open_item_stock

from tests.conftest import seed_books

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")
D = Decimal
WIB = ZoneInfo("Asia/Jakarta")


def wib(day, hour, minute=0):
    return datetime(2026, 9, day, hour, minute, tzinfo=WIB).astimezone(timezone.utc)


@pytest.fixture
async def engine():
    if not DB_URL:
        pytest.fail("INTEGRATION_DATABASE_URL unset — local Postgres is not configured (roadmap §2)")
    engine = create_async_engine(DB_URL, connect_args={"statement_cache_size": 0})
    yield engine
    await engine.dispose()


@pytest.fixture
async def client():
    from app.core.db import engine as app_engine

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await app_engine.dispose()


@pytest.fixture
async def shop(engine):
    """Day 10: 5 bills of Rp 20.000 (one of them voided afterwards by a flag
    below). Day 11: 2 bills. A page of 3 rows cuts day 10 in half."""
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        biz = Business(name="Day Totals Shop", owner_phone=f"62966{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with factory() as s:
        await s.execute(text("select set_config('app.current_business_id', :b, true)"), {"b": str(bid)})
        await seed_books(s, bid)
        await ensure_pricing_settings(s, bid)
        sari = Staff(business_id=bid, name="Sari", pin_hash=hash_pin("2345"))
        kopi = Item(business_id=bid, name="Kopi", unit="cup", current_stock=D(200), cost_price=D(8000), sell_price=D(20000))
        s.add_all([sari, kopi])
        await s.flush()
        await open_item_stock(s, kopi, unit_cost=kopi.cost_price)
        await ensure_default_variant(s, kopi)
        orders = []
        for day, hour in [(10, 9), (10, 10), (10, 11), (10, 12), (10, 13), (11, 9), (11, 10)]:
            o = await create_order(s, business_id=bid, staff_id=sari.id,
                                   lines=[OrderLineSpec(item_id=kopi.id, quantity=D(1))],
                                   payments=[PaymentSpec(method="cash", amount=D(20000))], sold_at=wib(day, hour))
            orders.append(o)
        # One of day 10's bills is voided: counted as a bill, not as takings.
        await s.execute(text("update orders set status='voided' where id=:i"), {"i": orders[0].order.id})
        await s.commit()
        ids = {"id": bid}
    ids["auth"] = {"Authorization": f"Bearer {create_token(business_id=str(bid), scope='owner')}"}
    yield ids
    async with factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


async def test_header_totals_cover_the_whole_day_not_the_loaded_page(client, shop):
    r = await client.get("/api/orders?limit=3&offset=0", headers=shop["auth"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["rows"]) == 3 and body["total"] == 7
    # The page holds all of day 11 and only one bill of day 10, yet both days
    # report their full figures.
    assert body["days"]["2026-09-11"] == {"count": 2, "total": "40000.00"}
    assert body["days"]["2026-09-10"] == {"count": 5, "total": "80000.00"}  # 5 bills, the voided one not in takings


async def test_totals_follow_the_cashier_filter_and_skip_days_off_the_page(client, shop):
    r = await client.get("/api/orders?limit=2&offset=0&since=2026-09-11&until=2026-09-11", headers=shop["auth"])
    days = r.json()["days"]
    assert list(days) == ["2026-09-11"]
    r = await client.get(f"/api/orders?staff_id={uuid.uuid4()}", headers=shop["auth"])
    assert r.json()["days"] == {} and r.json()["rows"] == []
