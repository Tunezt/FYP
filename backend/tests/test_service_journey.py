"""The service journey (svc-1 onwards): what a busy café does between "one
Americano" and "your order is ready", through the real API.

Each task in the svc sequence adds the tests for its part of the journey here,
so the file reads in the order a customer lives it: choose, order, pay, add,
prepare, hand over, and occasionally undo.

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import os
import uuid
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import create_menu_token, create_token, hash_pin
from app.main import app
from app.models import Business, Item, Order, OrderLine, OrderLineModifier, Staff
from app.services.catalog import create_modifier, create_modifier_group, create_variant, ensure_default_variant
from app.services.pricing import ensure_pricing_settings
from app.services.stock import open_item_stock

from tests.conftest import seed_books

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")
D = Decimal


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def client():
    from app.core.db import engine as app_engine

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await app_engine.dispose()


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


async def _make_cafe(session_factory, name="Kedai Jalan"):
    """Americano in two sizes with a required temperature (whose catalogue
    default is Panas) and optional extras; Roti with nothing to ask."""
    async with session_factory() as s:
        biz = Business(name=name, owner_phone=f"62963{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await seed_books(s, bid)
        await ensure_pricing_settings(s, bid)
        owner = Staff(business_id=bid, name="Bu Ratna", role="owner", pin_hash=hash_pin("1234"))
        sari = Staff(business_id=bid, name="Sari", pin_hash=hash_pin("2345"))
        americano = Item(business_id=bid, name="Americano", unit="cup", current_stock=D(40), cost_price=D(6000), sell_price=D(18000), reorder_threshold=D(2))
        roti = Item(business_id=bid, name="Roti", unit="pcs", current_stock=D(30), cost_price=D(6000), sell_price=D(15000), reorder_threshold=D(2))
        s.add_all([owner, sari, americano, roti])
        await s.flush()
        for it in (americano, roti):
            await open_item_stock(s, it, unit_cost=it.cost_price)
        standar = await ensure_default_variant(s, americano)
        large = await create_variant(s, americano, name="Large", sell_price=D(22000), cost_price=D(7500))
        await ensure_default_variant(s, roti)
        suhu = await create_modifier_group(s, americano, name="Suhu", selection="single", is_required=True)
        panas = await create_modifier(s, suhu, name="Panas", is_default=True)
        dingin = await create_modifier(s, suhu, name="Dingin")
        tambahan = await create_modifier_group(s, americano, name="Tambahan", selection="multi", max_select=2)
        shot = await create_modifier(s, tambahan, name="Extra shot", price_delta=D(5000))
        await s.commit()
        return {
            "bid": bid, "owner": owner.id, "sari": sari.id, "americano": americano.id, "roti": roti.id,
            "standar": standar.id, "large": large.id, "panas": panas.id, "dingin": dingin.id, "shot": shot.id,
            "pos": create_token(business_id=str(bid), scope="pos", staff_id=str(sari.id)),
            "menu": create_menu_token(str(bid)),
        }


@pytest.fixture
async def cafe(session_factory):
    c = await _make_cafe(session_factory)
    yield c
    async with session_factory() as s:
        row = await s.get(Business, c["bid"])
        if row:
            await s.delete(row)
        await s.commit()


def _line(c, *, variant=None, mods=(), qty=1, notes=None, item="americano"):
    return {"item_id": str(c[item]), "variant_id": str(c[variant]) if variant else None,
            "modifier_ids": [str(c[m]) for m in mods], "quantity": str(qty), "notes": notes}


def _cash(amount):
    return [{"method": "cash", "amount": str(amount)}]


# ── 1. Choices are the customer's ────────────────────────────────────────────


async def test_1_americano_cannot_be_sold_before_size_and_temperature_are_chosen(client, session_factory, cafe):
    c = cafe
    no_size = await client.post("/pos/orders", headers=_auth(c["pos"]), json={"lines": [_line(c, mods=["panas"])], "payments": _cash(18000)})
    assert no_size.status_code == 422 and no_size.json()["detail"] == "Ukuran Americano belum dipilih — pilih ukurannya dulu"
    # The catalogue's default temperature is Panas; nobody chose it, so nothing is assumed.
    no_temp = await client.post("/pos/orders", headers=_auth(c["pos"]), json={"lines": [_line(c, variant="standar")], "payments": _cash(18000)})
    assert no_temp.status_code == 422 and "Suhu" in no_temp.json()["detail"]
    # The QR menu asks the same questions.
    guest = await client.post(f"/menu/{c['menu']}/orders", json={"lines": [_line(c, mods=["dingin"])], "order_type": "takeaway"})
    assert guest.status_code == 422 and "Ukuran Americano" in guest.json()["detail"]
    guest = await client.post(f"/menu/{c['menu']}/orders", json={"lines": [_line(c, variant="large")], "order_type": "takeaway"})
    assert guest.status_code == 422 and "Suhu" in guest.json()["detail"]
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert (await s.execute(select(func.count(Order.id)))).scalar_one() == 0
        assert (await s.get(Item, c["americano"])).current_stock == D(40)
    # Answered: it sells, at the chosen size.
    ok = await client.post("/pos/orders", headers=_auth(c["pos"]), json={"lines": [_line(c, variant="large", mods=["panas"])], "payments": _cash(22000)})
    assert ok.status_code == 201, ok.text
    assert ok.json()["lines"][0]["variant_id"] == str(c["large"]) and D(ok.json()["total"]) == D(22000)
    # A product with nothing to ask stays one tap: no size, no modifier needed.
    roti = await client.post("/pos/orders", headers=_auth(c["pos"]), json={"lines": [_line(c, item="roti")], "payments": _cash(15000)})
    assert roti.status_code == 201, roti.text


async def test_2_differently_customised_americanos_stay_distinct_lines(client, session_factory, cafe):
    c = cafe
    lines = [
        _line(c, variant="standar", mods=["panas"], qty=1),
        _line(c, variant="large", mods=["dingin", "shot"], qty=2),
        _line(c, variant="standar", mods=["panas"], qty=1, notes="gelas kertas"),
    ]
    resp = await client.post("/pos/orders", headers=_auth(c["pos"]), json={"lines": lines, "payments": _cash(18000 + 2 * 27000 + 18000)})
    assert resp.status_code == 201, resp.text
    order_id = uuid.UUID(resp.json()["id"])
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        rows = (await s.execute(select(OrderLine).where(OrderLine.order_id == order_id))).scalars().all()
        assert len(rows) == 3
        mods = {}
        for r in rows:
            names = sorted((await s.execute(select(OrderLineModifier.name).where(OrderLineModifier.order_line_id == r.id))).scalars().all())
            mods[(r.variant_id, tuple(names), r.notes)] = (D(r.quantity), D(r.unit_price), D(r.line_total))
        assert mods == {
            (c["standar"], ("Panas",), None): (D(1), D(18000), D(18000)),
            (c["large"], ("Dingin", "Extra shot"), None): (D(2), D(27000), D(54000)),
            (c["standar"], ("Panas",), "gelas kertas"): (D(1), D(18000), D(18000)),
        }
    # The kitchen reads each as its own line, with every choice written out.
    kitchen = (await client.get("/pos/kitchen", headers=_auth(c["pos"]))).json()
    assert sorted((l["quantity"], tuple(l["modifiers"]), l["notes"] or "") for l in kitchen[0]["lines"]) == [
        ("1.000", ("Panas",), ""), ("1.000", ("Panas",), "gelas kertas"), ("2.000", ("Dingin", "Extra shot"), ""),
    ]
