"""prt-10 — the bridge finds each printer by its own network name.

A café router may hand a printer a different address after a power cut. With a
fixed address in the config that stops printing until someone edits a file; if
the two printers come back with each other's addresses, kitchen slips come out
at the front. So a printer may be configured by the name on its self-test page
("Hostname: IW-J300H-41CC") and is asked for afresh.

How the name is asked (LLMNR) was measured on the café's two IW-J300H on
3 Oct 2026; these tests use a stand-in responder and simulated printers on
loopback addresses. They prove the bridge's behaviour, not any printer's.

Needs the local Postgres (roadmap §2). No skip marker.
"""
import asyncio
import json
import socket
import struct
import threading

import pytest

from tests.test_open_bills import _order_jobs
from tests.test_prep_routing import _printer_token, _stations
from tests.test_print_bridge import AsgiApi, _drain, _paper_text, _takeaway, pb, printers, sp  # noqa: F401 (fixture)
from tests.test_service_journey import cafe, client, engine, session_factory  # noqa: F401 (fixtures)


class NameResponder:
    """Answers name questions the way the printers do: only for a name it
    holds, straight back to whoever asked."""

    def __init__(self, names: dict[str, str], *, lose: int = 0, wrong_id_first: bool = False, garbage: bool = False):
        self.names, self.lose, self.wrong_id_first, self.garbage = names, lose, wrong_id_first, garbage
        self.asked: list[str] = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.1)
        self.addr = self.sock.getsockname()
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while not self._stop.is_set():
            try:
                data, who = self.sock.recvfrom(1500)
            except socket.timeout:
                continue
            except OSError:
                return
            txid = struct.unpack("!H", data[:2])[0]
            name = data[13:13 + data[12]].decode()
            self.asked.append(name)
            if name not in self.names:
                continue                                     # nobody by that name: silence, as on a real network
            if self.lose:
                self.lose -= 1
                continue
            if self.garbage:
                self.sock.sendto(b"\x00\x01not a name answer", who)
                continue
            question = data[12:]
            answer = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 30, 4) + socket.inet_aton(self.names[name])

            def packet(i):
                return struct.pack("!HHHHHH", i, 0x8000, 1, 1, 0, 0) + question + answer

            if self.wrong_id_first:
                self.sock.sendto(packet((txid + 7) & 0xFFFF), who)   # an answer to somebody else's question
            self.sock.sendto(packet(txid), who)

    def close(self):
        self._stop.set()
        self.thread.join(2)
        self.sock.close()


@pytest.fixture
def responder():
    made = []

    def make(names, **kw):
        made.append(NameResponder(names, **kw))
        return made[-1]

    yield make
    for r in made:
        r.close()


def _find(name, r, **kw):
    return pb.find_by_name(name, "127.0.0.1", attempts=kw.pop("attempts", 3), timeout=kw.pop("timeout", 0.3), dest=r.addr)


# ── asking the network for a name ───────────────────────────────────────────


def test_a_printer_is_found_by_the_name_on_its_self_test_page(responder):
    r = responder({"IW-J300H-41CC": "192.168.1.23", "IW-J300H-2894": "192.168.1.25"})
    assert _find("IW-J300H-41CC", r) == "192.168.1.23"
    assert _find("IW-J300H-2894", r) == "192.168.1.25"
    # A printer that is off does not answer. That is "not found", never a guess.
    assert _find("IW-J300H-0000", r, attempts=2, timeout=0.15) is None
    assert r.asked.count("IW-J300H-0000") == 3               # everyone twice, then its last address directly


def test_a_question_lost_on_the_wifi_is_asked_again(responder):
    r = responder({"IW-J300H-41CC": "192.168.1.23"}, lose=2)
    assert _find("IW-J300H-41CC", r, attempts=3) == "192.168.1.23"
    assert r.asked == ["IW-J300H-41CC"] * 3
    quiet = responder({"IW-J300H-41CC": "192.168.1.23"}, lose=5)
    assert _find("IW-J300H-41CC", quiet, attempts=3, timeout=0.15) is None


def test_only_an_answer_to_our_own_question_counts(responder):
    r = responder({"IW-J300H-41CC": "192.168.1.23"}, wrong_id_first=True)
    assert _find("IW-J300H-41CC", r) == "192.168.1.23"       # the stray answer is skipped, ours is read
    junk = responder({"IW-J300H-41CC": "192.168.1.23"}, garbage=True)
    assert _find("IW-J300H-41CC", junk, attempts=2, timeout=0.15) is None
    # Refusals, questions echoed back and cut-off packets are not answers either.
    q = pb._name_question("IW-J300H-41CC", 0x1234)
    assert pb._name_answer(q, 0x1234) is None                                         # our own question
    refused = struct.pack("!HHHHHH", 0x1234, 0x8003, 1, 0, 0, 0) + q[12:]
    assert pb._name_answer(refused, 0x1234) is None
    good = struct.pack("!HHHHHH", 0x1234, 0x8000, 1, 1, 0, 0) + q[12:] + b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 30, 4) + bytes([10, 0, 0, 7])
    assert pb._name_answer(good, 0x1234) == "10.0.0.7"
    assert pb._name_answer(good[:-3], 0x1234) is None and pb._name_answer(b"", 0x1234) is None


def test_no_network_at_all_is_not_found_rather_than_a_crash():
    assert pb.find_by_name("IW-J300H-41CC", "127.0.0.1", attempts=1, timeout=0.1, dest=("127.0.0.1", 9)) is None


def test_a_printer_confirms_its_own_name_at_its_address_when_the_question_to_everyone_is_lost(responder):
    """Measured on the café's printers: asked directly, each answers for its
    own name and stays silent for the other's."""
    r = responder({"IW-J300H-41CC": "127.0.0.1"})
    nobody = ("127.0.0.1", 9)                                 # the network does not carry the question to everyone
    found = pb.find_by_name("IW-J300H-41CC", "127.0.0.1", attempts=1, timeout=0.2, dest=nobody, port=r.addr[1])
    assert found == "127.0.0.1" and r.asked == ["IW-J300H-41CC"]
    # The same address asked for the other printer's name: silence, so it is not mistaken for it.
    assert pb.find_by_name("IW-J300H-2894", "127.0.0.1", attempts=1, timeout=0.2, dest=nobody, port=r.addr[1]) is None


def test_a_printer_at_an_unknown_address_is_found_by_asking_each_neighbour(responder):
    r = responder({"IW-J300H-2894": "127.0.0.1"})
    assert pb.sweep_for_name("IW-J300H-2894", "127.0.0.9", port=r.addr[1], timeout=1.0) == "127.0.0.1"
    assert r.asked == ["IW-J300H-2894"]                       # one question per address, answered by one printer
    assert pb.sweep_for_name("IW-J300H-0000", "127.0.0.9", port=r.addr[1], timeout=0.3) is None


# ── configuration ───────────────────────────────────────────────────────────


def test_a_printer_may_be_configured_by_name_alone():
    cfg = pb.PrinterConfig.from_config("kitchen", {"name": "IW-J300H-2894", "model": "iware-iw-j300h"})
    assert (cfg.name, cfg.host, cfg.port) == ("IW-J300H-2894", "", 9100)
    both = pb.PrinterConfig.from_config("front", {"name": "IW-J300H-41CC", "host": "192.168.1.23"})
    assert (both.name, both.host) == ("IW-J300H-41CC", "192.168.1.23")
    # A fixed address on its own still works exactly as before.
    assert pb.PrinterConfig.from_config("front", {"host": "192.168.1.50"}).name == ""
    with pytest.raises(pb.ConfigError, match="needs name .* or host"):
        pb.PrinterConfig.from_config("front", {"model": "iware-iw-j300h"})
    for wrong in ("IW-J300H-41CC.local", "192.168.1.23", "printer depan", "-41CC"):
        with pytest.raises(pb.ConfigError, match="own network name"):
            pb.PrinterConfig.from_config("front", {"name": wrong})


def test_a_config_file_with_names_and_no_addresses_loads(tmp_path, monkeypatch):
    import base64

    def token(printer):
        body = base64.urlsafe_b64encode(json.dumps({"scope": "printer", "printer": printer}).encode()).decode().rstrip("=")
        return f"x.{body}.y"

    monkeypatch.setenv("FRONT_TOKEN", token("front"))
    monkeypatch.setenv("KITCHEN_TOKEN", token("kitchen"))
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"api_base": "https://api.example.test", "device_name": "tablet-kasir", "printers": {
        "front": {"name": "IW-J300H-41CC", "model": "iware-iw-j300h", "token_env": "FRONT_TOKEN"},
        "kitchen": {"name": "IW-J300H-2894", "model": "iware-iw-j300h", "token_env": "KITCHEN_TOKEN"}}}))
    config = pb.BridgeConfig.load(path)
    assert {slot: p.name for slot, (p, _) in config.printers.items()} == {"front": "IW-J300H-41CC", "kitchen": "IW-J300H-2894"}
    # Before the probe has run there is no address to quote, and the notice says where to get one.
    assert "address --check found" in pb.protocol_warning(config.printers["front"][0], tmp_path)


# ── the worker: where it sends, and where it refuses to ─────────────────────


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _named(slot, name, port, tmp_path, where, *, host="", book=None, clock=None):
    """A worker for a printer known by name. `where` is the network's current
    answer for each name (absent = the name does not answer)."""
    cfg = pb.PrinterConfig(slot=slot, name=name, host=host, port=port, connect_timeout=1.0, status_timeout=1.0,
                           io_timeout=2.0, confirm_timeout=1.5)
    asked = []

    def resolve(n, near=None):
        asked.append(n)
        return where.get(n)

    worker = pb.Worker(cfg, None, f"tes-{slot}", pb.Journal(tmp_path / f"journal-{slot}.json"),
                       clock=clock or Clock(), resolve=resolve, book=book)
    worker.asked = asked
    return worker


def _second_address(printers, sim):
    """Another printer on the same port at another loopback address: what a
    printer looks like after the router has given it a different address."""
    other = sp.SimPrinter("127.0.0.2", sim.port)
    try:
        other.start()
    except OSError:
        pytest.fail("this machine cannot listen on 127.0.0.2, which these tests need")
    return other


def test_a_printer_with_a_new_address_after_a_power_cut_is_found_again(printers, tmp_path):
    old = printers()
    where = {"IW-J300H-2894": "127.0.0.1"}
    worker = _named("kitchen", "IW-J300H-2894", old.port, tmp_path, where)
    assert worker.probe().state == "ready" and worker.address == "127.0.0.1"
    # Mati lampu. The printer comes back and the router gives it another address.
    old.stop()
    new = _second_address(printers, old)
    try:
        where["IW-J300H-2894"] = "127.0.0.2"
        first = worker.probe()                                # its old address is tried once and is dead
        assert first.state == "offline" and "IW-J300H-2894" in first.detail
        assert worker.probe().state == "ready"                # so the name is asked again, and it is found
        assert worker.address == "127.0.0.2"
        done = worker.deliver(pb.render(pb.test_document("kitchen"), worker.cfg.profile))
        assert (done.outcome, done.evidence) == ("printed", "printer_status")
        assert len(new.papers) == 1 and old.papers == []
    finally:
        new.stop()


def test_an_address_the_name_gave_is_not_asked_for_again_on_every_slip(printers, tmp_path):
    sim = printers()
    clock = Clock()
    worker = _named("front", "IW-J300H-41CC", sim.port, tmp_path, {"IW-J300H-41CC": "127.0.0.1"}, clock=clock)
    for _ in range(4):
        assert worker.probe().state == "ready"
    assert len(worker.asked) == 1
    clock.now += pb.Worker.NAME_RECHECK + 1                   # but it is not trusted for long
    assert worker.probe().state == "ready" and len(worker.asked) == 2
    worker.note_gap(2.0, 600.0)                               # and not at all after the tablet was frozen
    assert worker.probe().state == "ready" and len(worker.asked) == 3


def test_a_name_that_stops_answering_does_not_stop_a_printer_that_is_still_there(printers, tmp_path):
    sim = printers()
    clock = Clock()
    where = {"IW-J300H-41CC": "127.0.0.1"}
    worker = _named("front", "IW-J300H-41CC", sim.port, tmp_path, where, clock=clock)
    assert worker.probe().state == "ready"
    where.clear()                                             # the network stops carrying the question
    clock.now += 60
    assert worker.probe().state == "ready"                    # its last address is used as a fixed one would be
    done = worker.deliver(pb.render(pb.test_document("front"), worker.cfg.profile))
    assert done.outcome == "printed" and len(sim.papers) == 1
    clock.now += 60
    worker.probe()
    assert len(worker.asked) == 4                             # and the name is asked for again each time until it answers


def test_a_name_that_never_answered_falls_back_to_the_address_in_the_config(printers, tmp_path):
    sim = printers()
    with_host = _named("front", "IW-J300H-41CC", sim.port, tmp_path, {}, host="127.0.0.1")
    assert with_host.probe().state == "ready"
    without = _named("kitchen", "IW-J300H-2894", sim.port, tmp_path, {})
    state = without.probe()
    assert state.state == "offline" and "IW-J300H-2894 tidak ditemukan" in state.detail
    done = without.deliver(b"slip")
    assert done.outcome == "not_sent" and "tidak ditemukan" in done.state.detail
    assert sim.papers == []


def test_a_slip_never_goes_to_the_other_printer_when_addresses_swap(printers, tmp_path):
    """Both printers restart and the router swaps their addresses. The kitchen
    printer answers to its name; the front one is still starting up and does
    not. The front worker's last address is now the kitchen printer."""
    at_1 = printers()
    at_2 = _second_address(printers, at_1)
    try:
        book, clock = pb.AddressBook(), Clock()
        where = {"IW-J300H-41CC": "127.0.0.1", "IW-J300H-2894": "127.0.0.2"}
        front = _named("front", "IW-J300H-41CC", at_1.port, tmp_path, where, book=book, clock=clock)
        kitchen = _named("kitchen", "IW-J300H-2894", at_1.port, tmp_path, where, book=book, clock=clock)
        assert front.probe().state == "ready" and kitchen.probe().state == "ready"
        # The swap: the kitchen printer now holds 127.0.0.1, and the front one is silent.
        where.clear()
        where["IW-J300H-2894"] = "127.0.0.1"
        clock.now += 60
        assert kitchen.probe().state == "ready" and kitchen.address == "127.0.0.1"
        state = front.probe()
        assert state.state == "offline" and "tidak ditemukan" in state.detail
        done = front.deliver(pb.render(pb.test_document("front"), front.cfg.profile))
        assert done.outcome == "not_sent"                     # no byte left: the receipt waits, unmarked
        assert at_1.papers == [] and at_2.papers == []
        # The front printer finishes starting and answers: each slip goes to its own printer.
        where["IW-J300H-41CC"] = "127.0.0.2"
        assert front.probe().state == "ready" and front.address == "127.0.0.2"
        assert front.deliver(pb.render(pb.test_document("front"), front.cfg.profile)).outcome == "printed"
        assert kitchen.deliver(pb.render(pb.test_document("kitchen"), kitchen.cfg.profile)).outcome == "printed"
        assert "TES PRINTER DEPAN" in _paper_text(at_2.papers[0]) and _paper_text(at_1.papers[0]).startswith("DAPUR")
    finally:
        at_2.stop()


def test_the_two_workers_of_one_bridge_share_what_they_know_about_addresses(tmp_path):
    """The swap protection above only works if both workers read one book."""
    cfg = pb.BridgeConfig(api_base="https://api.example.test", device_name="tablet-kasir", state_dir=tmp_path, poll_seconds=2.0,
                          printers={"front": (pb.PrinterConfig(slot="front", name="IW-J300H-41CC"), "t"),
                                    "kitchen": (pb.PrinterConfig(slot="kitchen", name="IW-J300H-2894"), "t")})
    book = pb.AddressBook()
    a, b = pb.make_worker(cfg, "front", api=object(), book=book), pb.make_worker(cfg, "kitchen", api=object(), book=book)
    assert a.book is b.book
    book.note("kitchen", "10.0.0.5")
    assert book.holder("10.0.0.5", but="front") == "kitchen" and book.holder("10.0.0.5", but="kitchen") is None


# ── end to end: a real order, the real API, a printer that moved ────────────


async def test_an_order_placed_after_the_printer_moved_prints_once_at_its_new_address(
        client, session_factory, cafe, printers, tmp_path):
    c = cafe
    await _stations(client, c)
    old = printers()
    where = {"IW-J300H-2894": "127.0.0.1"}
    headers = await _printer_token(client, c, "kitchen")
    cfg = pb.PrinterConfig(slot="kitchen", name="IW-J300H-2894", port=old.port, connect_timeout=1.0, status_timeout=1.0,
                           io_timeout=2.0, confirm_timeout=1.5)
    worker = pb.Worker(cfg, AsgiApi(client, asyncio.get_running_loop(), headers), "tes-kitchen",
                       pb.Journal(tmp_path / "journal-kitchen.json"), resolve=lambda n, near=None: where.get(n))
    assert await _drain(worker) == ["idle"]                   # found, nothing to print yet
    old.stop()                                                # mati lampu; the router gives it a new address
    new = _second_address(printers, old)
    try:
        where["IW-J300H-2894"] = "127.0.0.2"
        sale = await _takeaway(client, c)
        results = []
        for _ in range(40):
            results.append(await asyncio.to_thread(worker.run_once))
            if results[-1] == "printed":
                break
            await asyncio.sleep(0.2)
        # The slip was taken, could not be sent to the dead address (so no paper
        # can exist) and went back unmarked; then the name was asked again.
        assert results[0] == "released" and results[-1] == "printed"
        assert len(new.papers) == 1 and old.papers == []
        text = _paper_text(new.papers[0])
        assert text.startswith("DAPUR") and "2x Roti" in text and "CETAK ULANG" not in text
        dapur = next(j for j in await _order_jobs(session_factory, c, sale["id"]) if j.kind == "kitchen_ticket")
        assert (dapur.status, dapur.evidence, dapur.copy) == ("printed", "printer_status", "original")
        assert worker.journal.entries == {}
    finally:
        new.stop()
