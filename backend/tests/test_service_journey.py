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


# ── 3-6, 9. Unpaid orders are held on the server; payment is the one posting ──


async def _count(s, model, *where):
    return (await s.execute(select(func.count(model.id)).where(*where))).scalar_one()


async def _footprint(s, order_id):
    from app.models import JournalEntry, KitchenEvent, Payment, StockMovement

    line_ids = select(OrderLine.id).where(OrderLine.order_id == order_id)
    return {
        "lines": await _count(s, OrderLine, OrderLine.order_id == order_id),
        "payments": await _count(s, Payment, Payment.order_id == order_id),
        "journal": await _count(s, JournalEntry, JournalEntry.source_type == "order", JournalEntry.source_id == order_id),
        "movements": await _count(s, StockMovement, StockMovement.source_id.in_(line_ids)),
        "kitchen_events": await _count(s, KitchenEvent, KitchenEvent.order_id == order_id),
    }


def _ref():
    return uuid.uuid4().hex[:20]


async def test_3_a_held_order_survives_switching_customers_and_a_refresh(client, session_factory, cafe):
    c = cafe
    first = await client.post("/pos/drafts", headers=_auth(c["pos"]), json={
        "lines": [_line(c, variant="large", mods=["dingin"], notes="es sedikit")], "guest_name": "Mbak Rina", "client_ref": _ref()})
    assert first.status_code == 201, first.text
    # The next customer is served while the first finds her wallet.
    second = await client.post("/pos/drafts", headers=_auth(c["pos"]), json={"lines": [_line(c, item="roti", qty=2)], "order_type": "dine_in", "table_label": "Meja 3"})
    assert second.status_code == 201, second.text
    # A refresh is a new request with nothing but the token: both are still there, in arrival order.
    active = (await client.get("/pos/active-orders", headers=_auth(c["pos"]))).json()
    assert [a["id"] for a in active] == [first.json()["id"], second.json()["id"]]
    held = active[0]
    assert held["payment"] == "unpaid" and held["prep"] is None and held["source"] == "pos" and held["guest_name"] == "Mbak Rina"
    assert held["staff_name"] == "Sari" and D(held["total"]) == D(22000) and held["rev"] == 0
    line = held["lines"][0]
    # Everything needed to reopen the line with the same answers.
    assert (line["size"], line["modifiers"], line["notes"]) == ("Large", ["Dingin"], "es sedikit")
    assert line["variant_id"] == str(c["large"]) and line["modifier_ids"] == [str(c["dingin"])]
    # Nothing was sold: no stock taken, no posting, nothing for the kitchen.
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert await _footprint(s, uuid.UUID(held["id"])) == {"lines": 0, "payments": 0, "journal": 0, "movements": 0, "kitchen_events": 0}
        assert (await s.get(Item, c["americano"])).current_stock == D(40)
    assert (await client.get("/pos/kitchen", headers=_auth(c["pos"]))).json() == []
    # A held draft is not a receipt, and cancelling one leaves no "voided sale" behind.
    second_id = second.json()["id"]
    cancel = await client.post(f"/pos/tickets/{second_id}/cancel", headers=_auth(c["pos"]), json={"reason": "tidak jadi"})
    assert cancel.status_code == 200 and cancel.json()["status"] == "voided"
    assert (await client.get("/pos/orders", headers=_auth(c["pos"]))).json()["rows"] == []
    assert [a["id"] for a in (await client.get("/pos/active-orders", headers=_auth(c["pos"]))).json()] == [first.json()["id"]]


async def test_4_a_qr_order_reaches_the_cashier_and_is_edited_before_settlement(client, session_factory, cafe):
    c = cafe
    placed = await client.post(f"/menu/{c['menu']}/orders", json={
        "lines": [_line(c, variant="standar", mods=["panas"])], "order_type": "takeaway", "guest_name": "Dimas", "client_ref": _ref()})
    assert placed.status_code == 201, placed.text
    tid = placed.json()["id"]
    active = (await client.get("/pos/active-orders", headers=_auth(c["pos"]))).json()
    assert [(a["id"], a["source"], a["payment"], a["code"]) for a in active] == [(tid, "menu", "unpaid", placed.json()["code"])]
    # At the counter Dimas makes it a large, iced, and adds a roti.
    edited = await client.put(f"/pos/open-orders/{tid}", headers=_auth(c["pos"]), json={
        "rev": 0, "lines": [_line(c, variant="large", mods=["dingin"]), _line(c, item="roti")]})
    assert edited.status_code == 200, edited.text
    assert edited.json()["rev"] == 1 and D(edited.json()["total"]) == D(37000)
    # A second tablet still holding revision 0 cannot overwrite it, nor pay for the old version.
    stale = await client.put(f"/pos/open-orders/{tid}", headers=_auth(c["pos"]), json={"rev": 0, "lines": [_line(c, item="roti")]})
    assert stale.status_code == 409 and "diubah di perangkat lain" in stale.json()["detail"]
    stale_pay = await client.post(f"/pos/tickets/{tid}/settle", headers=_auth(c["pos"]), json={"payments": _cash(18000), "rev": 0})
    assert stale_pay.status_code == 409
    # The guest's own page shows the edited order, not the one they sent.
    watched = (await client.get(f"/menu/{c['menu']}/orders/{tid}")).json()
    assert D(watched["total"]) == D(37000) and len(watched["lines"]) == 2
    paid = await client.post(f"/pos/tickets/{tid}/settle", headers=_auth(c["pos"]), json={"payments": _cash(37000), "rev": 1, "client_ref": _ref()})
    assert paid.status_code == 200, paid.text
    assert D(paid.json()["total"]) == D(37000) and sorted(l["item_name"] for l in paid.json()["lines"]) == ["Americano", "Roti"]
    # Paid: it cannot be edited any more.
    late = await client.put(f"/pos/open-orders/{tid}", headers=_auth(c["pos"]), json={"rev": 1, "lines": [_line(c, item="roti")]})
    assert late.status_code == 409 and "sudah dibayar" in late.json()["detail"]
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        row = await s.get(Order, uuid.UUID(tid))
        assert [r["rev"] for r in row.cart["revisions"]] == [0] and row.cart["revisions"][0]["staff_id"] == str(c["sari"])


async def test_5_an_unpaid_order_never_reaches_the_kitchen(client, session_factory, cafe):
    c = cafe
    draft = (await client.post("/pos/drafts", headers=_auth(c["pos"]), json={"lines": [_line(c, item="roti")]})).json()
    ticket = (await client.post(f"/menu/{c['menu']}/orders", json={"lines": [_line(c, item="roti")], "order_type": "takeaway"})).json()
    assert (await client.get("/pos/kitchen", headers=_auth(c["pos"]))).json() == []
    for oid in (draft["id"], ticket["id"]):
        refused = await client.post(f"/pos/kitchen/{oid}/state", headers=_auth(c["pos"]), json={"state": "preparing"})
        assert refused.status_code == 409 and "belum dibayar" in refused.json()["detail"]
    active = (await client.get("/pos/active-orders", headers=_auth(c["pos"]))).json()
    assert {a["payment"] for a in active} == {"unpaid"} and {a["prep"] for a in active} == {None}


async def test_6_payment_is_exactly_one_posting_and_one_kitchen_ticket(client, session_factory, cafe):
    c = cafe
    draft = (await client.post("/pos/drafts", headers=_auth(c["pos"]), json={
        "lines": [_line(c, variant="large", mods=["panas", "shot"], qty=2), _line(c, item="roti")]})).json()
    ref = _ref()
    body = {"payments": _cash(2 * 27000 + 15000), "rev": 0, "client_ref": ref}
    paid = await client.post(f"/pos/tickets/{draft['id']}/settle", headers=_auth(c["pos"]), json=body)
    assert paid.status_code == 200, paid.text
    # The same tap arriving again is the same payment, returned, not repeated.
    again = await client.post(f"/pos/tickets/{draft['id']}/settle", headers=_auth(c["pos"]), json=body)
    assert again.status_code == 200 and again.json()["id"] == paid.json()["id"] and again.json()["total"] == paid.json()["total"]
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert await _footprint(s, uuid.UUID(draft["id"])) == {"lines": 2, "payments": 1, "journal": 1, "movements": 2, "kitchen_events": 0}
        assert (await s.get(Item, c["americano"])).current_stock == D(38) and (await s.get(Item, c["roti"])).current_stock == D(29)
    kitchen = (await client.get("/pos/kitchen", headers=_auth(c["pos"]))).json()
    assert [k["order_id"] for k in kitchen] == [draft["id"]] and kitchen[0]["state"] == "new"
    active = (await client.get("/pos/active-orders", headers=_auth(c["pos"]))).json()
    assert [(a["id"], a["payment"], a["prep"], a["is_estimate"]) for a in active] == [(draft["id"], "paid", "new", False)]


async def test_9_retries_double_taps_and_two_devices_do_not_duplicate(client, session_factory, cafe):
    import asyncio

    c = cafe
    # The guest's phone resends after a dropped connection: one ticket.
    ref = _ref()
    body = {"lines": [_line(c, item="roti")], "order_type": "takeaway", "client_ref": ref}
    a, b = await asyncio.gather(client.post(f"/menu/{c['menu']}/orders", json=body), client.post(f"/menu/{c['menu']}/orders", json=body))
    assert a.status_code == b.status_code == 201 and a.json()["id"] == b.json()["id"]
    # A double tap on "Bayar" at the till: one sale, one stock movement.
    sale = {"lines": [_line(c, item="roti")], "payments": _cash(15000), "client_ref": _ref()}
    x, y = await asyncio.gather(client.post("/pos/orders", headers=_auth(c["pos"]), json=sale), client.post("/pos/orders", headers=_auth(c["pos"]), json=sale))
    assert {x.status_code, y.status_code} <= {200, 201} and x.json()["id"] == y.json()["id"]
    # Two tablets pay the same QR order at once, each with its own tap: one wins, one is told.
    tid = a.json()["id"]
    p1, p2 = await asyncio.gather(
        client.post(f"/pos/tickets/{tid}/settle", headers=_auth(c["pos"]), json={"payments": _cash(15000), "client_ref": _ref()}),
        client.post(f"/pos/tickets/{tid}/settle", headers=_auth(c["pos"]), json={"payments": _cash(15000), "client_ref": _ref()}),
    )
    assert sorted([p1.status_code, p2.status_code]) == [200, 409]
    # Two tablets edit the same unpaid draft from the same revision: one edit lands.
    draft = (await client.post("/pos/drafts", headers=_auth(c["pos"]), json={"lines": [_line(c, item="roti")]})).json()
    e1, e2 = await asyncio.gather(
        client.put(f"/pos/open-orders/{draft['id']}", headers=_auth(c["pos"]), json={"rev": 0, "lines": [_line(c, item="roti", qty=2)]}),
        client.put(f"/pos/open-orders/{draft['id']}", headers=_auth(c["pos"]), json={"rev": 0, "lines": [_line(c, item="roti", qty=3)]}),
    )
    assert sorted([e1.status_code, e2.status_code]) == [200, 409]
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        from app.models import Payment

        assert await _count(s, Order, Order.client_ref == ref) == 1
        assert await _count(s, Order, Order.source == "menu") == 1
        assert await _footprint(s, uuid.UUID(x.json()["id"])) == {"lines": 1, "payments": 1, "journal": 1, "movements": 1, "kitchen_events": 0}
        assert await _count(s, Payment, Payment.order_id == uuid.UUID(tid)) == 1
        assert (await s.get(Item, c["roti"])).current_stock == D(28)


# ── 7. After payment, "Tambah pesanan" is its own purchase ───────────────────


async def test_7_a_paid_order_addition_charges_only_the_new_items_and_cooks_only_them(client, session_factory, cafe):
    c = cafe
    first = await client.post("/pos/orders", headers=_auth(c["pos"]), json={
        "lines": [_line(c, variant="large", mods=["dingin"], qty=2)], "payments": _cash(44000), "order_type": "dine_in", "table_label": "Meja 2"})
    assert first.status_code == 201, first.text
    fid = first.json()["id"]
    # The kitchen has already started on it.
    assert (await client.post(f"/pos/kitchen/{fid}/state", headers=_auth(c["pos"]), json={"state": "preparing"})).status_code == 200
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        before = await _footprint(s, uuid.UUID(fid))
        original = await s.get(Order, uuid.UUID(fid))
        original_total = D(original.total)

    # The customer comes back for a roti.
    extra = await client.post("/pos/orders", headers=_auth(c["pos"]), json={
        "lines": [_line(c, item="roti")], "payments": _cash(15000), "parent_order_id": fid, "client_ref": _ref()})
    assert extra.status_code == 201, extra.text
    eid = extra.json()["id"]
    assert D(extra.json()["total"]) == D(15000) and extra.json()["parent_order_id"] == fid
    assert extra.json()["parent_number"] == fid[-8:].upper()

    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        # The original receipt is exactly as it was: same total, lines, payment, posting, progress.
        assert await _footprint(s, uuid.UUID(fid)) == before
        assert D((await s.get(Order, uuid.UUID(fid))).total) == original_total
        assert await _footprint(s, uuid.UUID(eid)) == {"lines": 1, "payments": 1, "journal": 1, "movements": 1, "kitchen_events": 0}

    kitchen = {k["order_id"]: k for k in (await client.get("/pos/kitchen", headers=_auth(c["pos"]))).json()}
    assert set(kitchen) == {fid, eid}
    assert kitchen[fid]["state"] == "preparing" and [l["quantity"] for l in kitchen[fid]["lines"]] == ["2.000"]
    assert kitchen[eid]["state"] == "new" and [l["name"] for l in kitchen[eid]["lines"]] == ["Roti"]
    assert kitchen[eid]["parent_code"] == kitchen[fid]["code"]
    receipt = (await client.get(f"/pos/orders/{eid}/receipt", headers=_auth(c["pos"]))).json()
    assert receipt["parent_number"] == fid[-8:].upper() and D(receipt["total"]) == D(15000)
    active = {a["id"]: a for a in (await client.get("/pos/active-orders", headers=_auth(c["pos"]))).json()}
    assert active[eid]["parent_code"] == active[fid]["code"] and active[fid]["parent_code"] is None

    # An addition to the addition still belongs to the original.
    third = await client.post("/pos/orders", headers=_auth(c["pos"]), json={
        "lines": [_line(c, item="roti")], "payments": _cash(15000), "parent_order_id": eid})
    assert third.status_code == 201 and third.json()["parent_order_id"] == fid

    # Before payment there is nothing to add *to*: extend the unpaid order instead.
    draft = (await client.post("/pos/drafts", headers=_auth(c["pos"]), json={"lines": [_line(c, item="roti")]})).json()
    refused = await client.post("/pos/orders", headers=_auth(c["pos"]), json={
        "lines": [_line(c, item="roti")], "payments": _cash(15000), "parent_order_id": draft["id"]})
    assert refused.status_code == 409 and "belum dibayar" in refused.json()["detail"]
