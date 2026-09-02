"""Generate the expanded vision sample set (roadmap M1-T2) with exact ground truth.

    cd backend && ./.venv/Scripts/python.exe ../scripts/vision-make-samples.py

Writes docs/vision-test-samples/generated/*.jpg and docs/vision-test-samples/manifest.json.
The manifest is the single source of truth for scoring (scripts/vision-baseline.py):
every entry carries category, provenance, the item list, the total, and whether the
document shows unit prices / units — so "the model invented a unit price" is measurable.

EVERYTHING produced here is GENERATED, and says so in its manifest `provenance`:
  * thermal_printed   — rendered receipts in a monospace font, with print defects
  * handwritten_font  — rendered with Windows handwriting-style fonts (Segoe Print,
                        Segoe Script, Ink Free, Bradley Hand). Not human handwriting.
  * blurred / angled  — Gaussian blur / perspective warp of the three Phase-14
                        AI-generated photos and of renders above
  * unreadable_control — a receipt blurred and noised past legibility; the only
                        correct answer is "ask" (gate triggered) or "no items"

Deterministic: seeded RNG, so re-running reproduces byte-identical inputs.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

REPO = Path(__file__).resolve().parent.parent
SAMPLES = REPO / "docs" / "vision-test-samples"
OUT = SAMPLES / "generated"
MANIFEST = SAMPLES / "manifest.json"
FONTS = Path("C:/Windows/Fonts")

rng = random.Random(20260903)


def rp(n: int) -> str:
    """12500 -> '12.500' (Indonesian thousands separator)."""
    return f"{n:,}".replace(",", ".")


# ── datasets (exact truth) ──────────────────────────────────────────────────

def items(rows):
    out = []
    for name, qty, unit, unit_price in rows:
        out.append({
            "name": name, "qty": qty, "unit": unit,
            "unit_price": unit_price, "line_total": round(qty * unit_price),
        })
    return out


THERMAL_A = {
    "store": "TOKO SUMBER REJEKI", "addr": "Jl. Melati No. 12, Depok", "date": "2026-08-14 09:41",
    "no": "TRX-000871",
    "items": items([
        ("Indomie Goreng", 5, "pcs", 3500),
        ("Aqua 600ml", 12, "pcs", 3000),
        ("Kopi Kapal Api", 10, "pcs", 1500),
        ("Gula Pasir 1kg", 2, "pcs", 17000),
        ("Minyak Goreng 2L", 1, "pcs", 36000),
        ("Telur 1kg", 2, "pcs", 28000),
        ("Sabun Lifebuoy", 3, "pcs", 4500),
        ("Rokok Sampoerna", 2, "pcs", 32000),
    ]),
}
THERMAL_B = {
    "store": "GROSIR BAROKAH", "addr": "Pasar Anyar Blok C-4, Bogor", "date": "2026-08-20 15:07",
    "no": "TRX-014220",
    "items": items([
        ("Beras 5kg", 1, "pcs", 72000),
        ("Susu UHT 1L", 4, "pcs", 18000),
        ("Teh Celup Sariwangi", 2, "pcs", 7500),
        ("Kecap Bango 600ml", 1, "pcs", 24000),
        ("Mie Sedaap", 10, "pcs", 3200),
        ("Gas 3kg", 1, "pcs", 22000),
    ]),
}
# Handwritten with a unit column and a unit-price column (like the neat original)
HAND_C = {
    "title": "Belanja pasar", "date": "12/8/26",
    "shows_unit_price": True, "shows_unit": True,
    "items": items([
        ("Beras", 10, "kg", 14000),
        ("Minyak goreng", 3, "liter", 19000),
        ("Gula", 2, "kg", 16000),
        ("Telur", 1, "kg", 28000),
        ("Bawang merah", 1, "kg", 35000),
        ("Cabai rawit", 0.5, "kg", 60000),
        ("Tahu", 10, "pcs", 1000),
    ]),
}
# Handwritten line totals only: no units, no unit prices (like the glare original)
HAND_D = {
    "title": "Nota warung bu Sri", "date": "3/9/26",
    "shows_unit_price": False, "shows_unit": False,
    "items": [
        {"name": "Kopi sachet", "qty": 2, "unit": "", "unit_price": 0, "line_total": 20000},
        {"name": "Teh celup", "qty": 1, "unit": "", "unit_price": 0, "line_total": 6500},
        {"name": "Mie instan", "qty": 3, "unit": "", "unit_price": 0, "line_total": 9000},
        {"name": "Minyak goreng", "qty": 1, "unit": "", "unit_price": 0, "line_total": 18000},
        {"name": "Sabun cuci", "qty": 2, "unit": "", "unit_price": 0, "line_total": 9000},
        {"name": "Gas 3kg", "qty": 1, "unit": "", "unit_price": 0, "line_total": 22000},
    ],
}

# Ground truth for the three Phase-14 originals (transcribed in docs/vision-results.md)
ORIG_NEAT = items([
    ("Minyak goreng 2L", 3, "botol", 38000), ("Gula pasir 1kg", 5, "kg", 15000),
    ("Kopi Sachet", 2, "dus", 88000), ("Telur", 1, "kg", 27000),
    ("Tepung terigu 1kg", 2, "kg", 12500), ("Susu kental manis", 3, "kaleng", 11000),
    ("Mie instan", 10, "bungkus", 3000), ("Kecap manis", 2, "botol", 10000),
    ("Sabun cuci piring", 3, "pouch", 6500), ("Rokok Surya 16", 4, "bungkus", 29000),
])
ORIG_MESSY = [
    {"name": "RB beras", "qty": 2, "unit": "", "unit_price": 0, "line_total": 28000},
    {"name": "gula psr", "qty": 0.25, "unit": "", "unit_price": 0, "line_total": 4500},
    {"name": "teh celup", "qty": 1, "unit": "", "unit_price": 0, "line_total": 11000},
    {"name": "indomie", "qty": 2, "unit": "", "unit_price": 0, "line_total": 6500},
    {"name": "minyak goring", "qty": 1, "unit": "", "unit_price": 0, "line_total": 14000},
    {"name": "kecap BH", "qty": 1, "unit": "", "unit_price": 0, "line_total": 17000},
    {"name": "saos tiram", "qty": 1, "unit": "", "unit_price": 0, "line_total": 6500},
]
ORIG_MEDIUM = [
    {"name": "Beras medium 10kg", "qty": 1, "unit": "", "unit_price": 0, "line_total": 140000},
    {"name": "Minyak goreng 2L", "qty": 2, "unit": "", "unit_price": 0, "line_total": 62000},
    {"name": "Gula pasir 1kg", "qty": 2, "unit": "", "unit_price": 0, "line_total": 28000},
    {"name": "Tepung terigu", "qty": 1, "unit": "", "unit_price": 0, "line_total": 12000},
    {"name": "Telur ayam", "qty": 1, "unit": "tray", "unit_price": 0, "line_total": 27000},
    {"name": "Mie instan", "qty": 10, "unit": "", "unit_price": 0, "line_total": 15000},
    {"name": "Kopi sachet", "qty": 20, "unit": "", "unit_price": 0, "line_total": 18000},
    {"name": "Teh celup", "qty": 1, "unit": "", "unit_price": 0, "line_total": 6000},
    {"name": "Susu kental manis", "qty": 2, "unit": "", "unit_price": 0, "line_total": 13000},
    {"name": "Gas 3kg", "qty": 1, "unit": "", "unit_price": 0, "line_total": 22000},
]


def total(rows) -> int:
    return int(round(sum(r["line_total"] for r in rows)))


# ── renderers ───────────────────────────────────────────────────────────────

def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size)


def render_thermal(doc: dict) -> Image.Image:
    f = font("consola.ttf", 22)
    fb = font("consolab.ttf", 24)
    w = 600
    lines: list[tuple[str, ImageFont.FreeTypeFont]] = [
        (doc["store"].center(40), fb), (doc["addr"].center(40), f), ("", f),
        (f"{doc['date']}   {doc['no']}", f), ("-" * 40, f),
    ]
    for it in doc["items"]:
        lines.append((it["name"][:24].ljust(24), f))
        lines.append((f"  {it['qty']:>3} x {rp(it['unit_price']):>8}  {rp(it['line_total']):>10}", f))
    lines.append(("-" * 40, f))
    t = total(doc["items"])
    paid = ((t // 50000) + 1) * 50000
    lines.append((f"{'TOTAL':<22}{rp(t):>18}", fb))
    lines.append((f"{'TUNAI':<22}{rp(paid):>18}", f))
    lines.append((f"{'KEMBALI':<22}{rp(paid - t):>18}", f))
    lines.append(("", f)); lines.append(("Terima kasih atas kunjungan Anda".center(40), f))
    h = 40 + 30 * len(lines) + 40
    img = Image.new("RGB", (w, h), (250, 250, 247))
    d = ImageDraw.Draw(img)
    y = 40
    for text, fnt in lines:
        d.text((28, y), text, font=fnt, fill=(38, 38, 38))
        y += 30
    return img


def lined_paper(w: int, h: int) -> Image.Image:
    img = Image.new("RGB", (w, h), (252, 251, 246))
    d = ImageDraw.Draw(img)
    for y in range(140, h, 56):
        d.line([(60, y), (w - 40, y)], fill=(205, 212, 226), width=2)
    d.line([(110, 0), (110, h)], fill=(232, 150, 150), width=2)
    return img


def render_handwritten(doc: dict, font_file: str, size: int = 40) -> Image.Image:
    w = 1240 if doc["shows_unit"] else 1000
    h = 160 + 56 * (len(doc["items"]) + 4)
    img = lined_paper(w, h)
    d = ImageDraw.Draw(img)
    fnt = font(font_file, size)
    ink = (36, 52, 128)
    d.text((130, 60), doc["title"], font=fnt, fill=ink)
    d.text((w - 300, 60), f"tgl {doc['date']}", font=font(font_file, size - 6), fill=ink)
    y = 150
    for it in doc["items"]:
        jitter = rng.randint(-3, 3)
        qty = f"{it['qty']:g}"
        if doc["shows_unit"]:
            d.text((130, y + jitter), it["name"], font=fnt, fill=ink)
            d.text((500, y + jitter), f"{qty} {it['unit']}", font=fnt, fill=ink)
            d.text((700, y + jitter), f"@{rp(it['unit_price'])}", font=fnt, fill=ink)
            d.text((1000, y + jitter), rp(it["line_total"]), font=fnt, fill=ink)
        else:
            d.text((130, y + jitter), f"{qty}  {it['name']}", font=fnt, fill=ink)
            d.text((760, y + jitter), rp(it["line_total"]), font=fnt, fill=ink)
        y += 56
    y += 20
    d.line([(w - 400, y), (w - 40, y)], fill=ink, width=3)
    d.text((w - 400, y + 10), "Total", font=fnt, fill=ink)
    d.text((w - 240, y + 10), rp(total(doc["items"])), font=fnt, fill=ink)
    return img.rotate(rng.uniform(-1.5, 1.5), resample=Image.BICUBIC, expand=False, fillcolor=(252, 251, 246))


# ── defects ─────────────────────────────────────────────────────────────────

def add_noise(img: Image.Image, sigma: float) -> Image.Image:
    arr = np.asarray(img).astype(np.float32)
    noise = np.random.default_rng(rng.randint(0, 2**31)).normal(0, sigma, arr.shape)
    return Image.fromarray(np.clip(arr + noise, 0, 255).astype(np.uint8))


def faded(img: Image.Image) -> Image.Image:
    arr = np.asarray(img).astype(np.float32)
    arr = 255 - (255 - arr) * 0.42  # lift the ink toward the paper
    return Image.fromarray(arr.astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.7))


def lowres(img: Image.Image, factor: float = 0.45) -> Image.Image:
    small = img.resize((int(img.width * factor), int(img.height * factor)), Image.BILINEAR)
    return small.resize(img.size, Image.BILINEAR)


def blurred(img: Image.Image, radius: float) -> Image.Image:
    return img.filter(ImageFilter.GaussianBlur(radius))


def _perspective_coeffs(src, dst):
    matrix = []
    for (x, y), (u, v) in zip(dst, src):
        matrix.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        matrix.append([0, 0, 0, x, y, 1, -v * x, -v * y])
    a = np.array(matrix, dtype=np.float64)
    b = np.array(src, dtype=np.float64).reshape(8)
    return np.linalg.solve(a, b)


def angled(img: Image.Image, strength: float = 0.18) -> Image.Image:
    """Camera held off-axis: the page is a trapezoid on a dark table."""
    w, h = img.size
    pad = int(w * 0.12)
    canvas = Image.new("RGB", (w + 2 * pad, h + 2 * pad), (68, 48, 34))
    canvas.paste(img, (pad, pad))
    W, H = canvas.size
    dx, dy = strength * W, strength * H * 0.4
    src = [(0, 0), (W, 0), (W, H), (0, H)]
    dst = [(dx, dy), (W - dx * 0.3, 0), (W, H), (dx * 0.6, H - dy)]
    coeffs = _perspective_coeffs(src, dst)
    return canvas.transform(canvas.size, Image.PERSPECTIVE, coeffs, Image.BICUBIC, fillcolor=(68, 48, 34))


def photo_like(img: Image.Image) -> Image.Image:
    """Mild camera noise + slight blur so renders are not pixel-perfect vectors."""
    return add_noise(img.filter(ImageFilter.GaussianBlur(0.4)), 4)


# ── build the set ───────────────────────────────────────────────────────────

def entry(file, category, provenance, rows, *, doc_type, shows_unit_price, shows_unit,
          expect_gate, note="", written_total=None):
    """`written_total` is the figure printed on the document when it differs from the
    sum of its lines — transcription is scored against what is written, and a
    lines-vs-total mismatch is recorded so a sum-check heuristic can be evaluated."""
    computed = total(rows)
    return {
        "file": file, "category": category, "provenance": provenance,
        "doc_type": doc_type, "shows_unit_price": shows_unit_price, "shows_unit": shows_unit,
        "expect_gate": expect_gate,
        "total": written_total if written_total is not None else computed,
        "lines_sum": computed, "items": rows, "note": note,
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []
    ai_photo = "AI-generated photo-realistic stand-in (Phase 14, July 2026)"

    # The three originals
    manifest += [
        entry("test-receipt-1-neat.png", "handwritten_photo", ai_photo, ORIG_NEAT,
              doc_type="stock_ledger", shows_unit_price=True, shows_unit=True, expect_gate=False,
              written_total=645500,
              note="the generated page writes TOTAL 645.500 but its lines sum to 635.500"),
        entry("test-receipt-2-messy.png", "handwritten_photo", ai_photo, ORIG_MESSY,
              doc_type="receipt", shows_unit_price=False, shows_unit=False, expect_gate=True,
              note="RB / ¼ / BH. readings are genuinely ambiguous"),
        entry("test-receipt-3-medium.png", "handwritten_photo", ai_photo, ORIG_MEDIUM,
              doc_type="receipt", shows_unit_price=False, shows_unit=False, expect_gate=None,
              note="one unit written ('tray'); glare band"),
    ]

    def save(img: Image.Image, name: str, quality: int = 88) -> str:
        img.convert("RGB").save(OUT / name, "JPEG", quality=quality)
        return f"generated/{name}"

    thermal_a = render_thermal(THERMAL_A)
    thermal_b = render_thermal(THERMAL_B)
    gen = "generated: rendered receipt (Consolas), "
    manifest += [
        entry(save(photo_like(thermal_a), "thermal-1-clean.jpg"), "thermal_printed", gen + "clean",
              THERMAL_A["items"], doc_type="receipt", shows_unit_price=True, shows_unit=False, expect_gate=False),
        entry(save(photo_like(faded(thermal_a)), "thermal-2-faded.jpg"), "thermal_printed", gen + "faded ink",
              THERMAL_A["items"], doc_type="receipt", shows_unit_price=True, shows_unit=False, expect_gate=None),
        entry(save(add_noise(thermal_b.rotate(2.5, resample=Image.BICUBIC, expand=True, fillcolor=(250, 250, 247)), 18),
                   "thermal-3-noisy-rotated.jpg"), "thermal_printed", gen + "sensor noise + 2.5° rotation",
              THERMAL_B["items"], doc_type="receipt", shows_unit_price=True, shows_unit=False, expect_gate=None),
        entry(save(photo_like(lowres(thermal_a)), "thermal-4-lowres.jpg"), "thermal_printed", gen + "low resolution (45%)",
              THERMAL_A["items"], doc_type="receipt", shows_unit_price=True, shows_unit=False, expect_gate=None),
    ]

    hand = "generated: rendered with a handwriting-style font, NOT human handwriting — "
    c_segoe = render_handwritten(HAND_C, "segoepr.ttf")
    manifest += [
        entry(save(photo_like(c_segoe), "hand-1-segoe-print.jpg"), "handwritten_font", hand + "Segoe Print",
              HAND_C["items"], doc_type="stock_ledger", shows_unit_price=True, shows_unit=True, expect_gate=False),
        entry(save(photo_like(render_handwritten(HAND_C, "Inkfree.ttf", 42)), "hand-2-ink-free.jpg"), "handwritten_font", hand + "Ink Free",
              HAND_C["items"], doc_type="stock_ledger", shows_unit_price=True, shows_unit=True, expect_gate=None),
        entry(save(photo_like(render_handwritten(HAND_D, "segoesc.ttf", 40)), "hand-3-segoe-script.jpg"), "handwritten_font", hand + "Segoe Script",
              HAND_D["items"], doc_type="receipt", shows_unit_price=False, shows_unit=False, expect_gate=None),
        entry(save(photo_like(render_handwritten(HAND_D, "BRADHITC.TTF", 44)), "hand-4-bradley.jpg"), "handwritten_font", hand + "Bradley Hand",
              HAND_D["items"], doc_type="receipt", shows_unit_price=False, shows_unit=False, expect_gate=None),
    ]

    neat = Image.open(SAMPLES / "test-receipt-1-neat.png").convert("RGB")
    medium = Image.open(SAMPLES / "test-receipt-3-medium.png").convert("RGB")
    blur_note = "generated: Gaussian blur of "
    manifest += [
        entry(save(blurred(neat, 3.5), "blur-1-neat-r3.5.jpg"), "blurred", blur_note + "test-receipt-1-neat.png (r=3.5)",
              ORIG_NEAT, doc_type="stock_ledger", shows_unit_price=True, shows_unit=True, expect_gate=None),
        entry(save(blurred(medium, 3.0), "blur-2-medium-r3.jpg"), "blurred", blur_note + "test-receipt-3-medium.png (r=3.0)",
              ORIG_MEDIUM, doc_type="receipt", shows_unit_price=False, shows_unit=False, expect_gate=None),
        entry(save(blurred(photo_like(thermal_a), 2.2), "blur-3-thermal-r2.2.jpg"), "blurred", blur_note + "thermal-1-clean (r=2.2)",
              THERMAL_A["items"], doc_type="receipt", shows_unit_price=True, shows_unit=False, expect_gate=None),
    ]

    ang = "generated: perspective warp of "
    manifest += [
        entry(save(angled(neat), "angle-1-neat.jpg"), "angled", ang + "test-receipt-1-neat.png",
              ORIG_NEAT, doc_type="stock_ledger", shows_unit_price=True, shows_unit=True, expect_gate=None),
        entry(save(angled(photo_like(thermal_b)), "angle-2-thermal.jpg"), "angled", ang + "a clean render of thermal B",
              THERMAL_B["items"], doc_type="receipt", shows_unit_price=True, shows_unit=False, expect_gate=None),
        entry(save(angled(photo_like(c_segoe), 0.22), "angle-3-hand-segoe.jpg"), "angled", ang + "hand-1-segoe-print",
              HAND_C["items"], doc_type="stock_ledger", shows_unit_price=True, shows_unit=True, expect_gate=None),
    ]

    control = add_noise(blurred(thermal_b, 11), 30)
    manifest += [
        entry(save(control, "control-unreadable.jpg"), "unreadable_control",
              "generated: thermal B blurred (r=11) and noised past legibility — deliberately unreadable",
              THERMAL_B["items"], doc_type="receipt", shows_unit_price=True, shows_unit=False, expect_gate=True,
              note="any confident item list here is a fabrication; the only right answers are no items or the gate"),
    ]

    MANIFEST.write_text(json.dumps({"images": manifest}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sizes = sum((SAMPLES / m["file"]).stat().st_size for m in manifest if m["file"].startswith("generated/"))
    print(f"{len(manifest)} entries ({len(manifest) - 3} generated, {sizes // 1024} KB) -> {MANIFEST}")


if __name__ == "__main__":
    main()
