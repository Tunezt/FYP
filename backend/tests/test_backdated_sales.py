"""M15-T10 — a sale that happened on paper, entered afterwards.

The offline queue (M14) covers a lost connection. It does not cover a lost
*device*: one tablet is one point of failure, and when it dies mid-service the
staff keep selling on paper. Those sales still have to reach the books at the
time they actually happened — otherwise the day's takings are wrong, the shift
reconciliation is wrong, and the stock is wrong by however many cups were
poured.

The roadmap's done-criterion is `test_a_paper_sale_from_two_days_ago_reaches_the_books`:
entered, lands on the correct business day, moves stock, posts to the ledger,
and the reconciliation invariant still holds.

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai.periods import business_day, period_range
from app.core.security import create_token, hash_pin
from app.main import app
from app.metrics import compute
from app.models import Business, Item, JournalEntry, JournalLine, Order, Payment, Staff, StockMovement
from app.services.catalog import ensure_default_variant
from app.services.pricing import ensure_pricing_settings
from app.services.stock import open_item_stock

from tests.conftest import seed_books

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")
D = Decimal
WIB = ZoneInfo("Asia/Jakarta")


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


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def cafe(session_factory):
    """A café whose day starts at 04:00, whose tablet died the night before
    last, and which has a stack of paper slips waiting to be typed in."""
    async with session_factory() as s:
        biz = Business(name="Kopi Nota Kertas", owner_phone=f"62969{uuid.uuid4().hex[:9]}",
                       timezone="Asia/Jakarta", day_start_hour=4)
        # Backdating is refused before the business existed, so the fixture's
        # café has to be older than the paper it is entering.
        biz.created_at = datetime.now(timezone.utc) - timedelta(days=30)
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await seed_books(s, bid)
        await ensure_pricing_settings(s, bid)
        owner = Staff(business_id=bid, name="Bu Ratna", role="owner", pin_hash=hash_pin("1234"))
        sari = Staff(business_id=bid, name="Sari", role="staff", pin_hash=hash_pin("2345"))
        gone = Staff(business_id=bid, name="Mantan", role="staff", pin_hash=hash_pin("9999"), is_active=False)
        kopi = Item(business_id=bid, name="Kopi", unit="cup", current_stock=D(50),
                    cost_price=D(8000), sell_price=D(20000))
        s.add_all([owner, sari, gone, kopi])
        await s.flush()
        await open_item_stock(s, kopi, unit_cost=kopi.cost_price)
        variant = await ensure_default_variant(s, kopi)
        await s.commit()
        c = {"bid": bid, "kopi": kopi.id, "variant": variant.id, "owner": owner.id,
             "sari": sari.id, "gone": gone.id,
             "ownertok": create_token(business_id=str(bid), scope="owner"),
             "postok": create_token(business_id=str(bid), scope="pos", staff_id=str(sari.id))}
    yield c
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


def _body(cafe, when, qty="2", **kw):
    body = {
        "sold_at": when.isoformat(),
        "staff_id": str(cafe["sari"]),
        "lines": [{"item_id": str(cafe["kopi"]), "variant_id": str(cafe["variant"]), "quantity": qty}],
        "payment_method": "cash",
    }
    body.update(kw)
    return body


# ── the done-criterion ───────────────────────────────────────────────────────


async def test_a_paper_sale_from_two_days_ago_reaches_the_books(session_factory, client, cafe):
    """Entered, lands on the correct business day, moves stock, posts to the
    ledger, and the reconciliation invariant still holds."""
    # The tablet died at 23:40 the night before last; this slip is from 00:20,
    # which under a 4am day belongs to the day before it.
    two_days_ago = (datetime.now(timezone.utc) - timedelta(days=2)).astimezone(WIB)
    when = two_days_ago.replace(hour=0, minute=20, second=0, microsecond=0).astimezone(timezone.utc)

    r = await client.post("/api/backdated-sales",
                          json=_body(cafe, when, note="dari nota kertas, tablet mati"),
                          headers=_auth(cafe["ownertok"]))
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["entry_source"] == "manual_backdated"
    assert out["total"] == "40000.00" and out["staff_name"] == "Sari"
    order_id = uuid.UUID(out["id"])

    async with session_factory() as s:
        await _set_tenant(s, cafe["bid"])
        biz = await s.get(Business, cafe["bid"])
        order = await s.get(Order, order_id)

        # …at the time it actually happened, not the time it was typed…
        assert order.sold_at == when
        assert order.entry_source == "manual_backdated"
        assert order.status == "completed"

        # …on the business day the night belonged to (M15-T4): 00:20 under a
        # 4am boundary is the previous date, and the day's window contains it.
        day = business_day(when, biz.timezone, biz.day_start_hour)
        assert day == (when.astimezone(WIB).date() - timedelta(days=1))
        since, until, _ = period_range("today", biz.timezone, now=when, day_start_hour=biz.day_start_hour)
        assert since <= when < until
        assert (await compute(s, biz, "revenue", period="today", now=when)).value == D("40000.00")
        # …and not on the calendar day it would have landed on without M15-T4.
        assert (await compute(s, biz, "revenue", period="yesterday", now=when)).value == D("0.00")

        # …stock moved, dated with the sale rather than with the typing…
        assert (await s.get(Item, cafe["kopi"])).current_stock == D("48.000")
        movement = (await s.execute(
            select(StockMovement).where(StockMovement.reason == "sale")
        )).scalars().one()
        assert D(movement.qty_delta) == D("-2.000")

        # …the reconciliation invariant holds (roadmap M2-T3)…
        summed = (await s.execute(
            select(func.coalesce(func.sum(StockMovement.qty_delta), 0))
            .where(StockMovement.item_id == cafe["kopi"])
        )).scalar_one()
        assert D(summed) == (await s.get(Item, cafe["kopi"])).current_stock

        # …the books balance, and the entry is posted at the sale's own moment…
        entry = (await s.execute(
            select(JournalEntry).where(JournalEntry.source_id == order_id,
                                       JournalEntry.event_type == "OrderCompleted")
        )).scalar_one()
        legs = (await s.execute(select(JournalLine).where(JournalLine.entry_id == entry.id))).scalars().all()
        assert sum(D(l.debit) for l in legs) == sum(D(l.credit) for l in legs) > 0
        assert entry.posted_at == when

        # …the money is recorded as taken…
        payment = (await s.execute(select(Payment).where(Payment.order_id == order_id))).scalars().one()
        assert (payment.method, D(payment.amount)) == ("cash", D("40000.00"))

        # …and the slip's own note came with it.
        assert order.source == "pos"          # the channel is unchanged; only the entry differs
        lines = (await s.execute(text(
            "select notes from order_lines where order_id = :o"), {"o": str(order_id)})).scalars().all()
        assert lines == ["dari nota kertas, tablet mati"]


async def test_an_ordinary_sale_is_still_live(session_factory, client, cafe):
    """The tag has to mean something, so the default must not drift: a sale rung
    up at the till is `live`, and nothing about it changed."""
    r = await client.post("/pos/orders", json={
        "lines": [{"item_id": str(cafe["kopi"]), "variant_id": str(cafe["variant"]), "quantity": "1"}],
        "payments": [{"method": "cash", "amount": "20000"}],
    }, headers=_auth(cafe["postok"]))
    assert r.status_code == 201, r.text
    async with session_factory() as s:
        await _set_tenant(s, cafe["bid"])
        order = await s.get(Order, uuid.UUID(r.json()["id"]))
        assert order.entry_source == "live"


async def test_the_order_list_shows_which_sales_were_typed_in(client, cafe):
    """"Visible in the audit trail" means the owner can see it without asking
    the database — the list says which rows a human dated."""
    when = datetime.now(timezone.utc) - timedelta(days=2)
    await client.post("/api/backdated-sales", json=_body(cafe, when), headers=_auth(cafe["ownertok"]))
    await client.post("/pos/orders", json={
        "lines": [{"item_id": str(cafe["kopi"]), "variant_id": str(cafe["variant"]), "quantity": "1"}],
        "payments": [{"method": "cash", "amount": "20000"}],
    }, headers=_auth(cafe["postok"]))

    rows = (await client.get("/api/orders?limit=10", headers=_auth(cafe["ownertok"]))).json()["rows"]
    by_source = {r["entry_source"] for r in rows}
    assert by_source == {"live", "manual_backdated"}
    backdated = [r for r in rows if r["entry_source"] == "manual_backdated"]
    assert len(backdated) == 1 and backdated[0]["total"] == "40000.00"


async def test_a_paper_sale_stays_out_of_todays_open_till(session_factory, client, cafe):
    """Found by entering one through the real screen: `create_order` stamps the
    cashier's currently-open shift on a sale, which is right for a sale being
    rung up and wrong for one typed in from paper. Money taken two days ago is
    not in tonight's drawer, so counting it there makes the cashier come up
    short by exactly the amount somebody typed in to be helpful (M7-T3)."""
    from app.services.shifts import cash_summary, open_shift

    async with session_factory() as s:
        await _set_tenant(s, cafe["bid"])
        await open_shift(s, cafe["bid"], staff_id=cafe["sari"], opening_float=D(100000))
        await s.commit()

    when = datetime.now(timezone.utc) - timedelta(days=2)
    r = await client.post("/api/backdated-sales", json=_body(cafe, when), headers=_auth(cafe["ownertok"]))
    assert r.status_code == 201, r.text

    async with session_factory() as s:
        await _set_tenant(s, cafe["bid"])
        order = await s.get(Order, uuid.UUID(r.json()["id"]))
        assert order.shift_id is None
        payment = (await s.execute(select(Payment).where(Payment.order_id == order.id))).scalars().one()
        assert payment.shift_id is None
        # The open till still expects only its float: nothing was added to it.
        from app.models import Shift

        shift = (await s.execute(select(Shift).where(Shift.status == "open"))).scalars().one()
        assert (await cash_summary(s, shift)).expected_cash == D("100000.00")

    # …while a sale rung up now does land in the drawer, as it always has.
    r = await client.post("/pos/orders", json={
        "lines": [{"item_id": str(cafe["kopi"]), "variant_id": str(cafe["variant"]), "quantity": "1"}],
        "payments": [{"method": "cash", "amount": "20000"}],
    }, headers=_auth(cafe["postok"]))
    assert r.status_code == 201, r.text
    async with session_factory() as s:
        await _set_tenant(s, cafe["bid"])
        from app.models import Shift

        shift = (await s.execute(select(Shift).where(Shift.status == "open"))).scalars().one()
        assert (await cash_summary(s, shift)).expected_cash == D("120000.00")


# ── the guards ───────────────────────────────────────────────────────────────


async def test_a_future_sale_is_refused(client, cafe):
    """Choosing a sale's timestamp is the power to move takings between days.
    Forward is never legitimate."""
    later = datetime.now(timezone.utc) + timedelta(hours=2)
    r = await client.post("/api/backdated-sales", json=_body(cafe, later), headers=_auth(cafe["ownertok"]))
    assert r.status_code == 422 and "masa depan" in r.json()["detail"]


async def test_a_sale_older_than_the_window_or_older_than_the_business_is_refused(client, cafe):
    ancient = datetime.now(timezone.utc) - timedelta(days=90)
    r = await client.post("/api/backdated-sales", json=_body(cafe, ancient), headers=_auth(cafe["ownertok"]))
    assert r.status_code == 422 and "60 hari" in r.json()["detail"]

    before_opening = datetime.now(timezone.utc) - timedelta(days=45)   # café is 30 days old
    r = await client.post("/api/backdated-sales", json=_body(cafe, before_opening), headers=_auth(cafe["ownertok"]))
    assert r.status_code == 422 and "terdaftar" in r.json()["detail"]


async def test_the_sale_must_name_a_real_active_cashier(client, cafe):
    when = datetime.now(timezone.utc) - timedelta(days=1)
    for bad in (str(cafe["gone"]), str(uuid.uuid4())):
        r = await client.post("/api/backdated-sales", json=_body(cafe, when, staff_id=bad),
                              headers=_auth(cafe["ownertok"]))
        assert r.status_code == 422 and "Kasir" in r.json()["detail"], bad


async def test_it_is_owner_only(client, cafe):
    when = datetime.now(timezone.utc) - timedelta(days=1)
    assert (await client.post("/api/backdated-sales", json=_body(cafe, when),
                              headers=_auth(cafe["postok"]))).status_code == 403


async def test_it_refuses_to_oversell_exactly_like_the_till(session_factory, client, cafe):
    """A paper sale is not a licence to go below zero: the same atomic guard
    applies, and a refused entry leaves nothing behind."""
    when = datetime.now(timezone.utc) - timedelta(days=1)
    r = await client.post("/api/backdated-sales", json=_body(cafe, when, qty="999"),
                          headers=_auth(cafe["ownertok"]))
    assert r.status_code == 409
    async with session_factory() as s:
        await _set_tenant(s, cafe["bid"])
        assert (await s.get(Item, cafe["kopi"])).current_stock == D("50.000")
        assert (await s.execute(select(func.count(Order.id)))).scalar_one() == 0


async def test_an_unknown_item_is_a_404_and_writes_nothing(session_factory, client, cafe):
    when = datetime.now(timezone.utc) - timedelta(days=1)
    body = _body(cafe, when)
    body["lines"] = [{"item_id": str(uuid.uuid4()), "quantity": "1"}]
    assert (await client.post("/api/backdated-sales", json=body,
                              headers=_auth(cafe["ownertok"]))).status_code == 404
    async with session_factory() as s:
        await _set_tenant(s, cafe["bid"])
        assert (await s.execute(select(func.count(Order.id)))).scalar_one() == 0
