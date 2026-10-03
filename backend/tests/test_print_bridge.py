"""prt-8 — the print bridge, and the queue rules it relies on.

The bridge (bridge/print_bridge.py) runs on the café network, pulls jobs from
the API and writes ESC/POS to network printers. These tests drive the real
bridge code against the real API and Postgres, with **simulated** printers
(bridge/sim_printer.py) on localhost TCP. They prove the software's behaviour:
routing, layout, cuts, reconnects, restarts and honest outcomes. They prove
nothing about any physical printer model.

Also here: the two queue bugs fixed in prt-8.
  1. An unresolved job older than 12 hours vanished from the cashier's queue
     while a printer could still take it. Now it stays visible, and a job
     waiting longer than HOLD_AFTER is *held* until a person decides.
  2. A device's result was not bound to the claim it answered. Now it must
     come from the device holding the job, about its current attempt.

Needs the local Postgres (roadmap §2). No skip marker.
"""
import asyncio
import json
import sys
import threading
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app.models import Business, PrintDevice, PrintJob
from app.services import printing
from tests.test_open_bills import _bill, _order_jobs, _uid
from tests.test_prep_routing import _owner, _printer_token, _stations, _texts
from tests.test_service_journey import (  # noqa: F401 (fixtures)
    _auth, _cash, _line, _make_cafe, _ref, _set_tenant, cafe, client, engine, session_factory,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bridge"))
import print_bridge as pb  # noqa: E402
import probe_printer as pp  # noqa: E402
import sim_printer as sp  # noqa: E402


# ── harness ─────────────────────────────────────────────────────────────────


class AsgiApi:
    """The bridge's `Api`, carried over the test's ASGI client instead of HTTPS.
    `lose` names endpoints whose next answer is lost after the server acted on
    it; `down` makes every call fail like a dead internet connection."""

    def __init__(self, client, loop, headers):
        self.client, self.loop, self.headers = client, loop, headers
        self.lose: list[str] = []
        self.down = False
        self.calls: list[tuple[str, int]] = []

    def post(self, path, body):
        if self.down:
            raise pb.ApiUnreachable("simulated outage")
        fut = asyncio.run_coroutine_threadsafe(self.client.post(path, json=body, headers=self.headers), self.loop)
        resp = fut.result(timeout=60)
        self.calls.append((path, resp.status_code))
        if resp.status_code >= 500:
            raise pb.ApiUnreachable(f"HTTP {resp.status_code}")
        for tail in list(self.lose):
            if path.endswith(tail):
                self.lose.remove(tail)
                raise pb.ApiUnreachable("answer lost on the way back")
        return resp.status_code, resp.json()


@pytest.fixture
def printers():
    made = []

    def make(**state):
        sim = sp.SimPrinter()
        for k, v in state.items():
            setattr(sim.state, k, v)
        made.append(sim.start())
        return sim

    yield make
    for sim in made:
        sim.stop()


async def _worker(client, c, sim, tmp_path, slot, *, status="gs_r", device=None, columns=48):
    headers = await _printer_token(client, c, slot)
    api = AsgiApi(client, asyncio.get_running_loop(), headers)
    cfg = pb.PrinterConfig(slot=slot, host="127.0.0.1", port=sim.port, status=status, connect_timeout=1.0,
                           status_timeout=1.0, io_timeout=2.0, confirm_timeout=1.5, profile=pb.Profile(columns=columns))
    return pb.Worker(cfg, api, device or f"tes-{slot}", pb.Journal(tmp_path / f"journal-{slot}.json"))


async def _drain(worker, limit=25):
    """Run the worker until it has nothing more to do right now."""
    results = []
    for _ in range(limit):
        results.append(await asyncio.to_thread(worker.run_once))
        if results[-1] not in ("printed", "uncertain", "failed"):
            break
    return results


async def _queue(client, c, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    return (await client.get(f"/pos/print-jobs?{q}", headers=_auth(c["pos"]))).json()


async def _row(session_factory, c, job_id) -> PrintJob:
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        return await s.get(PrintJob, uuid.UUID(str(job_id)))


async def _age(session_factory, c, job_id, **delta):
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        row = await s.get(PrintJob, uuid.UUID(str(job_id)))
        row.created_at = row.created_at - timedelta(**delta)
        await s.commit()


def _paper_text(paper) -> str:
    return "\n".join(paper.text)


async def _takeaway(client, c, **extra):
    sale = await client.post("/pos/orders", headers=_auth(c["pos"]), json={
        "lines": [_line(c, variant="large", mods=["dingin"], notes="es sedikit"), _line(c, item="roti", qty=2, notes="hangatkan")],
        "payments": _cash(52000), "order_type": "takeaway", "external_ref": "GF-7731", **extra})
    assert sale.status_code == 201, sale.text
    return sale.json()


# ── layout: every block becomes printer commands ────────────────────────────


EVERY_BLOCK = {"v": 1, "kind": "kitchen_ticket", "blocks": [
    {"t": "title", "text": "DAPUR"},
    {"t": "label", "text": "TAMBAHAN"},
    {"t": "banner", "text": "MEJA 7"},
    {"t": "line", "text": "Pesanan 042", "style": "bold", "size": "large"},
    {"t": "line", "text": "Tambahan 1", "style": "bold", "size": "large"},
    {"t": "kv", "left": "Makan di sini", "right": "14.05"},
    {"t": "rule"},
    {"t": "item", "qty": "2", "name": "Nasi Goreng Kampung", "size": "Besar", "modifiers": ["Pedas level 3"],
     "notes": "tanpa bawang goreng", "flag": "TUJUAN BELUM DIATUR"},
    {"t": "item_priced", "qty": "1.5", "name": "Es Teh", "size": None, "modifiers": ["Gula aren +Rp 2.000"],
     "notes": "sedikit es", "amount": "Rp 9.000"},
    {"t": "total", "left": "TOTAL SEMENTARA", "right": "Rp 9.000"},
    {"t": "label", "text": "BATAL"},
    {"t": "label", "text": "CETAK ULANG"},
    {"t": "label", "text": "BELUM DIBAYAR"},
    {"t": "text", "text": "Cetak ulang ke-1 · 14.10 · Sari", "align": "center"},
    {"t": "note", "text": "Alasan: tamu ganti menu — maaf"},
    {"t": "qr_hint", "text": "blok baru dari server"},
]}


@pytest.mark.parametrize("columns", [32, 48])
def test_every_block_the_app_produces_becomes_printer_commands(columns):
    data = pb.render(EVERY_BLOCK, pb.Profile(columns=columns))
    papers = sp.decode(data)
    assert len(papers) == 1 and papers[0].cut == "partial"          # one document, one cut, at the end
    assert data.endswith(b"\x1dV\x42\x03") and data.count(b"\x1dV") == 1
    lines = papers[0].lines
    assert all(len(l.text) * l.width <= columns for l in lines)     # nothing runs off the paper
    assert all(ch.isascii() for l in lines for ch in l.text)        # no codepage guesswork
    body = _paper_text(papers[0])
    for words in ("DAPUR", "MEJA 7", "Pesanan 042", "Tambahan 1", "Makan di sini", "14.05", "[TUJUAN BELUM DIATUR]",
                  "2x Nasi Goreng Kampung (Besar)", "- Pedas level 3", "* tanpa bawang goreng", "1.5x Es Teh",
                  "+ Gula aren +Rp 2.000", "sedikit es", "Rp 9.000", "TOTAL SEMENTARA",
                  "Cetak ulang ke-1 - 14.10 - Sari", "Alasan: tamu ganti menu - maaf", "blok baru dari server"):
        # An item is set double size (polish-2), so its name wraps under itself:
        # the words are compared with the wrapping taken out.
        assert words in body or words in " ".join(body.split()), (words, body)
    by_text = {l.text.strip(): l for l in lines}
    for label in ("TAMBAHAN", "BATAL", "CETAK ULANG", "BELUM DIBAYAR"):
        assert by_text[label].inverse and by_text[label].bold and by_text[label].width == 2
    assert by_text["MEJA 7"].width == 2 and by_text["MEJA 7"].height == 2
    # polish-2: what the kitchen cooks from is doubled both ways, never only stretched tall.
    name = [l for l in lines if l.text.startswith("2x Nasi Goreng")]
    assert len(name) == 1 and name[0].width == 2 and name[0].height == 2 and name[0].bold
    for part in ("- Pedas", "* tanpa"):
        row = [l for l in lines if l.text.strip().startswith(part)]
        assert len(row) == 1 and row[0].width == 2 and row[0].height == 2, part
    assert "-" * columns in body


def test_long_names_modifiers_and_notes_wrap_inside_the_paper():
    doc = {"v": 1, "blocks": [
        {"t": "item", "qty": "12", "name": "Nasi Goreng Kampung Spesial Dengan Telur Ceplok Dan Kerupuk Udang", "size": "Jumbo",
         "modifiers": ["Tambah " + "x" * 70], "notes": "Tolong dipisah sambalnya karena anak-anak ikut makan juga ya kak", "flag": None},
        {"t": "item_priced", "qty": "1", "name": "Paket Hemat Berdua Ayam Bakar Madu Lengkap", "size": None, "modifiers": [],
         "notes": None, "amount": "Rp 1.250.000"},
        {"t": "kv", "left": "Sebuah label kiri yang sangat panjang sekali", "right": "dan kanan juga panjang"},
    ]}
    papers = sp.decode(pb.render(doc, pb.Profile(columns=32)))
    lines = papers[0].lines
    assert all(len(l.text) * l.width <= 32 for l in lines)
    words = " ".join(l.text.strip() for l in lines)
    assert "12x Nasi Goreng Kampung Spesial Dengan Telur Ceplok Dan Kerupuk Udang (Jumbo)" in words
    # polish-2: item rows are double width, so a 32-column paper holds 16 of them and a
    # run of 70 is cut into pieces of 13 (was pieces of 27 at single width).
    assert "x" * 13 in words and sum(l.text.count("x") for l in lines) == 70 + 2     # split, not lost (+ the x of "12x" and "1x")
    assert all(l.width == 2 and l.height == 2 for l in lines if "xxxx" in l.text)
    assert "anak-anak ikut makan juga ya kak" in words and "Rp 1.250.000" in words
    # Continuation lines of an item hang under its name, not under the quantity.
    head = [l.text for l in lines if l.height == 2 and l.bold]
    assert head[0].startswith("12x ") and all(t.startswith("   ") for t in head[1:3])


def test_a_document_without_blocks_is_refused_before_any_byte_is_written():
    with pytest.raises(ValueError):
        pb.render({"v": 1}, pb.Profile())


def test_the_bridge_refuses_unsafe_or_mixed_up_configuration(tmp_path, monkeypatch):
    import base64

    def token(printer):
        body = base64.urlsafe_b64encode(json.dumps({"scope": "printer", "printer": printer, "exp": 9999999999}).encode()).decode().rstrip("=")
        return f"x.{body}.y"

    monkeypatch.setenv("FRONT_TOKEN", token("front"))
    monkeypatch.setenv("KITCHEN_TOKEN", token("front"))       # pasted into the wrong slot
    base = {"api_base": "https://api.example.test", "device_name": "kasir-laptop",
            "printers": {"front": {"host": "192.168.1.50", "token_env": "FRONT_TOKEN"}}}

    def load(cfg):
        path = tmp_path / "c.json"
        path.write_text(json.dumps(cfg))
        return pb.BridgeConfig.load(path)

    ok = load(base)
    assert ok.printers["front"][0].port == 9100 and ok.printers["front"][0].profile.columns == 48
    with pytest.raises(pb.ConfigError, match="https"):
        load({**base, "api_base": "http://api.example.test"})
    with pytest.raises(pb.ConfigError, match="token_env or token_file"):
        load({**base, "printers": {"front": {"host": "192.168.1.50", "token": token("front")}}})
    with pytest.raises(pb.ConfigError, match="'front' printer, not 'kitchen'"):
        load({**base, "printers": {"kitchen": {"host": "192.168.1.51", "token_env": "KITCHEN_TOKEN"}}})
    with pytest.raises(pb.ConfigError, match="80 seconds"):
        load({**base, "printers": {"front": {"host": "192.168.1.50", "token_env": "FRONT_TOKEN", "confirm_timeout": 90}}})
    assert load({**base, "printers": {"front": {"host": "h", "token_env": "FRONT_TOKEN", "paper_mm": 58}}}).printers["front"][0].profile.columns == 32


# ── the bridge against the real API, with simulated printers ────────────────


async def test_each_printer_gets_its_own_paper_in_order_with_a_cut_between(client, session_factory, cafe, printers, tmp_path):
    c = cafe
    await _stations(client, c)
    sale = await _takeaway(client, c)
    front_sim, kitchen_sim = printers(), printers()
    front = await _worker(client, c, front_sim, tmp_path, "front")
    kitchen = await _worker(client, c, kitchen_sim, tmp_path, "kitchen")

    assert await _drain(front) == ["printed", "printed", "idle"]
    assert await _drain(kitchen) == ["printed", "idle"]
    receipt, bar = front_sim.papers
    assert (receipt.cut, bar.cut) == ("partial", "partial")          # two separate pieces of paper
    assert "TOTAL" in _paper_text(receipt) and "Rp 52.000" in _paper_text(receipt)
    bar_text = _paper_text(bar)
    assert bar_text.startswith("BAR") and "PESANAN 001" in bar_text and "Driver: GF-7731" in bar_text
    assert "1x Americano (Large)" in bar_text and "- Dingin" in bar_text and "* es sedikit" in bar_text
    assert "Roti" not in bar_text and "Rp" not in bar_text
    (dapur,) = kitchen_sim.papers
    dapur_text = _paper_text(dapur)
    assert dapur_text.startswith("DAPUR") and "2x Roti" in dapur_text and "* hangatkan" in dapur_text and "Americano" not in dapur_text
    # The server knows how each outcome is known.
    for j in await _order_jobs(session_factory, c, sale["id"]):
        assert (j.status, j.evidence, j.attempts) == ("printed", "printer_status", 1)
    statuses = {j["kind"]: j["status"] for j in await _queue(client, c, scope="recent")}
    assert statuses == {"receipt": "printed", "bar_ticket": "printed", "kitchen_ticket": "printed"}
    assert front.journal.entries == {} and kitchen.journal.entries == {}


async def test_open_bill_rounds_cancellations_and_reprints_reach_paper_labelled(client, session_factory, cafe, printers, tmp_path):
    c = cafe
    await _stations(client, c)
    pos = _auth(c["pos"])
    front_sim, kitchen_sim = printers(), printers()
    front = await _worker(client, c, front_sim, tmp_path, "front", columns=32)
    kitchen = await _worker(client, c, kitchen_sim, tmp_path, "kitchen", columns=32)

    bill = await _bill(client, c, _line(c, variant="standar", mods=["panas"]), _line(c, item="roti", notes="tanpa mentega"))
    await _drain(front), await _drain(kitchen)
    more = await client.put(f"/pos/open-orders/{bill['id']}", headers=pos, json={
        "rev": bill["rev"], "lines": [_line(c, item="roti", qty=3)], "send": True})
    assert more.status_code == 200, more.text
    bill = more.json()
    await _drain(front), await _drain(kitchen)
    roti = next(l["uid"] for l in bill["lines"] if l["name"] == "Roti" and l["sent_batch"] == 2)
    cancelled = await client.post(f"/pos/open-orders/{bill['id']}/lines/{roti}/cancel", headers=pos,
                                  json={"rev": bill["rev"], "quantity": "1", "reason": "tamu kenyang"})
    assert cancelled.status_code == 200, cancelled.text
    await _drain(kitchen)

    front_texts = [_paper_text(p) for p in front_sim.papers]
    kitchen_texts = [_paper_text(p) for p in kitchen_sim.papers]
    assert [t.split("\n")[0] for t in kitchen_texts] == ["DAPUR", "DAPUR", "DAPUR"]
    first, second, notice = kitchen_texts
    assert "MEJA 7" in first and "* tanpa mentega" in first and "TAMBAHAN" not in first
    assert "TAMBAHAN" in second and "Tambahan 1" in second and "3x Roti" in second
    assert "BATAL" in notice and "1x Roti" in notice and "Alasan: tamu kenyang" in notice
    assert "BELUM DIBAYAR" in front_texts[0] and "Americano" in front_texts[1]      # nota, then the Bar slip
    assert "TAMBAHAN" in front_texts[2] and len(front_texts) == 3                   # the second round's nota only
    # A reprint is marked on paper, on the printer the original went to.
    first_dapur = next(j for j in await _order_jobs(session_factory, c, bill["id"]) if j.kind == "kitchen_ticket")
    assert (await client.post(f"/pos/print-jobs/{first_dapur.id}/reprint", headers=pos)).status_code == 201
    assert await _drain(kitchen) == ["printed", "idle"]
    reprint = _paper_text(kitchen_sim.papers[-1])
    assert "CETAK ULANG" in reprint and "Cetak ulang ke-1" in reprint and "MEJA 7" in reprint
    assert next(l for l in kitchen_sim.papers[-1].lines if l.text.strip() == "CETAK ULANG").inverse
    assert all(p.cut == "partial" for p in front_sim.papers + kitchen_sim.papers)


async def test_a_dead_kitchen_printer_never_holds_up_the_front(client, session_factory, cafe, printers, tmp_path):
    c = cafe
    await _stations(client, c)
    sale = await _takeaway(client, c)
    front_sim, kitchen_sim = printers(), printers()
    kitchen_sim.stop()                                               # switched off
    front = await _worker(client, c, front_sim, tmp_path, "front")
    kitchen = await _worker(client, c, kitchen_sim, tmp_path, "kitchen")
    stop = threading.Event()
    threads = [threading.Thread(target=w.run_forever, args=(stop, 0.2), daemon=True) for w in (front, kitchen)]
    for t in threads:
        t.start()
    try:
        for _ in range(100):
            if len(front_sim.papers) == 2:
                break
            await asyncio.sleep(0.1)
        assert len(front_sim.papers) == 2                            # both front papers, while the kitchen is dead
        dapur = next(j for j in await _order_jobs(session_factory, c, sale["id"]) if j.kind == "kitchen_ticket")
        # The bridge saw the printer was dead before taking the job: it waits, unclaimed and unmarked,
        # and the till is told why by the printer's own status line.
        assert (dapur.status, dapur.claimed_by, dapur.attempts) == ("pending", None, 0)
        queued = next(j for j in await _queue(client, c, printer="kitchen"))
        assert queued["status"] == "pending" and queued["copy_kind"] == "original"
        for _ in range(50):                                          # the dead printer's connect has to time out first
            seen = {d["printer"]: d for d in (await client.get("/pos/printers", headers=_auth(c["pos"]))).json()}
            if "kitchen" in seen:
                break
            await asyncio.sleep(0.1)
        assert seen["kitchen"]["state"] == "offline" and seen["front"]["state"] == "ready" and not seen["kitchen"]["silent"]
        assert "tidak terjangkau" in seen["kitchen"]["detail"]
        # The printer comes back: the slip prints once, as the original.
        kitchen_sim.restart()
        for _ in range(250):
            if kitchen_sim.papers:
                break
            await asyncio.sleep(0.1)
    finally:
        stop.set()
        for t in threads:
            await asyncio.to_thread(t.join, 10)
    assert len(kitchen_sim.papers) == 1 and "2x Roti" in _paper_text(kitchen_sim.papers[0])
    assert "CETAK ULANG" not in _paper_text(kitchen_sim.papers[0])
    row = await _row(session_factory, c, dapur.id)
    assert row.status == "printed" and row.error is None and row.copy == "original"


async def test_paper_out_before_a_job_sends_nothing_and_prints_it_once_refilled(client, session_factory, cafe, printers, tmp_path):
    c = cafe
    await _stations(client, c)
    await client.post("/pos/orders", headers=_auth(c["pos"]), json={"lines": [_line(c, item="roti")], "payments": _cash(15000)})
    sim = printers(paper="out")
    kitchen = await _worker(client, c, sim, tmp_path, "kitchen")
    assert await _drain(kitchen) == ["printer_down"]                 # the first look already sees it
    assert sim.papers == [] and (await _queue(client, c, printer="kitchen"))[0]["status"] == "pending"
    seen = (await client.get("/pos/printers", headers=_auth(c["pos"]))).json()
    assert [(d["state"], d["detail"]) for d in seen] == [("paper_out", "Kertas habis")]
    # Paper runs out between the look and the job: the job is released, not failed, not uncertain.
    sim.state.paper = "ok"
    kitchen.next_probe_at = 0
    kitchen.last_heartbeat_at = None
    kitchen.probe = lambda: pb.PrinterState("ready")              # the look happened just before the roll ran out
    sim.state.paper = "out"
    assert (await _drain(kitchen))[0] == "released"
    job = (await _queue(client, c, printer="kitchen"))[0]
    assert job["status"] == "pending" and "Kertas habis" in job["error"] and sim.papers == []
    del kitchen.probe
    sim.state.paper = "ok"
    kitchen.next_probe_at = 0
    assert await _drain(kitchen) == ["printed", "idle"]
    assert len(sim.papers) == 1
    row = await _row(session_factory, c, job["id"])
    assert (row.status, row.attempts, row.copy) == ("printed", 2, "original")


async def test_a_connection_cut_mid_job_is_uncertain_and_the_bridge_never_resends_it(client, session_factory, cafe, printers, tmp_path):
    c = cafe
    await _stations(client, c)
    pos = _auth(c["pos"])
    await client.post("/pos/orders", headers=pos, json={"lines": [_line(c, item="roti")], "payments": _cash(15000)})
    sim = printers(drop_after=120)
    kitchen = await _worker(client, c, sim, tmp_path, "kitchen")
    assert (await _drain(kitchen))[0] == "uncertain"
    job = (await _queue(client, c, printer="kitchen"))[0]
    # Shown as uncertain at once, with what is known: some bytes went out.
    assert job["status"] == "uncertain" and "mungkin tercetak" in job["error"]
    assert [p.cut for p in sim.papers] == ["torn"]
    # The bridge carries on and never prints it again on its own.
    kitchen.next_probe_at = 0
    assert await _drain(kitchen) == ["idle"]
    assert len(sim.papers) == 1
    assert (await client.post(f"/pos/print-jobs/{job['id']}/retry", headers=pos)).status_code == 409
    # The cashier's answer is a marked reprint.
    assert (await client.post(f"/pos/print-jobs/{job['id']}/reprint", headers=pos)).status_code == 201
    assert await _drain(kitchen) == ["printed", "idle"]
    assert "CETAK ULANG" in _paper_text(sim.papers[-1]) and sim.papers[-1].cut == "partial"


async def test_a_printer_that_never_confirms_is_uncertain_not_printed(client, session_factory, cafe, printers, tmp_path):
    c = cafe
    await _stations(client, c)
    await client.post("/pos/orders", headers=_auth(c["pos"]), json={"lines": [_line(c, item="roti")], "payments": _cash(15000)})
    sim = printers(stall_confirm=True)
    kitchen = await _worker(client, c, sim, tmp_path, "kitchen")
    assert (await _drain(kitchen))[0] == "uncertain"
    job = (await _queue(client, c, printer="kitchen"))[0]
    assert job["status"] == "uncertain" and "tidak mengonfirmasi" in job["error"]
    row = await _row(session_factory, c, job["id"])
    assert row.status == "claimed" and row.uncertain_at is not None and row.printed_at is None


async def test_without_printer_status_a_job_is_delivered_not_printed(client, session_factory, cafe, printers, tmp_path):
    c = cafe
    await _stations(client, c)
    await client.post("/pos/orders", headers=_auth(c["pos"]), json={"lines": [_line(c, item="roti")], "payments": _cash(15000)})
    sim = printers(status_supported=False, confirm_supported=False)
    strict = await _worker(client, c, sim, tmp_path, "kitchen")
    # A printer that ignores status requests is not silently trusted in gs_r mode.
    assert await _drain(strict) == ["printer_down"] and sim.papers == []
    assert "status" in (await client.get("/pos/printers", headers=_auth(c["pos"]))).json()[0]["detail"]
    plain = await _worker(client, c, sim, tmp_path / "plain", "kitchen", status="none")
    assert await _drain(plain) == ["printed", "idle"]
    job = (await _queue(client, c, scope="recent", printer="kitchen"))[0]
    assert job["status"] == "delivered" and job["evidence"] == "bytes_delivered"
    assert len(sim.papers) == 1
    devices = {d["device"]: d["state"] for d in (await client.get("/pos/printers", headers=_auth(c["pos"]))).json()}
    assert devices["tes-kitchen"] == "reachable"


async def test_a_restarted_bridge_releases_what_it_never_sent_and_never_resends_what_it_might_have(
        client, session_factory, cafe, printers, tmp_path):
    c = cafe
    await _stations(client, c)
    pos = _auth(c["pos"])
    for _ in range(2):
        await client.post("/pos/orders", headers=pos, json={"lines": [_line(c, item="roti")], "payments": _cash(15000)})
    sim = printers()
    before = await _worker(client, c, sim, tmp_path, "kitchen")
    # The first bridge took both jobs, began writing the first, and died.
    first = (await asyncio.to_thread(before.api.post, "/print/agent/claim", {"device": before.device}))[1]["job"]
    second = (await asyncio.to_thread(before.api.post, "/print/agent/claim", {"device": before.device}))[1]["job"]
    before.journal.put(first["id"], {"attempt": first["attempts"], "phase": "sending"})
    before.journal.put(second["id"], {"attempt": second["attempts"], "phase": "claimed"})

    after = await _worker(client, c, sim, tmp_path, "kitchen")         # same device name, same journal on disk
    assert set(after.journal.entries) == {first["id"], second["id"]}
    assert await _drain(after) == ["printed", "idle"]
    uncertain, printed = await _row(session_factory, c, first["id"]), await _row(session_factory, c, second["id"])
    assert uncertain.status == "claimed" and uncertain.uncertain_at is not None and "Bridge berhenti" in uncertain.error
    assert (printed.status, printed.attempts) == ("printed", 2)
    assert len(sim.papers) == 1                                        # the maybe-printed one was not sent again
    assert after.journal.entries == {} and json.loads((tmp_path / "journal-kitchen.json").read_text()) == {}


async def test_a_lost_claim_answer_is_recovered_without_printing_twice(client, session_factory, cafe, printers, tmp_path):
    c = cafe
    await _stations(client, c)
    await client.post("/pos/orders", headers=_auth(c["pos"]), json={"lines": [_line(c, item="roti")], "payments": _cash(15000)})
    sim = printers()
    kitchen = await _worker(client, c, sim, tmp_path, "kitchen")
    kitchen.api.lose.append("/claim")
    assert await _drain(kitchen) == ["api_down"]
    held = (await _queue(client, c, printer="kitchen"))[0]
    assert held["claimed_by"] == "tes-kitchen" and held["status"] == "sending"      # the server did hand it out
    assert await _drain(kitchen) == ["printed", "idle"]
    row = await _row(session_factory, c, held["id"])
    assert (row.status, row.attempts) == ("printed", 2) and len(sim.papers) == 1


async def test_a_lost_result_is_delivered_from_the_journal_later(client, session_factory, cafe, printers, tmp_path):
    c = cafe
    await _stations(client, c)
    await client.post("/pos/orders", headers=_auth(c["pos"]), json={"lines": [_line(c, item="roti")], "payments": _cash(15000)})
    sim = printers()
    kitchen = await _worker(client, c, sim, tmp_path, "kitchen")
    kitchen.api.lose.append("/result")
    assert await asyncio.to_thread(kitchen.run_once) == "printed"
    (entry,) = kitchen.journal.entries.values()
    assert entry["phase"] == "outcome" and entry["outcome"] == "printed"
    # The internet is down for a while; nothing new is claimed, nothing is resent.
    kitchen.api.down = True
    assert await _drain(kitchen) == ["api_down"]
    kitchen.api.down = False
    assert await _drain(kitchen) == ["idle"]
    job = (await _queue(client, c, scope="recent", printer="kitchen"))[0]
    assert job["status"] == "printed" and job["attempts"] == 1 and len(sim.papers) == 1 and kitchen.journal.entries == {}


# ── bug 2: a result must answer the claim the job is on now ─────────────────


async def test_a_result_is_bound_to_the_device_and_the_attempt_that_holds_the_job(client, session_factory, cafe):
    c = cafe
    await _stations(client, c)
    pos = _auth(c["pos"])
    await client.post("/pos/orders", headers=pos, json={"lines": [_line(c, item="roti")], "payments": _cash(15000)})
    kitchen = await _printer_token(client, c, "kitchen")

    def result(job_id, **body):
        return client.post(f"/print/agent/jobs/{job_id}/result", headers=kitchen, json=body)

    job = (await client.post("/print/agent/claim", headers=kitchen, json={"device": "dapur-1"})).json()["job"]
    assert job["attempts"] == 1
    # Another device on the same printer token cannot answer for it.
    other = await result(job["id"], device="dapur-2", ok=True)
    assert other.status_code == 409 and "percobaan" in other.json()["detail"]
    assert (await _row(session_factory, c, job["id"])).status == "claimed"
    assert (await result(job["id"], device="dapur-1", attempt=1, outcome="failed", error="kertas habis")).json()["status"] == "failed"
    assert (await client.post(f"/pos/print-jobs/{job['id']}/retry", headers=pos)).json()["status"] == "pending"
    again = (await client.post("/print/agent/claim", headers=kitchen, json={"device": "dapur-1"})).json()["job"]
    assert again["id"] == job["id"] and again["attempts"] == 2
    # A late acknowledgement of the first attempt, from the same device, is refused: in either direction...
    for late in ({"ok": True, "attempt": 1}, {"outcome": "printed", "attempt": 1, "evidence": "printer_status"},
                 {"ok": False, "attempt": 1}, {"ok": True}):                  # ...and with no attempt at all, now ambiguous
        refused = await result(job["id"], device="dapur-1", **late)
        assert refused.status_code == 409, late
    row = await _row(session_factory, c, job["id"])
    assert (row.status, row.printed_at, row.error) == ("claimed", None, None)
    # The current attempt's answer counts, and is idempotent.
    done = await result(job["id"], device="dapur-1", attempt=2, outcome="printed", evidence="printer_status")
    assert done.status_code == 200 and done.json()["status"] == "printed" and done.json()["evidence"] == "printer_status"
    assert (await result(job["id"], device="dapur-1", attempt=2, outcome="printed", evidence="printer_status")).status_code == 200
    # After it printed, a stale failure changes nothing.
    assert (await result(job["id"], device="dapur-1", attempt=1, ok=False, error="terlambat")).status_code == 409
    assert (await result(job["id"], device="dapur-1", attempt=2, ok=False)).status_code == 409
    assert (await _row(session_factory, c, job["id"])).status == "printed"
    # A contradictory or incomplete answer is refused before it reaches the queue.
    assert (await result(job["id"], device="dapur-1", attempt=2)).status_code == 422
    assert (await result(job["id"], device="dapur-1", attempt=2, ok=False, evidence="printer_status")).status_code == 422


async def test_release_and_uncertain_are_bound_to_the_claim_too(client, session_factory, cafe):
    c = cafe
    await _stations(client, c)
    await client.post("/pos/orders", headers=_auth(c["pos"]), json={"lines": [_line(c, item="roti")], "payments": _cash(15000)})
    kitchen = await _printer_token(client, c, "kitchen")
    job = (await client.post("/print/agent/claim", headers=kitchen, json={"device": "dapur-1"})).json()["job"]
    path = f"/print/agent/jobs/{job['id']}"
    assert (await client.post(f"{path}/release", headers=kitchen, json={"device": "dapur-2", "attempt": 1})).status_code == 409
    assert (await client.post(f"{path}/release", headers=kitchen, json={"device": "dapur-1", "attempt": 7})).status_code == 409
    assert (await client.post(f"{path}/release", headers=kitchen, json={"device": "dapur-1"})).status_code == 422
    released = await client.post(f"{path}/release", headers=kitchen, json={"device": "dapur-1", "attempt": 1, "error": "Belum terkirim: kertas habis"})
    assert released.status_code == 200 and released.json()["status"] == "pending" and released.json()["claimed_by"] is None
    assert (await client.post(f"{path}/release", headers=kitchen, json={"device": "dapur-1", "attempt": 1})).status_code == 200
    job = (await client.post("/print/agent/claim", headers=kitchen, json={"device": "dapur-1"})).json()["job"]
    assert job["attempts"] == 2 and job["error"] is None
    held = (await client.post("/print/agent/held", headers=kitchen, json={"device": "dapur-1"})).json()["jobs"]
    assert [(h["id"], h["attempt"]) for h in held] == [(job["id"], 2)]
    maybe = await client.post(f"{path}/result", headers=kitchen, json={"device": "dapur-1", "attempt": 2, "outcome": "uncertain",
                                                                      "error": "koneksi putus"})
    assert maybe.status_code == 200 and maybe.json()["status"] == "uncertain"
    # Once it may be on paper, it can no longer be put back as if nothing happened, nor offered back.
    assert (await client.post(f"{path}/release", headers=kitchen, json={"device": "dapur-1", "attempt": 2})).status_code == 409
    assert (await client.post("/print/agent/held", headers=kitchen, json={"device": "dapur-1"})).json()["jobs"] == []
    # The front printer's token cannot touch a kitchen job.
    front = await _printer_token(client, c, "front")
    assert (await client.post(f"{path}/release", headers=front, json={"device": "dapur-1", "attempt": 2})).status_code == 404


async def test_a_browser_printed_job_can_be_answered_on_its_second_try(client, session_factory, cafe):
    c = cafe
    await _stations(client, c)
    pos = _auth(c["pos"])
    await client.post("/pos/orders", headers=pos, json={"lines": [_line(c, item="roti")], "payments": _cash(15000)})
    receipt = next(j for j in await _queue(client, c, printer="front") if j["kind"] == "receipt")
    for attempt in (1, 2):
        assert (await client.post(f"/pos/print-jobs/{receipt['id']}/browser", headers=pos)).json()["attempts"] == attempt
        said_no = await client.post(f"/pos/print-jobs/{receipt['id']}/browser-result", headers=pos, json={"ok": False})
        assert said_no.status_code == 200 and said_no.json()["status"] == "failed"
    # A job a printer device holds cannot be marked failed from the till's browser dialog.
    kitchen = await _printer_token(client, c, "kitchen")
    dapur = (await client.post("/print/agent/claim", headers=kitchen, json={"device": "dapur-1"})).json()["job"]
    assert (await client.post(f"/pos/print-jobs/{dapur['id']}/browser-result", headers=pos, json={"ok": False})).status_code == 409


# ── bug 1: old unresolved jobs stay visible and are held, not sent as new ───


async def test_an_old_unprinted_ticket_stays_on_the_queue_and_is_held_from_printers(client, session_factory, cafe):
    c = cafe
    await _stations(client, c)
    pos = _auth(c["pos"])
    sale = (await client.post("/pos/orders", headers=pos, json={"lines": [_line(c, item="roti")], "payments": _cash(15000)})).json()
    dapur = next(j for j in await _order_jobs(session_factory, c, sale["id"]) if j.kind == "kitchen_ticket")
    await _age(session_factory, c, dapur.id, hours=13)
    kitchen = await _printer_token(client, c, "kitchen")
    # Yesterday's order is not handed to a printer that reconnects...
    assert (await client.post("/print/agent/claim", headers=kitchen, json={"device": "dapur-1"})).json()["job"] is None
    # ...and it has not disappeared from the cashier's screen either.
    open_jobs = {j["id"]: j for j in await _queue(client, c)}
    assert open_jobs[str(dapur.id)]["status"] == "held"
    assert str(dapur.id) not in {j["id"] for j in await _queue(client, c, scope="recent")}      # history stays 12 hours
    # Fresh work is not blocked behind it.
    await client.post("/pos/orders", headers=pos, json={"lines": [_line(c, item="roti", qty=4)], "payments": _cash(60000)})
    fresh = (await client.post("/print/agent/claim", headers=kitchen, json={"device": "dapur-1"})).json()["job"]
    assert fresh["id"] != str(dapur.id) and "4" in [b.get("qty") for b in fresh["document"]["blocks"]]


async def test_a_held_ticket_prints_marked_late_only_when_a_person_lets_it_through(client, session_factory, cafe, printers, tmp_path):
    c = cafe
    await _stations(client, c)
    pos = _auth(c["pos"])
    await client.post("/pos/orders", headers=pos, json={"lines": [_line(c, item="roti")], "payments": _cash(15000),
                                                        "order_type": "dine_in", "table_label": "Meja 9"})
    job = (await _queue(client, c, printer="kitchen"))[0]
    await _age(session_factory, c, job["id"], minutes=40)
    sim = printers()
    kitchen = await _worker(client, c, sim, tmp_path, "kitchen")
    assert await _drain(kitchen) == ["idle"] and sim.papers == []
    let = await client.post(f"/pos/print-jobs/{job['id']}/release", headers=pos)
    assert let.status_code == 200 and let.json()["status"] == "pending" and let.json()["released"] is True
    assert await _drain(kitchen) == ["printed", "idle"]
    (paper,) = sim.papers
    body = _paper_text(paper)
    assert "TERLAMBAT" in body and "Dibuat" in body and "Sari" in body and "MEJA 9" in body and "1x Roti" in body
    assert next(l for l in paper.lines if l.text.strip() == "TERLAMBAT").inverse
    row = await _row(session_factory, c, job["id"])
    assert row.copy == "original" and row.status == "printed"
    assert "TERLAMBAT" not in _texts(row.document)                   # the stored document stays as it was made


async def test_a_held_or_failed_ticket_can_be_withdrawn_but_nothing_else_can(client, session_factory, cafe):
    c = cafe
    await _stations(client, c)
    pos = _auth(c["pos"])
    sale = (await client.post("/pos/orders", headers=pos, json={
        "lines": [_line(c, variant="standar", mods=["panas"]), _line(c, item="roti")], "payments": _cash(33000)})).json()
    jobs = {j.kind: j for j in await _order_jobs(session_factory, c, sale["id"])}
    # Fresh and waiting: a printer will take it, so it cannot be withdrawn by hand.
    assert (await client.post(f"/pos/print-jobs/{jobs['kitchen_ticket'].id}/withdraw", headers=pos)).status_code == 409
    await _age(session_factory, c, jobs["kitchen_ticket"].id, hours=13)
    gone = await client.post(f"/pos/print-jobs/{jobs['kitchen_ticket'].id}/withdraw", headers=pos)
    assert gone.status_code == 200 and gone.json()["status"] == "cancelled" and gone.json()["withdrawn_by_person"] is True
    assert (await client.post(f"/pos/print-jobs/{jobs['kitchen_ticket'].id}/withdraw", headers=pos)).status_code == 200
    front = await _printer_token(client, c, "front")
    receipt = (await client.post("/print/agent/claim", headers=front, json={"device": "depan"})).json()["job"]
    # Taken by a printer and unanswered: it may be on paper, so it is not the till's to withdraw.
    assert (await client.post(f"/pos/print-jobs/{receipt['id']}/withdraw", headers=pos)).status_code == 409
    await client.post(f"/print/agent/jobs/{receipt['id']}/result", headers=front, json={"device": "depan", "ok": False, "error": "macet"})
    assert (await client.post(f"/pos/print-jobs/{receipt['id']}/withdraw", headers=pos)).json()["status"] == "cancelled"
    # Withdrawn rows are kept, not deleted.
    assert {j.kind: j.status for j in await _order_jobs(session_factory, c, sale["id"])} == {
        "receipt": "cancelled", "bar_ticket": "pending", "kitchen_ticket": "cancelled"}


async def test_retrying_an_old_failed_ticket_is_a_decision_to_print_it_late(client, session_factory, cafe):
    c = cafe
    await _stations(client, c)
    pos = _auth(c["pos"])
    await client.post("/pos/orders", headers=pos, json={"lines": [_line(c, item="roti")], "payments": _cash(15000)})
    kitchen = await _printer_token(client, c, "kitchen")
    job = (await client.post("/print/agent/claim", headers=kitchen, json={"device": "dapur-1"})).json()["job"]
    await client.post(f"/print/agent/jobs/{job['id']}/result", headers=kitchen, json={"device": "dapur-1", "ok": False, "error": "tutup terbuka"})
    await _age(session_factory, c, job["id"], minutes=30)
    assert (await client.post(f"/pos/print-jobs/{job['id']}/retry", headers=pos)).json()["released"] is True
    again = (await client.post("/print/agent/claim", headers=kitchen, json={"device": "dapur-1"})).json()["job"]
    assert again["id"] == job["id"] and again["document"]["late"] is True and "TERLAMBAT" in _texts(again["document"])


async def test_an_uncertain_job_from_yesterday_is_still_on_the_queue(client, session_factory, cafe):
    c = cafe
    await _stations(client, c)
    await client.post("/pos/orders", headers=_auth(c["pos"]), json={"lines": [_line(c, item="roti")], "payments": _cash(15000)})
    kitchen = await _printer_token(client, c, "kitchen")
    job = (await client.post("/print/agent/claim", headers=kitchen, json={"device": "dapur-1"})).json()["job"]
    await _age(session_factory, c, job["id"], hours=20)
    assert [(j["id"], j["status"]) for j in await _queue(client, c, printer="kitchen")] == [(job["id"], "sending")]
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        row = await s.get(PrintJob, uuid.UUID(job["id"]))
        row.claimed_at = row.claimed_at - timedelta(hours=20)
        await s.commit()
    assert [(j["id"], j["status"]) for j in await _queue(client, c, printer="kitchen")] == [(job["id"], "uncertain")]


# ── printer status reported by bridges ──────────────────────────────────────


async def test_heartbeats_are_scoped_to_their_printer_and_business(client, session_factory, cafe):
    c = cafe
    kitchen = await _printer_token(client, c, "kitchen")
    beat = await client.post("/print/agent/heartbeat", headers=kitchen, json={"device": "dapur-1", "state": "cover_open",
                                                                              "detail": "Tutup printer terbuka", "version": "1.0.0"})
    assert beat.status_code == 200
    assert (await client.post("/print/agent/heartbeat", headers=kitchen, json={"device": "dapur-1", "state": "meledak"})).status_code == 422
    assert (await client.post("/print/agent/heartbeat", headers=_auth(c["pos"]), json={"device": "x", "state": "ready"})).status_code == 403
    await client.post("/print/agent/heartbeat", headers=kitchen, json={"device": "dapur-1", "state": "ready"})
    seen = (await client.get("/pos/printers", headers=_auth(c["pos"]))).json()
    assert [(d["printer"], d["device"], d["state"], d["silent"]) for d in seen] == [("kitchen", "dapur-1", "ready", False)]
    assert (await client.get("/api/printers/devices", headers=_owner(c))).json()[0]["device"] == "dapur-1"
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        row = (await s.execute(select(PrintDevice))).scalar_one()
        row.last_seen_at = row.last_seen_at - timedelta(minutes=5)
        await s.commit()
    assert (await client.get("/pos/printers", headers=_auth(c["pos"]))).json()[0]["silent"] is True


async def test_print_devices_are_tenant_isolated(client, session_factory, cafe):
    c = cafe
    other = await _make_cafe(session_factory, name="Tetangga Bridge")
    try:
        await client.post("/print/agent/heartbeat", headers=await _printer_token(client, c, "front"),
                          json={"device": "depan", "state": "ready"})
        async with session_factory() as s:
            row = (await s.execute(text("select relrowsecurity, relforcerowsecurity from pg_class where relname = 'print_devices'"))).one()
            assert row == (True, True)
            assert (await s.execute(text("select policyname from pg_policies where tablename = 'print_devices'"))).scalars().all() == ["tenant_isolation"]
        async with session_factory() as s:
            await _set_tenant(s, other["bid"])
            assert (await s.execute(select(PrintDevice))).scalars().all() == []
            s.add(PrintDevice(business_id=c["bid"], printer="front", device="nyelonong", state="ready"))
            with pytest.raises(Exception):
                await s.flush()
            await s.rollback()
        async with session_factory() as s:
            await _set_tenant(s, c["bid"])
            assert [d.device for d in (await s.execute(select(PrintDevice))).scalars()] == ["depan"]
    finally:
        async with session_factory() as s:
            row = await s.get(Business, other["bid"])
            if row:
                await s.delete(row)
            await s.commit()


def test_status_bytes_are_read_the_way_escpos_defines_them():
    ok = 0x12
    assert pb.parse_realtime_status(ok, ok, ok).state == "ready"
    assert pb.parse_realtime_status(ok | 0x08, ok | 0x04, ok).state == "cover_open"
    assert pb.parse_realtime_status(ok | 0x08, ok | 0x20, ok | 0x60).state == "paper_out"
    assert pb.parse_realtime_status(ok, ok, ok | 0x0C).state == "paper_low"
    assert pb.parse_realtime_status(ok, ok | 0x40, ok).state == "error"
    assert pb.parse_realtime_status(ok | 0x08, ok, ok).state == "offline"
    with pytest.raises(pb.StatusUnavailable):
        pb.parse_realtime_status(0x00, ok, ok)                         # not a DLE EOT reply at all
    assert printing.HOLD_AFTER > printing.UNCERTAIN_AFTER


# ── the owner's setup test ticket (prt-9) ───────────────────────────────────


async def test_the_owner_asks_a_printer_for_a_test_ticket_and_is_told_what_happened(
        client, session_factory, cafe, printers, tmp_path):
    c = cafe
    owner = _owner(c)
    sim = printers()
    front = await _worker(client, c, sim, tmp_path, "front")
    # Nobody is running a bridge yet: there is no one to print it, and it says so.
    nobody = await client.post("/api/printers/front/test", headers=owner)
    assert nobody.status_code == 409 and "bridge" in nobody.json()["detail"]
    assert await _drain(front) == ["idle"]                       # the bridge reports in

    asked = await client.post("/api/printers/front/test", headers=owner)
    assert asked.status_code == 200 and asked.json()[0]["test_state"] == "waiting"
    assert await _drain(front) == ["printed", "idle"]
    paper = _paper_text(sim.papers[-1])
    assert "TES PRINTER" in paper and "Rp 28.000" in paper and sim.papers[-1].cut == "partial"
    seen = (await client.get("/api/printers/devices", headers=owner)).json()[0]
    assert seen["test_state"] == "printed" and "mengonfirmasi" in seen["test_detail"]
    # A test ticket is not an order: it never enters the queue.
    assert await _queue(client, c, scope="recent") == []
    # Asking again prints a second one, and the old answer does not linger.
    assert (await client.post("/api/printers/front/test", headers=owner)).json()[0]["test_state"] == "waiting"
    assert await _drain(front) == ["printed", "idle"] and len(sim.papers) == 2


async def test_a_test_ticket_for_a_printer_that_is_off_comes_back_as_failed(client, session_factory, cafe, printers, tmp_path):
    c = cafe
    owner = _owner(c)
    sim = printers()
    kitchen = await _worker(client, c, sim, tmp_path, "kitchen")
    assert await _drain(kitchen) == ["idle"]
    sim.stop()
    assert (await client.post("/api/printers/kitchen/test", headers=owner)).status_code == 200
    # The printer is unreachable, so the answer is a clear failure rather than silence.
    for _ in range(4):
        if (await client.get("/api/printers/devices", headers=owner)).json()[0]["test_state"] == "failed":
            break
        await asyncio.to_thread(kitchen.run_once)
    seen = (await client.get("/api/printers/devices", headers=owner)).json()[0]
    assert seen["test_state"] == "failed" and "Tidak terkirim" in seen["test_detail"]
    assert seen["state"] == "offline" and sim.papers == []


async def test_a_test_ticket_only_reaches_its_own_printer_and_its_own_business(client, session_factory, cafe, printers, tmp_path):
    c = cafe
    sims = {"front": printers(), "kitchen": printers()}
    workers = {slot: await _worker(client, c, sims[slot], tmp_path, slot) for slot in sims}
    for w in workers.values():
        await _drain(w)
    assert (await client.post("/api/printers/kitchen/test", headers=_owner(c))).status_code == 200
    assert await _drain(workers["front"]) == ["idle"] and sims["front"].papers == []
    assert await _drain(workers["kitchen"]) == ["printed", "idle"]
    kitchen_paper = _paper_text(sims["kitchen"].papers[-1])
    assert kitchen_paper.startswith("DAPUR") and "MEJA 12" in kitchen_paper and "Rp" not in kitchen_paper
    assert (await client.post("/api/printers/kitchen/test", headers=_auth(c["pos"]))).status_code == 403


def test_the_two_printers_get_different_test_tickets():
    front, kitchen = (sp.decode(pb.render(pb.test_document(s), pb.Profile(columns=48)))[0] for s in ("front", "kitchen"))
    assert "TES PRINTER DEPAN" in _paper_text(front) and "TOTAL" in _paper_text(front)
    assert "struk" in _paper_text(front) and "slip bar" in _paper_text(front)
    assert "DAPUR" in _paper_text(kitchen) and "Rp" not in _paper_text(kitchen)
    assert any(l.width == 2 and "MEJA 12" in l.text for l in kitchen.lines)      # readable across the kitchen


# ── the tablet: Android may freeze the bridge (prt-9) ───────────────────────


async def test_a_frozen_bridge_says_so_and_rechecks_what_it_was_holding(client, session_factory, cafe, printers, tmp_path):
    c = cafe
    sim = printers()
    worker = await _worker(client, c, sim, tmp_path, "front")
    await _drain(worker)
    worker.need_reconcile = False
    # A wait of 2 seconds that really took 7 minutes: Android froze this process.
    worker.note_gap(2.0, 420.0)
    assert worker.need_reconcile is True and round(worker.recent_suspension()) == 418
    await asyncio.to_thread(worker.heartbeat, pb.PrinterState("ready"), True)
    seen = (await client.get("/pos/printers", headers=_auth(c["pos"]))).json()[0]
    assert "sempat berhenti 7 menit" in seen["detail"]
    # Ordinary waiting is not a freeze, and a freeze stops being news after ten minutes.
    worker.note_gap(2.0, 12.0)
    assert round(worker.recent_suspension()) == 418
    worker.suspended_at = worker.clock() - 601
    assert worker.recent_suspension() == 0.0


# ── the printer model, and what is still only advertised (prt-9) ────────────


def test_the_iware_model_profile_is_a_starting_point_not_a_claim(tmp_path):
    cfg = pb.PrinterConfig.from_config("kitchen", {"host": "192.168.1.51", "model": "iware-iw-j300h"})
    assert (cfg.port, cfg.status, cfg.profile.columns, cfg.profile.cut) == (9100, "gs_r", 48, "partial")
    assert pb.MODELS["iware-iw-j300h"]["confidence"] == "advertised"
    # Until the printer has been asked, --check says the settings are unverified.
    warning = pb.protocol_warning(cfg, tmp_path)
    assert "NOT been verified" in warning and "probe_printer.py 192.168.1.51" in warning
    (tmp_path / "probe-report.json").write_text("[]")
    assert "check that its port" in pb.protocol_warning(cfg, tmp_path)
    # Anything the config states explicitly still wins over the model's defaults.
    custom = pb.PrinterConfig.from_config("front", {"host": "h", "model": "iware-iw-j300h", "port": 6001,
                                                    "status": "none", "paper_mm": 58})
    assert (custom.port, custom.status, custom.profile.columns) == (6001, "none", 32)
    with pytest.raises(pb.ConfigError, match="model must be one of"):
        pb.PrinterConfig.from_config("front", {"host": "h", "model": "epson-tm-t82"})


def test_the_buzzer_is_silent_until_its_command_has_been_confirmed():
    with pytest.raises(pb.ConfigError, match="verified"):
        pb.Profile.from_config({"beep": {"command": "esc_b", "times": 2}})
    with pytest.raises(pb.ConfigError, match="beep.command must be one of"):
        pb.Profile.from_config({"beep": {"command": "siren", "verified": True}})
    profile = pb.Profile.from_config({"beep": {"command": "esc_b", "times": 2, "duration": 3, "verified": True}})
    data = pb.render({"v": 1, "blocks": [{"t": "text", "text": "halo"}]}, profile)
    assert b"\x1bB\x02\x03" in data
    assert sp.decode(data)[0].beeped is True
    # A printer with no verified buzzer prints exactly the same ticket, silently.
    assert not sp.decode(pb.render({"v": 1, "blocks": [{"t": "text", "text": "halo"}]}, pb.Profile()))[0].beeped


# ── asking the printer what it speaks, instead of assuming (prt-9) ──────────


def test_the_probe_reports_what_a_printer_answers(printers):
    sim = printers()
    report = pp.probe("127.0.0.1", ports=[sim.port, sim.port + 1], timeout=1.0, paper=False, buzzer=False)
    assert report["ports"][str(sim.port)] == "open"
    escpos = report["escpos"][str(sim.port)]
    assert escpos["dle_eot_answers"] and escpos["dle_eot_looks_valid"] and escpos["gs_r_answers"]
    notes = " ".join(pp.recommend(report))
    assert f'"port": {sim.port}' in notes and '"status": "gs_r"' in notes
    assert sim.papers == []                                  # a probe prints nothing by default


def test_the_probe_is_honest_about_a_printer_that_answers_nothing(printers):
    sim = printers(status_supported=False, confirm_supported=False)
    report = pp.probe("127.0.0.1", ports=[sim.port], timeout=0.6, paper=False, buzzer=False)
    notes = " ".join(pp.recommend(report))
    assert '"status": "none"' in notes and f'"port": {sim.port}' in notes
    dead = pp.probe("127.0.0.1", ports=[sim.port + 7], timeout=0.4, paper=False, buzzer=False)
    assert "No raw printing port answered" in " ".join(pp.recommend(dead))


def test_the_probe_labels_each_cut_and_buzzer_attempt_on_paper(printers):
    sim = printers()
    report = pp.probe("127.0.0.1", ports=[sim.port], timeout=1.0, paper=True, buzzer=True)
    papers = sim.papers
    assert len(papers) >= len(pp.CUTS)
    first = _paper_text(papers[0])
    assert "TES POTONG" in first and "gs_v_66" in first          # the paper says which command made it
    tried = {c["key"] for c in report["paper_tests"]["cuts"]}
    assert tried == {k for k, _, _ in pp.CUTS}
    sounded = [p for p in papers if p.beeped]
    assert sounded and "TES BUNYI" in _paper_text(sounded[0])
    assert {b["key"] for b in report["paper_tests"]["buzzers"]} == {k for k, _, _ in pp.BUZZERS}


# ── which product goes to which station (prt-9) ─────────────────────────────


async def test_products_without_a_station_are_listed_rather_than_quietly_routed(client, session_factory, cafe):
    c = cafe
    owner = _owner(c)
    routing = (await client.get("/api/printers/routing", headers=owner)).json()
    assert (routing["bar"], routing["kitchen"], routing["none"]) == (0, 0, 0)
    assert [u["name"] for u in routing["unmapped"]] == ["Americano", "Roti"]
    await _stations(client, c)                                   # americano -> bar, roti -> kitchen
    routing = (await client.get("/api/printers/routing", headers=owner)).json()
    assert (routing["bar"], routing["kitchen"], routing["unmapped"]) == (1, 1, [])
    made = await client.post("/api/items", headers=owner, json={"name": "Pisang Goreng", "unit": "porsi",
                                                                "sell_price": "18000", "current_stock": "5"})
    assert made.status_code == 201
    # An ingredient is not something a customer orders, so it is never asked about.
    bahan = await client.post("/api/items", headers=owner, json={"name": "Tepung Terigu", "unit": "kg",
                                                                 "sell_price": "0", "current_stock": "10"})
    assert bahan.status_code == 201
    listed = (await client.get("/api/printers/routing", headers=owner)).json()
    assert [u["name"] for u in listed["unmapped"]] == ["Pisang Goreng"]        # not Tepung Terigu
    # An unmapped product still prints: on the Bar slip, flagged, never dropped.
    sale = await client.post("/pos/orders", headers=_auth(c["pos"]), json={
        "lines": [{"item_id": made.json()["id"], "variant_id": None, "modifier_ids": [], "quantity": "1", "notes": None}],
        "payments": _cash(18000)})
    assert sale.status_code == 201, sale.text
    jobs = {j.kind: _texts(j.document) for j in await _order_jobs(session_factory, c, sale.json()["id"])}
    assert "Pisang Goreng" in jobs["bar_ticket"] and "TUJUAN BELUM DIATUR" in jobs["bar_ticket"]
    assert "kitchen_ticket" not in jobs
    assert (await client.patch(f"/api/items/{made.json()['id']}", headers=owner, json={"prep_station": "kitchen"})).status_code == 200
    assert (await client.get("/api/printers/routing", headers=owner)).json()["unmapped"] == []


def test_the_simulator_reads_the_logo_and_qr_as_pictures_not_commands():
    """The receipt's logo (GS v 0, till-12) and WhatsApp QR (GS ( k, till-7)
    carry raw bytes. A raster whose pixels happen to spell a status request
    (DLE EOT, GS r) or a cut must stay one picture: the simulator must not
    answer it or cut the paper there, or a rehearsal would fail for a reason
    no real printer has."""
    import base64

    tricky = (b"\x10\x04\x01" + b"\x1dr\x01" + b"\x1dV\x42\x00" + b"\x1b@") * 6   # status, confirm, cut and reset, as pixels
    width_bytes, height = 12, 6                                                   # 96 dots x 6 rows
    assert len(tricky) == width_bytes * height
    doc = {"v": 1, "blocks": [
        {"t": "logo", "text": "POERNAMA", "width": width_bytes * 8, "height": height, "bits": base64.b64encode(tricky).decode()},
        {"t": "text", "text": "Jl. Merdeka 12", "align": "center"},
        {"t": "qr", "data": "https://wa.me/628111?text=STRUK%20ab12"},
        {"t": "text", "text": "Ref 1234ABCD", "align": "center"},
    ]}
    data = pb.render(doc, pb.Profile(columns=48))
    papers = sp.decode(data)
    assert len(papers) == 1 and papers[0].cut == "partial"          # the pixels did not cut the paper
    texts = [l.text.strip() for l in papers[0].lines if l.text.strip()]
    assert texts[0] == "[logo 96x6 dots]"
    assert "Jl. Merdeka 12" in texts and "Ref 1234ABCD" in texts
    assert any(t.startswith("[QR https://wa.me/628111") for t in texts)
    # The live reader walks the same command lengths: the raster is one piece.
    start = data.index(b"\x1dv0")
    assert sp._command_length(data, start) == 8 + width_bytes * height
