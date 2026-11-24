"""Screenshot the till (kasir), the kitchen display and the guest QR menu of a seeded demo café.

    python scripts/showcase_pos_shots.py OUT_DIR [--api ...] [--web ...] [--phone 628115819400] [--width 1024]

Pairs a tablet the way Settings -> Layar kasir does, logs a cashier in by PIN through the
till's own API, and takes one PNG per screen. It opens Sari's shift (Rp 200.000) first, so
run it against a throwaway local database, never the live one.
"""
import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright


def call(api, path, body=None, token=None, method=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(api + path, data=json.dumps(body).encode() if body is not None else None, headers=headers,
                                 method=method or ("POST" if body is not None else "GET"))
    return json.load(urllib.request.urlopen(req))


def who_in(page, name: str, pin: str) -> None:
    """The door: tap the cashier, then the PIN on the pad."""
    page.get_by_text(name, exact=True).first.click()
    page.wait_for_timeout(500)
    for digit in pin:
        page.get_by_role("button", name=digit, exact=True).first.click()
        page.wait_for_timeout(150)
    page.wait_for_timeout(1500)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--api", default="http://localhost:8123")
    ap.add_argument("--web", default="http://localhost:3123")
    ap.add_argument("--phone", default="628115819400")
    ap.add_argument("--width", type=int, default=1024)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    owner = call(args.api, "/auth/login-pin", {"phone": args.phone, "pin": "1234"})["token"]
    pairing = call(args.api, "/auth/pos-pairing", {}, owner)
    staff = call(args.api, f"/pos/business/{pairing['pairing_token']}")["staff"]
    sari = next(s for s in staff if s["name"] == "Sari")
    login = call(args.api, "/pos/login", {"pairing_token": pairing["pairing_token"], "staff_id": sari["id"], "pin": "2345"})   # for the shift
    menu = call(args.api, "/auth/menu-link", {}, owner)
    problems = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": args.width, "height": 768})
        page = ctx.new_page()
        page.on("console", lambda m: problems.append(f"console: {m.text[:140]}") if m.type == "error" else None)
        page.on("response", lambda r: problems.append(f"HTTP {r.status} {r.url[:100]}") if r.status >= 400 else None)

        def shot(name, full=False):
            try:
                page.wait_for_function("document.querySelectorAll('.skeleton').length === 0", timeout=10000)
            except Exception:
                problems.append(f"{name}: still loading")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1200)
            page.screenshot(path=str(out / f"pos-{args.width}-{name}.png"), full_page=full)
            print("shot", name)

        page.goto(args.web + pairing["pos_path"])   # 1. who is at the till
        shot("1-staff")
        who_in(page, "Sari", "2345")
        shot("2-shift-closed")                      # 2. no shift yet: the till takes no money
        gate = page.get_by_role("dialog")
        if gate.count():
            gate.locator("input").first.fill("200000")
            gate.get_by_role("button", name="Buka shift").click()
            page.wait_for_timeout(1500)
        shot("3-sell")                              # 3. the menu
        page.get_by_role("button", name="Makan di sini").first.click()
        for name in ("Croissant", "Cappuccino"):    # 4. a cart: one plain tile, one with sizes and options
            page.get_by_text(name, exact=True).first.click()
            page.wait_for_timeout(800)
            if name == "Cappuccino":
                shot("4a-item-options")
                page.get_by_text("Large", exact=False).first.click()
                page.get_by_text("Susu oat", exact=False).first.click()
                page.wait_for_timeout(400)
                shot("4a2-item-chosen")
                page.get_by_role("button", name="Tambah ke pesanan").click()
                page.wait_for_timeout(600)
        shot("4b-cart")
        page.get_by_text("Pesanan aktif", exact=False).first.click()
        shot("5-active-orders")                     # the guests who ordered from the table QR
        page.get_by_text("Riwayat transaksi", exact=False).first.click()
        shot("6-history")
        page.goto(args.web + "/menu/" + menu["menu_token"])
        shot("7-qr-menu", full=True)
        page.goto(args.web + "/kitchen/" + pairing["pairing_token"])
        who_in(page, "Dewi", "4567")
        shot("8-kitchen")
        browser.close()
    for pr in problems:
        print("PROBLEM", pr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
