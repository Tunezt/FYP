"""The café's own details on its receipts (till-5a).

Decision 3 of the owner's plan (1 Oct 2026): the receipt shows an address, a
phone number and an Instagram handle from the café's settings, never written
into the receipt code. Until Ibu Diah gives the real ones they are clearly
fake placeholders, and the settings page marks every field still equal to one
of them, so nobody mistakes "Jl. Lorem Ipsum" for an address.

The tax is the regional restaurant tax — legally PBJT makanan dan minuman
since UU 1/2022 (HKPD), formerly Pajak Restoran / "PB1" — at most 10%, set per
regency; small cafés may be under the threshold. It is *not* PPN: food and
drink served by a restaurant is a regional tax object, outside VAT. 10% is a
placeholder until the owner confirms with her Bapenda.

till-11 (owner's feedback, 2 Oct 2026): the tax is added *on top* of the menu
price, as Indonesian cafés print it — Subtotal, PB1 10%, Total — and it is
called "PB1", the name customers know from restaurant receipts. Both are
settings (Pengaturan → Harga & pajak), so "PBJT" or "Pajak Resto", or prices
that already include it, are one change away.

Because the tax is now *charged on top*, an unconfirmed rate would change what
real customers pay. So a real café (`app.bootstrap`, dashboard registration)
starts at 0% — the receipt shows no tax line — until the owner types the rate
Bapenda confirmed; the demo seed uses the 10% placeholder so the layout shows.
"""
from __future__ import annotations

import json
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

RECEIPT_PLACEHOLDERS: dict[str, str] = {
    "address": "Jl. Lorem Ipsum No. 1, Kota Dolor",
    "contact_phone": "0812-0000-0000",
    "instagram": "@poernama.cafe",
}

TAX_LABEL_PLACEHOLDER = "PB1"
TAX_RATE_PLACEHOLDER = Decimal("0.10")


def placeholder_fields(business) -> list[str]:
    """Which receipt details are still the placeholders."""
    return [f for f, v in RECEIPT_PLACEHOLDERS.items() if (getattr(business, f, None) or "").strip() == v]


def apply_placeholders(business) -> None:
    """A new café starts with the marked placeholders, to be replaced in
    Pengaturan before the first real receipt."""
    for field, value in RECEIPT_PLACEHOLDERS.items():
        if not getattr(business, field, None):
            setattr(business, field, value)


# till-12: the Poernama wordmark ("Horizon Swash", the signage lettering) as
# the printer's bitmap, made by scripts/receipt_logo.py from
# frontend/components/Wordmark.tsx. The only logo the product ships.
WORDMARK_ASSET = Path(__file__).resolve().parents[1] / "assets" / "receipt_logo.json"
RECEIPT_FOOTER_MAX = 200


@lru_cache(maxsize=1)
def _wordmark() -> dict:
    data = json.loads(WORDMARK_ASSET.read_text(encoding="utf-8"))
    return {k: data[k] for k in ("width", "height", "bits")}


def wordmark_logo() -> dict:
    """A fresh copy of the wordmark bitmap, to store on a business."""
    return dict(_wordmark())


def apply_tax_placeholder(pricing_row, rate: Decimal = TAX_RATE_PLACEHOLDER) -> None:
    pricing_row.tax_label = TAX_LABEL_PLACEHOLDER
    pricing_row.tax_rate = rate
    pricing_row.tax_inclusive = False   # till-11: added on top of the menu price


def tax_line_label(label: str | None, rate, inclusive: bool) -> str:
    """"PBJT 10% (termasuk)": the label the café chose, the rate, and whether
    it is inside the price or added to it."""
    pct = (Decimal(rate or 0) * 100).normalize()
    pct_text = f"{pct:f}".rstrip("0").rstrip(".") if "." in f"{pct:f}" else f"{pct:f}"
    base = f"{(label or 'Pajak').strip()} {pct_text}%"
    return f"{base} (termasuk)" if inclusive else base
