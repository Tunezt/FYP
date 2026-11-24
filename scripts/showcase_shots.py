"""Screenshot every dashboard page and Settings section of a seeded demo cafe.

    python scripts/showcase_shots.py OUT_DIR [--api http://localhost:8123] [--web http://localhost:3123]
        [--phone 628115819400] [--pin 1234] [--widths 1366,390]

Logs in with the owner PIN (the dashboard's own /auth/login-pin), fills the
token the way the login page does, then writes one PNG per page and width. Also
prints, per page, the console errors, HTTP >= 400 answers and horizontal overflow,
so "none are empty or broken" is a measured line, not a feeling.
"""
import argparse
import json
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

PAGES = ["overview", "sales", "inventory", "money", "customers", "promos", "alerts", "settings"]
SECTIONS = ["profil", "whatsapp", "struk", "pajak", "poin", "staf", "kasir", "printer", "menu-qr", "template", "impor"]


def login(api: str, phone: str, pin: str) -> str:
    req = urllib.request.Request(
        f"{api}/auth/login-pin", data=json.dumps({"phone": phone, "pin": pin}).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.load(urllib.request.urlopen(req))["token"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--api", default="http://localhost:8123")
    ap.add_argument("--web", default="http://localhost:3123")
    ap.add_argument("--phone", default="628115819400")
    ap.add_argument("--pin", default="1234")
    ap.add_argument("--widths", default="1366,390")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    token = login(args.api, args.phone, args.pin)
    problems = 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for width in [int(w) for w in args.widths.split(",")]:
            ctx = browser.new_context(viewport={"width": width, "height": 900})
            ctx.add_init_script(f"localStorage.setItem('wp_owner_token', {json.dumps(token)});")
            page = ctx.new_page()
            seen: list[str] = []
            page.on("console", lambda m: seen.append(f"console {m.type}: {m.text[:160]}") if m.type == "error" else None)
            page.on("response", lambda r: seen.append(f"HTTP {r.status} {r.url[:120]}") if r.status >= 400 else None)
            targets = [(n, f"/{n}") for n in PAGES] + [(f"settings-{s}", f"/settings#{s}") for s in SECTIONS]
            for name, path in targets:
                seen.clear()
                page.goto(args.web + path, wait_until="networkidle")
                for _ in range(3):   # every card has loaded when no loading skeleton is left, twice in a row
                    try:
                        page.wait_for_function("document.querySelectorAll('.skeleton').length === 0", timeout=15000)
                    except Exception:
                        seen.append("still loading after 15 s")
                        break
                    page.wait_for_timeout(600)
                # charts and counters animate when they scroll into view: walk down the page and back
                height = page.evaluate("document.documentElement.scrollHeight")
                for y in range(0, height, 500):
                    page.evaluate(f"window.scrollTo(0, {y})")
                    page.wait_for_timeout(120)
                page.evaluate("window.scrollTo(0, 0)")
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(1800)
                overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
                full = name in PAGES
                if not full:   # a settings section: bring that section to the top of the screen
                    page.evaluate(f"document.getElementById({json.dumps(path.split('#')[1])})?.scrollIntoView()")
                    page.wait_for_timeout(600)
                page.screenshot(path=str(out / f"{width}-{name}.png"), full_page=full)
                bad = [s for s in seen if "favicon" not in s]
                problems += len(bad) + (1 if overflow > 2 else 0)
                print(f"{width:>5} {name:<18} overflow={overflow:<4} problems={len(bad)} {'; '.join(bad[:3])}")
            ctx.close()
        browser.close()
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
