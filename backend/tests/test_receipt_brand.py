"""till-12 — a receipt laid out like an established café's, with the café's logo.

The owner's feedback of 2 October 2026 (point 6): "lets have our logo at the
top with the design of our signage, not just bland POERNAMA", and take what
works from receipts that cafés actually print. So, top to bottom: the logo
(the signage lettering as a printer bitmap, when the owner turns it on) or the
name; address and contacts; the order number large between double rules; the
details as label and value; each item named, then "qty x @price" and the
amount; the item count; Subtotal and the tax; TOTAL between double rules; the
payment and change; the café's own closing line; thanks; the reference.

The paper is checked through the bridge's real ESC/POS renderer at 80 mm /
48 columns, like test_receipt_content.py.
"""
import base64
import importlib.util
import json
from pathlib import Path

from app.services.business_profile import WORDMARK_ASSET, logo_bitmap, wordmark_logo
from tests.test_receipt_content import (  # noqa: F401 (fixtures)
    _paper_lines, _receipt_doc, _sell, cafe, client, engine, pb, session_factory,
)

ROOT = Path(__file__).resolve().parents[2]


def _blocks(doc, t):
    return [b for b in doc["blocks"] if b["t"] == t]


async def test_the_receipt_reads_top_to_bottom_like_a_cafe_receipt(client, session_factory, cafe):
    c = cafe
    sale = await _sell(client, c, [{"method": "cash", "amount": 52800, "tendered": 100000}])
    doc = await _receipt_doc(session_factory, c, sale["id"])
    kinds = [b["t"] for b in doc["blocks"]]
    # The name (no logo yet), then details, then the number between double rules.
    assert kinds[0] == "title" and doc["blocks"][0]["text"] == "POERNAMA"
    first_double = kinds.index("rule")
    assert doc["blocks"][first_double].get("style") == "double"
    assert kinds[first_double + 1] == "banner" and doc["blocks"][first_double + 2] == {"t": "rule", "style": "double"}

    paper = _paper_lines(doc)
    joined = "\n".join(paper)
    assert max(len(l) for l in paper) <= 48
    assert "=" * 48 in paper and "-" * 48 in paper
    labels = [l.split()[0] for l in paper if l and l[0].isalpha()]
    assert labels.index("Tanggal") < labels.index("Jenis") < labels.index("Kasir")
    assert any(l.startswith("Kasir") and l.endswith("Sari") for l in paper)
    # Items: the name on its own line, then quantity at unit price and the amount.
    roti = paper.index("Roti")
    assert paper[roti + 1].startswith("  1 x @15.000") and paper[roti + 1].endswith("15.000")
    assert any(l.startswith("Total item") and l.endswith("2") for l in paper)
    # TOTAL is set apart between double rules.
    total = next(i for i, l in enumerate(paper) if l.startswith("TOTAL"))
    assert paper[total - 1] == "=" * 48 and paper[total + 1] == "=" * 48
    subtotal = next(i for i, l in enumerate(paper) if l.startswith("Subtotal"))
    tax = next(i for i, l in enumerate(paper) if l.startswith("PB1 10%"))
    assert subtotal < tax < total
    # Takeaway: the customer is told to listen for the number; then thanks; the reference last.
    stripped = [l.strip() for l in paper]
    assert stripped.index("Sebutkan nomor pesanan saat mengambil.") < stripped.index("Terima kasih!")
    assert stripped[-1].startswith("Ref ")


async def test_the_owner_turns_on_the_logo_and_it_prints_as_a_bitmap(client, session_factory, cafe):
    c = cafe
    on = await client.patch("/api/business", headers=c["owner"], json={"receipt_logo": True})
    assert on.status_code == 200 and on.json()["receipt_logo"] is True
    sale = await _sell(client, c, [{"method": "qris", "amount": 52800}])
    doc = await _receipt_doc(session_factory, c, sale["id"])
    logo = doc["blocks"][0]
    assert logo["t"] == "logo" and logo["text"] == "POERNAMA"   # the name rides along for an old bridge
    assert (logo["width"], logo["height"]) == (448, 95)                # till-15: 56 mm across
    assert len(base64.b64decode(logo["bits"])) == 448 // 8 * 95

    printed = pb.render(doc, pb.Profile(columns=48))
    header = b"\x1dv0\x00" + bytes([56, 0, 95, 0])
    assert header in printed
    start = printed.index(header) + len(header)
    assert printed[start:start + 56 * 95] == base64.b64decode(logo["bits"])
    # No text line repeats the name: the logo is the name.
    assert "POERNAMA" not in "\n".join(_paper_lines(doc))

    # The screens are told too, and draw the same lettering.
    till = (await client.get(f"/pos/orders/{sale['id']}/receipt", headers=c["pos"])).json()
    assert till["business_logo"] is True

    off = await client.patch("/api/business", headers=c["owner"], json={"receipt_logo": False})
    assert off.json()["receipt_logo"] is False
    again = await _sell(client, c, [{"method": "qris", "amount": 52800}])
    assert (await _receipt_doc(session_factory, c, again["id"]))["blocks"][0]["t"] == "title"


def test_a_bitmap_that_does_not_add_up_prints_the_name_instead():
    w = pb.Writer(48)
    pb.render_block(w, {"t": "logo", "text": "POERNAMA", "width": 384, "height": 81, "bits": "AAAA"}, pb.Profile(columns=48))
    assert b"\x1dv0" not in w.buf and b"POERNAMA" in w.buf
    assert pb.logo_command(100, 10, base64.b64encode(b"\x00" * 130).decode()) == b""   # width not a multiple of 8
    assert pb.logo_command(640, 1, base64.b64encode(b"\x00" * 80).decode()) == b""     # wider than the head


async def test_the_cafe_writes_its_own_closing_line(client, session_factory, cafe):
    c = cafe
    saved = await client.patch("/api/business", headers=c["owner"], json={"receipt_footer": "  WiFi:  Poernama   sandi kopienak "})
    assert saved.status_code == 200 and saved.json()["receipt_footer"] == "WiFi: Poernama sandi kopienak"
    sale = await _sell(client, c, [{"method": "qris", "amount": 52800}])
    paper = [l.strip() for l in _paper_lines(await _receipt_doc(session_factory, c, sale["id"]))]
    assert paper.index("WiFi: Poernama sandi kopienak") < paper.index("Terima kasih!")
    receipt = (await client.get(f"/pos/orders/{sale['id']}/receipt", headers=c["pos"])).json()
    assert receipt["receipt_footer"] == "WiFi: Poernama sandi kopienak"

    too_long = await client.patch("/api/business", headers=c["owner"], json={"receipt_footer": "x" * 201})
    assert too_long.status_code == 422 and "catatan di bawah struk" in too_long.json()["detail"]
    cleared = await client.patch("/api/business", headers=c["owner"], json={"receipt_footer": ""})
    assert cleared.json()["receipt_footer"] is None


def test_the_logo_is_the_wordmark_from_the_signage():
    """The committed bitmap is exactly what scripts/receipt_logo.py makes from
    the wordmark the dashboard draws — regenerate it if the lettering changes."""
    spec = importlib.util.spec_from_file_location("receipt_logo", ROOT / "scripts" / "receipt_logo.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fresh = module.pack(module.render())
    stored = json.loads(WORDMARK_ASSET.read_text(encoding="utf-8"))
    assert (fresh["width"], fresh["height"], fresh["bits"]) == (stored["width"], stored["height"], stored["bits"])
    assert wordmark_logo() == {"name": "wordmark"}                     # a café stores which logo
    assert logo_bitmap(wordmark_logo()) == {k: stored[k] for k in ("width", "height", "bits")}
    bits = base64.b64decode(stored["bits"])
    ink = sum(bin(b).count("1") for b in bits) / (len(bits) * 8)
    assert 0.08 < ink < 0.5   # lettering, not a blank or a black box


def test_the_hairlines_print_unbroken():
    """till-15: the P's swash and the R's tail are the finest strokes. On paper
    a run of dots that touch only at a corner reads as a gap, so the pieces are
    counted joined edge to edge: one per letter shape, never dashes."""
    stored = json.loads(WORDMARK_ASSET.read_text(encoding="utf-8"))
    w, h = stored["width"], stored["height"]
    raw = base64.b64decode(stored["bits"])
    ink = {(x, y) for y in range(h) for x in range(w) if raw[y * (w // 8) + x // 8] & (0x80 >> (x % 8))}
    seen, pieces = set(), 0
    for start in ink:
        if start in seen:
            continue
        pieces += 1
        stack = [start]
        seen.add(start)
        while stack:
            x, y = stack.pop()
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                n = (x + dx, y + dy)
                if n in ink and n not in seen:
                    seen.add(n)
                    stack.append(n)
    # P (swash and bowl), the moon O (two crescents), E, R with its tail, N, A, M, A, counters aside:
    # a broken hairline shows up as dozens of fragments.
    assert pieces <= 10, pieces   # 8 today


def test_a_cafe_that_stored_a_whole_bitmap_still_prints_it():
    own = {"width": 8, "height": 1, "bits": base64.b64encode(b"\xff").decode()}
    assert logo_bitmap(own) == own
    assert logo_bitmap(None) is None and logo_bitmap({"name": "unknown"}) is None
