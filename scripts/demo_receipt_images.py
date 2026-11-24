"""Draw the photographed receipts (nota) of the demo café from backend/app/seed_data.py.

    cd backend && ./.venv/Scripts/python.exe ../scripts/demo_receipt_images.py

Writes frontend/public/demo-receipts/<file>.jpg. The seed stores these as
`image_url = "demo/<file>.jpg"` and the dashboard shows them as thumbnails, so a
cold demo has receipt photos without Supabase Storage and without a live upload.
Generated and invented, not photographs of anything real. Deterministic.
"""
import random
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from app.seed_data import NOTA_ADDRESS, NOTAS  # noqa: E402

OUT = ROOT / "frontend" / "public" / "demo-receipts"
FONTS = Path("C:/Windows/Fonts")
MONTHS = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]


def rp(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def font(name: str, size: int):
    return ImageFont.truetype(str(FONTS / name), size)


def render(fname: str, supplier: str, date_text: str, lines, style: str, seed: int) -> Image.Image:
    rng = random.Random(seed)
    mono, mono_b = font("cour.ttf", 30), font("courbd.ttf", 34)
    hand = font("Inkfree.ttf", 34) if style == "hand" else mono
    body = hand if style == "hand" else mono
    total = sum(q * p for _n, q, _u, p in lines)
    w, rows = 640, []
    rows.append((supplier.upper(), mono_b, "c"))
    rows.append((NOTA_ADDRESS.get(supplier, ""), mono, "c"))
    rows.append(("-" * 30, mono, "l"))
    rows.append((f"NOTA {rng.randint(100, 999)}      {date_text}", body, "l"))
    rows.append(("-" * 30, mono, "l"))
    for n, q, u, p in lines:
        rows.append((n, body, "l"))
        rows.append((f"  {q} {u} x {rp(p)}", body, "l"))
        rows.append((rp(q * p).rjust(12), body, "r"))
    rows.append(("-" * 30, mono, "l"))
    rows.append((f"TOTAL        Rp {rp(total)}", mono_b, "l"))
    rows.append(("Terima kasih", body, "c"))
    h = 60 + sum(int(f.size * 1.5) for _t, f, _a in rows) + 60
    paper = Image.new("RGB", (w, h), (250, 248, 242))
    d = ImageDraw.Draw(paper)
    y = 40
    for text, f, align in rows:
        tw = d.textlength(text, font=f)
        x = {"c": (w - tw) / 2, "r": w - tw - 50, "l": 40}[align]
        d.text((x, y + rng.randint(-1, 1)), text, font=f, fill=(34, 32, 30))
        y += int(f.size * 1.5)
    table = Image.new("RGB", (w + 260, h + 260), (176, 160, 138))
    table.paste(paper, (130, 130))
    table = table.rotate(rng.uniform(-3, 3), resample=Image.BICUBIC, fillcolor=(176, 160, 138))
    return table.filter(ImageFilter.GaussianBlur(0.6))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for i, (fname, supplier, nota_date, _hour, lines, style) in enumerate(NOTAS):
        day = datetime.fromisoformat(nota_date)
        img = render(fname, supplier, f"{day.day:02d}/{day.month:02d}/{day.year}", lines, style, seed=100 + i)
        img.save(OUT / f"{fname}.jpg", quality=82)
        print("wrote", OUT / f"{fname}.jpg")


if __name__ == "__main__":
    main()
