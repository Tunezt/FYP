#!/usr/bin/env python3
"""Find out what a network receipt printer actually speaks, before any code
assumes it.

The Iware IW-J300H is advertised as 80 mm, auto-cutter, ESC/POS, Wi-Fi/LAN and
an alarm buzzer. Advertised is not verified: the port it listens on, whether it
answers ESC/POS status requests over the network, which cut command its cutter
takes, and which command sounds its buzzer are all firmware details that no
marketplace listing states. This script asks the printer itself.

Nothing here is destructive. By default it only opens TCP connections and sends
status *queries*, which print nothing. Paper comes out only with --paper.

    python probe_printer.py 192.168.1.50                  ports + status only
    python probe_printer.py 192.168.1.50 --paper          + labelled test slips
    python probe_printer.py 192.168.1.50 --paper --buzzer + buzzer attempts

It writes probe-report.json next to itself. Send that file back, plus what you
saw and heard, and the bridge is configured from facts rather than guesses.

Standard library only: it runs in Termux on the cashier tablet, which is the
one machine guaranteed to be on the same Wi-Fi as the printers.
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Ports worth asking about. 9100 is the usual raw ESC/POS port ("JetDirect"),
# but Chinese printer modules also ship on 6001, 4000 and 9101/9102, and some
# expose an LPR (515), Telnet (23) or web configuration port instead.
CANDIDATE_PORTS = [9100, 9101, 9102, 6001, 4000, 8080, 80, 515, 23, 631, 8000, 9600]

DLE_EOT = {1: b"\x10\x04\x01", 2: b"\x10\x04\x02", 3: b"\x10\x04\x03", 4: b"\x10\x04\x04"}
GS_R = {1: b"\x1dr\x01", 2: b"\x1dr\x02"}
INIT = b"\x1b@"

# Cut commands to try, each preceded by a line naming it, so the paper says
# which one worked. Function B (GS V 66 n) feeds to the cutter first, which is
# why the bridge prefers it; some firmwares only implement function A.
CUTS = [
    ("gs_v_66", b"\x1dV\x42\x03", "GS V 66 3  (partial cut, function B)"),
    ("gs_v_65", b"\x1dV\x41\x03", "GS V 65 3  (full cut, function B)"),
    ("gs_v_1", b"\x1dV\x01", "GS V 1     (partial cut, function A)"),
    ("gs_v_0", b"\x1dV\x00", "GS V 0     (full cut, function A)"),
    ("esc_i", b"\x1bi", "ESC i      (partial cut, older)"),
    ("esc_m", b"\x1bm", "ESC m      (full cut, older)"),
]

# Buzzer/alarm candidates. The IW-J300H is sold as a kitchen printer with an
# alarm, but the command is not documented publicly and differs per board.
BUZZERS = [
    ("esc_b", b"\x1bB\x02\x03", "ESC B 2 3   (common on Chinese boards)"),
    ("esc_paren_a", b"\x1b(A\x04\x00\x30\x01\x02\x02", "ESC ( A     (Epson-style buzzer)"),
    ("bel", b"\x07", "BEL         (0x07)"),
    ("esc_c_bel", b"\x1b\x42\x03\x02", "ESC B 3 2   (swapped arguments)"),
    # The cash-drawer kick also drives the buzzer on some kitchen printers.
    ("drawer_kick", b"\x1bp\x00\x19\xfa", "ESC p 0     (drawer kick, sometimes wired to the beeper)"),
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def scan(host: str, ports: list[int], timeout: float) -> dict[int, bool]:
    open_ports = {}
    for port in ports:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                open_ports[port] = True
                print(f"  port {port:<5} OPEN")
        except OSError:
            open_ports[port] = False
    return open_ports


def ask(sock: socket.socket, request: bytes, timeout: float) -> int | None:
    sock.sendall(request)
    sock.settimeout(timeout)
    try:
        data = sock.recv(1)
    except socket.timeout:
        return None
    return data[0] if data else None


def escpos_status(host: str, port: int, timeout: float) -> dict:
    """Does it answer the two status requests the bridge relies on?"""
    out: dict = {"dle_eot": {}, "gs_r": {}}
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            for n, req in DLE_EOT.items():
                b = ask(s, req, timeout)
                out["dle_eot"][n] = None if b is None else f"{b:#04x}"
            # A real-time reply has bits 1 and 4 set, bits 0 and 7 clear.
            answered = [v for v in out["dle_eot"].values() if v is not None]
            out["dle_eot_answers"] = bool(answered)
            out["dle_eot_looks_valid"] = bool(answered) and all(
                (int(v, 16) & 0x93) == 0x12 for v in answered
            )
            for n, req in GS_R.items():
                b = ask(s, req, timeout)
                out["gs_r"][n] = None if b is None else f"{b:#04x}"
            out["gs_r_answers"] = any(v is not None for v in out["gs_r"].values())
    except OSError as e:
        out["error"] = f"{e.__class__.__name__}: {e}"
    return out


def http_probe(host: str, port: int, timeout: float) -> dict:
    """A web page on the printer usually means a configuration UI — and would
    be where any vendor cloud/polling mode is switched on, if one exists."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(f"GET / HTTP/1.0\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode())
            chunks = []
            while len(b"".join(chunks)) < 4096:
                try:
                    chunk = s.recv(1024)
                except socket.timeout:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
        body = b"".join(chunks).decode("latin-1", "replace")
        head, _, rest = body.partition("\r\n\r\n")
        title = ""
        low = rest.lower()
        if "<title>" in low:
            title = rest[low.index("<title>") + 7: low.index("</title>")] if "</title>" in low else ""
        return {"status_line": head.splitlines()[0] if head else "", "headers": head[:400], "title": title.strip()[:120],
                "looks_like_web_ui": bool(head)}
    except OSError as e:
        return {"error": f"{e.__class__.__name__}: {e}"}


def line(text: str) -> bytes:
    return text.encode("ascii", "replace") + b"\n"


def paper_tests(host: str, port: int, timeout: float, buzzer: bool) -> dict:
    """Print one labelled slip per cut command, and optionally per buzzer
    command, so the person at the printer can say which ones worked."""
    tried = {"cuts": [], "buzzers": []}
    for key, command, label in CUTS:
        payload = INIT + line("=== TES POTONG ===") + line(label) + line(f"kode: {key}") + line("") + line("")
        try:
            with socket.create_connection((host, port), timeout=timeout) as s:
                s.settimeout(timeout)
                s.sendall(payload + command)
                time.sleep(2.0)
            tried["cuts"].append({"key": key, "command": label, "sent": True})
            print(f"  sent cut test: {label}")
        except OSError as e:
            tried["cuts"].append({"key": key, "command": label, "sent": False, "error": str(e)})
        time.sleep(1.0)
    if buzzer:
        for key, command, label in BUZZERS:
            payload = INIT + line("=== TES BUNYI ===") + line(label) + line(f"kode: {key}") + line("")
            try:
                with socket.create_connection((host, port), timeout=timeout) as s:
                    s.settimeout(timeout)
                    s.sendall(payload + command + b"\n\n\n" + CUTS[0][1])
                    time.sleep(2.5)
                tried["buzzers"].append({"key": key, "command": label, "sent": True})
                print(f"  sent buzzer test: {label}  <- listen now")
            except OSError as e:
                tried["buzzers"].append({"key": key, "command": label, "sent": False, "error": str(e)})
            time.sleep(2.0)
    return tried


def probe(host: str, *, ports: list[int], timeout: float, paper: bool, buzzer: bool) -> dict:
    report: dict = {"host": host, "at": now(), "probe_version": 1, "ports": {}, "escpos": {}, "http": {}}
    print(f"\nProbing {host} …\n\nOpen ports:")
    open_ports = scan(host, ports, timeout)
    report["ports"] = {str(p): ("open" if ok else "closed") for p, ok in open_ports.items()}
    listening = [p for p, ok in open_ports.items() if ok]
    if not listening:
        print("  none. The printer is off, on another network, or its IP is different.")
        return report
    print("\nESC/POS status support (nothing prints):")
    for port in listening:
        if port in (80, 8080, 8000, 631):
            continue
        result = escpos_status(host, port, timeout)
        report["escpos"][str(port)] = result
        answers = "answers DLE EOT" if result.get("dle_eot_looks_valid") else (
            "replies, but not a valid status byte" if result.get("dle_eot_answers") else "silent to DLE EOT")
        gsr = "answers GS r" if result.get("gs_r_answers") else "silent to GS r"
        print(f"  port {port:<5} {answers}; {gsr}")
    for port in (80, 8080, 8000, 631):
        if open_ports.get(port):
            report["http"][str(port)] = http_probe(host, port, timeout)
            print(f"  port {port:<5} web page: {report['http'][str(port)].get('title') or 'no title'}")
    if paper:
        raw = next((p for p in listening if p not in (80, 8080, 8000, 631, 23)), None)
        if raw is None:
            print("\nNo raw port to print to; skipping paper tests.")
        else:
            print(f"\nPaper tests on port {raw} — watch the printer:")
            report["paper_tests"] = {"port": raw, **paper_tests(host, raw, timeout, buzzer)}
    return report


def recommend(report: dict) -> list[str]:
    notes = []
    raw_ports = [int(p) for p, r in report.get("escpos", {}).items() if r.get("dle_eot_answers") or r.get("gs_r_answers")]
    open_raw = [int(p) for p, state in report.get("ports", {}).items() if state == "open" and int(p) not in (80, 8080, 8000, 631, 23)]
    if raw_ports:
        best = raw_ports[0]
        valid = report["escpos"][str(best)].get("dle_eot_looks_valid")
        gsr = report["escpos"][str(best)].get("gs_r_answers")
        notes.append(f'Use "port": {best}.')
        if valid and gsr:
            notes.append('Use "status": "gs_r" — the printer confirms it processed a job, so the till can honestly say "Tercetak".')
        elif valid:
            notes.append('Use "status": "dle_eot" — paper/cover problems are detectable, but finishing a job is not confirmed.')
        else:
            notes.append('Use "status": "none" — jobs will show as "Terkirim ke printer", never "Tercetak".')
    elif open_raw:
        notes.append(f'Use "port": {open_raw[0]} with "status": "none": it accepts connections but answers no status request.')
    else:
        notes.append("No raw printing port answered. Check the printer's network mode (station vs access point) and its IP.")
    if report.get("http"):
        notes.append("There is a web configuration page on the printer: open it in the tablet's browser and look for "
                     "any cloud/server-push setting, the Wi-Fi mode, and a fixed-IP setting.")
    if report.get("paper_tests"):
        notes.append("Say which cut test actually cut the paper, and which buzzer test made a sound. "
                     "Those two answers set \"cut\" and \"beep\" in the bridge config.")
    return notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Find out what a network receipt printer speaks (non-destructive by default)")
    parser.add_argument("host", help="the printer's IP address on the restaurant Wi-Fi, e.g. 192.168.1.50")
    parser.add_argument("--ports", help="comma-separated ports to try instead of the default list")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--paper", action="store_true", help="print one labelled slip per cut command")
    parser.add_argument("--buzzer", action="store_true", help="with --paper: also try each buzzer command")
    parser.add_argument("--out", type=Path, default=Path(__file__).with_name("probe-report.json"))
    parser.add_argument("--label", default="", help="which printer this is, e.g. front or kitchen")
    args = parser.parse_args(argv)
    ports = [int(p) for p in args.ports.split(",")] if args.ports else CANDIDATE_PORTS
    report = probe(args.host, ports=ports, timeout=args.timeout, paper=args.paper, buzzer=args.buzzer)
    report["label"] = args.label
    report["recommendations"] = recommend(report)
    print("\nWhat this means:")
    for note in report["recommendations"]:
        print(f"  - {note}")
    out = args.out
    existing = []
    if out.exists():
        try:
            existing = json.loads(out.read_text("utf-8"))
            existing = existing if isinstance(existing, list) else [existing]
        except ValueError:
            existing = []
    existing.append(report)
    out.write_text(json.dumps(existing, indent=2), "utf-8")
    print(f"\nWritten to {out} — send that file back.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
