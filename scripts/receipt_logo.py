"""Rasterise the Poernama wordmark for an 80 mm thermal receipt (till-12).

The lettering is the approved "Horizon Swash" master, read from the outlines in
frontend/components/Wordmark.tsx (copied verbatim from the signage SVG, which
fills them even-odd). A thermal printer prints dots, so the logo travels in the
print document as a 1-bit bitmap: the bridge sends it with ESC/POS `GS v 0`
and the till's browser preview draws the same bits on a canvas.

    python scripts/receipt_logo.py            # writes backend/app/assets/receipt_logo.json
    python scripts/receipt_logo.py --png x.png  # also a PNG to look at

Width: 384 dots, two thirds of the 576-dot line of an 80 mm head at 203 dpi,
centred. Height follows the master's proportions (2450 : 517.28). Needs Pillow
(already in the backend's virtualenv); nothing at run time does.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "frontend" / "components" / "Wordmark.tsx"
OUT = ROOT / "backend" / "app" / "assets" / "receipt_logo.json"

WIDTH = 384          # dots; a multiple of 8
SUPERSAMPLE = 4      # drawn larger, then reduced: smoother curves at the edge
CURVE_STEPS = 24     # straight segments per cubic curve at the supersampled size


def _glyph_paths(source: str) -> list[str]:
    block = source.split("const GLYPHS = [", 1)[1].split("];", 1)[0]
    return re.findall(r'"([^"]+)"', block)


def _subpaths(d: str) -> list[list[tuple[float, float]]]:
    """M / L / C / Z (absolute), the only commands the master uses."""
    tokens = re.findall(r"[MLCZ]|-?\d+(?:\.\d+)?", d)
    out: list[list[tuple[float, float]]] = []
    pts: list[tuple[float, float]] = []
    i, cmd, cur = 0, None, (0.0, 0.0)
    while i < len(tokens):
        t = tokens[i]
        if t in "MLCZ":
            cmd = t
            i += 1
            if t == "Z":
                if pts:
                    out.append(pts)
                pts = []
            continue
        if cmd == "M":
            if pts:
                out.append(pts)
            cur = (float(tokens[i]), float(tokens[i + 1]))
            pts = [cur]
            i += 2
            cmd = "L"
        elif cmd == "L":
            cur = (float(tokens[i]), float(tokens[i + 1]))
            pts.append(cur)
            i += 2
        elif cmd == "C":
            x1, y1, x2, y2, x, y = (float(v) for v in tokens[i:i + 6])
            x0, y0 = cur
            for k in range(1, CURVE_STEPS + 1):
                s = k / CURVE_STEPS
                a, b, c, e = (1 - s) ** 3, 3 * (1 - s) ** 2 * s, 3 * (1 - s) * s ** 2, s ** 3
                pts.append((a * x0 + b * x1 + c * x2 + e * x, a * y0 + b * y1 + c * y2 + e * y))
            cur = (x, y)
            i += 6
        else:
            raise ValueError(f"unexpected token {t!r}")
    if pts:
        out.append(pts)
    return out


def render() -> Image.Image:
    source = SOURCE.read_text(encoding="utf-8")
    vb = re.search(r'VIEWBOX = "0 0 ([\d.]+) ([\d.]+)"', source)
    vw, vh = float(vb.group(1)), float(vb.group(2))
    big_w = WIDTH * SUPERSAMPLE
    scale = big_w / vw
    big_h = int(round(vh * scale))
    canvas = Image.new("1", (big_w, big_h), 0)
    for d in _glyph_paths(source):
        for contour in _subpaths(d):
            # even-odd: each contour flips the pixels it covers
            mask = Image.new("1", canvas.size, 0)
            ImageDraw.Draw(mask).polygon([(x * scale, y * scale) for x, y in contour], fill=1)
            canvas = ImageChops.logical_xor(canvas, mask)
    height = int(round(big_h / SUPERSAMPLE))
    grey = canvas.convert("L").resize((WIDTH, height), Image.Resampling.LANCZOS)
    return grey.point(lambda v: 255 if v >= 110 else 0).convert("1")   # 1 = ink


def pack(img: Image.Image) -> dict:
    w, h = img.size
    assert w % 8 == 0
    px = img.load()
    rows = bytearray()
    for y in range(h):
        for xb in range(0, w, 8):
            byte = 0
            for bit in range(8):
                if px[xb + bit, y]:
                    byte |= 0x80 >> bit
            rows.append(byte)
    return {"width": w, "height": h, "bits": base64.b64encode(bytes(rows)).decode("ascii"),
            "source": "Horizon Swash wordmark (frontend/components/Wordmark.tsx)"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--png", help="also write a PNG preview")
    args = ap.parse_args()
    img = render()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(pack(img)) + "\n", encoding="utf-8")
    if args.png:
        img.point(lambda v: 0 if v else 255).convert("L").save(args.png)
    print(f"{OUT.relative_to(ROOT)}: {img.size[0]} x {img.size[1]} dots")


if __name__ == "__main__":
    main()
