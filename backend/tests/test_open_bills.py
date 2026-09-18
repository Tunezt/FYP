"""bill-1 — a dine-in table keeps one open bill and pays when it leaves.

The owner's words: paying before every drink is a hassle for a table that is
"vibing"; the cashier adds to the table's order from Pesanan aktif; the order
closes when the customer pays. So a dine-in order is *sent* to be made in
batches before anything is paid: each send prints only its new items (Bar slip,
Dapur slip, and a nota for the table with the running total, BELUM DIBAYAR),
sent items are locked, a sent item is cancelled only with a reason (BATAL on
paper), and stock and books are written once, at payment.

Needs the local Postgres (roadmap §2). No skip marker.
"""
import asyncio
import uuid
from decimal import Decimal

from sqlalchemy import select

from app.models import Item, Order
from tests.test_prep_routing import _jobs, _owner, _printer_token, _stations, _texts
from tests.test_service_journey import (  # noqa: F401 (fixtures)
    _auth, _cash, _footprint, _line, _ref, _set_tenant, cafe, client, engine, session_factory,
)

D = Decimal


async def _bill(client, c, *lines, table="Meja 7", send=True, **extra):
    resp = await client.post("/pos/drafts", headers=_auth(c["pos"]), json={
        "lines": list(lines), "order_type": "dine_in", "table_label": table, "send": send, **extra})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _print_all(client, token, device="depan"):
    """A printer device takes and prints everything waiting for it."""
    kinds = []
    while (job := (await client.post("/print/agent/claim", headers=token, json={"device": device})).json()["job"]) is not None:
        kinds.append(job["kind"])
        await client.post(f"/print/agent/jobs/{job['id']}/result", headers=token, json={"device": device, "ok": True})
    return kinds


async def _order_jobs(session_factory, c, order_id):
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        return await _jobs(s, order_id)


async def _stock(session_factory, c, key):
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        return D((await s.get(Item, c[key])).current_stock)


def _uid(bill, name, size=None):
    return next(l["uid"] for l in bill["lines"] if l["name"] == name and (size is None or l["size"] == size))


# ── 1. Batches: each send prints only what is new; payment prints the receipt ─


async def test_a_table_orders_twice_and_pays_once_with_only_new_items_on_each_slip(client, session_factory, cafe):
    c = cafe
    await _stations(client, c)
    pos = _auth(c["pos"])
    bill = await _bill(client, c, _line(c, variant="standar", mods=["panas"]), _line(c, item="roti"))
    assert bill["payment"] == "unpaid" and bill["order_no"] == "001"
    assert bill["sent_batches"] == 1 and bill["unsent_count"] == 0
    assert {l["sent_batch"] for l in bill["lines"]} == {1} and all(l["uid"] for l in bill["lines"])

    first = {j.kind: j for j in await _order_jobs(session_factory, c, bill["id"])}
    assert set(first) == {"nota", "bar_ticket", "kitchen_ticket"}
    assert (first["nota"].printer, first["bar_ticket"].printer, first["kitchen_ticket"].printer) == ("front", "front", "kitchen")
    nota = _texts(first["nota"].document)
    assert "MEJA 7" in nota and "Pesanan 001" in nota and "BELUM DIBAYAR" in nota
    assert "TOTAL SEMENTARA" in nota and nota.count("Rp 33.000") >= 2       # this send and the bill so far
    bar = _texts(first["bar_ticket"].document)
    assert "Americano" in bar and "Roti" not in bar and not any("Rp" in t for t in bar)
    assert "Roti" in _texts(first["kitchen_ticket"].document)
    # Sent, not sold: nothing is taken or posted before payment.
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert await _footprint(s, uuid.UUID(bill["id"])) == {"lines": 0, "payments": 0, "journal": 0, "movements": 0, "kitchen_events": 0}
    assert await _stock(session_factory, c, "americano") == 40 and await _stock(session_factory, c, "roti") == 30

    # Another round: the cashier adds a large iced Americano from Pesanan aktif.
    more = await client.put(f"/pos/open-orders/{bill['id']}", headers=pos, json={
        "rev": bill["rev"], "lines": [_line(c, variant="large", mods=["dingin"])], "send": True})
    assert more.status_code == 200, more.text
    bill = more.json()
    assert bill["sent_batches"] == 2 and len(bill["lines"]) == 3 and D(bill["total"]) == 55000
    second = [j for j in await _order_jobs(session_factory, c, bill["id"]) if j.id not in {x.id for x in first.values()}]
    assert sorted(j.kind for j in second) == ["bar_ticket", "nota"]           # nothing new for the kitchen
    by_kind = {j.kind: _texts(j.document) for j in second}
    assert "TAMBAHAN" in by_kind["bar_ticket"] and "Tambahan 1" in by_kind["bar_ticket"]
    assert "Large" in by_kind["bar_ticket"] and "Standar" not in by_kind["bar_ticket"] and "Roti" not in by_kind["bar_ticket"]
    assert "Tambahan 1" in by_kind["nota"] and "Rp 22.000" in by_kind["nota"] and "Rp 55.000" in by_kind["nota"]
    assert "Roti" not in by_kind["nota"]

    # The table leaves and pays once. Only the receipt is new paper.
    paid = await client.post(f"/pos/tickets/{bill['id']}/settle", headers=pos, json={"payments": _cash(55000), "rev": bill["rev"]})
    assert paid.status_code == 200, paid.text
    after = await _order_jobs(session_factory, c, bill["id"])
    assert len(after) == 6 and [j.kind for j in after if j.kind == "receipt"] == ["receipt"]
    receipt = _texts(next(j for j in after if j.kind == "receipt").document)
    assert "Roti" in receipt and "Large" in receipt and "Rp 55.000" in receipt and "BELUM DIBAYAR" not in receipt
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        foot = await _footprint(s, uuid.UUID(bill["id"]))
        assert (foot["lines"], foot["payments"], foot["journal"], foot["movements"]) == (3, 1, 1, 3)
    assert await _stock(session_factory, c, "americano") == 38 and await _stock(session_factory, c, "roti") == 29
    # The front printer gives the table's paper in the order it was owed.
    front = await _printer_token(client, c, "front")
    assert await _print_all(client, front) == ["nota", "bar_ticket", "nota", "bar_ticket", "receipt"]


# ── 2. Sent items are locked; a cancellation has a reason and reaches paper ───


async def test_a_sent_item_is_locked_and_only_cancelled_with_a_reason_that_reaches_the_station(client, session_factory, cafe):
    c = cafe
    await _stations(client, c)
    pos = _auth(c["pos"])
    bill = await _bill(client, c, _line(c, variant="standar", mods=["panas"], qty=2), _line(c, item="roti"), table="Meja 4")
    americano, roti = _uid(bill, "Americano"), _uid(bill, "Roti")
    sent_line = next(l for l in bill["lines"] if l["uid"] == americano)
    as_device = {k: sent_line[k] for k in ("item_id", "variant_id", "modifier_ids", "notes")}
    # Changing a sent line through the cart is refused, whatever the device sends.
    changed = await client.put(f"/pos/open-orders/{bill['id']}", headers=pos, json={
        "rev": bill["rev"], "lines": [{**as_device, "quantity": "1", "uid": americano}]})
    assert changed.status_code == 409 and "sudah dikirim" in changed.json()["detail"]
    # Repeating it unchanged is fine and never duplicates it; new lines are unsent.
    kept = await client.put(f"/pos/open-orders/{bill['id']}", headers=pos, json={
        "rev": bill["rev"], "lines": [{**as_device, "quantity": "2", "uid": americano}, _line(c, item="roti")]})
    assert kept.status_code == 200, kept.text
    bill = kept.json()
    assert len(bill["lines"]) == 3 and bill["unsent_count"] == 1
    assert [l["quantity"] for l in bill["lines"] if l["uid"] == americano] == ["2"]
    # Omitting sent lines does not remove them either.
    omitted = await client.put(f"/pos/open-orders/{bill['id']}", headers=pos, json={"rev": bill["rev"], "lines": []})
    assert omitted.status_code == 200 and len(omitted.json()["lines"]) == 2 and omitted.json()["unsent_count"] == 0
    bill = omitted.json()

    # The front printer has printed the nota and Bar slip; the kitchen slip is still waiting.
    front = await _printer_token(client, c, "front")
    assert await _print_all(client, front) == ["nota", "bar_ticket"]
    no_reason = await client.post(f"/pos/open-orders/{bill['id']}/lines/{americano}/cancel", headers=pos,
                                  json={"rev": bill["rev"], "quantity": "1", "reason": "   "})
    assert no_reason.status_code == 422 and "alasan" in no_reason.json()["detail"]
    assert (await client.post(f"/pos/open-orders/{bill['id']}/lines/{americano}/cancel", headers=pos,
                              json={"rev": bill["rev"], "quantity": "1"})).status_code == 422
    one = await client.post(f"/pos/open-orders/{bill['id']}/lines/{americano}/cancel", headers=pos,
                            json={"rev": bill["rev"], "quantity": "1", "reason": "salah ukuran"})
    assert one.status_code == 200, one.text
    bill = one.json()
    assert [l["quantity"] for l in bill["lines"] if l["uid"] == americano] == ["1"] and D(bill["total"]) == 33000
    assert [(x["name"], x["quantity"], x["reason"]) for x in bill["cancelled_lines"]] == [("Americano", "1", "salah ukuran")]
    jobs = await _order_jobs(session_factory, c, bill["id"])
    notice = next(j for j in jobs if j.kind == "bar_cancel")
    texts = _texts(notice.document)
    assert notice.printer == "front" and notice.status == "pending"
    assert "BATAL" in texts and "Americano" in texts and "Alasan: salah ukuran" in texts and "Roti" not in texts
    assert next(b for b in notice.document["blocks"] if b.get("t") == "item")["qty"] == "1"
    # The roti's slip never left the queue and has nothing else on it: withdrawn, no BATAL.
    gone = await client.post(f"/pos/open-orders/{bill['id']}/lines/{roti}/cancel", headers=pos,
                             json={"rev": bill["rev"], "reason": "tamu tidak jadi"})
    assert gone.status_code == 200, gone.text
    jobs = await _order_jobs(session_factory, c, bill["id"])
    kitchen = [j for j in jobs if j.printer == "kitchen"]
    assert [(j.kind, j.status) for j in kitchen] == [("kitchen_ticket", "cancelled")]
    assert [l["name"] for l in gone.json()["lines"]] == ["Americano"]
    # Nothing is deleted: both cancellations are kept on the order.
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        order = await s.get(Order, uuid.UUID(bill["id"]))
        assert [v["reason"] for v in order.cart["voids"]] == ["salah ukuran", "tamu tidak jadi"]
    # A line that was never sent is not "cancelled"; it is simply edited out.
    assert (await client.post(f"/pos/open-orders/{bill['id']}/lines/nope123/cancel", headers=pos,
                              json={"rev": gone.json()["rev"], "reason": "x"})).status_code == 404


# ── 3. One open bill per table ───────────────────────────────────────────────


async def test_a_table_has_one_open_bill_however_its_name_is_typed(client, session_factory, cafe):
    c = cafe
    pos = _auth(c["pos"])
    first = await _bill(client, c, _line(c, item="roti"), table="Meja 7", send=False)
    for label in ("meja7", "7", " MEJA  7 "):
        again = await client.post("/pos/drafts", headers=pos, json={
            "lines": [_line(c, item="roti")], "order_type": "dine_in", "table_label": label})
        assert again.status_code == 409, label
        assert again.headers["X-Open-Bill-Id"] == first["id"] and "Meja 7 sudah punya tagihan terbuka" in again.json()["detail"]
    # Takeaway is not a table; another table is another bill.
    assert (await client.post("/pos/drafts", headers=pos, json={
        "lines": [_line(c, item="roti")], "order_type": "takeaway", "table_label": "7"})).status_code == 201
    eight = await _bill(client, c, _line(c, item="roti"), table="Meja 8", send=False)
    moved = await client.put(f"/pos/open-orders/{eight['id']}", headers=pos, json={
        "rev": eight["rev"], "lines": [_line(c, item="roti")], "table_label": "7"})
    assert moved.status_code == 409 and moved.headers["X-Open-Bill-Id"] == first["id"]
    # Two tablets opening Meja 9 at the same moment: exactly one bill.
    racing = await asyncio.gather(*[client.post("/pos/drafts", headers=pos, json={
        "lines": [_line(c, item="roti")], "order_type": "dine_in", "table_label": "Meja 9"}) for _ in range(4)])
    assert sorted(r.status_code for r in racing) == [201, 409, 409, 409]
    # Once the table has paid, it can start a new bill.
    paid = await client.post(f"/pos/tickets/{first['id']}/settle", headers=pos, json={"payments": _cash(15000), "rev": first["rev"]})
    assert paid.status_code == 200, paid.text
    assert (await client.post("/pos/drafts", headers=pos, json={
        "lines": [_line(c, item="roti")], "order_type": "dine_in", "table_label": "Meja 7"})).status_code == 201


# ── 4. What can be sent, and only once ───────────────────────────────────────


async def test_sending_is_for_dine_in_tables_and_happens_once(client, session_factory, cafe):
    c = cafe
    await _stations(client, c)
    pos = _auth(c["pos"])
    away = (await client.post("/pos/drafts", headers=pos, json={"lines": [_line(c, item="roti")], "order_type": "takeaway"})).json()
    r = await client.post(f"/pos/open-orders/{away['id']}/send", headers=pos, json={"rev": away["rev"]})
    assert r.status_code == 422 and "bawa pulang" in r.json()["detail"]
    nameless = (await client.post("/pos/drafts", headers=pos, json={"lines": [_line(c, item="roti")], "order_type": "dine_in"})).json()
    r = await client.post(f"/pos/open-orders/{nameless['id']}/send", headers=pos, json={"rev": nameless["rev"]})
    assert r.status_code == 422 and "nomor meja" in r.json()["detail"]

    bill = await _bill(client, c, _line(c, item="roti"), _line(c, variant="standar", mods=["panas"]), table="Meja 2", send=False)
    assert bill["sent_batches"] == 0 and bill["unsent_count"] == 2
    stale = await client.post(f"/pos/open-orders/{bill['id']}/send", headers=pos, json={"rev": bill["rev"] + 1})
    assert stale.status_code == 409
    taps = await asyncio.gather(*[client.post(f"/pos/open-orders/{bill['id']}/send", headers=pos, json={"rev": bill["rev"]}) for _ in range(3)])
    assert sorted(t.status_code for t in taps) == [200, 409, 409]
    jobs = await _order_jobs(session_factory, c, bill["id"])
    assert sorted(j.kind for j in jobs) == ["bar_ticket", "kitchen_ticket", "nota"]
    sent = next(t.json() for t in taps if t.status_code == 200)
    nothing = await client.post(f"/pos/open-orders/{bill['id']}/send", headers=pos, json={"rev": sent["rev"]})
    assert nothing.status_code == 409 and "Tidak ada item baru" in nothing.json()["detail"]
    # A replayed "open and send" returns the bill it already made and prints nothing twice.
    ref = _ref()
    once = await client.post("/pos/drafts", headers=pos, json={"lines": [_line(c, item="roti")], "order_type": "dine_in",
                                                               "table_label": "Meja 3", "send": True, "client_ref": ref})
    twice = await client.post("/pos/drafts", headers=pos, json={"lines": [_line(c, item="roti")], "order_type": "dine_in",
                                                                "table_label": "Meja 3", "send": True, "client_ref": ref})
    assert once.status_code == 201 and twice.status_code == 201 and once.json()["id"] == twice.json()["id"]
    assert len(await _order_jobs(session_factory, c, once.json()["id"])) == 2          # nota and Dapur slip, once


async def test_items_already_sent_to_other_tables_are_not_promised_twice(client, session_factory, cafe):
    c = cafe
    await _stations(client, c)
    pos = _auth(c["pos"])
    await _bill(client, c, _line(c, item="roti", qty=20), table="Meja 1")
    other = await _bill(client, c, _line(c, item="roti", qty=15), table="Meja 2", send=False)
    refused = await client.post(f"/pos/open-orders/{other['id']}/send", headers=pos, json={"rev": other["rev"]})
    assert refused.status_code == 409 and "tersisa 10" in refused.json()["detail"]
    fits = await client.put(f"/pos/open-orders/{other['id']}", headers=pos, json={
        "rev": other["rev"], "lines": [_line(c, item="roti", qty=10)], "send": True})
    assert fits.status_code == 200, fits.text
    assert await _stock(session_factory, c, "roti") == 30                          # still nothing taken before payment


# ── 5. Cancelling a whole bill, paying what is left, refunding a paid bill ───


async def test_a_sent_bill_is_cancelled_with_a_reason_and_every_station_hears(client, session_factory, cafe):
    c = cafe
    await _stations(client, c)
    pos = _auth(c["pos"])
    bill = await _bill(client, c, _line(c, variant="standar", mods=["panas"]), _line(c, item="roti"), table="Meja 5")
    await _print_all(client, await _printer_token(client, c, "front"))
    await _print_all(client, await _printer_token(client, c, "kitchen"), device="dapur")
    no_reason = await client.post(f"/pos/tickets/{bill['id']}/cancel", headers=pos, json={})
    assert no_reason.status_code == 422 and "alasan" in no_reason.json()["detail"]
    done = await client.post(f"/pos/tickets/{bill['id']}/cancel", headers=pos, json={"reason": "tamu pergi"})
    assert done.status_code == 200, done.text
    jobs = {j.kind: j for j in await _order_jobs(session_factory, c, bill["id"])}
    assert (jobs["bar_cancel"].printer, jobs["kitchen_cancel"].printer) == ("front", "kitchen")
    assert "Americano" in _texts(jobs["bar_cancel"].document) and "Roti" in _texts(jobs["kitchen_cancel"].document)
    assert "Alasan: tamu pergi" in _texts(jobs["kitchen_cancel"].document)
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        order = await s.get(Order, uuid.UUID(bill["id"]))
        assert order.status == "voided" and order.cart["cancelled"]["reason"] == "tamu pergi"
        assert await _footprint(s, order.id) == {"lines": 0, "payments": 0, "journal": 0, "movements": 0, "kitchen_events": 0}
    # An unsent bill is cancelled as before, reason optional, and prints nothing.
    quiet = await _bill(client, c, _line(c, item="roti"), table="Meja 6", send=False)
    assert (await client.post(f"/pos/tickets/{quiet['id']}/cancel", headers=pos, json={})).status_code == 200
    assert await _order_jobs(session_factory, c, quiet["id"]) == []


async def test_paying_sends_what_is_left_then_prints_only_the_receipt(client, session_factory, cafe):
    c = cafe
    await _stations(client, c)
    pos = _auth(c["pos"])
    bill = await _bill(client, c, _line(c, variant="standar", mods=["panas"]), table="Meja 3")
    added = await client.put(f"/pos/open-orders/{bill['id']}", headers=pos, json={"rev": bill["rev"], "lines": [_line(c, item="roti")]})
    assert added.status_code == 200 and added.json()["unsent_count"] == 1
    paid = await client.post(f"/pos/tickets/{bill['id']}/settle", headers=pos,
                             json={"payments": _cash(33000), "rev": added.json()["rev"], "client_ref": _ref()})
    assert paid.status_code == 200, paid.text
    jobs = await _order_jobs(session_factory, c, bill["id"])
    assert sorted(j.kind for j in jobs) == ["bar_ticket", "kitchen_ticket", "nota", "receipt"]
    last = next(j for j in jobs if j.kind == "kitchen_ticket")
    assert "TAMBAHAN" in _texts(last.document) and "Roti" in _texts(last.document)     # the roti still has to be made
    assert await _print_all(client, await _printer_token(client, c, "front")) == ["nota", "bar_ticket", "receipt"]
    # A dine-in order that was never sent is paid as before: receipt and slips at payment.
    never = await _bill(client, c, _line(c, item="roti"), table="Meja 11", send=False)
    assert (await client.post(f"/pos/tickets/{never['id']}/settle", headers=pos, json={"payments": _cash(15000), "rev": never["rev"]})).status_code == 200
    assert sorted(j.kind for j in await _order_jobs(session_factory, c, never["id"])) == ["kitchen_ticket", "receipt"]


async def test_refunding_a_paid_bill_tells_each_station_about_what_it_printed(client, session_factory, cafe):
    c = cafe
    await _stations(client, c)
    pos = _auth(c["pos"])
    bill = await _bill(client, c, _line(c, variant="standar", mods=["panas"]), _line(c, item="roti"), table="Meja 12")
    more = (await client.put(f"/pos/open-orders/{bill['id']}", headers=pos, json={
        "rev": bill["rev"], "lines": [_line(c, item="roti")], "send": True})).json()
    await _print_all(client, await _printer_token(client, c, "front"))
    await _print_all(client, await _printer_token(client, c, "kitchen"), device="dapur")
    assert (await client.post(f"/pos/tickets/{bill['id']}/settle", headers=pos, json={"payments": _cash(48000), "rev": more["rev"]})).status_code == 200
    refund = await client.post(f"/pos/orders/{bill['id']}/refund", headers=pos, json={"manager_pin": "1234", "note": "salah meja", "restock": True})
    assert refund.status_code == 200, refund.text
    notices = {j.kind: j for j in await _order_jobs(session_factory, c, bill["id"]) if j.kind.endswith("_cancel")}
    assert set(notices) == {"bar_cancel", "kitchen_cancel"}
    kitchen = [b for b in notices["kitchen_cancel"].document["blocks"] if b.get("t") == "item"]
    assert [(b["name"], b["qty"]) for b in kitchen] == [("Roti", "1"), ("Roti", "1")]     # both sends' roti
    assert await _stock(session_factory, c, "roti") == 30                                # restocked


# ── bill-2: the QR menu joins the table's bill; the cashier's Kirim accepts ───


def _qr(c, *lines, table="Meja 7", **extra):
    return {"lines": list(lines), "order_type": "dine_in", "table_label": table, "client_ref": _ref(), **extra}


async def test_a_qr_guest_joins_the_tables_bill_and_waits_for_the_cashier_to_send(client, session_factory, cafe):
    c = cafe
    await _stations(client, c)
    pos = _auth(c["pos"])
    bill = await _bill(client, c, _line(c, variant="standar", mods=["panas"]))            # the cashier opened Meja 7
    body = _qr(c, _line(c, item="roti"), table="7", guest_name="Andi", expected_total="15000")
    joined = await client.post(f"/menu/{c['menu']}/orders", json=body)
    assert joined.status_code == 201, joined.text
    guest = joined.json()
    key = guest["access_key"]
    assert guest["id"] == bill["id"] and key and guest["stage"] == "waiting" and guest["can_add"] is True
    assert guest["table_label"] == "Meja 7" and guest["guest_name"] is None           # not the bill owner's details
    assert [(l["name"], l["mine"], l["sent"]) for l in guest["lines"]] == [("Americano · Standar", False, True), ("Roti", True, False)]
    # Nothing is made or printed for a guest's items until the cashier sends them.
    assert len(await _order_jobs(session_factory, c, bill["id"])) == 2
    till = (await client.get(f"/pos/open-orders/{bill['id']}", headers=pos)).json()
    assert till["unsent_count"] == 1
    assert [(l["name"], l["from_guest"], l["guest_name"]) for l in till["lines"] if not l["sent_batch"]] == [("Roti", True, "Andi")]
    # A retried tap does not add the roti twice.
    again = await client.post(f"/menu/{c['menu']}/orders", json=body)
    assert again.json()["access_key"] == key and len(again.json()["lines"]) == 2

    sent = await client.post(f"/pos/open-orders/{bill['id']}/send", headers=pos, json={"rev": till["rev"]})
    assert sent.status_code == 200, sent.text
    watch = f"/menu/{c['menu']}/orders/{bill['id']}"
    assert (await client.get(f"{watch}?key={key}")).json()["stage"] == "sent"
    nota = next(j for j in await _order_jobs(session_factory, c, bill["id"]) if j.kind == "nota" and "Roti" in _texts(j.document))
    assert "TAMBAHAN" in _texts(nota.document)
    # Another round from the phone waits again.
    more = await client.post(f"{watch}/lines", json={"key": key, "lines": [_line(c, variant="large", mods=["dingin"])],
                                                     "client_ref": _ref(), "expected_total": "22000"})
    assert more.status_code == 200, more.text
    assert more.json()["stage"] == "waiting" and [l["mine"] for l in more.json()["lines"]] == [False, True, True]
    assert (await client.post(f"{watch}/lines", json={"key": "bukan-kunci-ini", "lines": [_line(c, item="roti")]})).status_code == 404
    # A bill the cashier opened is not public: without a key it is not found.
    assert (await client.get(watch)).status_code == 404
    # Paying closes it for everyone at the table.
    current = (await client.get(f"/pos/open-orders/{bill['id']}", headers=pos)).json()
    paid = await client.post(f"/pos/tickets/{bill['id']}/settle", headers=pos, json={"payments": _cash(55000), "rev": current["rev"]})
    assert paid.status_code == 200, paid.text
    assert (await client.get(f"{watch}?key={key}")).json()["stage"] == "paid"
    late = await client.post(f"{watch}/lines", json={"key": key, "lines": [_line(c, item="roti")]})
    assert late.status_code == 409 and "sudah dibayar" in late.json()["detail"]


async def test_two_phones_at_an_empty_table_make_one_bill_and_each_sees_its_own_items(client, session_factory, cafe):
    c = cafe
    first, second = await asyncio.gather(
        client.post(f"/menu/{c['menu']}/orders", json=_qr(c, _line(c, item="roti"), table="Meja 5", guest_name="Rina")),
        client.post(f"/menu/{c['menu']}/orders", json=_qr(c, _line(c, variant="large", mods=["panas"]), table="meja 5", guest_name="Bayu")),
    )
    assert first.status_code == 201 and second.status_code == 201
    a, b = first.json(), second.json()
    assert a["id"] == b["id"] and a["access_key"] != b["access_key"]
    watch = f"/menu/{c['menu']}/orders/{a['id']}"
    views = [(await client.get(f"{watch}?key={v['access_key']}")).json() for v in (a, b)]
    for view in views:
        assert len(view["lines"]) == 2 and sum(l["mine"] for l in view["lines"]) == 1 and view["table_label"].lower() == "meja 5"   # whichever phone came first named it
    assert {l["name"] for v in views for l in v["lines"] if l["mine"]} == {"Roti", "Americano · Large"}
    # Only the phone that started the bill sees the name it gave; the other sees none.
    assert sorted(v["guest_name"] is None for v in views) == [False, True]
    # A cashier opening Meja 5 is pointed at that bill.
    busy = await client.post("/pos/drafts", headers=_auth(c["pos"]), json={
        "lines": [_line(c, item="roti")], "order_type": "dine_in", "table_label": "5"})
    assert busy.status_code == 409 and busy.headers["X-Open-Bill-Id"] == a["id"]


async def test_a_joining_guest_is_quoted_for_their_own_items_only(client, session_factory, cafe):
    c = cafe
    bill = await _bill(client, c, _line(c, item="roti", qty=2), table="Meja 9", send=False)
    moved = await client.post(f"/menu/{c['menu']}/orders", json=_qr(c, _line(c, item="roti"), table="9", expected_total="30000"))
    assert moved.status_code == 409 and "Harga" in moved.json()["detail"]
    unchanged = (await client.get(f"/pos/open-orders/{bill['id']}", headers=_auth(c["pos"]))).json()
    assert len(unchanged["lines"]) == 1 and unchanged["rev"] == bill["rev"]
    ok = await client.post(f"/menu/{c['menu']}/orders", json=_qr(c, _line(c, item="roti"), table="9", expected_total="15000"))
    assert ok.status_code == 201 and D(ok.json()["total"]) == 45000                  # the table's bill so far


async def test_a_takeaway_qr_order_is_paid_before_it_is_made(client, session_factory, cafe):
    c = cafe
    placed = (await client.post(f"/menu/{c['menu']}/orders", json={**_qr(c, _line(c, item="roti")), "order_type": "takeaway"})).json()
    assert placed["stage"] == "pay"
    watch = f"/menu/{c['menu']}/orders/{placed['id']}?key={placed['access_key']}"
    paid = await client.post(f"/pos/tickets/{placed['id']}/settle", headers=_auth(c["pos"]), json={"payments": _cash(15000)})
    assert paid.status_code == 200, paid.text
    assert (await client.get(watch)).json()["stage"] == "paid"
