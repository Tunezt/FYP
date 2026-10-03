#!/usr/bin/env python3
"""Poernama print bridge: pulls print jobs from the API and prints them on
network ESC/POS printers (raw TCP, usually port 9100).

Why it exists: the API runs in the cloud and cannot open a connection to a
printer on the café's Wi-Fi, and the till is a Chrome page that cannot open a
raw TCP socket. Something on the café network has to *pull* the jobs over
HTTPS and *push* bytes to the printers. This is that something. It is one file,
standard library only (Python 3.9+), so it runs unchanged on a Windows or Mac
laptop, a Linux box or Raspberry Pi, or an Android phone under Termux.

What it promises, and what it does not:

* Each printer has its own worker thread and its own token. A dead kitchen
  printer never holds up the front printer.
* One job at a time per printer, oldest first, one paper cut per job.
* A job is only reported **printed** with evidence: `printer_status` when the
  printer answered a status request sent *after* the job's cut (GS r), which it
  only does once it has processed everything before it; `bytes_delivered` when
  the printer merely accepted the bytes. The till shows the two differently.
* If nothing was written to the printer (unreachable, paper out, cover open
  before the job started) the job is **released** back to the queue, unmarked,
  because no paper can exist. It prints when the printer is back.
* If any byte may have reached the printer and the outcome is not confirmed,
  the job is reported **uncertain** and is never resent by the bridge. The till
  offers a marked reprint (CETAK ULANG) or "Kertas sudah ada".
* A journal on disk (fsynced before the first byte is written) lets a
  restarted bridge tell "never sent" (release) from "may be on paper"
  (uncertain), including when a claim's HTTP response was lost.

Physical printing is not verified by this program or its tests. See
bridge/README.md for the hardware acceptance checklist.
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import re
import signal
import socket
import struct
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

VERSION = "1.0.0"
log = logging.getLogger("print_bridge")

# ── ESC/POS ─────────────────────────────────────────────────────────────────

ESC, GS, DLE, LF = b"\x1b", b"\x1d", b"\x10", b"\n"
INIT = ESC + b"@"
CODEPAGE_PC437 = ESC + b"t\x00"


def _align(n: int) -> bytes:
    return ESC + b"a" + bytes([n])


def _bold(on: bool) -> bytes:
    return ESC + b"E" + bytes([1 if on else 0])


def _size(width: int, height: int) -> bytes:
    return GS + b"!" + bytes([((width - 1) << 4) | (height - 1)])


def _font(n: int) -> bytes:
    """ESC M: 0 = Font A (12x24 dots, 48 to a line on 80 mm), 1 = Font B (9x17, 64 to a line)."""
    return ESC + b"M" + bytes([n])


FONT_DOTS = {0: 12, 1: 9}       # character width in dots, before any doubling


def _inverse(on: bool) -> bytes:
    return GS + b"B" + bytes([1 if on else 0])


def qr_command(data: str, module: int = 6) -> bytes:
    """GS ( k, QR model 2, error correction M, centred. Standard ESC/POS, but
    NOT yet seen on the café's IW-J300H: only receipts carry it, and only once
    the WhatsApp bot is live (till-7). A printer that ignores it prints the
    caption under it, which still says what to do."""
    payload = data.encode("ascii", "replace")
    n = len(payload) + 3
    return (
        _align(1)
        + GS + b"(k\x04\x00\x31\x41\x32\x00"              # model 2
        + GS + b"(k\x03\x00\x31\x43" + bytes([max(1, min(module, 16))])   # module size
        + GS + b"(k\x03\x00\x31\x45\x31"                # error correction M
        + GS + b"(k" + bytes([n % 256, n // 256]) + b"\x31\x50\x30" + payload   # store
        + GS + b"(k\x03\x00\x31\x51\x30"                # print
        + LF + _align(0)
    )


def logo_command(width: int, height: int, bits: str) -> bytes:
    """till-12: the café's logo, dot for dot. GS v 0 (print raster bit image),
    normal density, centred: `width` dots (a multiple of 8, at most the 576 of
    an 80 mm head), `height` rows, `bits` base64 of rows of width/8 bytes, most
    significant bit first, 1 = ink. Standard ESC/POS, but NOT yet seen on the
    café's IW-J300H; a document whose bitmap does not add up prints nothing
    here rather than garbage."""
    import base64
    import binascii

    try:
        data = base64.b64decode(bits, validate=True)
    except (binascii.Error, ValueError):
        return b""
    width_bytes = width // 8
    if width <= 0 or width % 8 or width > 576 or height <= 0 or height > 2000 or len(data) != width_bytes * height:
        return b""
    return (
        _align(1)
        + GS + b"v0\x00" + bytes([width_bytes % 256, width_bytes // 256, height % 256, height // 256])
        + data
        + LF + _align(0)
    )


def cut_command(mode: str, feed: int) -> bytes:
    """GS V function B: feed to the cutter plus `feed` lines, then cut."""
    if mode == "none":
        return ESC + b"d" + bytes([max(0, min(feed + 3, 255))])
    return GS + b"V" + bytes([65 if mode == "full" else 66, max(0, min(feed, 255))])


STATUS_REQUESTS = {1: DLE + b"\x04\x01", 2: DLE + b"\x04\x02", 4: DLE + b"\x04\x04"}
PROCESSED_REQUEST = GS + b"r\x01"   # transmit paper sensor status — answered in order, after preceding data

# Buzzer commands differ per board and none is safe to assume. probe_printer.py
# prints one labelled slip per candidate; whichever one sounded goes in the config.
BEEP_COMMANDS = {
    "esc_b": lambda times, duration: ESC + b"B" + bytes([times & 0xFF, duration & 0xFF]),
    "esc_paren_a": lambda times, duration: ESC + b"(A" + bytes([4, 0, 48, 1, times & 0xFF, duration & 0xFF]),
    "bel": lambda times, duration: b"\x07" * max(1, times),
    "drawer_kick": lambda times, duration: ESC + b"p\x00\x19\xfa",
}


def beep_command(beep: dict) -> bytes:
    name = beep.get("command", "esc_b")
    if name not in BEEP_COMMANDS:
        raise ConfigError(f"beep.command must be one of {', '.join(BEEP_COMMANDS)} (whichever probe_printer.py showed works)")
    return BEEP_COMMANDS[name](int(beep.get("times", 2)), int(beep.get("duration", 3)))


# Known printer models: the defaults to start from, and how sure we are of
# them. "advertised" means the seller's specification, not a tested fact — the
# bridge says so in --check until probe_printer.py has confirmed the model.
MODELS = {
    "iware-iw-j300h": {
        "paper_mm": 80, "columns": 48, "cut": "partial", "status": "gs_r", "port": 9100,
        "confidence": "advertised",
        "note": "Iware IW-J300H: 80 mm, auto-cutter, ESC/POS, Wi-Fi/LAN/USB/Bluetooth/serial, kitchen alarm. "
                "Port, status support, cut command and buzzer command are NOT published: run probe_printer.py.",
    },
    "generic-escpos-80": {"paper_mm": 80, "columns": 48, "cut": "partial", "status": "gs_r", "port": 9100,
                          "confidence": "generic", "note": "Generic 80 mm ESC/POS over raw TCP."},
    "generic-escpos-58": {"paper_mm": 58, "columns": 32, "cut": "partial", "status": "gs_r", "port": 9100,
                          "confidence": "generic", "note": "Generic 58 mm ESC/POS over raw TCP."},
}


@dataclass
class Profile:
    """How one printer lays out paper. 80 mm with font A is 48 columns, 58 mm is 32."""
    columns: int = 48
    cut: str = "partial"            # partial · full · none
    feed_lines: int = 3
    banner_scale: int = 2           # MEJA 7 / PESANAN 042 at 2x2 (3 = 3x3)
    # {"command": "esc_b", "times": 2, "duration": 3}. Only sent when the
    # command has been confirmed on the real printer with probe_printer.py:
    # an unverified buzzer byte can make some firmwares print rubbish.
    beep: dict | None = None

    @classmethod
    def from_config(cls, cfg: dict) -> "Profile":
        model = MODELS.get(cfg.get("model", ""), {})
        paper = int(cfg.get("paper_mm", model.get("paper_mm", 80)))
        columns = int(cfg.get("columns") or (model.get("columns") if paper == model.get("paper_mm") else None)
                      or (32 if paper <= 58 else 48))
        cut = cfg.get("cut", model.get("cut", "partial"))
        if cut not in ("partial", "full", "none"):
            raise ConfigError(f"cut must be partial, full or none, not {cut!r}")
        scale = int(cfg.get("banner_scale", 2))
        if scale not in (1, 2, 3):
            raise ConfigError("banner_scale must be 1, 2 or 3")
        if columns < 24:
            raise ConfigError("columns must be at least 24")
        beep = cfg.get("beep")
        if beep:
            if not beep.get("verified"):
                raise ConfigError('beep must carry "verified": true — run probe_printer.py --paper --buzzer first and '
                                  "set the command that actually sounded")
            beep_command(beep)                        # fails now, not mid-service
        return cls(columns=columns, cut=cut, feed_lines=int(cfg.get("feed_lines", 3)), banner_scale=scale, beep=beep)


_CHARMAP = {
    "×": "x", "·": "-", "•": "-", "—": "-", "–": "-", "‘": "'", "’": "'", "“": '"', "”": '"',
    "…": "...", " ": " ", "\t": " ", "±": "+/-", "°": " ", "€": "EUR",
}


def to_printer_text(text) -> str:
    """Printable ASCII only. Codepage support differs between models, and a
    wrong codepage prints garbage in the middle of a kitchen order, so every
    character is reduced to something any ESC/POS printer prints the same."""
    out = []
    for ch in str(text if text is not None else ""):
        ch = _CHARMAP.get(ch, ch)
        if all(32 <= ord(c) < 127 for c in ch):
            out.append(ch)
            continue
        folded = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode("ascii")
        out.append(folded if folded and folded.isprintable() else "?")
    return "".join(out)


def wrap(text: str, width: int, first_indent: str = "", rest_indent: str | None = None) -> list[str]:
    """Word wrap to `width` characters. A word longer than a line is split, never
    allowed to run past the edge of the paper."""
    rest_indent = first_indent if rest_indent is None else rest_indent
    text = " ".join(to_printer_text(text).split())
    lines: list[str] = []
    current = first_indent
    indent = first_indent
    for word in text.split(" ") if text else []:
        while True:
            room = width - len(current)
            sep = "" if current == indent else " "
            if len(sep) + len(word) <= room:
                current += sep + word
                break
            if current != indent:
                lines.append(current)
                indent = rest_indent
                current = indent
                continue
            # the word alone is longer than the line: split it
            take = max(1, width - len(current))
            current += word[:take]
            word = word[take:]
            lines.append(current)
            indent = rest_indent
            current = indent
            if not word:
                break
    if current.strip() or not lines:
        lines.append(current)
    return lines


class Writer:
    def __init__(self, columns: int):
        self.columns = columns
        self.buf = bytearray()

    def row(self, text: str, *, align: str = "left", bold: bool = False, width: int = 1, height: int = 1,
            inverse: bool = False, font: int = 0) -> None:
        text = to_printer_text(text)
        # Measured in dots: `columns` counts Font A characters, 12 dots each.
        assert len(text) * width * FONT_DOTS[font] <= self.columns * 12, (text, width, font, self.columns)
        self.buf += _align({"left": 0, "center": 1, "right": 2}[align])
        if font:
            self.buf += _font(font)
        self.buf += _bold(bold) + _size(width, height) + _inverse(inverse)
        self.buf += text.encode("ascii") + LF
        self.buf += _inverse(False) + _size(1, 1) + _bold(False) + _align(0)
        if font:
            self.buf += _font(0)

    def rows(self, lines: list[str], **style) -> None:
        for line in lines:
            self.row(line, **style)

    def kv(self, left: str, right: str, *, bold: bool = False, height: int = 1) -> None:
        left, right = to_printer_text(left), to_printer_text(right)
        if len(left) + 1 + len(right) <= self.columns:
            self.row(left + " " * (self.columns - len(left) - len(right)) + right, bold=bold, height=height)
            return
        self.rows(wrap(left, self.columns), bold=bold, height=height)
        for line in wrap(right, self.columns):
            self.row(line.rjust(self.columns), bold=bold, height=height)


def _item_head(block: dict) -> str:
    head = f"{to_printer_text(block.get('qty') or '')}x {to_printer_text(block.get('name') or '')}"
    if block.get("size"):
        head += f" ({to_printer_text(block['size'])})"
    return head


def render_block(w: Writer, block: dict, profile: Profile) -> None:
    t = block.get("t")
    cols = w.columns
    if t == "title":
        w.rows(wrap(block.get("text", ""), cols), align="center", bold=True)
    elif t == "logo":
        # till-12: the logo as a bitmap; a bitmap that does not add up falls
        # back to the café's name in bold, so the receipt still says whose it is.
        raster = logo_command(int(block.get("width") or 0), int(block.get("height") or 0), str(block.get("bits") or ""))
        if raster:
            w.buf += raster
        elif block.get("text"):
            w.rows(wrap(block["text"], cols), align="center", bold=True)
    elif t == "label":
        # An inverse bar across the paper: TAMBAHAN, BATAL, CETAK ULANG, BELUM DIBAYAR, TERLAMBAT.
        half = cols // 2
        for line in wrap(block.get("text", ""), half):
            w.row(line.center(half), bold=True, width=2, height=2, inverse=True)
        # A clear line under the bar: the next row otherwise prints against
        # its black edge and the two read as one smudge.
        w.row("")
    elif t == "banner":
        s = profile.banner_scale
        w.rows(wrap(block.get("text", ""), cols // s), align="center", bold=True, width=s, height=s)
    elif t == "line":
        large = block.get("size") == "large"
        w.rows(wrap(block.get("text", ""), cols), align="center", bold=block.get("style") == "bold", height=2 if large else 1)
    elif t == "kv":
        w.kv(block.get("left") or "", block.get("right") or "")
    elif t == "rule":
        w.row(("=" if block.get("style") == "double" else "-") * cols)
    elif t == "item":
        if block.get("flag"):
            w.rows(wrap(f"[{block['flag']}]", cols), bold=True)
        # What the bar and the kitchen cook from. Three sizes were tried on the
        # café's printers (4 Oct 2026): Font A double height only gave tall thin
        # letters that had to be studied; Font A doubled both ways read well but
        # held 24 to a line, wrapped "(Standar)" under every drink and used far
        # too much paper. Font B doubled both ways sits between them: letters
        # 4 mm tall with their natural shape, 32 to a line, so a name with its
        # size stays on one row. Extras share one row instead of taking one
        # each, and items follow one another without a blank row between.
        big = (cols * 12) // (FONT_DOTS[1] * 2)
        style = dict(font=1, width=2, height=2)
        head = _item_head(block)
        hang = " " * min(len(to_printer_text(block.get("qty") or "")) + 2, 6)
        w.rows(wrap(head, big, "", hang), bold=True, **style)
        mods = [str(m) for m in block.get("modifiers") or [] if str(m).strip()]
        if mods:
            w.rows(wrap("- " + ", ".join(mods), big, " ", "   "), **style)
        if block.get("notes"):
            w.rows(wrap(f"* {block['notes']}", big, " ", "   "), bold=True, **style)
    elif t == "item_priced" and block.get("unit_price"):
        # till-12: the name on its own line, then "2 x @15.000" and the amount
        # on the right, the way café receipts set an item.
        name = to_printer_text(block.get("name") or "")
        if block.get("size"):
            name += f" ({to_printer_text(block['size'])})"
        w.rows(wrap(name, cols), bold=True)
        w.kv(f"  {to_printer_text(block.get('qty') or '')} x @{to_printer_text(block['unit_price'])}", to_printer_text(block.get("amount") or ""))
        for m in block.get("modifiers") or []:
            w.rows(wrap(f"+ {m}", cols, "  ", "    "))
        if block.get("notes"):
            w.rows(wrap(f"* {block['notes']}", cols, "  ", "    "))
    elif t == "item_priced":
        amount = to_printer_text(block.get("amount") or "")
        room = max(cols - len(amount) - 1, cols // 2)
        head = wrap(_item_head(block), room, "", "   ")
        w.row(head[0].ljust(cols - len(amount)) + amount if len(head[0]) + len(amount) < cols else head[0])
        if len(head[0]) + len(amount) >= cols:
            w.row(amount.rjust(cols))
        w.rows(head[1:])
        for m in block.get("modifiers") or []:
            w.rows(wrap(f"+ {m}", cols, "  ", "    "))
        if block.get("notes"):
            w.rows(wrap(block["notes"], cols, "  ", "  "))
    elif t == "total":
        w.kv(block.get("left") or "", block.get("right") or "", bold=True, height=2)
    elif t == "note":
        w.rows(wrap(block.get("text", ""), cols), bold=True)
    elif t == "text":
        w.rows(wrap(block.get("text", ""), cols), align="center" if block.get("align") == "center" else "left",
               bold=block.get("style") == "bold")
    elif t == "qr":
        # till-7: the WhatsApp receipt link as a QR code under the receipt.
        if block.get("data"):
            w.buf += qr_command(str(block["data"]))
    else:
        # A block this bridge does not know (a newer server). Print whatever
        # words it carries rather than silently dropping part of an order.
        words = [str(block[k]) for k in ("text", "left", "qty", "name", "size", "right", "amount") if block.get(k)]
        words += [str(m) for m in block.get("modifiers") or []]
        if block.get("notes"):
            words.append(str(block["notes"]))
        log.warning("unknown block type %r printed as plain text", t)
        if words:
            w.rows(wrap(" ".join(words), cols))


def render(document: dict, profile: Profile) -> bytes:
    """One job's frozen document → one run of ESC/POS ending in one cut."""
    if not isinstance(document, dict) or not isinstance(document.get("blocks"), list):
        raise ValueError("document has no blocks")
    w = Writer(profile.columns)
    for block in document["blocks"]:
        if not isinstance(block, dict):
            raise ValueError("block is not an object")
        render_block(w, block, profile)
    out = bytearray(INIT + CODEPAGE_PC437)
    out += w.buf
    if profile.beep:
        out += beep_command(profile.beep)
    out += cut_command(profile.cut, profile.feed_lines)
    return bytes(out)


# ── Talking to one printer ──────────────────────────────────────────────────

BLOCKING = {"offline", "paper_out", "cover_open", "error"}


@dataclass
class Delivery:
    """What is known after trying to put one document on paper."""
    outcome: str                    # printed · uncertain · not_sent
    evidence: str | None = None     # printer_status · bytes_delivered
    error: str | None = None
    state: "PrinterState | None" = None    # why nothing was sent


@dataclass
class PrinterState:
    state: str          # ready · reachable · paper_low · paper_out · cover_open · offline · error · unknown
    detail: str = ""

    @property
    def blocking(self) -> bool:
        return self.state in BLOCKING


class StatusUnavailable(Exception):
    """The printer did not answer a status request (it may not support it)."""


def _valid_rt(b: int) -> bool:
    # DLE EOT replies: bit 1 and bit 4 fixed on, bit 0 and bit 7 fixed off.
    return (b & 0x93) == 0x12


def parse_realtime_status(s1: int, s2: int, s4: int) -> PrinterState:
    if not all(_valid_rt(b) for b in (s1, s2, s4)):
        raise StatusUnavailable(f"balasan status tidak dikenali ({s1:#04x} {s2:#04x} {s4:#04x})")
    if s2 & 0x04:
        return PrinterState("cover_open", "Tutup printer terbuka")
    if s4 & 0x60 or s2 & 0x20:
        return PrinterState("paper_out", "Kertas habis")
    if s2 & 0x40:
        return PrinterState("error", "Printer melaporkan error (pemotong macet atau kepala terlalu panas)")
    if s1 & 0x08:
        return PrinterState("offline", "Printer sedang offline")
    if s4 & 0x0C:
        return PrinterState("paper_low", "Kertas hampir habis")
    return PrinterState("ready", "")


class PrinterConnection:
    """One TCP connection to a raw ESC/POS port, for one job or one probe."""

    def __init__(self, host: str, port: int, *, connect_timeout: float, io_timeout: float):
        self.host, self.port = host, port
        self.connect_timeout, self.io_timeout = connect_timeout, io_timeout
        self.sock: socket.socket | None = None
        self.sent = 0

    def __enter__(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=self.connect_timeout)
        self.sock.settimeout(self.io_timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return self

    def __exit__(self, *exc):
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None

    def _ask(self, request: bytes, timeout: float) -> int | None:
        """One real-time status byte. Bytes that cannot be a DLE EOT reply
        (a late GS r answer) are skipped rather than misread."""
        deadline = time.monotonic() + timeout
        self.sock.sendall(request)
        try:
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    return None
                self.sock.settimeout(left)
                try:
                    data = self.sock.recv(1)
                except socket.timeout:
                    return None
                if not data:
                    raise ConnectionError("printer menutup koneksi")
                if _valid_rt(data[0]):
                    return data[0]
        finally:
            self.sock.settimeout(self.io_timeout)

    def status(self, timeout: float) -> PrinterState:
        replies = {}
        for n, req in STATUS_REQUESTS.items():
            b = self._ask(req, timeout)
            if b is None:
                raise StatusUnavailable("printer tidak menjawab permintaan status (DLE EOT)")
            replies[n] = b
        return parse_realtime_status(replies[1], replies[2], replies[4])

    def write(self, data: bytes, chunk: int = 1024) -> None:
        """Send everything, counting what the OS accepted, so a failure can tell
        'nothing left this machine' from 'some of it may have been printed'."""
        view = memoryview(data)
        while self.sent < len(data):
            n = self.sock.send(view[self.sent:self.sent + chunk])
            if n == 0:
                raise ConnectionError("koneksi ke printer tertutup")
            self.sent += n

    def confirm_processed(self, timeout: float) -> int | None:
        """GS r 1 goes into the printer's buffer behind the job and is answered
        only when the printer reaches it, i.e. after it has processed the job
        up to and including the cut. None when no answer came in time."""
        deadline = time.monotonic() + timeout
        self.sock.sendall(PROCESSED_REQUEST)
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            self.sock.settimeout(left)
            try:
                data = self.sock.recv(1)
            except socket.timeout:
                return None
            if not data:
                raise ConnectionError("printer menutup koneksi sebelum konfirmasi")
            if (data[0] & 0x90) == 0:
                return data[0]
            # anything else (e.g. an automatic status byte) is skipped


# ── Finding a printer by its own name (prt-10) ──────────────────────────────
#
# A café router hands out addresses and may hand out a different one after a
# power cut. A printer's own network name does not change: it is on its
# self-test page ("Hostname: IW-J300H-41CC"), and the IW-J300H answers when the
# local network is asked for that name (LLMNR, RFC 4795: a DNS-shaped question
# sent to 224.0.0.252:5355, answered straight back to the asker). Measured on
# the café's two printers, 3 Oct 2026. They do not answer mDNS or NetBIOS.

LLMNR_GROUP = ("224.0.0.252", 5355)
_NAME_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_txid = [int.from_bytes(os.urandom(2), "big")]


class PrinterNotFound(OSError):
    """No address to try for this printer right now. Nothing was sent."""


def _name_question(name: str, txid: int) -> bytes:
    label = name.encode("ascii")
    return struct.pack("!HHHHHH", txid, 0, 1, 0, 0, 0) + bytes([len(label)]) + label + b"\x00" + struct.pack("!HH", 1, 1)


def _skip_name(data: bytes, i: int) -> int:
    while True:
        n = data[i]
        if n == 0:
            return i + 1
        if n & 0xC0 == 0xC0:        # a pointer back into the packet ends the name
            return i + 2
        i += 1 + n


def _name_answer(data: bytes, txid: int) -> str | None:
    """The IPv4 address in an answer to our question, or None for anything else
    (somebody else's answer, a refusal, a truncated or malformed packet)."""
    try:
        rid, flags, questions, answers = struct.unpack("!HHHH", data[:8])
        if rid != txid or not flags & 0x8000 or flags & 0x000F or not answers:
            return None
        i = 12
        for _ in range(questions):
            i = _skip_name(data, i) + 4
        for _ in range(answers):
            i = _skip_name(data, i)
            rtype, rclass, _ttl, size = struct.unpack("!HHIH", data[i:i + 10])
            i += 10
            if rtype == 1 and rclass & 0x7FFF == 1 and size == 4 and len(data) >= i + 4:
                return socket.inet_ntoa(data[i:i + 4])
            i += size
    except (IndexError, struct.error):
        pass
    return None


def _lan_address(near: str | None) -> str | None:
    """This machine's own address on the network the printers are on: the one
    it would use to reach `near` (the printer's last address), else its default
    route. No packet is sent."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((near or "192.0.2.1", 9))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def _ask(name: str, targets: list[tuple[str, int]], own: str | None, timeout: float) -> str | None:
    """Put one question to each target and wait for the first real answer."""
    _txid[0] = (_txid[0] + 1) & 0xFFFF
    txid = _txid[0]
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError:
        return None
    try:
        if own and not own.startswith("127."):
            try:
                s.bind((own, 0))
            except OSError:
                pass
        try:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        except OSError:
            pass
        question = _name_question(name, txid)
        sent = 0
        for target in targets:
            try:
                s.sendto(question, target)
                sent += 1
            except OSError:
                pass
        deadline = time.monotonic() + timeout
        while sent:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            s.settimeout(left)
            try:
                data, _ = s.recvfrom(1500)
            except socket.timeout:
                break
            except OSError:          # e.g. "port unreachable" from one of many targets: keep listening
                continue
            found = _name_answer(data, txid)
            if found:
                return found
    finally:
        s.close()
    return None


SWEEP_EVERY = 60.0
_swept_at: dict[str, float] = {}


def sweep_for_name(name: str, own: str, port: int = LLMNR_GROUP[1], timeout: float = 0.7) -> str | None:
    """Ask every address next to ours (x.y.z.1–254) directly. Only the printer
    with that name answers. This finds it on a network that does not pass a
    question sent to everyone. Café routers hand out x.y.z.* addresses; a
    larger network is not searched."""
    prefix = own.rsplit(".", 1)[0]
    return _ask(name, [(f"{prefix}.{i}", port) for i in range(1, 255) if f"{prefix}.{i}" != own], own, timeout)


def find_by_name(name: str, near: str | None = None, *, attempts: int = 3, timeout: float = 0.7,
                 dest: tuple[str, int] = LLMNR_GROUP, port: int | None = None) -> str | None:
    """Which address `name` has right now, or None: the printer is off or not
    on this network. Three ways, cheapest first:

    1. ask everyone on the network, a few times (a question sent to everyone
       on Wi-Fi is not retried by the radio the way ordinary traffic is);
    2. ask the printer's last address directly — the IW-J300H confirms its own
       name there and stays silent for any other name;
    3. at most once a minute, ask every neighbouring address directly."""
    port = port or dest[1]
    own = _lan_address(near)
    for _ in range(attempts):
        found = _ask(name, [dest], own, timeout)
        if found:
            return found
    if near:
        found = _ask(name, [(near, port)], own, timeout)
        if found:
            return found
    now = time.monotonic()
    if own and not own.startswith("127.") and now - _swept_at.get(name, -SWEEP_EVERY) >= SWEEP_EVERY:
        _swept_at[name] = now
        return sweep_for_name(name, own, port, timeout)
    return None


class AddressBook:
    """Which address each named printer was last found at. The workers share
    it so that a printer's old address is never used once the *other* printer
    has been found living there."""

    def __init__(self):
        self._lock = threading.Lock()
        self._found: dict[str, str] = {}

    def note(self, slot: str, address: str) -> None:
        with self._lock:
            self._found[slot] = address

    def holder(self, address: str, but: str) -> str | None:
        with self._lock:
            return next((slot for slot, found in self._found.items() if found == address and slot != but), None)


# ── Talking to the API ──────────────────────────────────────────────────────


class ConfigError(Exception):
    pass


class ApiUnreachable(Exception):
    """No answer from the API (network down, DNS, timeout, 5xx)."""


class Api:
    """POST JSON to the Poernama API with one printer's token."""

    def __init__(self, base_url: str, token: str, *, timeout: float = 15.0):
        self.base_url, self.token, self.timeout = base_url.rstrip("/"), token, timeout

    def post(self, path: str, body: dict) -> tuple[int, dict]:
        req = urllib.request.Request(
            self.base_url + path, data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json",
                     "User-Agent": f"poernama-print-bridge/{VERSION}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code >= 500:
                raise ApiUnreachable(f"HTTP {e.code}") from e
            try:
                payload = json.loads(e.read() or b"{}")
            except ValueError:
                payload = {}
            return e.code, payload
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise ApiUnreachable(str(e)) from e


# ── The journal ─────────────────────────────────────────────────────────────


class Journal:
    """What this printer's worker is in the middle of, on disk, so a restart
    knows whether bytes may have reached the printer. Written atomically and
    fsynced before the first byte of a job goes out."""

    def __init__(self, path: Path):
        self.path = path
        self.entries: dict[str, dict] = {}
        if path.exists():
            try:
                self.entries = json.loads(path.read_text("utf-8")) or {}
            except (ValueError, OSError) as e:
                # A journal that cannot be read means "unknown", which must not
                # be treated as "nothing was sent". Refuse to start.
                raise ConfigError(f"journal {path} is unreadable ({e}); inspect it before restarting") from e

    def put(self, job_id: str, entry: dict) -> None:
        self.entries[job_id] = {**entry, "at": time.time()}
        self._save()

    def update(self, job_id: str, **changes) -> None:
        self.put(job_id, {**self.entries.get(job_id, {}), **changes})

    def remove(self, job_id: str) -> None:
        if self.entries.pop(job_id, None) is not None:
            self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.entries, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)


# ── One printer's worker ────────────────────────────────────────────────────


@dataclass
class PrinterConfig:
    slot: str                       # front · kitchen
    host: str = ""                  # a fixed address; with `name`, only the first place to look
    port: int = 9100
    status: str = "gs_r"            # gs_r · dle_eot · none
    connect_timeout: float = 5.0
    status_timeout: float = 3.0
    io_timeout: float = 10.0
    confirm_timeout: float = 30.0
    profile: Profile = field(default_factory=Profile)
    model: str = ""
    name: str = ""                  # the printer's own network name; found afresh, so its address may change

    @classmethod
    def from_config(cls, slot: str, cfg: dict) -> "PrinterConfig":
        name = str(cfg.get("name") or "").strip()
        if not cfg.get("host") and not name:
            raise ConfigError(f"printers.{slot} needs name (the Hostname on the printer's self-test page, e.g. "
                              "IW-J300H-41CC) or host (the printer's IP address on the café network)")
        if name and not _NAME_RE.match(name):
            raise ConfigError(f"printers.{slot}.name must be the printer's own network name exactly as its self-test "
                              f"page prints it (letters, digits and hyphens, no dots), not {name!r}")
        if cfg.get("model") and cfg["model"] not in MODELS:
            raise ConfigError(f"printers.{slot}.model must be one of {', '.join(MODELS)}")
        model = MODELS.get(cfg.get("model", ""), {})
        status = cfg.get("status", model.get("status", "gs_r"))
        if status not in ("gs_r", "dle_eot", "none"):
            raise ConfigError(f"printers.{slot}.status must be gs_r, dle_eot or none")
        pc = cls(slot=slot, host=str(cfg.get("host") or ""), port=int(cfg.get("port", model.get("port", 9100))), status=status,
                 connect_timeout=float(cfg.get("connect_timeout", 5)), status_timeout=float(cfg.get("status_timeout", 3)),
                 io_timeout=float(cfg.get("io_timeout", 10)), confirm_timeout=float(cfg.get("confirm_timeout", 30)),
                 profile=Profile.from_config(cfg), model=str(cfg.get("model", "")), name=name)
        # The till calls a claimed job "uncertain" after 90 s without an answer;
        # a confirmation that can outlast that would show uncertain for a job
        # that is about to be confirmed.
        if pc.connect_timeout + pc.io_timeout + pc.confirm_timeout > 80:
            raise ConfigError(f"printers.{slot}: connect_timeout + io_timeout + confirm_timeout must stay under 80 seconds")
        return pc


class Worker:
    """Pulls one printer's jobs and prints them one at a time."""

    HEARTBEAT_EVERY = 30.0
    NAME_RECHECK = 15.0             # how long an address the name gave is used before asking again

    def __init__(self, cfg: PrinterConfig, api, device: str, journal: Journal, *,
                 clock: Callable[[], float] = time.monotonic,
                 resolve: Callable[[str, "str | None"], "str | None"] = find_by_name,
                 book: AddressBook | None = None):
        self.cfg, self.api, self.device, self.journal, self.clock = cfg, api, device[:60], journal, clock
        self.log = logging.getLogger(f"print_bridge.{cfg.slot}")
        # Where the printer is. With a name it is asked for afresh; the address
        # in the config is then only the first place to look.
        self.resolve, self.book = resolve, book or AddressBook()
        self.address: str | None = cfg.host or None
        self.address_asked_at: float | None = None
        self.need_reconcile = True
        self.printer_down = False
        self.next_probe_at = 0.0
        self.probe_backoff = 2.0
        self.last_heartbeat_at: float | None = None
        self.last_state: PrinterState | None = None
        self.revoked = False
        # Android may freeze this process when the screen goes off or Chrome is
        # in front. A gap far longer than the wait we asked for is the evidence,
        # and it is reported rather than guessed at: see docs in README.md.
        self.suspended_for = 0.0
        self.suspended_at: float | None = None
        # The owner's setup test: asked for by the server, answered on the next heartbeat.
        self.wants_test = False
        self.pending_test: tuple[str, str | None] | None = None

    # API helpers -------------------------------------------------------------

    def _post(self, path: str, body: dict) -> tuple[int, dict]:
        status, data = self.api.post(path, body)
        if status in (401, 403):
            self.revoked = True
            self.log.error("token for the %s printer was refused (%s): %s — issue a new one in the dashboard",
                           self.cfg.slot, status, data.get("detail"))
        return status, data

    def _answer(self, job_id: str, path: str, body: dict) -> bool:
        """Deliver a result or release. True when the server has settled it (so
        the journal entry can go), False when it must be tried again later."""
        try:
            status, data = self._post(f"/print/agent/jobs/{job_id}/{path}", {"device": self.device, **body})
        except ApiUnreachable as e:
            self.log.warning("could not reach the API to %s job %s: %s (will retry)", path, job_id, e)
            return False
        if status == 200:
            return True
        if status in (404, 409):
            # The server has moved on (a person confirmed or withdrew it, or
            # this attempt is stale). Nothing more to say about this attempt.
            self.log.warning("server declined %s for job %s (%s): %s", path, job_id, status, data.get("detail"))
            return True
        return False

    def _settle(self, job_id: str) -> bool:
        entry = self.journal.entries.get(job_id)
        if entry is None:
            return True
        attempt = entry.get("attempt")
        phase = entry.get("phase")
        if phase == "claimed":
            ok = self._answer(job_id, "release", {"attempt": attempt, "error": entry.get("reason") or
                                                  "Bridge berhenti sebelum mengirim ke printer"})
        elif phase == "sending":
            ok = self._answer(job_id, "result", {"attempt": attempt, "outcome": "uncertain",
                                                 "error": "Bridge berhenti saat mengirim ke printer — slip mungkin tercetak"})
        else:
            ok = self._answer(job_id, "result", {"attempt": attempt, "outcome": entry["outcome"],
                                                 "evidence": entry.get("evidence"), "error": entry.get("error")})
        if ok:
            self.journal.remove(job_id)
        return ok

    def flush(self) -> bool:
        return all(self._settle(job_id) for job_id in list(self.journal.entries))

    def reconcile(self) -> bool:
        """Jobs the server says this device holds that the journal has never
        seen were claimed but never sent (the claim's answer was lost, or the
        bridge stopped before writing the journal): release them."""
        try:
            status, data = self._post("/print/agent/held", {"device": self.device})
        except ApiUnreachable as e:
            self.log.warning("reconcile postponed, API unreachable: %s", e)
            return False
        if status != 200:
            return False
        for held in data.get("jobs", []):
            if held["id"] in self.journal.entries:
                continue
            self.journal.put(held["id"], {"attempt": held["attempt"], "phase": "claimed",
                                          "reason": "Diambil bridge tapi belum dikirim ke printer"})
            self.log.info("releasing job %s: held by this device but never sent", held["id"])
            self._settle(held["id"])
        self.need_reconcile = False
        return True

    def heartbeat(self, state: PrinterState, force: bool = False) -> None:
        now = self.clock()
        changed = self.last_state is None or (state.state, state.detail) != (self.last_state.state, self.last_state.detail)
        self.last_state = state
        if not (force or changed or self.last_heartbeat_at is None or now - self.last_heartbeat_at >= self.HEARTBEAT_EVERY):
            return
        detail = state.detail
        frozen = self.recent_suspension()
        if frozen:
            note = f"bridge sempat berhenti {round(frozen / 60)} menit" if frozen >= 90 else f"bridge sempat berhenti {int(frozen)} detik"
            detail = f"{detail} - {note}" if detail else note
        body = {"device": self.device, "state": state.state, "detail": detail[:200], "version": VERSION}
        if self.pending_test is not None:
            body["test_result"], body["test_detail"] = self.pending_test[0], (self.pending_test[1] or "")[:300]
        try:
            status, data = self._post("/print/agent/heartbeat", body)
            self.last_heartbeat_at = now
            if status == 200:
                self.pending_test = None
                self.wants_test = self.wants_test or bool(data.get("test_print"))
        except ApiUnreachable:
            pass

    # Printer helpers ---------------------------------------------------------

    def locate(self) -> str | None:
        """The address to try now. A printer with a name is asked for by name,
        so a new address from the router after a power cut is simply found. If
        the name does not answer, its last address is still tried, as a fixed
        address would be — unless the other printer has since been found
        there, because then a slip would come out in the wrong room."""
        if not self.cfg.name:
            return self.cfg.host
        now = self.clock()
        if self.address and self.address_asked_at is not None and now - self.address_asked_at < self.NAME_RECHECK:
            return self.address
        found = self.resolve(self.cfg.name, self.address)
        if found:
            if found != self.address:
                self.log.info("%s printer %s is at %s%s", self.cfg.slot, self.cfg.name, found,
                              f" (was {self.address})" if self.address else "")
            self.address, self.address_asked_at = found, now
            self.book.note(self.cfg.slot, found)
            return found
        self.address_asked_at = None
        if self.address and self.book.holder(self.address, but=self.cfg.slot):
            self.log.warning("%s printer %s did not answer, and its last address %s now belongs to the other printer",
                             self.cfg.slot, self.cfg.name, self.address)
            return None
        return self.address

    def _lost(self) -> None:
        """A connection failed: ask for the name again before the next try."""
        self.address_asked_at = None

    def _connection(self) -> PrinterConnection:
        host = self.locate()
        if host is None:
            raise PrinterNotFound(f"Printer {self.cfg.name} tidak ditemukan di jaringan (mati, atau belum tersambung ke Wi-Fi)")
        return PrinterConnection(host, self.cfg.port, connect_timeout=self.cfg.connect_timeout,
                                 io_timeout=self.cfg.io_timeout)

    def _unreachable(self, e: OSError) -> PrinterState:
        self._lost()
        if isinstance(e, PrinterNotFound):
            return PrinterState("offline", str(e))
        where = f"{self.cfg.name} di {self.address}" if self.cfg.name else f"di {self.address}"
        return PrinterState("offline", f"Printer tidak terjangkau {where}:{self.cfg.port} ({e.__class__.__name__})")

    def probe(self) -> PrinterState:
        try:
            with self._connection() as conn:
                if self.cfg.status == "none":
                    return PrinterState("reachable", "Printer menerima koneksi; status tidak dibaca (status: none)")
                return conn.status(self.cfg.status_timeout)
        except StatusUnavailable as e:
            return PrinterState("error", f"{e} — kalau model ini tidak mendukung status, pakai \"status\": \"none\"")
        except OSError as e:
            return self._unreachable(e)

    def _down(self, state: PrinterState) -> None:
        if not self.printer_down:
            self.log.warning("%s printer unavailable: %s %s", self.cfg.slot, state.state, state.detail)
        self.printer_down = True
        self.next_probe_at = self.clock() + self.probe_backoff
        self.probe_backoff = min(self.probe_backoff * 2, 30.0)
        self.heartbeat(state)

    def _up(self, state: PrinterState) -> None:
        if self.printer_down:
            self.log.info("%s printer back: %s", self.cfg.slot, state.state)
        self.printer_down = False
        self.probe_backoff = 2.0
        self.heartbeat(state)

    # The loop ----------------------------------------------------------------

    def run_once(self) -> str:
        """One step. Returns what happened: revoked · api_down · printer_down ·
        idle · printed · uncertain · failed · released."""
        if self.revoked:
            return "revoked"
        if not self.flush():
            return "revoked" if self.revoked else "api_down"
        if self.need_reconcile and not self.reconcile():
            return "revoked" if self.revoked else "api_down"
        if self.printer_down:
            if self.clock() < self.next_probe_at:
                if self.wants_test:            # the owner is waiting: say why it cannot print
                    self.test_print()
                return "printer_down"
            state = self.probe()
            if state.blocking:
                self._down(state)
                return "printer_down"
            self._up(state)
        elif self.last_heartbeat_at is None or self.clock() - self.last_heartbeat_at >= self.HEARTBEAT_EVERY:
            state = self.probe()
            if state.blocking:
                self._down(state)
                return "printer_down"
            self._up(state)
        try:
            status, data = self._post("/print/agent/claim", {"device": self.device})
        except ApiUnreachable as e:
            # The server may have handed us a job whose answer never arrived.
            self.log.warning("claim failed, API unreachable: %s", e)
            self.need_reconcile = True
            return "api_down"
        if status != 200:
            return "revoked" if self.revoked else "api_down"
        self.wants_test = self.wants_test or bool(data.get("test_print"))
        job = data.get("job")
        if job is None:
            return self.test_print() if self.wants_test else "idle"
        outcome = self.process(job)
        if self.wants_test and not self.printer_down:
            self.test_print()
        return outcome

    def deliver(self, data: bytes, on_first_byte: Callable[[], None] | None = None) -> Delivery:
        """Put one document on one printer, and say what is known afterwards.
        This is the only place bytes reach a printer: a job and the owner's
        test ticket take exactly the same path.

        `not_sent` means no byte of it left this machine, so no paper can
        exist; `uncertain` means some may have, and nobody can say."""
        try:
            conn = self._connection().__enter__()
        except OSError as e:
            self._lost()
            return Delivery("not_sent", state=PrinterState("offline", str(e) if isinstance(e, PrinterNotFound)
                                                           else f"Printer tidak terjangkau ({e.__class__.__name__})"))
        try:
            if self.cfg.status != "none":
                try:
                    before = conn.status(self.cfg.status_timeout)
                except StatusUnavailable as e:
                    return Delivery("not_sent", state=PrinterState("error", str(e)))
                except OSError as e:
                    return Delivery("not_sent", state=PrinterState("offline", f"Koneksi printer putus ({e.__class__.__name__})"))
                if before.blocking:
                    return Delivery("not_sent", state=before)
            # From here on, paper may exist. The caller records that before any byte leaves.
            if on_first_byte is not None:
                on_first_byte()
            try:
                conn.write(data)
            except OSError as e:
                if conn.sent == 0:
                    return Delivery("not_sent", state=PrinterState("offline", f"Koneksi printer putus sebelum mengirim ({e.__class__.__name__})"))
                self._down(PrinterState("offline", "Koneksi printer putus saat mencetak"))
                return Delivery("uncertain", error=f"Koneksi printer putus setelah {conn.sent} dari {len(data)} byte — "
                                                   "slip mungkin tercetak sebagian")
            if self.cfg.status == "gs_r":
                try:
                    reply = conn.confirm_processed(self.cfg.confirm_timeout)
                except OSError as e:
                    self._down(PrinterState("offline", "Koneksi printer putus saat mencetak"))
                    return Delivery("uncertain", error=f"Koneksi printer putus sebelum printer mengonfirmasi "
                                                       f"({e.__class__.__name__}) — slip mungkin tercetak sebagian, cek kertasnya")
                if reply is None:
                    try:
                        why = conn.status(self.cfg.status_timeout)
                    except StatusUnavailable:
                        why = PrinterState("unknown", "printer tidak menjawab")
                    except OSError:
                        why = PrinterState("offline", "koneksi printer putus")
                    if why.blocking:
                        self._down(why)
                    reason = why.detail or "tidak ada jawaban"
                    return Delivery("uncertain", error=f"Printer tidak mengonfirmasi selesai dalam "
                                                       f"{int(self.cfg.confirm_timeout)} detik ({reason}) — "
                                                       "slip mungkin tercetak, cek kertasnya")
                if reply & 0x0C:
                    self._down(PrinterState("paper_out", "Kertas habis"))
                return Delivery("printed", evidence="printer_status")
            if self.cfg.status == "dle_eot":
                try:
                    after = conn.status(self.cfg.status_timeout)
                except (StatusUnavailable, OSError):
                    after = PrinterState("unknown", "status sesudah cetak tidak terbaca")
                if after.blocking or after.state == "unknown":
                    if after.blocking:
                        self._down(after)
                    return Delivery("uncertain", error=f"Sesudah dikirim, printer melapor: {after.detail} — "
                                                       "slip mungkin tercetak, cek kertasnya")
            return Delivery("printed", evidence="bytes_delivered")
        finally:
            conn.__exit__(None, None, None)

    def process(self, job: dict) -> str:
        jid, attempt = job["id"], job["attempts"]
        self.journal.put(jid, {"attempt": attempt, "phase": "claimed", "kind": job.get("kind")})
        self.log.info("printing %s %s (%s, attempt %s)", job.get("kind"), job.get("order_label"), jid, attempt)
        try:
            data = render(job["document"], self.cfg.profile)
        except Exception as e:     # nothing reached the printer: a confirmed failure
            self.log.exception("could not render job %s", jid)
            return self._finish(jid, "failed", None, f"Slip tidak bisa diubah ke perintah printer ({e.__class__.__name__})")
        done = self.deliver(data, on_first_byte=lambda: self.journal.update(jid, phase="sending"))
        if done.outcome == "not_sent":
            self.journal.update(jid, phase="claimed")
            return self._release(jid, done.state or PrinterState("offline", "belum terkirim"))
        return self._finish(jid, done.outcome, done.evidence, done.error)

    def test_print(self) -> str:
        """The owner pressed *Cetak tes* in the dashboard. It prints the same
        way a real ticket does, so what it proves is what a real ticket would
        prove — and the answer goes back on the next heartbeat."""
        self.log.info("printing a test ticket for the %s printer", self.cfg.slot)
        try:
            data = render(test_document(self.cfg.slot), self.cfg.profile)
        except Exception as e:
            self.pending_test = ("failed", f"Slip tes tidak bisa dibuat ({e.__class__.__name__})")
            self.wants_test = False
            return "failed"
        done = self.deliver(data)
        if done.outcome == "not_sent":
            state = done.state or PrinterState("offline", "belum terkirim")
            self._down(state)
            self.pending_test = ("failed", f"Tidak terkirim: {state.detail or state.state}")
        elif done.outcome == "uncertain":
            self.pending_test = ("uncertain", done.error)
        elif done.evidence == "printer_status":
            self.pending_test = ("printed", "Printer mengonfirmasi selesai mencetak")
        else:
            self.pending_test = ("delivered", "Printer menerima datanya; tidak bisa memastikan kertas keluar")
        result = self.pending_test[0]
        self.wants_test = False
        # Reported at once: the owner is standing at the printer waiting for it.
        self.heartbeat(self.last_state or PrinterState("unknown", ""), force=True)
        return result

    def _release(self, jid: str, state: PrinterState) -> str:
        self.journal.update(jid, phase="claimed", reason=f"Belum terkirim: {state.detail or state.state}")
        self._down(state)
        self._settle(jid)
        return "released"

    def _finish(self, jid: str, outcome: str, evidence: str | None, error: str | None) -> str:
        self.journal.update(jid, phase="outcome", outcome=outcome, evidence=evidence, error=error)
        if outcome != "printed":
            self.log.warning("job %s: %s — %s", jid, outcome, error)
        self._settle(jid)
        return outcome

    def note_gap(self, expected: float, actual: float) -> None:
        """A wait that took much longer than asked for means this process was
        frozen (Android doze, the tablet asleep, the host suspended). Jobs it
        was holding may have been handed out by nobody else, but its own view
        is stale, so it reconciles before claiming anything new."""
        overshoot = actual - expected
        if overshoot < 30.0:
            return
        self.suspended_for, self.suspended_at = overshoot, self.clock()
        self.need_reconcile = True
        self._lost()                    # the printers may have been restarted meanwhile
        self.log.warning("this worker was frozen for %.0f s (asked to wait %.0f s) — Android battery management, "
                         "sleep, or the host suspending. Reconciling.", overshoot, expected)

    def recent_suspension(self) -> float:
        """Seconds this worker was frozen for, if it happened in the last 10 minutes."""
        if self.suspended_at is None or self.clock() - self.suspended_at > 600:
            return 0.0
        return self.suspended_for

    def run_forever(self, stop: threading.Event, poll_seconds: float = 2.0) -> None:
        backoff = poll_seconds
        while not stop.is_set():
            try:
                result = self.run_once()
            except Exception:           # a bug must not kill the printer's thread
                self.log.exception("unexpected error; continuing")
                result = "api_down"
            if result in ("printed", "uncertain", "failed"):
                backoff = poll_seconds
                continue
            if result == "idle" or result == "released":
                wait = poll_seconds
                backoff = poll_seconds
            elif result == "printer_down":
                wait = max(0.5, self.next_probe_at - self.clock())
            elif result == "revoked":
                wait = 300.0
                self.revoked = False    # try again later: the owner may have re-issued the token and edited config
            else:
                backoff = min(backoff * 2, 60.0)
                wait = backoff
            before = self.clock()
            stop.wait(wait)
            self.note_gap(wait, self.clock() - before)


# ── Configuration and the program ───────────────────────────────────────────


def _token_claims(token: str) -> dict:
    try:
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        raise ConfigError("the printer token is not a valid token (copy it again from Pengaturan → Printer)")


def load_token(slot: str, cfg: dict, base_dir: Path) -> str:
    if cfg.get("token"):
        raise ConfigError(f"printers.{slot}: do not put the token in the config file; use token_env or token_file")
    token = None
    if cfg.get("token_env"):
        token = os.environ.get(cfg["token_env"])
        if not token:
            raise ConfigError(f"environment variable {cfg['token_env']} (printers.{slot}.token_env) is not set")
    elif cfg.get("token_file"):
        path = (base_dir / cfg["token_file"]).expanduser()
        try:
            token = path.read_text("utf-8").strip()
        except OSError as e:
            raise ConfigError(f"cannot read printers.{slot}.token_file {path}: {e}") from e
    else:
        raise ConfigError(f"printers.{slot} needs token_env or token_file")
    claims = _token_claims(token)
    if claims.get("scope") != "printer":
        raise ConfigError(f"printers.{slot}: this is not a printer device token")
    if claims.get("printer") != slot:
        raise ConfigError(f"printers.{slot}: this token is for the {claims.get('printer')!r} printer, not {slot!r}")
    if claims.get("exp") and claims["exp"] < time.time():
        raise ConfigError(f"printers.{slot}: the token expired; issue a new one in Pengaturan → Printer")
    return token


def check_api_base(url: str, allow_http: bool) -> str:
    parts = urlsplit(url)
    if parts.scheme == "https":
        return url
    if parts.scheme == "http" and (allow_http or parts.hostname in ("localhost", "127.0.0.1", "::1")):
        return url
    raise ConfigError("api_base must be https:// (tokens must not travel in clear text)")


@dataclass
class BridgeConfig:
    api_base: str
    device_name: str
    state_dir: Path
    poll_seconds: float
    printers: dict[str, tuple[PrinterConfig, str]]

    @classmethod
    def load(cls, path: Path) -> "BridgeConfig":
        try:
            raw = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError) as e:
            raise ConfigError(f"cannot read config {path}: {e}") from e
        base_dir = path.parent
        api = check_api_base(str(raw.get("api_base", "")), bool(raw.get("allow_insecure_http")))
        name = str(raw.get("device_name") or "").strip()
        if not name or len(name) > 40:
            raise ConfigError("device_name is required (1–40 characters, unique to this bridge installation)")
        printers = {}
        for slot in ("front", "kitchen"):
            cfg = (raw.get("printers") or {}).get(slot)
            if cfg is None or cfg.get("enabled") is False:
                continue
            printers[slot] = (PrinterConfig.from_config(slot, cfg), load_token(slot, cfg, base_dir))
        if not printers:
            raise ConfigError("configure at least one printer under printers.front or printers.kitchen")
        state = Path(raw.get("state_dir") or "state")
        return cls(api_base=api, device_name=name, state_dir=state if state.is_absolute() else base_dir / state,
                   poll_seconds=max(1.0, float(raw.get("poll_seconds", 2))), printers=printers)


def make_worker(config: BridgeConfig, slot: str, api=None, book: AddressBook | None = None) -> Worker:
    pcfg, token = config.printers[slot]
    return Worker(pcfg, api or Api(config.api_base, token), f"{config.device_name}-{slot}",
                  Journal(config.state_dir / f"journal-{slot}.json"), book=book)


def run(config: BridgeConfig) -> int:
    stop = threading.Event()
    book = AddressBook()
    workers = [make_worker(config, slot, book=book) for slot in config.printers]
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, lambda *_: stop.set())
        except (ValueError, OSError):
            pass
    threads = [threading.Thread(target=w.run_forever, args=(stop, config.poll_seconds), name=f"printer-{w.cfg.slot}", daemon=True)
               for w in workers]
    for t in threads:
        t.start()
    log.info("print bridge %s running for %s (device %s)", VERSION, ", ".join(config.printers), config.device_name)
    try:
        while not stop.is_set():
            stop.wait(1.0)
    except KeyboardInterrupt:
        stop.set()
    for t in threads:
        t.join(timeout=config.printers[t.name.split("-", 1)[1]][0].confirm_timeout + 20)
    log.info("print bridge stopped")
    return 0


def test_document(slot: str) -> dict:
    """Two different test tickets, because the two printers do different jobs:
    the front one prints receipts and Bar slips, the kitchen one prints food
    tickets in large type. Whoever is holding the paper can tell at a glance
    which printer they are standing next to."""
    if slot == "kitchen":
        return {"v": 1, "kind": "test", "blocks": [
            {"t": "title", "text": "DAPUR"},
            {"t": "label", "text": "TES PRINTER"},
            {"t": "banner", "text": "MEJA 12"},
            {"t": "line", "text": "Pesanan 042", "style": "bold", "size": "large"},
            {"t": "kv", "left": "Makan di sini", "right": "12.34"},
            {"t": "rule"},
            {"t": "item", "qty": "2", "name": "Nasi Goreng Kampung Spesial Pedas", "size": "Besar",
             "modifiers": ["Tanpa bawang", "Telur ceplok"], "notes": "alergi udang", "flag": None},
            {"t": "rule"},
            {"t": "text", "text": "Kalau slip ini terbaca dan kertas terpotong, printer dapur siap dipakai.",
             "align": "center"},
        ]}
    return {"v": 1, "kind": "test", "blocks": [
        {"t": "title", "text": "TES PRINTER DEPAN"},
        {"t": "label", "text": "TES PRINTER"},
        {"t": "banner", "text": "PESANAN 042"},
        {"t": "kv", "left": "Bawa pulang", "right": "12.34"},
        {"t": "rule"},
        {"t": "item_priced", "qty": "1", "name": "Es Kopi Susu", "size": "Large",
         "modifiers": ["Extra shot +Rp 5.000"], "notes": None, "amount": "Rp 28.000"},
        {"t": "total", "left": "TOTAL", "right": "Rp 28.000"},
        {"t": "rule"},
        {"t": "text", "text": "Printer depan mencetak struk pelanggan, nota meja dan slip bar.", "align": "center"},
    ]}


def protocol_warning(pcfg: "PrinterConfig", base_dir: Path) -> str | None:
    """A model's defaults come from its seller's specification until someone
    has asked the printer itself. Say so, once, where it will be read."""
    model = MODELS.get(pcfg.model, {})
    if model.get("confidence") != "advertised":
        return None
    report = base_dir / "probe-report.json"
    if report.exists():
        return (f"{pcfg.model}: settings came from the specification; probe-report.json exists — "
                "check that its port, status and cut match this config.")
    return (f"{pcfg.model}: port {pcfg.port}, status mode {pcfg.status} and the cut command are from the "
            "seller's specification and have NOT been verified on the printer. Run:  "
            f"python probe_printer.py {pcfg.host or '<the address --check found>'} --label {pcfg.slot}")


def check(config: BridgeConfig, print_test: bool) -> int:
    """Setup check: every configured printer and the API, without taking a job."""
    ok = True
    book = AddressBook()
    for slot, (pcfg, token) in config.printers.items():
        warning = protocol_warning(pcfg, config.state_dir.parent)
        if warning:
            print(f"[{slot}] UNVERIFIED  {warning}")
        worker = make_worker(config, slot, book=book)
        if pcfg.name:
            found = worker.locate()
            if worker.address_asked_at is not None:
                print(f"[{slot}] name {pcfg.name}: found at {found}")
            elif found:
                print(f"[{slot}] name {pcfg.name}: DID NOT ANSWER — trying the address from the config, {found}. "
                      "If this stays, the printer is off, on another Wi-Fi, or the name is not the one on its self-test page.")
            else:
                print(f"[{slot}] name {pcfg.name}: DID NOT ANSWER and the config has no host to fall back on. Is the "
                      "printer on, on this Wi-Fi, and is the name the Hostname on its self-test page?")
        state = worker.probe()
        print(f"[{slot}] printer {worker.address or pcfg.name}:{pcfg.port} status-mode={pcfg.status}: {state.state} {state.detail}")
        ok &= not state.blocking
        try:
            status, data = worker.api.post("/print/agent/heartbeat", {"device": worker.device, "state": state.state,
                                                                       "detail": state.detail[:200], "version": VERSION})
            print(f"[{slot}] API {config.api_base}: HTTP {status} {data.get('detail', '') if status != 200 else 'ok'}")
            ok &= status == 200
        except ApiUnreachable as e:
            print(f"[{slot}] API {config.api_base}: unreachable ({e})")
            ok = False
        if print_test and not state.blocking:
            data = render(test_document(slot), pcfg.profile)
            with worker._connection() as conn:
                conn.write(data)
                if pcfg.status == "gs_r":
                    reply = conn.confirm_processed(pcfg.confirm_timeout)
                    print(f"[{slot}] test page sent; printer {'confirmed processing' if reply is not None else 'did NOT confirm'}")
                else:
                    print(f"[{slot}] test page sent (no confirmation in status mode {pcfg.status}) — look at the paper")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:                  # a console or redirected log on Windows may use a legacy code page
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description="Poernama print bridge: API print jobs to network ESC/POS printers")
    parser.add_argument("--config", default="bridge-config.json", type=Path)
    parser.add_argument("--probe", metavar="IP", help="ask a printer what it speaks (see probe_printer.py) and exit")
    parser.add_argument("--find", metavar="NAME", help="ask the network which address a printer's name has, and exit")
    parser.add_argument("--check", action="store_true", help="check printers and API, take no jobs")
    parser.add_argument("--test-print", action="store_true", help="with --check: print a local test page on each printer")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.probe:
        import probe_printer

        return probe_printer.main([args.probe])
    if args.find:
        found = find_by_name(args.find)
        print(f"{args.find}: {found}" if found else f"{args.find}: no answer (printer off, on another Wi-Fi, or a different name)")
        return 0 if found else 1
    try:
        config = BridgeConfig.load(args.config)
        if args.check or args.test_print:
            return check(config, args.test_print)
        return run(config)
    except ConfigError as e:
        log.error("configuration: %s", e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
