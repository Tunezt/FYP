"""fin-p1-3 — the owner's daily summary, and Monday's weekly one (CP1 §1.5).

Sep 2026: Monday is the 14th. Last week (7th-13th) has 3 bills, the week before
has 1; the 14th has 2 bills. WhatsApp is mocked: the test reads the text.

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import hash_pin
from app.jobs.summary import build_summary, rp, send_summary
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
async def shop(engine):
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        biz = Business(name="Summary Shop", owner_phone=f"62967{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with factory() as s:
        await s.execute(text("select set_config('app.current_business_id', :b, true)"), {"b": str(bid)})
        await seed_books(s, bid)
        await ensure_pricing_settings(s, bid)
        sari = Staff(business_id=bid, name="Sari", pin_hash=hash_pin("2345"))
        kopi = Item(business_id=bid, name="Kopi", unit="cup", current_stock=D(500), cost_price=D(8000), sell_price=D(20000))
        s.add_all([sari, kopi])
        await s.flush()
        await open_item_stock(s, kopi, unit_cost=kopi.cost_price)
        await ensure_default_variant(s, kopi)
        for day, hour in [(2, 10), (8, 10), (9, 10), (12, 10), (14, 9), (14, 10)]:
            await create_order(s, business_id=bid, staff_id=sari.id,
                               lines=[OrderLineSpec(item_id=kopi.id, quantity=D(1))],
                               payments=[PaymentSpec(method="cash", amount=D(20000))], sold_at=wib(day, hour))
        await s.commit()
        biz = await s.get(Business, bid)
    yield factory, biz
    async with factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


async def _build(factory, biz, day):
    async with factory() as s:
        await s.execute(text("select set_config('app.current_business_id', :b, true)"), {"b": str(biz.id)})
        return await build_summary(s, biz, now=wib(day, 23, 30))


async def test_monday_message_has_the_day_and_last_week(shop):
    factory, biz = shop
    msg = await _build(factory, biz, 14)
    assert "Senin 14 Sep" in msg and "Rp 40.000 dari 2 transaksi" in msg
    assert "Terlaris: Kopi (2)" in msg
    assert "minggu lalu, 7 Sep - 13 Sep" in msg and "Rp 60.000 dari 3 transaksi" in msg
    assert "naik 200%" in msg  # 60.000 against the week before's 20.000


async def test_a_midweek_day_has_no_weekly_part_and_a_closed_day_sends_nothing(shop):
    factory, biz = shop
    assert "minggu lalu" not in await _build(factory, biz, 12)
    assert await _build(factory, biz, 13) is None  # Sunday, no sales


async def test_gateway_sends_text_and_cloud_api_sends_the_template(shop):
    factory, biz = shop
    for provider, sent in (("gateway", "send_text"), ("cloud", "send_template")):
        async with factory() as s:
            await s.execute(text("select set_config('app.current_business_id', :b, true)"), {"b": str(biz.id)})
            with patch("app.jobs.summary.send_text", AsyncMock()) as t, \
                 patch("app.jobs.summary.send_template", AsyncMock()) as tpl, \
                 patch("app.jobs.summary.get_settings") as gs:
                gs.return_value.whatsapp_provider = provider
                gs.return_value.whatsapp_alert_template = "business_alert"
                assert await send_summary(s, biz, now=wib(12, 23, 30)) is True
                called = {"send_text": t, "send_template": tpl}[sent]
                other = {"send_text": tpl, "send_template": t}[sent]
                called.assert_awaited_once()
                other.assert_not_awaited()
                if provider == "cloud":
                    assert "\n" not in tpl.await_args.args[2][2]  # template parameters take no newlines


def test_rupiah_format():
    assert rp(D("1447000.00")) == "Rp 1.447.000" and rp(None) == "Rp 0"
