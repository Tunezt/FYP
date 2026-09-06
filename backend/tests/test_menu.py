"""M11-T1 — QR e-menu at /menu/{token}, ordering into the same order queue as
the POS. A guest's order is an *open* row in the till's own `orders` table:
placing it takes nothing (no stock, no money, no journal, no lines); settling
it at the till is exactly a sale, written on that same row; cancelling keeps
the row. The menu token opens the menu and nothing else.

The app is driven in-process through httpx's ASGI transport on the test's own
event loop (the sync TestClient spins a loop per request, which strands the
app's pooled asyncpg connections); the app engine is disposed after each test.

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import os
import uuid
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import create_menu_token, create_pairing_token, create_token, hash_pin
from app.main import app
from app.metrics.registry import compute
from app.models import Business, Item, JournalEntry, JournalLine, Order, OrderLine, Payment, Staff, StockMovement
from app.services.catalog import ensure_default_variant
from app.services.pricing import ensure_pricing_settings
from app.services.stock import open_item_stock

from tests.conftest import seed_books

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")
D = Decimal
BID0 = "11111111-1111-1111-1111-111111111111"


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


async def _make_shop(session_factory, name, items):
    async with session_factory() as s:
        biz = Business(name=name, owner_phone=f"62964{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await seed_books(s, bid)
        await ensure_pricing_settings(s, bid)
        owner = Staff(business_id=bid, name="Bu Ratna", role="owner", pin_hash=hash_pin("1234"))
        sari = Staff(business_id=bid, name="Sari", pin_hash=hash_pin("2345"))
        s.add_all([owner, sari])
        rows = {}
        for item_name, unit, stock, cost, price in items:
            it = Item(business_id=bid, name=item_name, unit=unit, current_stock=D(stock), cost_price=D(cost), sell_price=D(price), reorder_threshold=D(2))
            s.add(it)
            await s.flush()
            if D(stock) > 0:
                await open_item_stock(s, it, unit_cost=it.cost_price)
            await ensure_default_variant(s, it)
            rows[item_name] = it.id
        await s.commit()
        return {"bid": bid, "owner": owner.id, "sari": sari.id, **rows}


@pytest.fixture
async def shop(session_factory):
    """A café: Kopi 20.000 (20 in stock), Teh 8.000 (10), Roti 15.000 (none left)."""
    c = await _make_shop(session_factory, "Warung QR", [("Kopi", "cup", 20, 8000, 20000), ("Teh", "cup", 10, 3000, 8000), ("Roti", "pcs", 0, 6000, 15000), ("Gula", "kg", 5, 12000, 0)])
    c["token"] = create_menu_token(str(c["bid"]))
    c["pos"] = create_token(business_id=str(c["bid"]), scope="pos", staff_id=str(c["sari"]))
    yield c
    async with session_factory() as s:
        row = await s.get(Business, c["bid"])
        if row:
            await s.delete(row)
        await s.commit()


@pytest.fixture
async def other_shop(session_factory):
    c = await _make_shop(session_factory, "Tetangga", [("Bakso", "porsi", 30, 9000, 18000)])
    yield c
    async with session_factory() as s:
        row = await s.get(Business, c["bid"])
        if row:
            await s.delete(row)
        await s.commit()


def _cart(c, kopi=2, teh=1):
    return {"lines": [{"item_id": str(c["Kopi"]), "quantity": str(kopi)}, {"item_id": str(c["Teh"]), "quantity": str(teh)}],
            "order_type": "dine_in", "table_label": "Meja 4", "guest_name": "Dina"}


def _cash(amount):
    return {"payments": [{"method": "cash", "amount": str(amount)}]}


async def _footprint(s, c, order_id):
    """Everything a sale leaves behind, counted for one order id."""
    lines = (await s.execute(select(func.count(OrderLine.id)).where(OrderLine.order_id == order_id))).scalar_one()
    pays = (await s.execute(select(func.count(Payment.id)).where(Payment.order_id == order_id))).scalar_one()
    entries = (await s.execute(select(func.count(JournalEntry.id)).where(JournalEntry.source_type == "order", JournalEntry.source_id == order_id))).scalar_one()
    moves = (await s.execute(
        select(func.count(StockMovement.id)).where(StockMovement.source_id.in_(select(OrderLine.id).where(OrderLine.order_id == order_id)))
    )).scalar_one()
    kopi = Decimal((await s.get(Item, c["Kopi"])).current_stock)
    teh = Decimal((await s.get(Item, c["Teh"])).current_stock)
    return {"lines": int(lines), "payments": int(pays), "journal_entries": int(entries), "movements": int(moves), "kopi": kopi, "teh": teh}


# ── the token ────────────────────────────────────────────────────────────────


async def test_the_menu_token_opens_the_menu_and_nothing_else(client):
    # Garbage and the *other* tokens are not a menu.
    bad = await client.get("/menu/not-a-jwt")
    assert bad.status_code == 401 and "Tautan menu" in bad.json()["detail"]
    for wrong in (create_pairing_token(BID0), create_token(business_id=BID0, scope="pos", staff_id=BID0), create_token(business_id=BID0, scope="owner")):
        assert (await client.get(f"/menu/{wrong}")).status_code == 401
    # And the menu token is not a key to the till or the dashboard.
    menu = create_menu_token(BID0)
    for method, path in (("GET", "/pos/items"), ("GET", "/pos/tickets"), ("GET", "/api/overview"), ("POST", "/auth/menu-link")):
        resp = await client.request(method, path, headers=_auth(menu), json={} if method == "POST" else None)
        assert resp.status_code == 403, f"{method} {path} → {resp.status_code}"


async def test_the_owner_mints_the_menu_link(client):
    resp = await client.post("/auth/menu-link", headers=_auth(create_token(business_id=BID0, scope="owner")))
    assert resp.status_code == 200
    body = resp.json()
    assert body["menu_path"] == f"/menu/{body['menu_token']}"
    assert (await client.get(body["menu_path"])).status_code == 404     # a valid token; no such business here


# ── the menu ─────────────────────────────────────────────────────────────────


async def test_the_menu_shows_this_shops_items_without_stock_figures(client, shop, other_shop):
    body = (await client.get(f"/menu/{shop['token']}")).json()
    assert body["business_name"] == "Warung QR"
    by_name = {i["name"]: i for i in body["items"]}
    assert set(by_name) == {"Kopi", "Teh", "Roti"}            # never the neighbour's Bakso, never the Gula ingredient (no selling price)
    assert by_name["Kopi"]["available"] and not by_name["Roti"]["available"]
    assert "current_stock" not in by_name["Kopi"] and "cost_price" not in by_name["Kopi"]
    assert by_name["Kopi"]["variants"][0]["is_default"] is True


# ── placing ──────────────────────────────────────────────────────────────────


async def test_placing_a_ticket_writes_one_open_row_and_takes_nothing(client, session_factory, shop):
    c = shop
    resp = await client.post(f"/menu/{c['token']}/orders", json=_cart(c))
    assert resp.status_code == 201, resp.text
    t = resp.json()
    assert t["status"] == "open" and t["is_estimate"] is True and t["code"].startswith("M-")
    assert t["table_label"] == "Meja 4" and t["guest_name"] == "Dina" and t["order_type"] == "dine_in"
    assert [(l["name"], l["quantity"], l["line_total"]) for l in t["lines"]] == [("Kopi", "2", "40000.00"), ("Teh", "1", "8000.00")]
    assert Decimal(t["subtotal"]) == D(48000) and Decimal(t["total"]) == D(48000)   # no tax/service configured
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        row = await s.get(Order, uuid.UUID(t["id"]))
        assert row.status == "open" and row.source == "menu" and row.staff_id is None
        assert row.cart["lines"][0]["item_name"] == "Kopi" and row.cart["lines"][0]["base_price"] == "20000.00"
        fp = await _footprint(s, c, row.id)
        assert fp == {"lines": 0, "payments": 0, "journal_entries": 0, "movements": 0, "kopi": D(20), "teh": D(10)}
    # The guest can watch it — theirs, not a made-up id.
    assert (await client.get(f"/menu/{c['token']}/orders/{t['id']}")).json()["status"] == "open"
    assert (await client.get(f"/menu/{c['token']}/orders/{uuid.uuid4()}")).status_code == 404


async def test_an_item_that_is_out_is_refused_at_the_menu(client, shop):
    c = shop
    resp = await client.post(f"/menu/{c['token']}/orders", json={"lines": [{"item_id": str(c["Roti"]), "quantity": "1"}]})
    assert resp.status_code == 409 and "Roti" in resp.json()["detail"]
    resp = await client.post(f"/menu/{c['token']}/orders", json={"lines": [{"item_id": str(c["Kopi"]), "quantity": "21"}]})
    assert resp.status_code == 409


async def test_an_open_ticket_is_not_a_sale_to_the_registry(client, session_factory, shop):
    c = shop

    async def figures():
        async with session_factory() as s:
            await _set_tenant(s, c["bid"])
            biz = await s.get(Business, c["bid"])
            return tuple([(await compute(s, biz, m, period="today")).value for m in ("revenue", "transaction_count")])

    before = await figures()
    assert (await client.post(f"/menu/{c['token']}/orders", json=_cart(c))).status_code == 201
    assert await figures() == before


async def test_the_queue_is_capped(client, shop, monkeypatch):
    import app.services.tickets as tickets

    monkeypatch.setattr(tickets, "MAX_OPEN_TICKETS", 2)
    c = shop
    assert (await client.post(f"/menu/{c['token']}/orders", json=_cart(c))).status_code == 201
    assert (await client.post(f"/menu/{c['token']}/orders", json=_cart(c))).status_code == 201
    resp = await client.post(f"/menu/{c['token']}/orders", json=_cart(c))
    assert resp.status_code == 429 and "kasir" in resp.json()["detail"]


# ── the till ─────────────────────────────────────────────────────────────────


async def test_settling_at_the_till_is_exactly_a_sale_on_the_same_row(client, session_factory, shop):
    c = shop
    ticket = (await client.post(f"/menu/{c['token']}/orders", json=_cart(c))).json()
    queue = (await client.get("/pos/tickets", headers=_auth(c["pos"]))).json()
    assert [q["id"] for q in queue] == [ticket["id"]] and queue[0]["table_label"] == "Meja 4"

    settled = await client.post(f"/pos/tickets/{ticket['id']}/settle", headers=_auth(c["pos"]), json=_cash(48000))
    assert settled.status_code == 200, settled.text
    body = settled.json()
    assert body["id"] == ticket["id"] and Decimal(body["total"]) == D(48000)
    assert [(l["item_name"], l["remaining_stock"]) for l in body["lines"]] == [("Kopi", "18.000"), ("Teh", "9.000")]

    # The same cart rung up at the till directly, for comparison.
    direct = (await client.post("/pos/orders", headers=_auth(c["pos"]), json={**_cart(c), **_cash(48000)})).json()

    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        row = await s.get(Order, uuid.UUID(ticket["id"]))
        assert row.status == "completed" and row.source == "menu" and row.staff_id == c["sari"] and row.order_type == "dine_in"
        assert row.cart["lines"][0]["item_name"] == "Kopi"          # what the guest asked for is kept
        fp_ticket = await _footprint(s, c, row.id)
        fp_direct = await _footprint(s, c, uuid.UUID(direct["id"]))
        assert fp_ticket["kopi"] == D(16) and fp_ticket["teh"] == D(8)
        for k in ("lines", "payments", "journal_entries", "movements"):
            assert fp_ticket[k] == fp_direct[k] > 0, (k, fp_ticket, fp_direct)
        costs = (await s.execute(select(OrderLine.unit_cost_at_sale).where(OrderLine.order_id == row.id).order_by(OrderLine.unit_price.desc()))).scalars().all()
        assert [Decimal(x) for x in costs] == [D(8000), D(3000)]

        # The books: the ticket's entry balances and equals the direct sale's.
        async def sides(order_id):
            return (await s.execute(
                select(func.sum(JournalLine.debit), func.sum(JournalLine.credit)).join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
                .where(JournalEntry.source_type == "order", JournalEntry.source_id == order_id)
            )).one()

        dt, ct = await sides(row.id)
        dd, cd = await sides(uuid.UUID(direct["id"]))
        assert dt == ct == dd == cd and dt > 0
        biz = await s.get(Business, c["bid"])
        assert (await compute(s, biz, "revenue", period="today")).value == D(96000)
        assert (await compute(s, biz, "transaction_count", period="today")).value == 2

    assert (await client.get("/pos/tickets", headers=_auth(c["pos"]))).json() == []
    again = await client.post(f"/pos/tickets/{ticket['id']}/settle", headers=_auth(c["pos"]), json=_cash(48000))
    assert again.status_code == 409 and "sudah dibayar" in again.json()["detail"]
    watched = (await client.get(f"/menu/{c['token']}/orders/{ticket['id']}")).json()
    assert watched["status"] == "completed" and watched["is_estimate"] is False


async def test_a_settlement_that_fails_leaves_the_ticket_open_and_untouched(client, session_factory, shop):
    c = shop
    ticket = (await client.post(f"/menu/{c['token']}/orders", json=_cart(c, kopi=5))).json()
    # Payments that do not add up: refused, and the claim on the ticket rolls back with it.
    wrong = await client.post(f"/pos/tickets/{ticket['id']}/settle", headers=_auth(c["pos"]), json=_cash(10))
    assert wrong.status_code == 422 and "tidak sama dengan total" in wrong.json()["detail"]
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        (await s.get(Item, c["Kopi"])).current_stock = D(3)       # sold out under the guest meanwhile
        await s.commit()
    resp = await client.post(f"/pos/tickets/{ticket['id']}/settle", headers=_auth(c["pos"]), json=_cash(108000))
    assert resp.status_code == 409 and "Stok tidak cukup" in resp.json()["detail"]
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        row = await s.get(Order, uuid.UUID(ticket["id"]))
        assert row.status == "open"                                  # the claim rolled back with the sale
        fp = await _footprint(s, c, row.id)
        assert fp == {"lines": 0, "payments": 0, "journal_entries": 0, "movements": 0, "kopi": D(3), "teh": D(10)}
    assert [q["id"] for q in (await client.get("/pos/tickets", headers=_auth(c["pos"]))).json()] == [ticket["id"]]


async def test_cancelling_keeps_the_row_and_says_why(client, session_factory, shop):
    c = shop
    ticket = (await client.post(f"/menu/{c['token']}/orders", json=_cart(c))).json()
    resp = await client.post(f"/pos/tickets/{ticket['id']}/cancel", headers=_auth(c["pos"]), json={"reason": "kopi habis"})
    assert resp.status_code == 200 and resp.json()["status"] == "voided"
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        row = await s.get(Order, uuid.UUID(ticket["id"]))
        assert row.status == "voided" and row.cart["cancelled"]["reason"] == "kopi habis" and row.cart["cancelled"]["staff_id"] == str(c["sari"])
        assert row.cart["lines"][0]["item_name"] == "Kopi"
        biz = await s.get(Business, c["bid"])
        assert (await compute(s, biz, "transaction_count", period="today")).value == 0
    assert (await client.get("/pos/tickets", headers=_auth(c["pos"]))).json() == []
    late = await client.post(f"/pos/tickets/{ticket['id']}/settle", headers=_auth(c["pos"]), json=_cash(48000))
    assert late.status_code == 409 and "dibatalkan" in late.json()["detail"]
    assert (await client.get(f"/menu/{c['token']}/orders/{ticket['id']}")).json()["status"] == "voided"
    # A POS order that is not a ticket is not in this queue at all.
    direct = (await client.post("/pos/orders", headers=_auth(c["pos"]), json={**_cart(c), **_cash(48000)})).json()
    assert (await client.post(f"/pos/tickets/{direct['id']}/cancel", headers=_auth(c["pos"]), json={})).status_code == 404
