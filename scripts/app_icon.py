"""Draw the Kasir home-screen icons from the same mark as frontend/app/icon.svg (kasir-4).

Android's "Tambahkan ke layar utama" wants PNG icons at 192 and 512 px; an
SVG-only manifest gets a generic letter tile on some builds. The mark is the
wordmark's eclipse O (the second glyph of the signage master) in warm ivory on
the ink disc, exactly as icon.svg places it.

    python scripts/app_icon.py     # writes frontend/public/icons/kasir-*.png

* kasir-192.png, kasir-512.png: the round mark on a transparent square.
* kasir-maskable-512.png: ink to every edge with the mark inside the safe
  zone, for launchers that cut their own shape.

Needs Pillow (in the backend's virtualenv); nothing at run time does.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

from receipt_logo import SOURCE, _glyph_paths, _subpaths

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "frontend" / "public" / "icons"

INK = (0x24, 0x24, 0x26, 255)
IVORY = (0xFF, 0xF4, 0xDE, 255)
VIEW = 640.0                       # icon.svg's viewBox
SCALE, DX, DY = 1.68196, -756.90, -181.34   # icon.svg's transform on the glyph
SUPER = 4


def _mark(size: int, shrink: float) -> Image.Image:
    """The O as an 8-bit coverage mask, `size` px square, scaled about the centre."""
    big = size * SUPER
    k = big / VIEW
    glyph = _glyph_paths(SOURCE.read_text(encoding="utf-8"))[1]
    canvas = Image.new("1", (big, big), 0)
    for contour in _subpaths(glyph):
        pts = []
        for x, y in contour:
            sx, sy = x * SCALE + DX, y * SCALE + DY                    # into the 640 box
            sx, sy = (sx - VIEW / 2) * shrink + VIEW / 2, (sy - VIEW / 2) * shrink + VIEW / 2
            pts.append((sx * k, sy * k))
        mask = Image.new("1", canvas.size, 0)
        ImageDraw.Draw(mask).polygon(pts, fill=1)
        canvas = ImageChops.logical_xor(canvas, mask)                  # even-odd
    return canvas.convert("L").resize((size, size), Image.Resampling.LANCZOS)


def icon(size: int, *, maskable: bool) -> Image.Image:
    big = size * SUPER
    if maskable:
        img = Image.new("RGBA", (size, size), INK)
        mark = _mark(size, 0.8)             # inside the 80% safe circle, with air
    else:
        disc = Image.new("L", (big, big), 0)
        ImageDraw.Draw(disc).ellipse((0, 0, big - 1, big - 1), fill=255)
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        img.paste(Image.new("RGBA", (size, size), INK), (0, 0), disc.resize((size, size), Image.Resampling.LANCZOS))
        mark = _mark(size, 1.0)
    img.paste(Image.new("RGBA", (size, size), IVORY), (0, 0), mark)
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, size, maskable in (("kasir-192.png", 192, False), ("kasir-512.png", 512, False),
                                 ("kasir-maskable-512.png", 512, True)):
        icon(size, maskable=maskable).save(OUT / name, optimize=True)
        print("wrote", (OUT / name).relative_to(ROOT), (OUT / name).stat().st_size, "bytes")


if __name__ == "__main__":
    main()
