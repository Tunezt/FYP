#!/usr/bin/env python3
"""A simulated network ESC/POS printer, for testing the print bridge without
hardware. It is **not** evidence that any real printer behaves the same way.

It listens on TCP like a receipt printer's raw port, understands the subset of
ESC/POS the bridge sends, answers real-time status requests (DLE EOT 1/2/4)
and the in-order status request (GS r 1), and splits what it receives into
"paper" at each cut. Faults can be switched on to rehearse what the bridge
must survive: refused connections, paper out, cover open, a connection that
drops part-way through a job, a printer that never confirms, one that ignores
status requests entirely.

Run it by hand to watch slips arrive:

    python sim_printer.py --port 9100 --name dapur
"""
from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
from dataclasses import dataclass, field

ESC, GS, DLE = 0x1B, 0x1D, 0x10


@dataclass
class Line:
    text: str
    bold: bool = False
    inverse: bool = False
    width: int = 1
    height: int = 1
    align: int = 0
    font: int = 0                 # 0 = Font A (12 dots wide), 1 = Font B (9 dots wide)

    @property
    def dots(self) -> int:
        """How far across the paper this row reaches, in dots."""
        return len(self.text) * self.width * (9 if self.font else 12)


@dataclass
class Paper:
    """One piece of paper: everything between two cuts."""
    raw: bytes
    lines: list[Line]
    cut: str                      # partial · full · none (feed only) · torn (connection dropped, no cut)
    beeped: bool = False

    @property
    def text(self) -> list[str]:
        return [l.text for l in self.lines]


def _command_length(buf: bytes, i: int) -> int | None:
    """Bytes in the command starting at buf[i], or None if incomplete."""
    first = buf[i]
    if first not in (ESC, GS, DLE):
        return 1
    if i + 1 >= len(buf):
        return None
    op = buf[i + 1]
    if first == ESC:
        n = {ord("@"): 2, ord("B"): 4}.get(op, 3)
    elif first == GS:
        if op == ord("V"):
            if i + 2 >= len(buf):
                return None
            n = 4 if buf[i + 2] in (65, 66) else 3
        elif op == ord("v"):
            # GS v 0 m xL xH yL yH d…: a raster image (the receipt logo, till-12).
            # Its bytes are pixels, never commands: one piece, however long.
            if i + 8 > len(buf):
                return None
            n = 8 + (buf[i + 4] + 256 * buf[i + 5]) * (buf[i + 6] + 256 * buf[i + 7])
        elif op == ord("("):
            # GS ( k pL pH …: a 2D-code function (the WhatsApp QR, till-7).
            if i + 5 > len(buf):
                return None
            n = 5 + buf[i + 3] + 256 * buf[i + 4]
        else:
            n = 3
    else:
        n = 3
    return n if i + n <= len(buf) else None


def decode(data: bytes) -> list[Paper]:
    """ESC/POS bytes → pieces of paper with styled lines. Status requests are
    not paper and are skipped."""
    papers: list[Paper] = []
    lines: list[Line] = []
    style = dict(bold=False, inverse=False, width=1, height=1, align=0, font=0)
    text = bytearray()
    start = 0
    beeped = False
    i = 0

    def flush_line():
        lines.append(Line(text.decode("ascii", "replace"), **style))
        text.clear()

    while i < len(data):
        n = _command_length(data, i)
        if n is None:
            break
        cmd = data[i:i + n]
        if n == 1:
            if cmd == b"\n":
                flush_line()
            else:
                text.extend(cmd)
        elif cmd[0] == ESC:
            op = cmd[1]
            if op == ord("E"):
                style["bold"] = bool(cmd[2])
            elif op == ord("M"):
                style["font"] = cmd[2] & 1
            elif op == ord("a"):
                style["align"] = cmd[2]
            elif op == ord("B"):
                beeped = True
            elif op == ord("d") and text:
                flush_line()
            elif op == ord("@") and lines:
                # A new document started without a cut before it: the previous
                # one is still on the roll, uncut.
                papers.append(Paper(bytes(data[start:i]), lines, "none", beeped))
                lines, beeped, start = [], False, i
        elif cmd[0] == GS:
            op = cmd[1]
            if op == ord("!"):
                style["width"], style["height"] = (cmd[2] >> 4) + 1, (cmd[2] & 0x0F) + 1
            elif op == ord("B"):
                style["inverse"] = bool(cmd[2])
            elif op == ord("v"):
                if text:
                    flush_line()
                width, height = (cmd[4] + 256 * cmd[5]) * 8, cmd[6] + 256 * cmd[7]
                lines.append(Line(f"[logo {width}x{height} dots]", align=style["align"]))
            elif op == ord("(") and len(cmd) > 7 and cmd[6] == 0x50:
                qr_data = cmd[8:].decode("ascii", "replace")
                lines.append(Line(f"[QR {qr_data}]", align=style["align"]))
            elif op == ord("V"):
                if text:
                    flush_line()
                m = cmd[2]
                papers.append(Paper(bytes(data[start:i + n]), lines, "full" if m in (0, 48, 65) else "partial", beeped))
                lines, beeped, start = [], False, i + n
        i += n
    if text:
        flush_line()
    if lines:
        # Ends with a feed and no cut (cut: none), or stopped part-way (torn).
        papers.append(Paper(bytes(data[start:]), lines, "none" if data[-3:-1] == b"\x1bd" else "torn", beeped))
    return papers


@dataclass
class SimState:
    paper: str = "ok"                 # ok · near_end · out
    cover_open: bool = False
    offline: bool = False
    status_supported: bool = True     # answers DLE EOT
    confirm_supported: bool = True    # answers GS r 1
    stall_confirm: bool = False       # takes GS r but never answers (printing stopped)
    drop_after: int | None = None     # close the connection after this many job bytes (one time)
    confirm_delay: float = 0.0


class SimPrinter:
    def __init__(self, host: str = "127.0.0.1", port: int = 0, name: str = "sim", echo: bool = False):
        self.host, self.name, self.echo = host, name, echo
        self.state = SimState()
        self.received = bytearray()        # every job byte ever received, status requests excluded
        self.connections = 0
        self.status_requests = 0
        self._lock = threading.Lock()
        self._sock: socket.socket | None = None
        self._stop = threading.Event()
        self._requested_port = port
        self.port = port

    # lifecycle -------------------------------------------------------------

    def start(self) -> "SimPrinter":
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((self.host, self._requested_port or self.port))
        s.listen(5)
        s.settimeout(0.2)
        self._sock, self.port = s, s.getsockname()[1]
        self._stop.clear()
        threading.Thread(target=self._accept, name=f"sim-{self.name}", daemon=True).start()
        return self

    def stop(self) -> None:
        """Stop listening: connections are refused, like a printer switched off."""
        self._stop.set()
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        time.sleep(0.25)

    def restart(self) -> None:
        self._requested_port = self.port
        self.start()

    @property
    def papers(self) -> list[Paper]:
        with self._lock:
            return decode(bytes(self.received))

    # serving ---------------------------------------------------------------

    def _accept(self) -> None:
        sock = self._sock
        while not self._stop.is_set():
            try:
                conn, _ = sock.accept()
            except (socket.timeout, OSError):
                continue
            with self._lock:
                self.connections += 1
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _status_byte(self, n: int) -> int:
        st = self.state
        if n == 1:
            return 0x12 | (0x08 if (st.offline or st.cover_open or st.paper == "out") else 0)
        if n == 2:
            return 0x12 | (0x04 if st.cover_open else 0) | (0x20 if st.paper == "out" else 0)
        if n == 4:
            return 0x12 | {"ok": 0, "near_end": 0x0C, "out": 0x6C}[st.paper]
        return 0x12

    def _serve(self, conn: socket.socket) -> None:
        buf = bytearray()
        job_bytes = 0
        conn.settimeout(0.2)
        try:
            while not self._stop.is_set():
                try:
                    chunk = conn.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    return
                buf += chunk
                i = 0
                while i < len(buf):
                    n = _command_length(buf, i)
                    if n is None:
                        break
                    cmd = bytes(buf[i:i + n])
                    i += n
                    if cmd[:2] == b"\x10\x04":
                        with self._lock:
                            self.status_requests += 1
                        if self.state.status_supported:
                            conn.sendall(bytes([self._status_byte(cmd[2])]))
                        continue
                    if cmd[:2] == b"\x1dr":
                        if self.state.confirm_supported and not self.state.stall_confirm and self.state.paper != "out" \
                                and not self.state.cover_open:
                            time.sleep(self.state.confirm_delay)
                            conn.sendall(bytes([0x0C if self.state.paper == "out" else (0x03 if self.state.paper == "near_end" else 0)]))
                        continue
                    drop = self.state.drop_after
                    if drop is not None and job_bytes + len(cmd) > drop:
                        keep = max(0, drop - job_bytes)
                        with self._lock:
                            self.received += cmd[:keep]
                        self.state.drop_after = None
                        conn.shutdown(socket.SHUT_RDWR)
                        return
                    job_bytes += len(cmd)
                    with self._lock:
                        self.received += cmd
                    if self.echo and cmd[:2] == b"\x1dV":
                        self._print_last()
                del buf[:i]
        except OSError:
            return
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _print_last(self) -> None:
        papers = self.papers
        if not papers:
            return
        p = papers[-1]
        width = max((l.dots // 12 for l in p.lines), default=32)
        print(f"\n--- {self.name}: kertas #{len(papers)} ({p.cut} cut{', beep' if p.beeped else ''}) " + "-" * 10)
        for l in p.lines:
            mark = ("[inv]" if l.inverse else "") + ("[b]" if l.bold else "") + (f"[{l.width}x{l.height}]" if (l.width, l.height) != (1, 1) else "") + ("[fontB]" if l.font else "")
            text = l.text.center(width // l.width) if l.align == 1 else l.text
            print(f"| {text}   {mark}")
        print("-" * 40, flush=True)


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:                       # a Windows console may use a legacy code page
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description="Simulated network ESC/POS printer (for testing only)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9100)
    parser.add_argument("--name", default="sim")
    parser.add_argument("--paper", choices=("ok", "near_end", "out"), default="ok")
    parser.add_argument("--no-status", action="store_true", help="ignore DLE EOT and GS r (a printer without status)")
    args = parser.parse_args()
    sim = SimPrinter(args.host, args.port, args.name, echo=True)
    sim.state.paper = args.paper
    sim.state.status_supported = sim.state.confirm_supported = not args.no_status
    sim.start()
    print(f"simulated printer {args.name!r} on {args.host}:{sim.port} — Ctrl+C to stop")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        sim.stop()


if __name__ == "__main__":
    main()
