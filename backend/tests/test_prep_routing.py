"""prt-2 onwards — where each item is prepared, and what that routes.

Bar (at the front, beside the cashier), Dapur (the back kitchen), or nothing to
prepare. Set explicitly per item by the owner; snapshotted on every sold line.
Needs the local Postgres (roadmap §2). No skip marker.
"""
import uuid
from decimal import Decimal

from sqlalchemy import select

from app.core.security import create_token
from app.models import Item, OrderLine
from tests.test_service_journey import (  # noqa: F401 (fixtures)
    _auth, _cash, _line, _make_cafe, _ref, _set_tenant, cafe, client, engine, session_factory,
)

D = Decimal


def _owner(c):
    return {"Authorization": f"Bearer {create_token(business_id=str(c['bid']), scope='owner')}"}


async def test_the_station_is_set_by_the_owner_never_guessed(client, session_factory, cafe):
    c = cafe
    owner = _owner(c)
    # A new item has no station until someone says where it is made, whatever its name.
    made = await client.post("/api/items", headers=owner, json={"name": "Nasi Goreng Kampung", "unit": "porsi", "sell_price": "30000"})
    assert made.status_code == 201 and made.json()["prep_station"] is None
    items = {i["name"]: i for i in (await client.get("/pos/items", headers=_auth(c["pos"]))).json()}
    assert items["Americano"]["prep_station"] is None and items["Nasi Goreng Kampung"]["prep_station"] is None
    assert (await client.patch(f"/api/items/{c['americano']}", headers=owner, json={"prep_station": "bar"})).json()["prep_station"] == "bar"
    assert (await client.patch(f"/api/items/{c['roti']}", headers=owner, json={"prep_station": "kitchen"})).json()["prep_station"] == "kitchen"
    assert (await client.patch(f"/api/items/{c['roti']}", headers=owner, json={"prep_station": "oven"})).status_code == 422
    listed = {i["name"]: i for i in (await client.get("/api/items", headers=owner)).json()}
    assert listed["Americano"]["prep_station"] == "bar" and listed["Roti"]["prep_station"] == "kitchen"
    # A plain till token cannot change it.
    assert (await client.patch(f"/api/items/{c['roti']}", headers=_auth(c["pos"]), json={"prep_station": "bar"})).status_code == 403


async def test_a_sold_line_remembers_where_it_was_made(client, session_factory, cafe):
    c = cafe
    owner = _owner(c)
    await client.patch(f"/api/items/{c['americano']}", headers=owner, json={"prep_station": "bar"})
    await client.patch(f"/api/items/{c['roti']}", headers=owner, json={"prep_station": "kitchen"})
    sale = (await client.post("/pos/orders", headers=_auth(c["pos"]), json={
        "lines": [_line(c, variant="standar", mods=["panas"]), _line(c, item="roti")], "payments": _cash(33000)})).json()
    placed = (await client.post(f"/menu/{c['menu']}/orders", json={"lines": [_line(c, item="roti")], "order_type": "takeaway"})).json()
    # The owner moves the roti to the front display tomorrow; yesterday's line does not move.
    await client.patch(f"/api/items/{c['roti']}", headers=owner, json={"prep_station": "bar"})
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        stations = dict((await s.execute(
            select(OrderLine.item_id, OrderLine.prep_station).where(OrderLine.order_id == uuid.UUID(sale["id"]))
        )).all())
        assert stations == {c["americano"]: "bar", c["roti"]: "kitchen"}
        from app.models import Order

        ticket = await s.get(Order, uuid.UUID(placed["id"]))
        assert ticket.cart["lines"][0]["prep_station"] == "kitchen"
        assert (await s.get(Item, c["roti"])).prep_station == "bar"


# ── prt-3: the print queue and its documents ────────────────────────────────

import asyncio  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.models import Business, Order, PrintJob  # noqa: E402
from app.services import printing  # noqa: E402


async def _stations(client, c, *, americano="bar", roti="kitchen"):
    owner = _owner(c)
    if americano:
        await client.patch(f"/api/items/{c['americano']}", headers=owner, json={"prep_station": americano})
    if roti:
        await client.patch(f"/api/items/{c['roti']}", headers=owner, json={"prep_station": roti})


async def _jobs(s, order_id):
    return (await s.execute(
        select(PrintJob).where(PrintJob.order_id == uuid.UUID(str(order_id))).order_by(PrintJob.created_at, PrintJob.kind)
    )).scalars().all()


def _texts(doc):
    out = []
    for b in doc["blocks"]:
        for k in ("text", "left", "right", "name", "size", "notes", "amount", "flag"):
            if b.get(k):
                out.append(str(b[k]))
        out += b.get("modifiers", [])
    return out


async def test_a_mixed_order_prints_a_receipt_a_bar_slip_and_a_kitchen_slip_under_one_number(client, session_factory, cafe):
    c = cafe
    await _stations(client, c)
    sale = (await client.post("/pos/orders", headers=_auth(c["pos"]), json={
        "lines": [_line(c, variant="large", mods=["dingin", "shot"], qty=2, notes="es sedikit"), _line(c, item="roti", notes="potong dua")],
        "payments": _cash(2 * 27000 + 15000), "order_type": "dine_in", "table_label": "Meja 7", "guest_name": "Laras",
    })).json()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        jobs = {j.kind: j for j in await _jobs(s, sale["id"])}
    assert set(jobs) == {"receipt", "bar_ticket", "kitchen_ticket"}
    assert (jobs["receipt"].printer, jobs["bar_ticket"].printer, jobs["kitchen_ticket"].printer) == ("front", "front", "kitchen")
    assert all(j.status == "pending" and j.copy == "original" for j in jobs.values())
    bar, kitchen, receipt = (_texts(jobs[k].document) for k in ("bar_ticket", "kitchen_ticket", "receipt"))
    # Same identity on all three: the table first, the number under it.
    for doc in (jobs["bar_ticket"].document, jobs["kitchen_ticket"].document, jobs["receipt"].document):
        heads = [b["text"] for b in doc["blocks"] if b["t"] in ("banner", "line")]
        assert heads[:2] == ["MEJA 7", "Pesanan 001"]
    # The bar slip is only the bar's work, every choice written out; no money, no name.
    assert "Americano" in bar and "Large" in bar and "Dingin" in bar and "Extra shot" in bar and "es sedikit" in bar
    assert "Roti" not in bar and not any("Rp" in t for t in bar) and "Laras" not in bar
    assert "Ref 001-B0" in bar and "Makan di sini" in bar
    assert "Roti" in kitchen and "potong dua" in kitchen and "Americano" not in kitchen and not any("Rp" in t for t in kitchen)
    # The receipt has everything, with prices and the payment.
    assert "Americano" in receipt and "Roti" in receipt and "Rp 69.000" in receipt and "Tunai" in receipt


async def test_a_drink_only_order_sends_nothing_to_the_kitchen_and_none_prints_no_slip(client, session_factory, cafe):
    c = cafe
    await _stations(client, c, roti="none")
    drinks = (await client.post("/pos/orders", headers=_auth(c["pos"]), json={
        "lines": [_line(c, variant="standar", mods=["panas"])], "payments": _cash(18000)})).json()
    bottle = (await client.post("/pos/orders", headers=_auth(c["pos"]), json={
        "lines": [_line(c, item="roti")], "payments": _cash(15000)})).json()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert sorted(j.kind for j in await _jobs(s, drinks["id"])) == ["bar_ticket", "receipt"]
        assert [j.kind for j in await _jobs(s, bottle["id"])] == ["receipt"]
        banner = [b["text"] for b in (await _jobs(s, drinks["id"]))[0].document["blocks"] if b["t"] == "banner"]
        assert banner == ["PESANAN 001"]


async def test_an_item_with_no_station_goes_to_the_front_and_says_so(client, session_factory, cafe):
    c = cafe
    await _stations(client, c, americano=None, roti="kitchen")
    sale = (await client.post("/pos/orders", headers=_auth(c["pos"]), json={
        "lines": [_line(c, variant="standar", mods=["panas"])], "payments": _cash(18000)})).json()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        bar = {j.kind: j for j in await _jobs(s, sale["id"])}["bar_ticket"]
        assert "TUJUAN BELUM DIATUR" in _texts(bar.document)


async def test_an_addition_prints_only_its_own_items_labelled_as_a_batch(client, session_factory, cafe):
    c = cafe
    await _stations(client, c)
    pos = _auth(c["pos"])
    first = (await client.post("/pos/orders", headers=pos, json={
        "lines": [_line(c, variant="standar", mods=["panas"]), _line(c, item="roti")], "payments": _cash(33000), "order_type": "takeaway"})).json()
    extra = (await client.post("/pos/orders", headers=pos, json={
        "lines": [_line(c, item="roti", qty=2)], "payments": _cash(30000), "parent_order_id": first["id"]})).json()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert len(await _jobs(s, first["id"])) == 3               # the original's paper is not produced again
        added = {j.kind: j for j in await _jobs(s, extra["id"])}
        assert set(added) == {"receipt", "kitchen_ticket"}          # no bar items in the addition, so no bar slip
        slip = added["kitchen_ticket"].document
        labels = [b["text"] for b in slip["blocks"] if b["t"] in ("label", "banner", "line")]
        assert labels[:3] == ["TAMBAHAN", "PESANAN 001", "Tambahan 1"]
        assert [(b["qty"], b["name"]) for b in slip["blocks"] if b["t"] == "item"] == [("2", "Roti")]
        assert "Ref 001-D1" in _texts(slip)
        receipt = _texts(added["receipt"].document)
        assert "Rp 30.000" in receipt and "Americano" not in receipt


async def test_payment_retries_create_no_second_print_job(client, session_factory, cafe):
    c = cafe
    await _stations(client, c)
    pos = _auth(c["pos"])
    body = {"lines": [_line(c, item="roti")], "payments": _cash(15000), "client_ref": _ref()}
    a, b = await asyncio.gather(client.post("/pos/orders", headers=pos, json=body), client.post("/pos/orders", headers=pos, json=body))
    assert a.json()["id"] == b.json()["id"]
    ticket = (await client.post(f"/menu/{c['menu']}/orders", json={"lines": [_line(c, item="roti")], "order_type": "takeaway"})).json()
    pay = {"payments": _cash(15000), "client_ref": _ref()}
    for _ in range(3):
        assert (await client.post(f"/pos/tickets/{ticket['id']}/settle", headers=pos, json=pay)).status_code == 200
    draft = (await client.post("/pos/drafts", headers=pos, json={"lines": [_line(c, item="roti")]})).json()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert sorted(j.kind for j in await _jobs(s, a.json()["id"])) == ["kitchen_ticket", "receipt"]
        assert sorted(j.kind for j in await _jobs(s, ticket["id"])) == ["kitchen_ticket", "receipt"]
        assert await _jobs(s, draft["id"]) == []                   # unpaid owes no paper


async def test_the_queue_is_honest_about_failure_uncertainty_reprints_and_confirmation(session_factory, client, cafe):
    c = cafe
    await _stations(client, c)
    await client.post("/pos/orders", headers=_auth(c["pos"]), json={"lines": [_line(c, item="roti")], "payments": _cash(15000)})
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        k1 = await printing.claim_next(s, printer="kitchen", device="dapur-1")
        assert k1 is not None and k1.status == "claimed" and k1.kind == "kitchen_ticket"
        assert await printing.claim_next(s, printer="kitchen", device="dapur-2") is None
        failed = await printing.report_result(s, job_id=k1.id, ok=False, device="dapur-1", error="kertas habis")
        assert failed.status == "failed" and failed.error == "kertas habis"
        assert (await printing.retry(s, job_id=k1.id)).status == "pending"
        again = await printing.claim_next(s, printer="kitchen", device="dapur-1")
        assert again.id == k1.id and again.attempts == 2
        assert printing.display_status(again) == "sending"
        assert printing.display_status(again, now=again.claimed_at + timedelta(minutes=5)) == "uncertain"
        with pytest.raises(printing.PrintInvalid):
            await printing.retry(s, job_id=again.id)             # paper may exist: no silent re-queue
        copy = await printing.reprint(s, job_id=again.id, staff_id=c["sari"])
        assert copy.copy == "reprint" and copy.reprint_of == again.id and copy.status == "pending"
        texts = _texts(copy.document)
        assert "CETAK ULANG" in texts and any(t.startswith("Cetak ulang ke-1") and "Sari" in t for t in texts)
        second = await printing.reprint(s, job_id=copy.id, staff_id=c["sari"])
        assert second.dedupe_key.endswith(":reprint:2") and second.reprint_of == again.id
        confirmed = await printing.confirm_printed(s, job_id=again.id, staff_id=c["sari"])
        assert confirmed.status == "printed" and confirmed.confirmed_by == c["sari"]
        with pytest.raises(printing.PrintInvalid):
            await printing.report_result(s, job_id=again.id, ok=False, device="dapur-1")
        await s.commit()


async def test_cancelling_after_a_slip_may_be_on_paper_prints_a_batal_notice(client, session_factory, cafe):
    c = cafe
    await _stations(client, c)
    pos = _auth(c["pos"])
    sale = (await client.post("/pos/orders", headers=pos, json={
        "lines": [_line(c, variant="standar", mods=["panas"]), _line(c, item="roti")], "payments": _cash(33000),
        "order_type": "dine_in", "table_label": "Meja 3"})).json()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        k = await printing.claim_next(s, printer="kitchen", device="dapur-1")
        await printing.report_result(s, job_id=k.id, ok=True, device="dapur-1")
        await s.commit()
    voided = await client.post(f"/pos/orders/{sale['id']}/void", headers=pos, json={"manager_pin": "1234", "note": "salah meja"})
    assert voided.status_code == 200, voided.text
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        jobs = {j.kind: j for j in await _jobs(s, sale["id"])}
        assert jobs["kitchen_ticket"].status == "printed"
        notice = jobs["kitchen_cancel"]
        assert notice.printer == "kitchen" and notice.status == "pending"
        texts = _texts(notice.document)
        assert "BATAL" in texts and "MEJA 3" in texts and "Roti" in texts and "Americano" not in texts
        assert jobs["bar_ticket"].status == "cancelled" and "bar_cancel" not in jobs
        assert jobs["receipt"].status == "pending"
        assert (await s.get(Order, uuid.UUID(sale["id"]))).status == "voided"
        assert (await s.get(Item, c["roti"])).current_stock == D(30)


async def test_a_backdated_paper_sale_prints_nothing(client, session_factory, cafe):
    c = cafe
    await _stations(client, c)
    async with session_factory() as s:
        created = (await s.get(Business, c["bid"])).created_at
    # Typed in from paper afterwards (entry_source manual_backdated), at a time the shop existed.
    sold_at = min(created + timedelta(seconds=1), datetime.now(timezone.utc)).isoformat()
    made = await client.post("/api/backdated-sales", headers=_owner(c), json={
        "sold_at": sold_at, "staff_id": str(c["sari"]), "payment_method": "cash",
        "lines": [{"item_id": str(c["roti"]), "quantity": "1"}]})
    assert made.status_code in (200, 201), made.text
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert await _jobs(s, made.json()["id"]) == []


async def test_print_jobs_are_tenant_isolated(client, session_factory, cafe):
    c = cafe
    await _stations(client, c)
    other = await _make_cafe(session_factory, name="Tetangga Cetak")
    try:
        sale = (await client.post("/pos/orders", headers=_auth(c["pos"]), json={"lines": [_line(c, item="roti")], "payments": _cash(15000)})).json()
        async with session_factory() as s:
            row = (await s.execute(text("select relrowsecurity, relforcerowsecurity from pg_class where relname = 'print_jobs'"))).one()
            assert row == (True, True)
            assert (await s.execute(text("select policyname from pg_policies where tablename = 'print_jobs'"))).scalars().all() == ["tenant_isolation"]
        async with session_factory() as s:
            await _set_tenant(s, other["bid"])
            assert (await s.execute(select(PrintJob))).scalars().all() == []
            assert await printing.claim_next(s, printer="kitchen", device="tetangga") is None
            s.add(PrintJob(business_id=c["bid"], order_id=uuid.UUID(sale["id"]), printer="front", kind="receipt",
                           dedupe_key="x", document={"v": 1, "blocks": []}))
            with pytest.raises(Exception):
                await s.flush()
            await s.rollback()
    finally:
        async with session_factory() as s:
            row = await s.get(Business, other["bid"])
            if row:
                await s.delete(row)
            await s.commit()
