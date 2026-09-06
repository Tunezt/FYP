"""M15-T4 — the business day boundary.

A café that closes at 23:30 settles its last bill after midnight. On a calendar
day those takings land on tomorrow: the owner's daily number is wrong, the
shift reconciliation straddles two days, and the anomaly baseline learns from a
split night. `businesses.day_start_hour = 4` moves the boundary, so the
business day runs 04:00 → 04:00 and 00:15 belongs to the night that produced it.

The roadmap's done-criterion is the second half of this file: a sale at 00:15
with `day_start_hour = 4` reports under the previous day, in the metric layer
AND on the dashboard. The first half pins the arithmetic without a database.

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai.periods import PERIODS, business_day, day_bounds, period_range
from app.core.security import create_token, hash_pin
from app.jobs.rules import local_day
from app.main import app
from app.metrics import compute, local_day_windows, local_month_windows
from app.models import Business, Item, Staff
from app.services.catalog import ensure_default_variant
from app.services.orders import OrderLineSpec, PaymentSpec, create_order
from app.services.pricing import ensure_pricing_settings
from app.services.stock import open_item_stock

from tests.conftest import seed_books

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")
D = Decimal
TZ = "Asia/Jakarta"
WIB = ZoneInfo(TZ)

# Thursday 3 September 2026 in Jakarta. The café trades into the small hours:
# the last bill of Thursday night is settled at 00:15 on Friday.
THU = date(2026, 9, 3)
LAST_CALL = datetime(2026, 9, 3, 23, 50, tzinfo=WIB).astimezone(timezone.utc)
AFTER_MIDNIGHT = datetime(2026, 9, 4, 0, 15, tzinfo=WIB).astimezone(timezone.utc)
CLOSING = datetime(2026, 9, 4, 1, 0, tzinfo=WIB).astimezone(timezone.utc)   # staff cash up


# ── the arithmetic, no database ──────────────────────────────────────────────


def test_00_15_belongs_to_the_night_before():
    assert business_day(AFTER_MIDNIGHT, TZ, 4) == THU
    assert business_day(LAST_CALL, TZ, 4) == THU
    # …and on a calendar day it does not, which is the bug being fixed.
    assert business_day(AFTER_MIDNIGHT, TZ, 0) == date(2026, 9, 4)
    # 04:00 exactly is the new day: the boundary is [start, next start).
    assert business_day(datetime(2026, 9, 4, 4, 0, tzinfo=WIB), TZ, 4) == date(2026, 9, 4)
    assert business_day(datetime(2026, 9, 4, 3, 59, tzinfo=WIB), TZ, 4) == THU


def test_today_runs_from_the_start_hour_to_the_start_hour():
    start, end, label = period_range("today", TZ, CLOSING, day_start_hour=4)
    assert start == datetime(2026, 9, 3, 4, 0, tzinfo=WIB).astimezone(timezone.utc)
    assert end == datetime(2026, 9, 4, 4, 0, tzinfo=WIB).astimezone(timezone.utc)
    assert start <= LAST_CALL < end and start <= AFTER_MIDNIGHT < end
    assert label == "hari ini"
    # The same instant on a calendar day is already Friday, and Thursday's late
    # takings have fallen out of "today".
    plain_start, _, _ = period_range("today", TZ, CLOSING)
    assert plain_start == datetime(2026, 9, 4, tzinfo=WIB).astimezone(timezone.utc)
    assert AFTER_MIDNIGHT >= plain_start > LAST_CALL


def test_the_pieces_still_abut_with_no_gap_and_no_overlap():
    for hour in (0, 4, 6, 23):
        y_start, y_end, _ = period_range("yesterday", TZ, CLOSING, day_start_hour=hour)
        t_start, t_end, _ = period_range("today", TZ, CLOSING, day_start_hour=hour)
        assert y_end == t_start and (t_end - t_start) == timedelta(days=1), hour
        lw_start, lw_end, _ = period_range("last_week", TZ, CLOSING, day_start_hour=hour)
        tw_start, tw_end, _ = period_range("this_week", TZ, CLOSING, day_start_hour=hour)
        assert lw_end == tw_start and (tw_end - tw_start) == timedelta(days=7), hour
        lm_start, lm_end, _ = period_range("last_month", TZ, CLOSING, day_start_hour=hour)
        tm_start, _, _ = period_range("this_month", TZ, CLOSING, day_start_hour=hour)
        assert lm_end == tm_start and lm_start < lm_end, hour


def test_every_period_is_anchored_on_the_start_hour():
    for period in PERIODS:
        start, end, _ = period_range(period, TZ, CLOSING, day_start_hour=4)
        assert start.astimezone(WIB).hour == 4, period
        assert end.astimezone(WIB).hour == 4, period


def test_the_week_starts_on_the_monday_business_day():
    # 04:00 Monday 31 August, not 04:00 Sunday: the business day at 01:00 on
    # Friday is still Thursday, and Thursday's week began on Monday the 31st.
    start, _, _ = period_range("this_week", TZ, CLOSING, day_start_hour=4)
    assert start == datetime(2026, 8, 31, 4, 0, tzinfo=WIB).astimezone(timezone.utc)
    # Monday 00:30 is Sunday's business day, so its week is the week before.
    monday_small_hours = datetime(2026, 8, 31, 0, 30, tzinfo=WIB).astimezone(timezone.utc)
    start, _, _ = period_range("this_week", TZ, monday_small_hours, day_start_hour=4)
    assert start == datetime(2026, 8, 24, 4, 0, tzinfo=WIB).astimezone(timezone.utc)


def test_zero_is_exactly_the_old_behaviour():
    for period in PERIODS:
        assert period_range(period, TZ, CLOSING, day_start_hour=0) == period_range(period, TZ, CLOSING), period


@pytest.mark.parametrize("bad", [None, 24, -1, 99, "", "four"])
def test_a_bad_start_hour_degrades_to_the_calendar_day_rather_than_shifting_the_books(bad):
    assert period_range("today", TZ, CLOSING, day_start_hour=bad) == period_range("today", TZ, CLOSING)
    assert business_day(AFTER_MIDNIGHT, TZ, bad) == date(2026, 9, 4)


def test_day_bounds_names_a_day_by_the_date_it_starts_on():
    start, end = day_bounds(THU, TZ, 4)
    assert start == datetime(2026, 9, 3, 4, 0, tzinfo=WIB).astimezone(timezone.utc)
    assert end == datetime(2026, 9, 4, 4, 0, tzinfo=WIB).astimezone(timezone.utc)


def test_the_nightly_dedup_key_uses_the_business_day():
    """A job re-run at 00:30 must not open a second day's worth of alerts for
    the night it is still reporting on."""
    shop = Business(name="x", owner_phone="x", timezone=TZ, day_start_hour=4)
    assert local_day(shop, CLOSING) == THU.isoformat()
    shop.day_start_hour = 0
    assert local_day(shop, CLOSING) == "2026-09-04"


def test_the_day_and_month_windows_carry_the_boundary():
    shop = Business(name="x", owner_phone="x", timezone=TZ, day_start_hour=4)
    windows = local_day_windows(shop, 3, now=CLOSING)
    assert [k for k, _s, _e in windows] == ["2026-09-01", "2026-09-02", "2026-09-03"]
    key, since, until = windows[-1]
    assert key == THU.isoformat() and since <= AFTER_MIDNIGHT < until
    months = local_month_windows(shop, 3, now=CLOSING)
    assert [k for k, _s, _e in months] == ["2026-07", "2026-08", "2026-09"]
    # Each month starts on the 1st at 04:00 and abuts the next with no gap.
    for i, (_k, since, until) in enumerate(months):
        assert since.astimezone(WIB).hour == 4 and since.astimezone(WIB).day == 1
        if i + 1 < len(months):
            assert until == months[i + 1][1]


# ── the metric layer and the dashboard (needs the local Postgres) ────────────


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
async def late_night(session_factory):
    """A café that closes at 23:30 with `day_start_hour = 4`. Two bills on
    Thursday night: 23:50 for 40.000 and 00:15 (Friday's clock) for 30.000."""
    async with session_factory() as s:
        biz = Business(name="Kopi Larut Malam", owner_phone=f"62961{uuid.uuid4().hex[:9]}",
                       timezone=TZ, day_start_hour=4)
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await seed_books(s, bid)
        await ensure_pricing_settings(s, bid)
        owner = Staff(business_id=bid, name="Bu Ratna", role="owner", pin_hash=hash_pin("1234"))
        sari = Staff(business_id=bid, name="Sari", pin_hash=hash_pin("2345"))
        kopi = Item(business_id=bid, name="Kopi", unit="cup", current_stock=D(50),
                    cost_price=D(8000), sell_price=D(10000))
        s.add_all([owner, sari, kopi])
        await s.flush()
        await open_item_stock(s, kopi, unit_cost=kopi.cost_price)
        await ensure_default_variant(s, kopi)

        async def sell(qty, paid, when):
            return await create_order(
                s, business_id=bid, staff_id=sari.id,
                lines=[OrderLineSpec(item_id=kopi.id, quantity=D(qty))],
                payments=[PaymentSpec(method="cash", amount=D(paid))], sold_at=when,
            )

        await sell(4, 40000, LAST_CALL)
        await sell(3, 30000, AFTER_MIDNIGHT)
        await s.commit()
        c = {"bid": bid, "kopi": kopi.id,
             "ownertok": create_token(business_id=str(bid), scope="owner")}
    yield c
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


async def test_the_metric_layer_puts_the_00_15_sale_under_the_previous_day(session_factory, late_night):
    """The roadmap's done-criterion, metric-layer half."""
    async with session_factory() as s:
        await _set_tenant(s, late_night["bid"])
        shop = await s.get(Business, late_night["bid"])
        assert shop.day_start_hour == 4

        # Cashing up at 01:00: "today" is still Thursday and holds both bills.
        today = await compute(s, shop, "revenue", period="today", now=CLOSING)
        assert today.value == D("70000.00")
        assert (await compute(s, shop, "transaction_count", period="today", now=CLOSING)).value == 2
        assert today.since == datetime(2026, 9, 3, 4, 0, tzinfo=WIB).astimezone(timezone.utc)

        # Thursday's daily series carries the 00:15 bill; Friday's is empty.
        windows = {k: (a, b) for k, a, b in local_day_windows(shop, 2, now=CLOSING)}
        assert set(windows) == {"2026-09-02", "2026-09-03"}
        thu_since, thu_until = windows["2026-09-03"]
        assert (await compute(s, shop, "revenue", since=thu_since, until=thu_until)).value == D("70000.00")

        # The same shop on a calendar day splits the night in two — the bug.
        shop.day_start_hour = 0
        await s.flush()
        assert (await compute(s, shop, "revenue", period="today", now=CLOSING)).value == D("30000.00")
        assert (await compute(s, shop, "revenue", period="yesterday", now=CLOSING)).value == D("40000.00")
        await s.rollback()


async def test_the_dashboard_puts_the_00_15_sale_under_the_previous_day(client, late_night):
    """The roadmap's done-criterion, dashboard half: the trend chart and the
    profit-and-loss statement, over HTTP, as the owner sees them."""
    auth = {"Authorization": f"Bearer {late_night['ownertok']}"}

    r = await client.get("/api/business", headers=auth)
    assert r.status_code == 200 and r.json()["day_start_hour"] == 4

    # The trend is dense and keyed by business day: Thursday holds both bills.
    days = (datetime.now(timezone.utc) - LAST_CALL).days + 3
    r = await client.get(f"/api/sales-trend?days={min(days, 365)}", headers=auth)
    assert r.status_code == 200
    by_day = {p["date"]: p for p in r.json()}
    assert by_day["2026-09-03"]["revenue"] == 70000.0
    assert by_day["2026-09-03"]["transactions"] == 2
    assert by_day["2026-09-04"]["revenue"] == 0.0

    # Laba rugi for "3 September" runs 03/09 04:00 → 04/09 04:00.
    r = await client.get("/api/statements/profit-loss?since=2026-09-03&until=2026-09-03", headers=auth)
    assert r.status_code == 200 and r.json()["revenue_total"] == "70000.00"
    r = await client.get("/api/statements/profit-loss?since=2026-09-04&until=2026-09-04", headers=auth)
    assert r.json()["revenue_total"] == "0.00"


async def test_the_owner_can_set_the_boundary_and_a_bad_hour_is_refused(client, late_night):
    auth = {"Authorization": f"Bearer {late_night['ownertok']}"}
    r = await client.patch("/api/business", json={"day_start_hour": 6}, headers=auth)
    assert r.status_code == 200 and r.json()["day_start_hour"] == 6
    assert (await client.get("/api/business", headers=auth)).json()["day_start_hour"] == 6
    for bad in (24, -1):
        assert (await client.patch("/api/business", json={"day_start_hour": bad}, headers=auth)).status_code == 422
    # A profile edit that does not mention the hour leaves it alone.
    r = await client.patch("/api/business", json={"name": "Kopi Larut Malam"}, headers=auth)
    assert r.status_code == 200 and r.json()["day_start_hour"] == 6
    await client.patch("/api/business", json={"day_start_hour": 4}, headers=auth)


async def test_the_database_refuses_an_out_of_range_hour(session_factory, late_night):
    """The API bound is a 422 in Indonesian; the check constraint is what makes
    it true of every writer, including a hand-run SQL statement."""
    from asyncpg.exceptions import CheckViolationError
    from sqlalchemy.exc import IntegrityError

    async with session_factory() as s:
        await _set_tenant(s, late_night["bid"])
        with pytest.raises(IntegrityError) as excinfo:
            await s.execute(
                text("update businesses set day_start_hour = 24 where id = :bid"),
                {"bid": str(late_night["bid"])},
            )
            await s.flush()
        assert isinstance(excinfo.value.orig.__cause__, CheckViolationError)
        await s.rollback()
