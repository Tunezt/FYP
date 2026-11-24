"""fin-po-link — every dashboard page the bot names must exist.

The draft-purchase-order reply once told the owner to confirm the order under
"Stok › Pesanan pembelian", a page nobody built. A user-facing string that names a
dashboard page ("halaman Stok", "Stok › Pesanan pembelian") must name a left-nav
entry of the dashboard, and a sub-page after the arrow must exist as text in that
entry's page source.
"""
import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
APP = ROOT / "backend" / "app"
DASH = ROOT / "frontend" / "app" / "(dashboard)"

PAGE_WORD = re.compile(r"halaman ([A-Z][a-z]+)")
BREADCRUMB = re.compile(r"\b([A-Z][a-z]+) › ([A-Z][^)\"'.]+)")


def _nav() -> dict[str, str]:
    """label -> route folder, from the dashboard layout's own nav list."""
    layout = (DASH / "layout.tsx").read_text(encoding="utf-8")
    return {m.group(2): m.group(1) for m in re.finditer(r'href: "/([a-z-]+)", label: "([^"]+)"', layout)}


def _strings():
    for path in APP.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                yield path.relative_to(ROOT), node.value


def test_the_nav_is_readable_and_every_route_has_a_page():
    nav = _nav()
    assert {"Stok", "Keuangan", "Pengaturan"} <= set(nav)
    for label, folder in nav.items():
        assert (DASH / folder / "page.tsx").exists(), f"nav entry {label} has no page"


def test_every_dashboard_page_the_bot_names_exists():
    nav = _nav()
    seen = 0
    bad = []
    for where, text in _strings():
        for m in PAGE_WORD.finditer(text):
            seen += 1
            if m.group(1) not in nav:
                bad.append(f"{where}: halaman {m.group(1)} is not in the dashboard nav")
        for m in BREADCRUMB.finditer(text):
            seen += 1
            page, sub = m.group(1), m.group(2).strip()
            folder = nav.get(page)
            sources = "".join(p.read_text(encoding="utf-8") for p in (DASH / folder).rglob("*.tsx")) if folder else ""
            if folder is None or sub.lower() not in sources.lower():
                bad.append(f"{where}: '{page} › {sub}' does not exist in the dashboard")
    assert seen, "the check found no page mentions at all: its patterns have rotted"
    assert not bad, "\n".join(bad)
