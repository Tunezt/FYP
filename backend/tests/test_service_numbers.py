"""prt-1 — the daily service number ("Pesanan 042").

One sequence per business per business day, shared by the till and the QR
menu, allocated once when an order is first persisted, and carried by every
screen and document that names the order. Driven through the real API against
the local Postgres (roadmap §2). No skip marker.
"""
import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.core.security import create_token
from app.models import Business, Order, ServiceNumberCounter
from app.services.orders import OrderLineSpec, PaymentSpec, create_order
from app.services.service_numbers import service_label
from app.services.tickets import hold_draft
from tests.test_service_journey import (  # noqa: F401 (fixtures)
    _auth, _cash, _line, _make_cafe, _ref, _set_tenant, cafe, client, engine, session_factory,
)

D = Decimal


async def test_concurrent_till_and_qr_orders_get_distinct_consecutive_numbers(client, session_factory, cafe):
    c = cafe
    pos = _auth(c["pos"])
    calls = []
    for i in range(12):
        if i % 3 == 0:
            calls.append(client.post(f"/menu/{c['menu']}/orders", json={"lines": [_line(c, item="roti")], "order_type": "takeaway"}))
        elif i % 3 == 1:
            calls.append(client.post("/pos/drafts", headers=pos, json={"lines": [_line(c, item="roti")]}))
        else:
            calls.append(client.post("/pos/orders", headers=pos, json={"lines": [_line(c, item="roti")], "payments": _cash(15000)}))
    responses = await asyncio.gather(*calls)
    assert all(r.status_code == 201 for r in responses), [r.text for r in responses if r.status_code != 201]
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        numbers = sorted((await s.execute(select(Order.service_number).where(Order.service_number.is_not(None)))).scalars().all())
        assert numbers == list(range(1, 13))
        dates = set((await s.execute(select(Order.service_date))).scalars().all())
        assert len(dates) == 1
        assert (await s.get(ServiceNumberCounter, (c["bid"], next(iter(dates))))).last_number == 12
    labels = {r.json().get("order_no") for r in responses}
    assert labels == {f"{n:03d}" for n in range(1, 13)}


async def test_the_number_survives_retries_edits_payment_and_every_screen(client, session_factory, cafe):
    c = cafe
    pos = _auth(c["pos"])
    first = await client.post("/pos/orders", headers=pos, json={"lines": [_line(c, item="roti")], "payments": _cash(15000)})
    assert first.json()["order_no"] == "001"
    ref = _ref()
    body = {"lines": [_line(c, variant="large", mods=["dingin"])], "order_type": "dine_in", "table_label": "Meja 7", "client_ref": ref}
    placed = (await client.post(f"/menu/{c['menu']}/orders", json=body)).json()
    again = (await client.post(f"/menu/{c['menu']}/orders", json=body)).json()   # the phone retries
    assert placed["order_no"] == again["order_no"] == "002" and placed["id"] == again["id"]
    tid, key = placed["id"], placed["access_key"]
    edited = (await client.put(f"/pos/open-orders/{tid}", headers=pos, json={"rev": 0, "lines": [_line(c, variant="large", mods=["dingin"]), _line(c, item="roti")]})).json()
    assert edited["order_no"] == "002" and edited["rev"] == 1
    pay = {"payments": _cash(37000), "rev": 1, "client_ref": _ref()}
    paid = (await client.post(f"/pos/tickets/{tid}/settle", headers=pos, json=pay)).json()
    replay = (await client.post(f"/pos/tickets/{tid}/settle", headers=pos, json=pay)).json()
    assert paid["order_no"] == replay["order_no"] == "002"
    # Every place the order is named says the same thing.
    receipt = (await client.get(f"/pos/orders/{tid}/receipt", headers=pos)).json()
    watched = (await client.get(f"/menu/{c['menu']}/orders/{tid}?key={key}")).json()
    active = {a["id"]: a for a in (await client.get("/pos/active-orders", headers=pos)).json()}[tid]
    kitchen = {k["order_id"]: k for k in (await client.get("/pos/kitchen", headers=pos)).json()}[tid]
    assert receipt["order_no"] == watched["order_no"] == active["order_no"] == kitchen["order_no"] == "002"
    assert receipt["service_date"] == active["service_date"] and active["previous_day"] is False
    # The number is not a key: the old receipt reference still finds the sale.
    assert receipt["number"] == tid[-8:].upper()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert (await s.execute(select(func.count(Order.id)))).scalar_one() == 2


async def test_a_cancelled_number_is_never_reused(client, session_factory, cafe):
    c = cafe
    pos = _auth(c["pos"])
    draft = (await client.post("/pos/drafts", headers=pos, json={"lines": [_line(c, item="roti")]})).json()
    assert draft["order_no"] == "001"
    assert (await client.post(f"/pos/tickets/{draft['id']}/cancel", headers=pos, json={"reason": "tidak jadi"})).status_code == 200
    # A request that fails before anything is written does not burn a number either.
    refused = await client.post("/pos/orders", headers=pos, json={"lines": [_line(c, item="roti")], "payments": _cash(1)})
    assert refused.status_code == 422
    nxt = (await client.post("/pos/orders", headers=pos, json={"lines": [_line(c, item="roti")], "payments": _cash(15000)})).json()
    assert nxt["order_no"] == "002"


async def test_additions_share_the_number_and_count_batches(client, session_factory, cafe):
    c = cafe
    pos = _auth(c["pos"])
    first = (await client.post("/pos/orders", headers=pos, json={"lines": [_line(c, item="roti")], "payments": _cash(15000)})).json()
    one = (await client.post("/pos/orders", headers=pos, json={"lines": [_line(c, item="roti")], "payments": _cash(15000), "parent_order_id": first["id"]})).json()
    held = (await client.post("/pos/drafts", headers=pos, json={"lines": [_line(c, item="roti")], "parent_order_id": one["id"]})).json()
    other = (await client.post("/pos/orders", headers=pos, json={"lines": [_line(c, item="roti")], "payments": _cash(15000)})).json()
    assert (first["order_no"], first["batch_no"]) == ("001", 0)
    assert (one["order_no"], one["batch_no"]) == ("001", 1)
    assert (held["order_no"], held["batch_no"]) == ("001", 2)
    assert (other["order_no"], other["batch_no"]) == ("002", 0)


async def test_numbers_restart_on_the_business_day_and_history_says_which_day(client, session_factory, cafe):
    c = cafe
    async with session_factory() as s:
        biz = await s.get(Business, c["bid"])
        biz.day_start_hour = 4
        await s.commit()
    # 02:30 WIB on the 10th belongs to the business day of the 9th; 05:00 starts the 10th.
    late = datetime(2026, 9, 9, 19, 30, tzinfo=timezone.utc)     # 02:30 WIB, 10 Sep
    morning = datetime(2026, 9, 9, 22, 0, tzinfo=timezone.utc)   # 05:00 WIB, 10 Sep
    evening = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)   # 19:00 WIB, 9 Sep
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        made = []
        for at in (evening, late, morning):
            created = await create_order(s, business_id=c["bid"], staff_id=c["sari"], sold_at=at,
                                         lines=[OrderLineSpec(item_id=c["roti"], quantity=D(1))],
                                         payments=[PaymentSpec(method="cash", amount=D(15000))])
            made.append(created.order)
        await s.commit()
        assert [(o.service_date, service_label(o)) for o in made] == [
            (date(2026, 9, 9), "001"), (date(2026, 9, 9), "002"), (date(2026, 9, 10), "001"),
        ]
    owner = {"Authorization": f"Bearer {create_token(business_id=str(c['bid']), scope='owner')}"}
    found = (await client.get("/api/orders?q=001", headers=owner)).json()["rows"]
    assert sorted((r["service_date"], r["order_no"]) for r in found) == [("2026-09-09", "001"), ("2026-09-10", "001")]
    one_day = (await client.get("/api/orders?q=001&since=2026-09-10&until=2026-09-10", headers=owner)).json()["rows"]
    assert [(r["service_date"], r["order_no"]) for r in one_day] == [("2026-09-10", "001")]


async def test_an_order_carried_past_the_day_boundary_keeps_its_number_and_is_marked(client, session_factory, cafe):
    c = cafe
    yesterday = datetime.now(timezone.utc) - timedelta(days=1, hours=1)
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        held = await hold_draft(s, business_id=c["bid"], staff_id=c["sari"], now=yesterday,
                                lines=[OrderLineSpec(item_id=c["roti"], quantity=D(1))])
        await s.commit()
    pos = _auth(c["pos"])
    today = (await client.post("/pos/orders", headers=pos, json={"lines": [_line(c, item="roti")], "payments": _cash(15000)})).json()
    active = {a["id"]: a for a in (await client.get("/pos/active-orders", headers=pos)).json()}
    carried = active[str(held.id)]
    assert carried["order_no"] == "001" and carried["previous_day"] is True
    assert today["order_no"] == "001" and carried["service_date"] != (await _today_of(client, pos, today["id"]))
    # Paying it today does not renumber it.
    paid = (await client.post(f"/pos/tickets/{held.id}/settle", headers=pos, json={"payments": _cash(15000)})).json()
    assert paid["order_no"] == "001"
    receipt = (await client.get(f"/pos/orders/{held.id}/receipt", headers=pos)).json()
    assert receipt["service_date"] == carried["service_date"]


async def _today_of(client, pos, order_id):
    return (await client.get(f"/pos/orders/{order_id}/receipt", headers=pos)).json()["service_date"]


async def test_numbers_run_past_999_and_old_orders_keep_their_old_reference(client, session_factory, cafe):
    c = cafe
    pos = _auth(c["pos"])
    first = (await client.post("/pos/orders", headers=pos, json={"lines": [_line(c, item="roti")], "payments": _cash(15000)})).json()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        await s.execute(text("update service_number_counters set last_number = 999"))
        legacy = Order(business_id=c["bid"], status="completed", total=D(0), subtotal=D(0))
        s.add(legacy)
        await s.commit()
        legacy_id = legacy.id
    big = (await client.post("/pos/orders", headers=pos, json={"lines": [_line(c, item="roti")], "payments": _cash(15000)})).json()
    assert first["order_no"] == "001" and big["order_no"] == "1000"
    receipt = (await client.get(f"/pos/orders/{legacy_id}/receipt", headers=pos)).json()
    assert receipt["order_no"] == f"#{str(legacy_id)[-4:].upper()}" and receipt["service_date"] is None


async def test_service_number_counters_are_tenant_isolated(client, session_factory, cafe):
    c = cafe
    other = await _make_cafe(session_factory, name="Tetangga Nomor")
    try:
        await client.post("/pos/orders", headers=_auth(c["pos"]), json={"lines": [_line(c, item="roti")], "payments": _cash(15000)})
        theirs = (await client.post("/pos/orders", headers=_auth(other["pos"]), json={"lines": [_line(other, item="roti")], "payments": _cash(15000)})).json()
        assert theirs["order_no"] == "001"      # a sequence per business, not global
        async with session_factory() as s:
            row = (await s.execute(text("select relrowsecurity, relforcerowsecurity from pg_class where relname = 'service_number_counters'"))).one()
            assert row == (True, True)
            assert (await s.execute(text("select policyname from pg_policies where tablename = 'service_number_counters'"))).scalars().all() == ["tenant_isolation"]
        async with session_factory() as s:
            await _set_tenant(s, other["bid"])
            rows = (await s.execute(select(ServiceNumberCounter))).scalars().all()
            assert [r.business_id for r in rows] == [other["bid"]]
            s.add(ServiceNumberCounter(business_id=c["bid"], service_date=date(2030, 1, 1), last_number=1))
            with pytest.raises(Exception):
                await s.flush()
            await s.rollback()
    finally:
        async with session_factory() as s:
            row = await s.get(Business, other["bid"])
            if row:
                await s.delete(row)
            await s.commit()
