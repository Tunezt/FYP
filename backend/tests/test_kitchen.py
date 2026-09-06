"""M11-T2 — kitchen display with ticket states and a bump action. Every paid
order is a kitchen ticket (a till sale or a settled e-menu ticket); an unpaid
or voided one is not. States move forward only and are append-only events;
`done` bumps the ticket off the board. The guest's ticket page shows the
kitchen's state once paid.

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import create_menu_token, create_token, hash_pin
from app.main import app
from app.models import Business, Item, KitchenEvent, Order, Staff
from app.services.catalog import create_modifier, create_modifier_group, ensure_default_variant
from app.services.kitchen import BOARD_WINDOW_HOURS, KitchenInvalid, board, current_state, set_state
from app.services.orders import OrderLineSpec, PaymentSpec, create_order, void_order
from app.services.pricing import ensure_pricing_settings
from app.services.stock import open_item_stock
from app.services.tickets import place_ticket, settle_ticket

from tests.conftest import seed_books

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")
D = Decimal
NOW = datetime.now(timezone.utc)


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


async def _make_shop(session_factory, name):
    async with session_factory() as s:
        biz = Business(name=name, owner_phone=f"62965{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await seed_books(s, bid)
        await ensure_pricing_settings(s, bid)
        owner = Staff(business_id=bid, name="Bu Ratna", role="owner", pin_hash=hash_pin("1234"))
        sari = Staff(business_id=bid, name="Sari", pin_hash=hash_pin("2345"))
        kopi = Item(business_id=bid, name="Kopi", unit="cup", current_stock=D(50), cost_price=D(8000), sell_price=D(20000), reorder_threshold=D(2))
        roti = Item(business_id=bid, name="Roti", unit="pcs", current_stock=D(50), cost_price=D(6000), sell_price=D(15000), reorder_threshold=D(2))
        s.add_all([owner, sari, kopi, roti])
        await s.flush()
        for it in (kopi, roti):
            await open_item_stock(s, it, unit_cost=it.cost_price)
            await ensure_default_variant(s, it)
        group = await create_modifier_group(s, kopi, name="Gula", selection="single", is_required=False)
        less = await create_modifier(s, group, name="Gula sedikit", price_delta=D(0))
        await s.commit()
        return {"bid": bid, "sari": sari.id, "kopi": kopi.id, "roti": roti.id, "less": less.id,
                "pos": create_token(business_id=str(bid), scope="pos", staff_id=str(sari.id)), "menu": create_menu_token(str(bid))}


@pytest.fixture
async def shop(session_factory):
    c = await _make_shop(session_factory, "Dapur Senja")
    yield c
    async with session_factory() as s:
        row = await s.get(Business, c["bid"])
        if row:
            await s.delete(row)
        await s.commit()


async def _sale(s, c, *, sold_at=None, notes=None, modifier_ids=()):
    created = await create_order(
        s, business_id=c["bid"], staff_id=c["sari"],
        lines=[OrderLineSpec(item_id=c["kopi"], quantity=D(2), notes=notes, modifier_ids=list(modifier_ids)), OrderLineSpec(item_id=c["roti"], quantity=D(1))],
        payments=[PaymentSpec(method="cash", amount=D(55000))], sold_at=sold_at,
    )
    return created.order


async def _menu_ticket(s, c, *, settle=True):
    ticket = await place_ticket(s, business_id=c["bid"], lines=[OrderLineSpec(item_id=c["roti"], quantity=D(3))],
                                order_type="dine_in", table_label="Meja 2", guest_name="Dina", note="cepat ya")
    if settle:
        await settle_ticket(s, business_id=c["bid"], ticket=ticket, staff_id=c["sari"], payments=[PaymentSpec(method="qris", amount=D(45000))])
    return ticket


async def test_paid_orders_are_tickets_and_unpaid_or_voided_are_not(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        sale = await _sale(s, c, notes="tanpa es", modifier_ids=[c["less"]])
        paid_ticket = await _menu_ticket(s, c)
        await _menu_ticket(s, c, settle=False)                      # still waiting for the till
        voided = await _sale(s, c)
        await void_order(s, business_id=c["bid"], order_id=voided.id, staff_id=c["sari"], manager_pin="1234")
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        tickets = await board(s)
        assert [t.order_id for t in tickets] == [sale.id, paid_ticket.id]      # oldest first; no unpaid, no voided
        till, guest = tickets
        assert till.state == guest.state == "new" and till.state_since is None
        assert till.code == f"#{str(sale.id)[-4:].upper()}" and till.source == "pos"
        assert [(l.name, l.quantity, l.modifiers, l.notes) for l in till.lines] == [("Kopi", D(2), ["Gula sedikit"], "tanpa es"), ("Roti", D(1), [], None)]
        assert guest.code.startswith("M-") and guest.table_label == "Meja 2" and guest.guest_name == "Dina" and guest.note == "cepat ya"
        assert [(l.name, l.quantity) for l in guest.lines] == [("Roti", D(3))]


async def test_states_move_forward_only_and_done_bumps_the_ticket_off(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        a = await _sale(s, c)
        b = await _sale(s, c)
        t = await set_state(s, business_id=c["bid"], order_id=a.id, state="preparing", staff_id=c["sari"])
        assert t.state == "preparing" and t.state_since is not None
        assert (await set_state(s, business_id=c["bid"], order_id=a.id, state="preparing", staff_id=c["sari"])).state == "preparing"  # double tap: no-op
        t = await set_state(s, business_id=c["bid"], order_id=a.id, state="ready", staff_id=c["sari"])
        assert t.state == "ready"
        with pytest.raises(KitchenInvalid) as exc:
            await set_state(s, business_id=c["bid"], order_id=a.id, state="preparing", staff_id=c["sari"])
        assert exc.value.code == "transition" and exc.value.current == "ready"
        assert [x.order_id for x in await board(s)] == [a.id, b.id]
        # The bump: done, from ready — and from new directly for the other one.
        assert (await set_state(s, business_id=c["bid"], order_id=a.id, state="done", staff_id=c["sari"])).state == "done"
        assert [x.order_id for x in await board(s)] == [b.id]
        assert (await set_state(s, business_id=c["bid"], order_id=b.id, state="done", staff_id=c["sari"])).state == "done"
        assert await board(s) == []
        with pytest.raises(KitchenInvalid) as exc:
            await set_state(s, business_id=c["bid"], order_id=a.id, state="ready", staff_id=c["sari"])
        assert exc.value.code == "transition" and exc.value.current == "done"
        # History, not state: three appended rows for `a`, in order, none rewritten.
        events = (await s.execute(select(KitchenEvent).where(KitchenEvent.order_id == a.id).order_by(KitchenEvent.created_at))).scalars().all()
        assert [e.state for e in events] == ["preparing", "ready", "done"] and all(e.staff_id == c["sari"] for e in events)
        await s.commit()


async def test_only_paid_orders_can_move(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        waiting = await _menu_ticket(s, c, settle=False)
        with pytest.raises(KitchenInvalid) as exc:
            await set_state(s, business_id=c["bid"], order_id=waiting.id, state="preparing", staff_id=c["sari"])
        assert exc.value.code == "not_paid"
        with pytest.raises(KitchenInvalid) as exc:
            await set_state(s, business_id=c["bid"], order_id=uuid.uuid4(), state="preparing", staff_id=c["sari"])
        assert exc.value.code == "not_found"
        assert (await s.execute(select(func.count(KitchenEvent.id)))).scalar_one() == 0


async def test_the_board_forgets_what_nobody_bumped_yesterday(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        stale = await _sale(s, c, sold_at=NOW - timedelta(hours=BOARD_WINDOW_HOURS + 1))
        fresh = await _sale(s, c, sold_at=NOW - timedelta(hours=BOARD_WINDOW_HOURS - 1))
        ids = [t.order_id for t in await board(s, now=NOW)]
        assert fresh.id in ids and stale.id not in ids


async def test_the_guest_sees_the_kitchens_state_once_paid(client, session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        ticket = await _menu_ticket(s, c, settle=False)
        await s.commit()
    watch = f"/menu/{c['menu']}/orders/{ticket.id}"
    body = (await client.get(watch)).json()
    assert body["status"] == "open" and body["kitchen_state"] is None
    settled = await client.post(f"/pos/tickets/{ticket.id}/settle", headers=_auth(c["pos"]), json={"payments": [{"method": "cash", "amount": "45000"}]})
    assert settled.status_code == 200, settled.text
    assert (await client.get(watch)).json()["kitchen_state"] == "new"
    # The kitchen, through the API: the board lists it, "siap" reaches the guest, the bump clears it.
    kitchen = (await client.get("/pos/kitchen", headers=_auth(c["pos"]))).json()
    assert [k["order_id"] for k in kitchen] == [str(ticket.id)] and kitchen[0]["state"] == "new" and kitchen[0]["table_label"] == "Meja 2"
    moved = await client.post(f"/pos/kitchen/{ticket.id}/state", headers=_auth(c["pos"]), json={"state": "ready"})
    assert moved.status_code == 200 and moved.json()["state"] == "ready" and moved.json()["state_since"] is not None
    assert (await client.get(watch)).json()["kitchen_state"] == "ready"
    back = await client.post(f"/pos/kitchen/{ticket.id}/state", headers=_auth(c["pos"]), json={"state": "preparing"})
    assert back.status_code == 409 and "sudah siap" in back.json()["detail"]
    bumped = await client.post(f"/pos/kitchen/{ticket.id}/state", headers=_auth(c["pos"]), json={"state": "done"})
    assert bumped.status_code == 200 and (await client.get("/pos/kitchen", headers=_auth(c["pos"]))).json() == []
    assert (await client.get(watch)).json()["kitchen_state"] == "done"
    # A menu token is not a kitchen key; an unpaid ticket is refused with a reason.
    assert (await client.get("/pos/kitchen", headers=_auth(c["menu"]))).status_code == 403
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        waiting = await _menu_ticket(s, c, settle=False)
        await s.commit()
    refused = await client.post(f"/pos/kitchen/{waiting.id}/state", headers=_auth(c["pos"]), json={"state": "preparing"})
    assert refused.status_code == 409 and "belum dibayar" in refused.json()["detail"]


async def test_kitchen_events_are_tenant_isolated(session_factory, shop):
    """The policy is on the table (same migration), and it holds: another
    tenant sees none of these rows and cannot write one for this order."""
    c = shop
    other = await _make_shop(session_factory, "Tetangga")
    try:
        async with session_factory() as s:
            await _set_tenant(s, c["bid"])
            sale = await _sale(s, c)
            await set_state(s, business_id=c["bid"], order_id=sale.id, state="preparing", staff_id=c["sari"])
            await s.commit()
        async with session_factory() as s:
            row = (await s.execute(text(
                "select relrowsecurity, relforcerowsecurity from pg_class where relname = 'kitchen_events'"
            ))).one()
            assert row == (True, True)
            names = (await s.execute(text("select policyname from pg_policies where tablename = 'kitchen_events'"))).scalars().all()
            assert names == ["tenant_isolation"]
        async with session_factory() as s:
            await _set_tenant(s, other["bid"])
            assert (await s.execute(select(func.count(KitchenEvent.id)))).scalar_one() == 0
            assert (await current_state(s, sale.id)) == ("new", None)
            s.add(KitchenEvent(business_id=c["bid"], order_id=sale.id, state="done", staff_id=None))
            with pytest.raises(Exception):
                await s.flush()
            await s.rollback()
        async with session_factory() as s:
            await _set_tenant(s, c["bid"])
            assert (await current_state(s, sale.id))[0] == "preparing"
    finally:
        async with session_factory() as s:
            row = await s.get(Business, other["bid"])
            if row:
                await s.delete(row)
            await s.commit()
