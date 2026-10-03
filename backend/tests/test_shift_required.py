"""till-4 — a shift must be open before selling (the owner's decision, 1 Oct 2026).

The cashier counts the opening cash into the drawer (*Buka shift*) before the
first sale. The till blocks its sell screen behind that sheet, and the server
enforces it too: with `businesses.require_shift` on, money taken at the till
without the cashier's open shift is refused with a message saying what to do,
and nothing is written. A café without the setting sells as M7-T1 always
allowed (`shift_id` NULL): that is what every older test exercises, unchanged.
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
from app.models import Business, CashMovement, Item, Order, Payment, Staff
from app.services.catalog import ensure_default_variant
from app.services.stock import open_item_stock
from app.services.suppliers import create_supplier

from tests.conftest import seed_books

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")


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


@pytest.fixture
async def client():
    from app.core.db import engine as app_engine

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await app_engine.dispose()


async def _set_tenant(session, business_id):
    await session.execute(text("select set_config('app.current_business_id', :bid, true)"), {"bid": str(business_id)})


async def _cafe(session_factory, *, require_shift: bool):
    async with session_factory() as s:
        biz = Business(name="Shift Wajib", owner_phone=f"62987{uuid.uuid4().hex[:9]}", require_shift=require_shift)
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await seed_books(s, bid)
        sari = Staff(business_id=bid, name="Sari", pin_hash=hash_pin("2345"))
        kopi = Item(business_id=bid, name="Kopi", unit="cup", current_stock=Decimal(20), cost_price=Decimal(8000), sell_price=Decimal(20000))
        s.add_all([sari, kopi])
        await s.flush()
        await open_item_stock(s, kopi, unit_cost=kopi.cost_price)
        await ensure_default_variant(s, kopi)
        supplier = await create_supplier(s, bid, name="Toko Kopi")
        await s.commit()
        return {
            "bid": bid, "kopi": kopi.id, "supplier": supplier.id,
            "pos": {"Authorization": f"Bearer {create_token(business_id=str(bid), scope='pos', staff_id=str(sari.id))}"},
            "menu": create_menu_token(str(bid)),
        }


@pytest.fixture
async def strict(session_factory):
    ids = await _cafe(session_factory, require_shift=True)
    yield ids
    async with session_factory() as s:
        row = await s.get(Business, ids["bid"])
        if row:
            await s.delete(row)
        await s.commit()


def _sale(c):
    return {"lines": [{"item_id": str(c["kopi"]), "quantity": 1}], "payments": [{"method": "cash", "amount": 20000}],
            "order_type": "takeaway"}


async def _count(session_factory, c, model):
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        return (await s.execute(select(func.count(model.id)))).scalar_one()


async def test_the_till_is_told_the_rule(client, strict):
    resp = await client.get("/pos/config", headers=strict["pos"])
    assert resp.status_code == 200 and resp.json()["require_shift"] is True


async def test_no_money_is_taken_before_the_shift_is_opened(client, session_factory, strict):
    c = strict
    refused = await client.post("/pos/orders", headers=c["pos"], json=_sale(c))
    assert refused.status_code == 409 and refused.json()["detail"].startswith("Shift belum dibuka")
    legacy = await client.post("/pos/sales", headers=c["pos"], json={"item_id": str(c["kopi"]), "quantity": 1})
    assert legacy.status_code == 409
    petty = await client.post("/pos/cash", headers=c["pos"], json={"kind": "petty_cash", "amount": 5000, "reason": "es batu"})
    assert petty.status_code == 409
    assert await _count(session_factory, c, Order) == 0
    assert await _count(session_factory, c, Payment) == 0
    assert await _count(session_factory, c, CashMovement) == 0
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert (await s.get(Item, c["kopi"])).current_stock == Decimal("20.000")      # no stock moved

    # A supplier paid by bank transfer never touches the drawer, so it needs no shift.
    transfer = await client.post("/pos/cash", headers=c["pos"], json={
        "kind": "supplier_payment", "via": "transfer", "amount": 30000, "reason": "bayar nota", "supplier_id": str(c["supplier"]),
    })
    assert transfer.status_code == 201, transfer.text

    opened = await client.post("/pos/shift/open", headers=c["pos"], json={"opening_float": 100000})
    assert opened.status_code == 201
    sold = await client.post("/pos/orders", headers=c["pos"], json=_sale(c))
    assert sold.status_code == 201, sold.text
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        order = await s.get(Order, uuid.UUID(sold.json()["id"]))
        assert order.shift_id == uuid.UUID(opened.json()["id"])               # attributed to the shift just opened

    closed = await client.post("/pos/shift/close", headers=c["pos"], json={"counted_cash": 120000})
    assert closed.status_code == 200 and closed.json()["variance"] == "0.00"
    again = await client.post("/pos/orders", headers=c["pos"], json=_sale(c))
    assert again.status_code == 409                                          # closed again: the next shift first


async def test_a_qr_order_can_be_placed_but_not_paid_without_a_shift(client, session_factory, strict):
    """The guest may order from the table at any time; the cashier takes the
    money only inside a shift."""
    c = strict
    placed = await client.post(f"/menu/{c['menu']}/orders", json={
        "lines": [{"item_id": str(c["kopi"]), "variant_id": None, "modifier_ids": [], "quantity": 1}],
        "order_type": "takeaway", "client_ref": uuid.uuid4().hex,
    })
    assert placed.status_code == 201, placed.text
    tid = placed.json()["id"]
    pay = {"payments": [{"method": "cash", "amount": 20000}], "rev": 0}
    refused = await client.post(f"/pos/tickets/{tid}/settle", headers=c["pos"], json=pay)
    assert refused.status_code == 409 and "Shift belum dibuka" in refused.json()["detail"]
    await client.post("/pos/shift/open", headers=c["pos"], json={"opening_float": 0})
    paid = await client.post(f"/pos/tickets/{tid}/settle", headers=c["pos"], json=pay)
    assert paid.status_code == 200, paid.text


async def test_a_cafe_without_the_rule_sells_as_before(client, session_factory):
    c = await _cafe(session_factory, require_shift=False)
    try:
        assert (await client.get("/pos/config", headers=c["pos"])).json()["require_shift"] is False
        sold = await client.post("/pos/orders", headers=c["pos"], json=_sale(c))
        assert sold.status_code == 201
        async with session_factory() as s:
            await _set_tenant(s, c["bid"])
            assert (await s.get(Order, uuid.UUID(sold.json()["id"]))).shift_id is None
    finally:
        async with session_factory() as s:
            row = await s.get(Business, c["bid"])
            if row:
                await s.delete(row)
            await s.commit()


async def test_new_cafes_start_with_the_rule_on(session_factory):
    from app.bootstrap import bootstrap

    phone = f"0812{uuid.uuid4().int % 10**8:08d}"
    made = await bootstrap(name="Kafe Baru", owner_name="Pemilik", phone=phone, pin="1234")
    try:
        async with session_factory() as s:
            assert (await s.get(Business, made.business_id)).require_shift is True
    finally:
        async with session_factory() as s:
            row = await s.get(Business, made.business_id)
            if row:
                await s.delete(row)
            await s.commit()
