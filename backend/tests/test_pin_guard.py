"""M15-T12 — PIN brute-force protection.

A 4-digit PIN is 10,000 combinations. The threat is not a stranger on the
internet: it is the person holding the tablet all shift, who can script the pad
and have the owner's PIN inside an hour, and the pairing link is a bearer URL
anyone who has seen it can replay from their own phone.

The whole design is shaped by one constraint — a café cannot have its till
locked during a rush — so this is an escalating cooldown, never a lock. The
roadmap's done-criterion is four claims and each has a test here: a scripted
100-attempt run is throttled and logged; a correct PIN after the cooldown
expires still works; the owner can see and clear it; and a busy till that
fat-fingers a PIN twice is never slowed down.

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

from app.core.security import create_pairing_token, create_token, hash_pin
from app.main import app
from app.models import Business, Item, PinAttempt, RequestLog, Staff
from app.services import pin_guard
from app.services.catalog import ensure_default_variant
from app.services.orders import (
    ManagerPinRejected, ManagerPinThrottled, OrderLineSpec, PaymentSpec, create_order,
    verify_manager_pin, void_order,
)
from app.services.pricing import ensure_pricing_settings
from app.services.stock import open_item_stock

from tests.conftest import seed_books

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")
D = Decimal
PIN = {"owner": "1234", "manager": "4321", "sari": "2345", "budi": "3456"}


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


@pytest.fixture(autouse=True)
async def dispose_app_engine():
    """`pin_guard.record_failure` opens its own session through the app's engine
    (it has to — see its docstring), so every test here touches that pool even
    when it never goes near the HTTP client. pytest-asyncio gives each test its
    own event loop, which strands the pool's asyncpg connections in the previous
    one: the same trap M11-T1 and M15-T3 hit."""
    yield
    from app.core.db import engine as app_engine

    await app_engine.dispose()


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


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def cafe(session_factory):
    """One café, four PINs, one sale already on the books to void."""
    async with session_factory() as s:
        biz = Business(name="Kopi Gembok", owner_phone=f"62970{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await seed_books(s, bid)
        await ensure_pricing_settings(s, bid)
        owner = Staff(business_id=bid, name="Bu Ratna", role="owner", pin_hash=hash_pin(PIN["owner"]))
        yudi = Staff(business_id=bid, name="Pak Yudi", role="manager", pin_hash=hash_pin(PIN["manager"]))
        sari = Staff(business_id=bid, name="Sari", role="staff", pin_hash=hash_pin(PIN["sari"]))
        budi = Staff(business_id=bid, name="Budi", role="staff", pin_hash=hash_pin(PIN["budi"]))
        kopi = Item(business_id=bid, name="Kopi", unit="cup", current_stock=D(50),
                    cost_price=D(8000), sell_price=D(20000))
        s.add_all([owner, yudi, sari, budi, kopi])
        await s.flush()
        await open_item_stock(s, kopi, unit_cost=kopi.cost_price)
        await ensure_default_variant(s, kopi)
        sold = await create_order(
            s, business_id=bid, staff_id=sari.id,
            lines=[OrderLineSpec(item_id=kopi.id, quantity=D(1))],
            payments=[PaymentSpec(method="cash", amount=D(20000))],
        )
        await s.commit()
        c = {"bid": bid, "owner": owner.id, "yudi": yudi.id, "sari": sari.id, "budi": budi.id,
             "order": sold.order.id, "kopi": kopi.id,
             "pairing": create_pairing_token(str(bid), 1),
             "ownertok": create_token(business_id=str(bid), scope="owner"),
             "postok": create_token(business_id=str(bid), scope="pos", staff_id=str(sari.id))}
    yield c
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


def _login(cafe, staff_key="sari", pin="0000"):
    return {"pairing_token": cafe["pairing"], "staff_id": str(cafe[staff_key]), "pin": pin}


# ── the ladder itself, without a database ────────────────────────────────────


def test_the_cooldown_escalates_and_the_first_few_failures_are_free():
    ladder = pin_guard._cooldown_for
    for n in (1, 2, 3, 4):
        assert ladder("pos_login", n) is None, n          # a fat finger costs nothing
    assert ladder("pos_login", 5) == timedelta(seconds=30)
    assert ladder("pos_login", 9) == timedelta(seconds=30)
    assert ladder("pos_login", 10) == timedelta(minutes=2)
    assert ladder("pos_login", 19) == timedelta(minutes=2)
    assert ladder("pos_login", 20) == timedelta(minutes=15)
    assert ladder("pos_login", 100) == timedelta(minutes=15)   # it never becomes a lock
    assert ladder("manager_pin", 5) == timedelta(seconds=30)
    # One shared tablet carries every cashier's mistakes, so it is far slacker.
    assert ladder("pos_device", 5) is None and ladder("pos_device", 19) is None
    assert ladder("pos_device", 20) == timedelta(seconds=30)


# ── /pos/login ───────────────────────────────────────────────────────────────


async def test_two_fat_fingers_in_a_rush_are_never_slowed_down(client, cafe):
    """The clause that decides whether the owner keeps this switched on."""
    for _ in range(4):
        r = await client.post("/pos/login", json=_login(cafe))
        assert r.status_code == 401 and r.json()["detail"] == "PIN salah — coba lagi"
    # …and the right PIN still goes straight through.
    assert (await client.post("/pos/login", json=_login(cafe, pin=PIN["sari"]))).status_code == 200


async def test_a_scripted_run_is_throttled_and_logged(session_factory, client, cafe):
    """The roadmap's done-criterion: 100 attempts against /pos/login."""
    codes = []
    for _ in range(100):
        codes.append((await client.post("/pos/login", json=_login(cafe))).status_code)

    # The first five are ordinary rejections; everything after is a cooldown.
    assert codes[:4] == [401] * 4
    assert codes[4] == 429 and set(codes[4:]) == {429}
    body = (await client.post("/pos/login", json=_login(cafe))).json()["detail"]
    assert "Terlalu banyak PIN salah" in body and "tunggu" in body

    async with session_factory() as s:
        await _set_tenant(s, cafe["bid"])
        row = (await s.execute(
            select(PinAttempt).where(PinAttempt.scope == "pos_login")
        )).scalars().one()
        # A request that arrives during a cooldown is another attempt at the PIN,
        # so hammering escalates instead of sitting on 30 seconds for ever: 100
        # tries plus the one probe above land on the top rung.
        assert row.failures == 101
        assert row.locked_until is not None
        assert row.locked_until - row.last_failed_at == timedelta(minutes=15)

        # …and every single attempt is in request_logs, whether or not it tripped
        # a threshold — the pattern is what an owner reads afterwards, and five
        # lines would not describe what actually happened.
        logged = (await s.execute(
            select(RequestLog.status, func.count()).where(RequestLog.path == "/pos/login")
            .group_by(RequestLog.status)
        )).all()
        # Two counters are running, so the first five attempts write two rows
        # each: the per-staff one and the device one. 4 x2 plus the device's
        # fifth = 9 plain failures, then 97 refusals once the ladder is in force.
        assert dict(logged) == {"pin_failed": 9, "pin_locked": 97}


async def test_a_correct_pin_after_the_cooldown_expires_still_works(session_factory, client, cafe):
    """The other half of "cooldown, not lock": it ends by itself."""
    for _ in range(5):
        await client.post("/pos/login", json=_login(cafe))
    assert (await client.post("/pos/login", json=_login(cafe, pin=PIN["sari"]))).status_code == 429

    # Wind the clock on rather than sleeping 30 seconds in a test suite.
    async with session_factory() as s:
        await _set_tenant(s, cafe["bid"])
        row = (await s.execute(select(PinAttempt).where(PinAttempt.scope == "pos_login"))).scalars().one()
        row.locked_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        await s.commit()

    r = await client.post("/pos/login", json=_login(cafe, pin=PIN["sari"]))
    assert r.status_code == 200 and r.json()["staff_name"] == "Sari"

    # A correct PIN forgives what came before it, so the next mistake starts
    # from zero rather than from five.
    async with session_factory() as s:
        await _set_tenant(s, cafe["bid"])
        assert (await s.execute(select(func.count(PinAttempt.id)))).scalar_one() == 0


async def test_one_cashier_being_throttled_does_not_stop_the_others(client, cafe):
    """Per staff row, so a locked-out colleague cannot close the till."""
    for _ in range(5):
        await client.post("/pos/login", json=_login(cafe))
    assert (await client.post("/pos/login", json=_login(cafe, pin=PIN["sari"]))).status_code == 429
    r = await client.post("/pos/login", json=_login(cafe, staff_key="budi", pin=PIN["budi"]))
    assert r.status_code == 200 and r.json()["staff_name"] == "Budi"


async def test_spraying_across_many_staff_is_caught_by_the_device_counter(session_factory, client, cafe):
    """Four guesses each against five people never trips anybody's own counter.
    The device counter is the net under that."""
    for staff_key in ("sari", "budi"):
        for _ in range(4):
            await client.post("/pos/login", json=_login(cafe, staff_key=staff_key))
    async with session_factory() as s:
        await _set_tenant(s, cafe["bid"])
        device = (await s.execute(
            select(PinAttempt).where(PinAttempt.scope == "pos_device")
        )).scalars().one()
        assert device.failures == 8 and device.locked_until is None   # still trading
        # Nobody's own counter has fired either.
        for row in (await s.execute(select(PinAttempt).where(PinAttempt.scope == "pos_login"))).scalars():
            assert row.locked_until is None

    # Push the shared counter to its own threshold and the whole device cools.
    async with session_factory() as s:
        await _set_tenant(s, cafe["bid"])
        row = (await s.execute(select(PinAttempt).where(PinAttempt.scope == "pos_device"))).scalars().one()
        row.failures = 19
        await s.commit()
    await client.post("/pos/login", json=_login(cafe, staff_key="budi"))
    r = await client.post("/pos/login", json=_login(cafe, staff_key="budi", pin=PIN["budi"]))
    assert r.status_code == 429


async def test_guessing_at_a_staff_id_that_does_not_exist_is_throttled_too(client, cafe):
    """The 401 is deliberately the same for wrong-person and wrong-PIN, so the
    counter has to work on the id that was tried, not on a row that was found."""
    ghost = {"pairing_token": cafe["pairing"], "staff_id": str(uuid.uuid4()), "pin": "0000"}
    codes = [(await client.post("/pos/login", json=ghost)).status_code for _ in range(6)]
    assert codes[:4] == [401] * 4 and codes[4] == 429


async def test_re_pairing_forgives_the_old_device(session_factory, client, cafe):
    """M15-T8 says everything issued before a re-pair is void. The counter that
    belonged to the retired device generation goes with it, so the replacement
    tablet does not inherit a cooldown from the one that was stolen."""
    for _ in range(5):
        await client.post("/pos/login", json=_login(cafe))
    fresh = (await client.post("/auth/pos-pairing/reset", headers=_auth(cafe["ownertok"]))).json()["pairing_token"]
    r = await client.post("/pos/login", json={"pairing_token": fresh,
                                              "staff_id": str(cafe["budi"]), "pin": PIN["budi"]})
    assert r.status_code == 200


# ── the manager PIN, counted separately ──────────────────────────────────────


async def test_the_manager_pin_is_throttled_against_whoever_is_asking(session_factory, client, cafe):
    """This is the gate on voids, refunds and discounts — the ways money leaves —
    so it gets the same ladder as a staff PIN and its own counter."""
    pos = _auth(cafe["postok"])
    for _ in range(4):
        r = await client.post(f"/pos/orders/{cafe['order']}/void",
                              json={"manager_pin": "0000"}, headers=pos)
        assert r.status_code == 403, r.text
    r = await client.post(f"/pos/orders/{cafe['order']}/void", json={"manager_pin": "0000"}, headers=pos)
    assert r.status_code == 429 and "Terlalu banyak PIN salah" in r.json()["detail"]
    # Even the *right* PIN waits, or the cooldown would be trivially bypassed.
    r = await client.post(f"/pos/orders/{cafe['order']}/void",
                          json={"manager_pin": PIN["manager"]}, headers=pos)
    assert r.status_code == 429

    async with session_factory() as s:
        await _set_tenant(s, cafe["bid"])
        row = (await s.execute(
            select(PinAttempt).where(PinAttempt.scope == "manager_pin")
        )).scalars().one()
        assert row.subject == f"asked_by:{cafe['sari']}" and row.failures == 6   # 5 tried, 1 refused
        # Counted apart from the login ladder: guessing manager PINs must not be
        # hidden by, or hide, a cashier fumbling their own.
        assert (await s.execute(
            select(func.count(PinAttempt.id)).where(PinAttempt.scope == "pos_login")
        )).scalar_one() == 0
        assert (await s.execute(
            select(func.count(RequestLog.id)).where(RequestLog.path == "verify_manager_pin")
        )).scalar_one() == 6


async def test_a_correct_manager_pin_clears_it_and_the_void_still_happens(session_factory, cafe):
    async with session_factory() as s:
        await _set_tenant(s, cafe["bid"])
        for _ in range(4):
            with pytest.raises(ManagerPinRejected):
                await verify_manager_pin(s, "0000", business_id=cafe["bid"], acting_staff_id=cafe["sari"])
        approver = await verify_manager_pin(
            s, PIN["manager"], business_id=cafe["bid"], acting_staff_id=cafe["sari"]
        )
        assert approver.name == "Pak Yudi"
        assert (await s.execute(select(func.count(PinAttempt.id)))).scalar_one() == 0
        # …and the override it was guarding goes through as normal.
        rev = await void_order(s, business_id=cafe["bid"], order_id=cafe["order"],
                               staff_id=cafe["sari"], manager_pin=PIN["manager"])
        assert rev.order.status == "voided"
        await s.rollback()


async def test_the_throttle_raises_its_own_exception_not_a_rejection(session_factory, cafe):
    """A caller must be able to tell "wrong PIN" from "wait" — they are a 403
    and a 429, and the ladder must not be reported as a bad PIN."""
    async with session_factory() as s:
        await _set_tenant(s, cafe["bid"])
        for _ in range(4):
            with pytest.raises(ManagerPinRejected):
                await verify_manager_pin(s, "0000", business_id=cafe["bid"], acting_staff_id=cafe["budi"])
        with pytest.raises(ManagerPinThrottled) as excinfo:
            await verify_manager_pin(s, "0000", business_id=cafe["bid"], acting_staff_id=cafe["budi"])
        assert 0 < excinfo.value.cooldown.seconds <= 30
        await s.rollback()


# ── the rolling window ───────────────────────────────────────────────────────


async def test_yesterdays_fumbling_does_not_add_to_todays(session_factory, cafe):
    """Without a window, four honest mistakes a week apart eventually lock
    somebody out for a quarter of an hour."""
    async with session_factory() as s:
        await _set_tenant(s, cafe["bid"])
        subject = pin_guard.staff_subject(cafe["sari"])
        long_ago = datetime.now(timezone.utc) - timedelta(hours=3)
        for i in range(4):
            await pin_guard.record_failure(cafe["bid"], "pos_login", subject,
                                           path="/pos/login", now=long_ago + timedelta(seconds=i))
        row = (await s.execute(select(PinAttempt))).scalars().one()
        assert row.failures == 4 and row.locked_until is None

        earned = await pin_guard.record_failure(cafe["bid"], "pos_login", subject, path="/pos/login")
        assert earned is None                                  # the count restarted
        await s.refresh(row)
        assert row.failures == 1


# ── what the owner sees, and can undo ────────────────────────────────────────


async def test_the_owner_sees_the_attempts_and_can_clear_a_cooldown(client, cafe):
    for _ in range(5):
        await client.post("/pos/login", json=_login(cafe))
    assert (await client.post("/pos/login", json=_login(cafe, pin=PIN["sari"]))).status_code == 429

    owner = _auth(cafe["ownertok"])
    rows = (await client.get("/api/pin-lockouts", headers=owner)).json()
    by_scope = {r["scope"]: r for r in rows}
    assert set(by_scope) == {"pos_login", "pos_device"}
    assert by_scope["pos_login"]["who"] == "Sari"              # a name, not a uuid
    assert by_scope["pos_login"]["failures"] == 6 and by_scope["pos_login"]["locked_now"] is True
    assert by_scope["pos_device"]["who"] == "perangkat kasir ini"
    assert by_scope["pos_device"]["locked_now"] is False       # counted, not cooling

    # The owner is the person who can tell "forgot their PIN" from "trying
    # everyone else's", so they can let Sari straight back in.
    r = await client.delete(f"/api/pin-lockouts/{by_scope['pos_login']['id']}", headers=owner)
    assert r.status_code == 204
    r = await client.post("/pos/login", json=_login(cafe, pin=PIN["sari"]))
    assert r.status_code == 200

    assert (await client.delete(f"/api/pin-lockouts/{uuid.uuid4()}", headers=owner)).status_code == 404
    assert (await client.get("/api/pin-lockouts", headers=_auth(cafe["postok"]))).status_code == 403


async def test_a_manager_lockout_names_who_was_asking(client, cafe):
    pos = _auth(cafe["postok"])
    for _ in range(5):
        await client.post(f"/pos/orders/{cafe['order']}/void", json={"manager_pin": "0000"}, headers=pos)
    rows = (await client.get("/api/pin-lockouts", headers=_auth(cafe["ownertok"]))).json()
    manager = [r for r in rows if r["scope"] == "manager_pin"]
    assert len(manager) == 1 and manager[0]["who"] == "diminta oleh Sari"


# ── tenant isolation (roadmap §2) ────────────────────────────────────────────


async def test_rls_isolates_pin_attempts(session_factory, cafe):
    """A new table with a business_id needs a test proving business A cannot
    read business B's rows in it. Here that matters twice over: the rows name
    staff and say which of them is being guessed at."""
    async with session_factory() as s:
        other = Business(name="Kopi Tetangga", owner_phone=f"62971{uuid.uuid4().hex[:9]}")
        s.add(other)
        await s.commit()
        other_id = other.id
    try:
        async with session_factory() as s:
            await _set_tenant(s, cafe["bid"])
            await pin_guard.record_failure(cafe["bid"], "pos_login",
                                           pin_guard.staff_subject(cafe["sari"]), path="/pos/login")

        async with session_factory() as s:
            await _set_tenant(s, other_id)
            assert (await s.execute(select(PinAttempt))).scalars().all() == []

        async with session_factory() as s:
            await _set_tenant(s, cafe["bid"])
            assert len((await s.execute(select(PinAttempt))).scalars().all()) == 1
            s.add(PinAttempt(business_id=other_id, scope="pos_login", subject="staff:smuggled"))
            with pytest.raises(Exception):
                await s.commit()
    finally:
        async with session_factory() as s:
            row = await s.get(Business, other_id)
            if row:
                await s.delete(row)
            await s.commit()
