"""Owner login with a PIN — the second door, while WhatsApp OTP is gated.

Meta gates Authentication templates behind business verification (see
docs/whatsapp-templates.md), so the WhatsApp code cannot be the only way into
the dashboard: a café would be locked out of its own books by a review queue.

This door has to be narrow, and these tests are the fence:
  * the owner's PIN opens it; a cashier's PIN never does,
  * every failure says the same thing, so it cannot be used to discover which
    phone numbers are registered,
  * guessing is throttled on its own stricter ladder (3 → 30s, 5 → 2 min,
    10 → 15 min), which puts a 4-digit PIN out of reach of a script,
  * a correct PIN forgives what came before it.

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import os
import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import hash_pin
from app.main import app
from app.models import Business, PinAttempt, Staff

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")

OWNER_PHONE = "6281200099001"
CASHIER_PHONE = "6281200099002"
OWNER_PIN = "4821"
CASHIER_PIN = "7310"


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
    """One café: an owner with a PIN, and a cashier with a different one."""
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        biz = Business(name="PIN Login Café", owner_phone=OWNER_PHONE)
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with factory() as s:
        await s.execute(text("select set_config('app.current_business_id', :b, true)"), {"b": str(bid)})
        s.add_all([
            Staff(business_id=bid, name="Bu Ratna", role="owner", phone=OWNER_PHONE, pin_hash=hash_pin(OWNER_PIN)),
            Staff(business_id=bid, name="Sari", role="staff", phone=CASHIER_PHONE, pin_hash=hash_pin(CASHIER_PIN)),
        ])
        await s.commit()
    yield bid
    async with factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


async def _login(client, phone=OWNER_PHONE, pin=OWNER_PIN):
    return await client.post("/auth/login-pin", json={"phone": phone, "pin": pin})


async def _clear_counter(engine, bid):
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        await s.execute(text("select set_config('app.current_business_id', :b, true)"), {"b": str(bid)})
        for row in (await s.execute(text("select id from pin_attempts"))).all():
            await s.delete(await s.get(PinAttempt, row[0]))
        await s.commit()


async def test_the_owner_pin_opens_the_dashboard(client, shop):
    r = await _login(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["registered"] is True and body["token"]
    assert body["business"]["id"] == str(shop)

    # The token is a real owner token, not a stub: it works on an owner route.
    me = await client.get("/api/business", headers={"Authorization": f"Bearer {body['token']}"})
    assert me.status_code == 200 and me.json()["id"] == str(shop)


async def test_a_cashiers_pin_does_not_open_the_dashboard(client, shop):
    """The till and the books are different doors. Sari can sell; she cannot
    read the takings."""
    r = await _login(client, phone=CASHIER_PHONE, pin=CASHIER_PIN)
    assert r.status_code == 401
    r = await _login(client, phone=OWNER_PHONE, pin=CASHIER_PIN)
    assert r.status_code == 401


async def test_every_failure_says_the_same_thing(client, shop):
    """Wrong PIN, wrong number, no business at all: one message, so the
    endpoint cannot be used to find out which numbers are registered."""
    wrong_pin = await _login(client, pin="9999")
    unknown = await _login(client, phone="6281200099999", pin="9999")
    assert wrong_pin.status_code == unknown.status_code == 401
    assert wrong_pin.json()["detail"] == unknown.json()["detail"]


async def test_guessing_is_throttled_on_its_own_ladder(client, shop, engine):
    await _clear_counter(engine, shop)
    for attempt in range(2):
        r = await _login(client, pin="0000")
        assert r.status_code == 401, f"attempt {attempt}: {r.text}"

    # The third failure is the one that earns the cooldown, so it answers with
    # the wait rather than the usual refusal — the same shape as the till.
    third = await _login(client, pin="0000")
    assert third.status_code == 429
    assert "tunggu" in third.json()["detail"].lower()

    # While it is in force the PIN is not even checked, so the correct one is
    # refused too. That is the point: a script cannot keep guessing.
    assert (await _login(client, pin="0000")).status_code == 429
    assert (await _login(client)).status_code == 429


async def test_a_correct_pin_forgives_what_came_before(client, shop, engine):
    await _clear_counter(engine, shop)
    for _ in range(2):
        assert (await _login(client, pin="1111")).status_code == 401
    assert (await _login(client)).status_code == 200

    # Counter cleared: two more misses do not reach the 3-failure cooldown.
    for _ in range(2):
        assert (await _login(client, pin="1111")).status_code == 401


async def test_a_malformed_pin_is_refused_before_any_lookup(client, shop):
    for bad in ("", "abc", "12", "1" * 13, "12 34"):
        r = await client.post("/auth/login-pin", json={"phone": OWNER_PHONE, "pin": bad})
        assert r.status_code == 422, bad


async def test_attempts_are_recorded_for_the_owner_to_see(client, shop, engine):
    """M15-T12's lockout list is how the owner notices someone trying. A
    dashboard login attempt has to appear there like any other."""
    await _clear_counter(engine, shop)
    for _ in range(3):
        await _login(client, pin="2222")

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        await s.execute(text("select set_config('app.current_business_id', :b, true)"), {"b": str(shop)})
        rows = (await s.execute(text(
            "select scope, subject, failures from pin_attempts where scope = 'owner_login'"
        ))).all()
    assert rows and rows[0][1] == f"phone:{OWNER_PHONE}" and rows[0][2] >= 3
