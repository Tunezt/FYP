"""M15-T11 — the void and refund screen, from the API the screen actually calls.

M15-T7 built the authorisation, the manager role and the `approvals` trail for a
screen that did not exist: `POST /pos/orders/{id}/void` and `/refund` were
tested but nothing in the app ever called them, so the runbook's instruction for
the commonest café incident was "write it on the paper sheet and call a
developer".

What was missing was the way in — a list to find the sale in. This file covers
that: the till's list of today's sales, the owner's list of all of them, the
receipt both read, and the reversal both post, asserted through to the reversing
lines, the stock movements, the ledger and the audit row.

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

from app.core.security import create_token, hash_pin
from app.main import app
from app.models import Approval, Business, Item, Order, OrderLine, Payment, RequestLog, Staff, StockMovement
from app.services.catalog import ensure_default_variant
from app.services.orders import OrderLineSpec, PaymentSpec, create_order, list_orders, order_number
from app.services.pricing import ensure_pricing_settings
from app.services.stock import open_item_stock

from tests.conftest import seed_books

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")
D = Decimal
WIB = ZoneInfo("Asia/Jakarta")

# The café runs a 4am business day, so "today's sales" at 00:30 must still be
# last night's — the till list and M15-T4 have to agree.
LAST_NIGHT = datetime(2026, 9, 3, 23, 50, tzinfo=WIB).astimezone(timezone.utc)
AFTER_MIDNIGHT = datetime(2026, 9, 4, 0, 15, tzinfo=WIB).astimezone(timezone.utc)
LAST_WEEK = datetime(2026, 8, 27, 14, 0, tzinfo=WIB).astimezone(timezone.utc)


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
async def shop(session_factory):
    """A café with a 4am business day, a manager on the floor, and three sales:
    one last week, one at 23:50 last night, and one at 00:15 after it."""
    async with session_factory() as s:
        biz = Business(name="Kopi Salah Pencet", owner_phone=f"62966{uuid.uuid4().hex[:9]}",
                       timezone="Asia/Jakarta", day_start_hour=4)
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await seed_books(s, bid)
        await ensure_pricing_settings(s, bid)
        owner = Staff(business_id=bid, name="Bu Ratna", role="owner", pin_hash=hash_pin("1234"))
        yudi = Staff(business_id=bid, name="Pak Yudi", role="manager", pin_hash=hash_pin("4321"))
        sari = Staff(business_id=bid, name="Sari", role="staff", pin_hash=hash_pin("2345"))
        kopi = Item(business_id=bid, name="Kopi", unit="cup", current_stock=D(50),
                    cost_price=D(8000), sell_price=D(20000))
        s.add_all([owner, yudi, sari, kopi])
        await s.flush()
        await open_item_stock(s, kopi, unit_cost=kopi.cost_price)
        variant = await ensure_default_variant(s, kopi)

        async def sell(qty, paid, when):
            return await create_order(
                s, business_id=bid, staff_id=sari.id,
                lines=[OrderLineSpec(item_id=kopi.id, quantity=D(qty))],
                payments=[PaymentSpec(method="cash", amount=D(paid))], sold_at=when,
            )

        old = await sell(1, 20000, LAST_WEEK)
        evening = await sell(3, 60000, LAST_NIGHT)
        late = await sell(2, 40000, AFTER_MIDNIGHT)
        await s.commit()
        c = {"bid": bid, "kopi": kopi.id, "variant": variant.id,
             "owner": owner.id, "yudi": yudi.id, "sari": sari.id,
             "old": old.order.id, "evening": evening.order.id, "late": late.order.id,
             "ownertok": create_token(business_id=str(bid), scope="owner"),
             "postok": create_token(business_id=str(bid), scope="pos", staff_id=str(sari.id))}
    yield c
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


# ── finding the sale ─────────────────────────────────────────────────────────


async def test_the_till_lists_the_business_day_not_the_calendar_day(session_factory, shop):
    """A 23:50 sale must still be on the cashier's list at 00:15, or the screen
    is useless in exactly the hour mistakes are most likely (M15-T4 + M15-T11)."""
    async with session_factory() as s:
        await _set_tenant(s, shop["bid"])
        biz = await s.get(Business, shop["bid"])
        from app.ai.periods import period_range

        since, until, _ = period_range("today", biz.timezone, now=AFTER_MIDNIGHT,
                                       day_start_hour=biz.day_start_hour)
        rows, total = await list_orders(s, since=since, until=until)
        assert total == 2
        assert [r.id for r in rows] == [shop["late"], shop["evening"]]      # newest first
        assert shop["old"] not in [r.id for r in rows]
        # …and the row says enough to recognise the sale across the counter.
        late = rows[0]
        assert late.number == order_number(shop["late"])
        assert (late.total, late.line_count, late.staff_name, late.status) == (D("40000.00"), 1, "Sari", "completed")


async def test_the_receipt_number_is_what_a_cashier_can_actually_type(session_factory, shop):
    async with session_factory() as s:
        await _set_tenant(s, shop["bid"])
        number = order_number(shop["evening"])
        for typed in (number, number.lower(), number[2:], f"{number[:4]}-{number[4:]}"):
            rows, total = await list_orders(s, q=typed)
            assert total == 1 and rows[0].id == shop["evening"], typed
        assert (await list_orders(s, q="ZZZZZZZZ"))[1] == 0


async def test_an_open_e_menu_ticket_is_not_in_the_list(session_factory, shop):
    """An unpaid QR order is not a sale, has its own screen, and cancelling one
    is a different action — it must not appear among things you can void."""
    async with session_factory() as s:
        await _set_tenant(s, shop["bid"])
        s.add(Order(business_id=shop["bid"], status="open", source="menu", total=D(15000),
                    sold_at=AFTER_MIDNIGHT, table_label="4"))
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, shop["bid"])
        rows, _total = await list_orders(s)
        assert all(r.status != "open" for r in rows)
        assert len(rows) == 3


async def test_the_till_list_is_reachable_and_the_owner_list_reaches_further(client, shop):
    r = await client.get("/pos/orders", headers=_auth(shop["postok"]))
    assert r.status_code == 200
    # The seeded sales are dated Sept 2026; "today" for the till is the real
    # today, so the shape is what matters here, not the contents.
    assert set(r.json()) == {"total", "rows"}

    r = await client.get("/api/orders?limit=50", headers=_auth(shop["ownertok"]))
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert [row["id"] for row in rows] == [str(shop["late"]), str(shop["evening"]), str(shop["old"])]
    assert rows[0]["number"] == order_number(shop["late"])

    r = await client.get(f"/api/orders?q={order_number(shop['old'])}", headers=_auth(shop["ownertok"]))
    assert r.json()["total"] == 1 and r.json()["rows"][0]["id"] == str(shop["old"])


async def test_both_lists_refuse_the_other_side_s_token(client, shop):
    assert (await client.get("/pos/orders", headers=_auth(shop["ownertok"]))).status_code == 403
    assert (await client.get("/api/orders", headers=_auth(shop["postok"]))).status_code == 403


# ── reading it before reversing it ───────────────────────────────────────────


async def test_the_owner_and_the_till_read_the_same_receipt(client, shop):
    till = await client.get(f"/pos/orders/{shop['evening']}/receipt", headers=_auth(shop["postok"]))
    owner = await client.get(f"/api/orders/{shop['evening']}/receipt", headers=_auth(shop["ownertok"]))
    assert till.status_code == owner.status_code == 200
    assert till.json() == owner.json()
    assert till.json()["number"] == order_number(shop["evening"])
    assert till.json()["total"] == "60000.00" and till.json()["status"] == "completed"
    assert (await client.get(f"/api/orders/{uuid.uuid4()}/receipt", headers=_auth(shop["ownertok"]))).status_code == 404


# ── the done-criterion: a cashier voids, with a manager PIN, through the API
#    the screen calls, and everything downstream lands ────────────────────────


async def test_a_cashier_voids_a_wrong_sale_and_everything_downstream_lands(session_factory, client, shop):
    order_id = shop["evening"]
    before = await client.get(f"/pos/orders/{order_id}/receipt", headers=_auth(shop["postok"]))
    assert before.json()["status"] == "completed"

    # No PIN, or a cashier's own, and nothing happens.
    assert (await client.post(f"/pos/orders/{order_id}/void", json={},
                              headers=_auth(shop["postok"]))).status_code == 422
    r = await client.post(f"/pos/orders/{order_id}/void", json={"manager_pin": "2345"},
                          headers=_auth(shop["postok"]))
    assert r.status_code == 403 and "manajer" in r.json()["detail"]

    # The manager is standing right there.
    r = await client.post(f"/pos/orders/{order_id}/void",
                          json={"manager_pin": "4321", "note": "salah pencet"},
                          headers=_auth(shop["postok"]))
    assert r.status_code == 200
    out = r.json()
    assert out["status"] == "voided"
    assert [D(l["quantity"]) for l in out["reversing_lines"]] == [D("-3.000")]
    assert D(out["reversing_lines"][0]["stock_after"]) == D("47.000")   # 50 - 1 - 3 - 2, plus the 3 back
    assert [D(p["amount"]) for p in out["reversing_payments"]] == [D("-60000.00")]

    async with session_factory() as s:
        await _set_tenant(s, shop["bid"])
        order = await s.get(Order, order_id)
        assert order.status == "voided"

        # M3-T4: reversing lines, not deletions — the original is still readable.
        lines = (await s.execute(select(OrderLine).where(OrderLine.order_id == order_id))).scalars().all()
        assert sorted(D(l.quantity) for l in lines) == [D("-3.000"), D("3.000")]
        payments = (await s.execute(select(Payment).where(Payment.order_id == order_id))).scalars().all()
        assert sorted(D(p.amount) for p in payments) == [D("-60000.00"), D("60000.00")]

        # …the stock movement carries the right reason and the shelf is square…
        reasons = (await s.execute(
            select(StockMovement.reason, StockMovement.qty_delta)
            .where(StockMovement.item_id == shop["kopi"]).order_by(StockMovement.created_at)
        )).all()
        assert ("sale_void", D("3.000")) in [(r_, D(q)) for r_, q in reasons]
        assert (await s.get(Item, shop["kopi"])).current_stock == D("47.000")

        # …the reconciliation invariant still holds (roadmap M2-T3)…
        summed = (await s.execute(
            select(func.coalesce(func.sum(StockMovement.qty_delta), 0))
            .where(StockMovement.item_id == shop["kopi"])
        )).scalar_one()
        assert D(summed) == (await s.get(Item, shop["kopi"])).current_stock

        # …the books balance…
        from app.models import JournalEntry, JournalLine

        entries = (await s.execute(
            select(JournalEntry).where(JournalEntry.source_id == order_id)
        )).scalars().all()
        assert {e.event_type for e in entries} == {"OrderCompleted", "OrderVoided"}
        for entry in entries:
            legs = (await s.execute(select(JournalLine).where(JournalLine.entry_id == entry.id))).scalars().all()
            assert sum(D(l.debit) for l in legs) == sum(D(l.credit) for l in legs)

        # …and M15-T7's audit row names who allowed it and who asked.
        appr = (await s.execute(select(Approval).where(Approval.order_id == order_id))).scalar_one()
        assert appr.action == "void" and appr.approved_by == shop["yudi"]
        assert appr.approver_role == "manager" and appr.requested_by == shop["sari"]
        assert appr.amount == D("60000.00") and appr.note == "salah pencet"

    # The screen can show the result: the receipt now reads as voided.
    after = await client.get(f"/pos/orders/{order_id}/receipt", headers=_auth(shop["postok"]))
    assert after.json()["status"] == "voided"
    # And it cannot be voided twice.
    r = await client.post(f"/pos/orders/{order_id}/void", json={"manager_pin": "4321"},
                          headers=_auth(shop["postok"]))
    assert r.status_code == 409 and "dibatalkan" in r.json()["detail"]


async def test_the_owner_reverses_a_sale_found_after_the_shift_closed(session_factory, client, shop):
    """The other half of the screen: last week's sale, from the office, with the
    same guard and the same audit row — a void from the dashboard must not be a
    different kind of void."""
    order_id = shop["old"]
    owner = _auth(shop["ownertok"])
    assert (await client.post(f"/api/orders/{order_id}/refund", json={"manager_pin": "2345"},
                              headers=owner)).status_code == 403

    r = await client.post(f"/api/orders/{order_id}/refund",
                          json={"manager_pin": "1234", "restock": False, "note": "kopi tumpah"},
                          headers=owner)
    assert r.status_code == 200 and r.json()["status"] == "refunded"
    # restock=False: the money goes back, the cup does not.
    assert r.json()["reversing_lines"][0]["stock_after"] is None

    async with session_factory() as s:
        await _set_tenant(s, shop["bid"])
        assert (await s.get(Item, shop["kopi"])).current_stock == D("44.000")   # 50 − 1 − 3 − 2
        appr = (await s.execute(select(Approval).where(Approval.order_id == order_id))).scalar_one()
        assert appr.action == "refund" and appr.approved_by == shop["owner"]
        assert appr.approver_role == "owner" and appr.note == "kopi tumpah"
        # The channel says which screen it came from, for the request log.
        logged = (await s.execute(
            select(RequestLog.channel, RequestLog.path).where(RequestLog.path.like("%refund%"))
        )).all()
        assert ("dashboard", "/api/orders/{id}/refund") in logged


async def test_a_refund_that_restocks_puts_the_cups_back(session_factory, client, shop):
    r = await client.post(f"/api/orders/{shop['late']}/refund",
                          json={"manager_pin": "4321", "restock": True},
                          headers=_auth(shop["ownertok"]))
    assert r.status_code == 200
    assert D(r.json()["reversing_lines"][0]["stock_after"]) == D("46.000")
    async with session_factory() as s:
        await _set_tenant(s, shop["bid"])
        assert (await s.get(Item, shop["kopi"])).current_stock == D("46.000")
        summed = (await s.execute(
            select(func.coalesce(func.sum(StockMovement.qty_delta), 0))
            .where(StockMovement.item_id == shop["kopi"])
        )).scalar_one()
        assert D(summed) == D("46.000")


async def test_the_reversal_endpoints_refuse_the_other_side_s_token(client, shop):
    assert (await client.post(f"/api/orders/{shop['old']}/void", json={"manager_pin": "1234"},
                              headers=_auth(shop["postok"]))).status_code == 403
    assert (await client.post(f"/pos/orders/{shop['old']}/void", json={"manager_pin": "1234"},
                              headers=_auth(shop["ownertok"]))).status_code == 403
