"""Riwayat transaksi: filter by business day and by cashier.

The owner's two questions about the history are "which day?" and "who rang it
up?". Both are filtered in SQL rather than in the page, or the answer is only
ever the current page of 40. The date filter counts *business* days (M15-T4),
so a café that closes at 00:15 finds that bill under the night it belongs to,
and `until` is inclusive — asking for one day means that whole day.

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
from app.services.expenses import record_expense
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
    """A 04:00 café. Sari sells on the 10th and at 00:15 on the 11th (still the
    10th's business day); Budi sells once on the 11th."""
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        biz = Business(name="Filter Shop", owner_phone=f"62965{uuid.uuid4().hex[:9]}", day_start_hour=4)
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with factory() as s:
        await s.execute(text("select set_config('app.current_business_id', :b, true)"), {"b": str(bid)})
        await seed_books(s, bid)
        await ensure_pricing_settings(s, bid)
        sari = Staff(business_id=bid, name="Sari", pin_hash=hash_pin("2345"))
        budi = Staff(business_id=bid, name="Budi", pin_hash=hash_pin("3456"))
        kopi = Item(business_id=bid, name="Kopi", unit="cup", current_stock=D(200), cost_price=D(8000), sell_price=D(20000))
        s.add_all([sari, budi, kopi])
        await s.flush()
        await open_item_stock(s, kopi, unit_cost=kopi.cost_price)
        await ensure_default_variant(s, kopi)

        async def sell(staff, qty, when):
            await create_order(s, business_id=bid, staff_id=staff.id,
                               lines=[OrderLineSpec(item_id=kopi.id, quantity=D(qty))],
                               payments=[PaymentSpec(method="cash", amount=D(20000 * qty))], sold_at=when)

        await sell(sari, 1, wib(10, 12))
        await sell(sari, 2, wib(11, 0, 15))     # after midnight: the 10th's business day
        await sell(budi, 3, wib(11, 13))
        await record_expense(s, bid, amount=D(12000), description="es batu", category="operasional",
                             source="manual", occurred_at=wib(10, 12))
        await record_expense(s, bid, amount=D(40000), description="susu", category="bahan baku",
                             source="manual", occurred_at=wib(11, 0, 15))     # the 10th's business day
        await record_expense(s, bid, amount=D(9000), description="plastik", category="operasional",
                             source="manual", occurred_at=wib(11, 13))
        await s.commit()
        ids = {"id": bid, "sari": sari.id, "budi": budi.id}
    ids["auth"] = {"Authorization": f"Bearer {create_token(business_id=str(bid), scope='owner')}"}
    yield ids
    async with factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


async def _rows(client, shop, query=""):
    r = await client.get(f"/api/sales?page=1&page_size=40{query}", headers=shop["auth"])
    assert r.status_code == 200, r.text
    body = r.json()
    return body["total"], [(row["staff_name"], float(row["total_price"])) for row in body["rows"]]


async def test_unfiltered_is_unchanged(client, shop):
    total, rows = await _rows(client, shop)
    assert total == 3 and len(rows) == 3


async def test_one_business_day_includes_the_bill_settled_after_midnight(client, shop):
    total, rows = await _rows(client, shop, "&since=2026-09-10&until=2026-09-10")
    assert total == 2
    assert sorted(rows) == [("Sari", 20000.0), ("Sari", 40000.0)]


async def test_the_next_day_holds_only_its_own_sale(client, shop):
    total, rows = await _rows(client, shop, "&since=2026-09-11&until=2026-09-11")
    assert total == 1 and rows == [("Budi", 60000.0)]


async def test_a_range_covers_both_ends_inclusively(client, shop):
    total, _ = await _rows(client, shop, "&since=2026-09-10&until=2026-09-11")
    assert total == 3
    total, _ = await _rows(client, shop, "&since=2026-09-12&until=2026-09-20")
    assert total == 0


async def test_filtering_by_cashier(client, shop):
    total, rows = await _rows(client, shop, f"&staff_id={shop['sari']}")
    assert total == 2 and {name for name, _ in rows} == {"Sari"}
    total, rows = await _rows(client, shop, f"&staff_id={shop['budi']}")
    assert total == 1 and rows == [("Budi", 60000.0)]


async def test_cashier_and_day_together(client, shop):
    total, rows = await _rows(client, shop, f"&staff_id={shop['sari']}&since=2026-09-10&until=2026-09-10")
    assert total == 2
    total, _ = await _rows(client, shop, f"&staff_id={shop['budi']}&since=2026-09-10&until=2026-09-10")
    assert total == 0


async def test_the_total_is_the_filtered_total_not_the_page(client, shop):
    r = await client.get(f"/api/sales?page=1&page_size=1&staff_id={shop['sari']}", headers=shop["auth"])
    body = r.json()
    assert body["total"] == 2 and len(body["rows"]) == 1


async def test_a_nonsense_filter_is_refused_rather_than_ignored(client, shop):
    r = await client.get("/api/sales?since=bukan-tanggal", headers=shop["auth"])
    assert r.status_code == 422
    r = await client.get("/api/sales?staff_id=not-a-uuid", headers=shop["auth"])
    assert r.status_code == 422


# ── the same filters on the money page's expense history ────────────────────


async def _expenses(client, shop, query=""):
    r = await client.get(f"/api/expenses?page=1&page_size=40{query}", headers=shop["auth"])
    assert r.status_code == 200, r.text
    body = r.json()
    return body["total"], [(row["category"], float(row["amount"])) for row in body["rows"]]


async def test_expenses_unfiltered_and_by_business_day(client, shop):
    total, _ = await _expenses(client, shop)
    assert total == 3
    total, rows = await _expenses(client, shop, "&since=2026-09-10&until=2026-09-10")
    assert total == 2 and sorted(rows) == [("bahan baku", 40000.0), ("operasional", 12000.0)]


async def test_expenses_by_category(client, shop):
    total, rows = await _expenses(client, shop, "&category=operasional")
    assert total == 2 and {c for c, _ in rows} == {"operasional"}
    total, _ = await _expenses(client, shop, "&category=bahan baku&since=2026-09-11&until=2026-09-11")
    assert total == 0          # that one was settled at 00:15, on the 10th's business day


async def test_alerts_take_the_same_range(client, shop):
    r = await client.get("/api/alerts?since=2026-09-10&until=2026-09-11", headers=shop["auth"])
    assert r.status_code == 200 and r.json() == []
    r = await client.get("/api/alerts?severity=high", headers=shop["auth"])
    assert r.status_code == 200
    r = await client.get("/api/alerts?since=bukan-tanggal", headers=shop["auth"])
    assert r.status_code == 422


# ── the same filters on the receipt list the Penjualan page now shows ────────


async def _orders(client, shop, query=""):
    r = await client.get(f"/api/orders?limit=40{query}", headers=shop["auth"])
    assert r.status_code == 200, r.text
    body = r.json()
    return body["total"], [(row["staff_name"], row["number"], float(row["total"])) for row in body["rows"]]


async def test_orders_are_receipts_not_lines(client, shop):
    total, rows = await _orders(client, shop)
    assert total == 3                      # three bills, whatever their line counts
    assert all(len(number) == 8 for _staff, number, _total in rows)


async def test_orders_by_business_day_and_cashier(client, shop):
    total, rows = await _orders(client, shop, "&since=2026-09-10&until=2026-09-10")
    assert total == 2 and {s for s, _n, _t in rows} == {"Sari"}     # incl. the 00:15 bill
    total, rows = await _orders(client, shop, f"&staff_id={shop['budi']}")
    assert total == 1 and rows[0][0] == "Budi"
    total, _ = await _orders(client, shop, f"&staff_id={shop['budi']}&since=2026-09-10&until=2026-09-10")
    assert total == 0


async def test_orders_reject_a_nonsense_filter(client, shop):
    r = await client.get("/api/orders?since=bukan-tanggal", headers=shop["auth"])
    assert r.status_code == 422
    r = await client.get("/api/orders?staff_id=not-a-uuid", headers=shop["auth"])
    assert r.status_code == 422
