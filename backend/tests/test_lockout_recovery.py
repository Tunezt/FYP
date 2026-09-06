"""M15-T8 — lockout recovery: the tablet is wiped, the link is lost, the PIN is
forgotten, and it is the middle of service.

Two levers, both owner-only and both from the dashboard:

  * **Reset a staff PIN.** The old one stops working at once; the cashier is
    back on the till on the next screen.
  * **Re-pair.** `businesses.pairing_generation` goes up, which retires every
    kiosk link *and* every live till session issued before it. Blunt on purpose:
    you re-pair because a device is out of your hands.

The done-criterion — "a wiped kiosk is back to taking sales" — is
`test_a_wiped_kiosk_is_back_to_taking_sales`, which walks the whole recovery in
the order a person would: cut the old device off, make a new link, open it, log
in, ring up a sale.

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import os
import uuid
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import create_pairing_token, create_token, decode_token, hash_pin
from app.main import app
from app.models import Business, Item, Order, Staff
from app.services.catalog import ensure_default_variant
from app.services.pricing import ensure_pricing_settings
from app.services.stock import open_item_stock

from tests.conftest import seed_books

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")
D = Decimal
STALE = "Perangkat ini sudah tidak dipasangkan"


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
    await session.execute(
        text("select set_config('app.current_business_id', :bid, true)"), {"bid": str(business_id)}
    )


@pytest.fixture
async def cafe(session_factory):
    """One café, one owner, one cashier, one sellable item — and the tablet at
    the counter, paired and logged in, exactly as it is when it goes missing."""
    async with session_factory() as s:
        biz = Business(name="Kopi Terkunci", owner_phone=f"62964{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await seed_books(s, bid)
        await ensure_pricing_settings(s, bid)
        owner = Staff(business_id=bid, name="Bu Ratna", role="owner", pin_hash=hash_pin("1234"))
        sari = Staff(business_id=bid, name="Sari", role="staff", pin_hash=hash_pin("2345"))
        kopi = Item(business_id=bid, name="Kopi", unit="cup", current_stock=D(50),
                    cost_price=D(8000), sell_price=D(20000))
        s.add_all([owner, sari, kopi])
        await s.flush()
        await open_item_stock(s, kopi, unit_cost=kopi.cost_price)
        variant = await ensure_default_variant(s, kopi)
        await s.commit()
        c = {"bid": bid, "kopi": kopi.id, "variant": variant.id, "owner": owner.id, "sari": sari.id,
             "ownertok": create_token(business_id=str(bid), scope="owner"),
             "pairing": create_pairing_token(str(bid), 1),
             "postok": create_token(business_id=str(bid), scope="pos", staff_id=str(sari.id), generation=1)}
    yield c
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def _ring_up(client, token, c, qty=1, paid=20000):
    return await client.post(
        "/pos/orders",
        json={"lines": [{"item_id": str(c["kopi"]), "variant_id": str(c["variant"]), "quantity": str(qty)}],
              "payments": [{"method": "cash", "amount": str(paid)}]},
        headers=_auth(token),
    )


# ── a forgotten PIN, mid-service ─────────────────────────────────────────────


async def test_the_owner_resets_a_forgotten_pin_and_the_old_one_dies(client, cafe):
    login = {"pairing_token": cafe["pairing"], "staff_id": str(cafe["sari"])}
    assert (await client.post("/pos/login", json={**login, "pin": "2345"})).status_code == 200

    r = await client.post(f"/auth/staff/{cafe['sari']}/pin", json={"pin": "8899"},
                          headers=_auth(cafe["ownertok"]))
    assert r.status_code == 200 and r.json()["name"] == "Sari"

    assert (await client.post("/pos/login", json={**login, "pin": "2345"})).status_code == 401
    r = await client.post("/pos/login", json={**login, "pin": "8899"})
    assert r.status_code == 200 and r.json()["staff_name"] == "Sari"


async def test_the_owner_can_reset_their_own_pin_too(client, cafe):
    """The owner's PIN is also the approval PIN, so forgetting it locks the till
    out of voids and refunds, not just out of the owner's own shifts."""
    r = await client.post(f"/auth/staff/{cafe['owner']}/pin", json={"pin": "5566"},
                          headers=_auth(cafe["ownertok"]))
    assert r.status_code == 200 and r.json()["role"] == "owner"
    login = {"pairing_token": cafe["pairing"], "staff_id": str(cafe["owner"])}
    assert (await client.post("/pos/login", json={**login, "pin": "1234"})).status_code == 401
    assert (await client.post("/pos/login", json={**login, "pin": "5566"})).status_code == 200


async def test_a_pin_reset_is_not_a_reactivation_and_is_not_an_identity_change(session_factory, client, cafe):
    """Resetting the PIN of someone who has left must not put them back on the
    till, and what they already rang up keeps their name on it."""
    sold = await _ring_up(client, cafe["postok"], cafe)
    assert sold.status_code == 201, sold.text

    await client.post(f"/auth/staff/{cafe['sari']}/deactivate", headers=_auth(cafe["ownertok"]))
    r = await client.post(f"/auth/staff/{cafe['sari']}/pin", json={"pin": "7777"},
                          headers=_auth(cafe["ownertok"]))
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert (await client.post("/pos/login", json={"pairing_token": cafe["pairing"],
                                                  "staff_id": str(cafe["sari"]), "pin": "7777"})).status_code == 401

    async with session_factory() as s:
        await _set_tenant(s, cafe["bid"])
        order = (await s.execute(select(Order))).scalars().one()
        assert order.staff_id == cafe["sari"]


async def test_a_pin_reset_is_owner_only_and_validated(client, cafe):
    assert (await client.post(f"/auth/staff/{cafe['sari']}/pin", json={"pin": "9999"},
                              headers=_auth(cafe["postok"]))).status_code == 403
    assert (await client.post(f"/auth/staff/{uuid.uuid4()}/pin", json={"pin": "9999"},
                              headers=_auth(cafe["ownertok"]))).status_code == 404
    for bad in ("12", "abcd", "12345678"):
        assert (await client.post(f"/auth/staff/{cafe['sari']}/pin", json={"pin": bad},
                                  headers=_auth(cafe["ownertok"]))).status_code == 422, bad


# ── a lost device ────────────────────────────────────────────────────────────


async def test_showing_the_pairing_link_again_cuts_nobody_off(client, cafe):
    """The owner opens Pengaturan to read the link out over the phone. That must
    not log the counter out mid-service — only an explicit re-pair does."""
    first = (await client.post("/auth/pos-pairing", headers=_auth(cafe["ownertok"]))).json()["pairing_token"]
    second = (await client.post("/auth/pos-pairing", headers=_auth(cafe["ownertok"]))).json()["pairing_token"]
    assert decode_token(first)["gen"] == decode_token(second)["gen"] == 1
    for token in (first, second, cafe["pairing"]):
        assert (await client.get(f"/pos/business/{token}")).status_code == 200
    assert (await client.get("/pos/items", headers=_auth(cafe["postok"]))).status_code == 200


async def test_re_pairing_retires_the_old_link_and_the_session_it_was_holding(client, cafe):
    """The stolen-tablet case. Stopping new logins is not enough — the device
    already holds a till token that is good for hours."""
    assert (await client.get("/pos/items", headers=_auth(cafe["postok"]))).status_code == 200

    r = await client.post("/auth/pos-pairing/reset", headers=_auth(cafe["ownertok"]))
    assert r.status_code == 200
    fresh = r.json()["pairing_token"]
    assert decode_token(fresh)["gen"] == 2

    # The old link cannot even reach the "who are you" screen…
    r = await client.get(f"/pos/business/{cafe['pairing']}")
    assert r.status_code == 401 and STALE in r.json()["detail"]
    # …nor attempt a login with a PIN it may have seen…
    r = await client.post("/pos/login", json={"pairing_token": cafe["pairing"],
                                              "staff_id": str(cafe["sari"]), "pin": "2345"})
    assert r.status_code == 401 and STALE in r.json()["detail"]
    # …and the session it was already holding is over, including for selling.
    r = await client.get("/pos/items", headers=_auth(cafe["postok"]))
    assert r.status_code == 401 and STALE in r.json()["detail"]
    assert (await _ring_up(client, cafe["postok"], cafe)).status_code == 401

    assert (await client.get(f"/pos/business/{fresh}")).status_code == 200


async def test_re_pairing_is_owner_only_and_the_counter_only_goes_up(session_factory, client, cafe):
    assert (await client.post("/auth/pos-pairing/reset", headers=_auth(cafe["postok"]))).status_code == 403
    assert (await client.post("/auth/pos-pairing/reset", headers=_auth(cafe["ownertok"]))).status_code == 200
    assert (await client.post("/auth/pos-pairing/reset", headers=_auth(cafe["ownertok"]))).status_code == 200
    async with session_factory() as s:
        await _set_tenant(s, cafe["bid"])
        assert (await s.get(Business, cafe["bid"])).pairing_generation == 3
        # The constraint is the floor, so no future writer can zero it.
        with pytest.raises(Exception):
            await s.execute(text("update businesses set pairing_generation = 0 where id = :b"),
                            {"b": str(cafe["bid"])})
            await s.flush()
        await s.rollback()


async def test_one_cafes_re_pairing_does_not_touch_another(session_factory, client, cafe):
    """The counter lives on the business row, so a café that re-pairs must not
    log out the café next door."""
    async with session_factory() as s:
        other = Business(name="Kopi Tetangga", owner_phone=f"62965{uuid.uuid4().hex[:9]}")
        s.add(other)
        await s.commit()
        other_id, other_pairing = other.id, create_pairing_token(str(other.id), 1)
    try:
        await client.post("/auth/pos-pairing/reset", headers=_auth(cafe["ownertok"]))
        assert (await client.get(f"/pos/business/{other_pairing}")).status_code == 200
        assert (await client.get(f"/pos/business/{cafe['pairing']}")).status_code == 401
    finally:
        async with session_factory() as s:
            row = await s.get(Business, other_id)
            if row:
                await s.delete(row)
            await s.commit()


# ── the done-criterion ───────────────────────────────────────────────────────


async def test_a_wiped_kiosk_is_back_to_taking_sales(client, cafe):
    """The whole recovery, in the order a person does it: the tablet is gone, so
    cut it off, make a new link, open it on the replacement, log in with a PIN
    the owner has just reset, and sell something."""
    owner = _auth(cafe["ownertok"])

    # 1. Cut the lost device off and take the new link.
    fresh = (await client.post("/auth/pos-pairing/reset", headers=owner)).json()["pairing_token"]
    assert (await client.get("/pos/items", headers=_auth(cafe["postok"]))).status_code == 401

    # 2. The cashier has also forgotten her PIN; the owner sets a new one.
    assert (await client.post(f"/auth/staff/{cafe['sari']}/pin", json={"pin": "4040"},
                              headers=owner)).status_code == 200

    # 3. The replacement tablet opens the link and sees the "who are you" screen.
    r = await client.get(f"/pos/business/{fresh}")
    assert r.status_code == 200 and "Sari" in [s["name"] for s in r.json()["staff"]]

    # 4. She signs in.
    r = await client.post("/pos/login", json={"pairing_token": fresh,
                                              "staff_id": str(cafe["sari"]), "pin": "4040"})
    assert r.status_code == 200
    token = r.json()["token"]
    assert decode_token(token)["gen"] == 2

    # 5. The till is a till again: menu, sale, receipt.
    assert (await client.get("/pos/items", headers=_auth(token))).status_code == 200
    sold = await _ring_up(client, token, cafe, qty=2, paid=40000)
    assert sold.status_code == 201, sold.text
    order_id = sold.json()["id"]
    receipt = await client.get(f"/pos/orders/{order_id}/receipt", headers=_auth(token))
    assert receipt.status_code == 200 and receipt.json()["total"] == "40000.00"
    # …and the sale is the business's, on the books, under her name.
    r = await client.get("/api/sales?page=1&page_size=5", headers=owner)
    assert r.status_code == 200 and r.json()["total"] >= 1
