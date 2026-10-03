"""The café's own details on its receipts (till-5a).

Decision 3 of the owner's plan (1 Oct 2026): the receipt shows an address, a
phone number and an Instagram handle from the café's settings, never written
into the receipt code. Until Ibu Diah gives the real ones they are clearly
fake placeholders, and the settings page marks every field still equal to one
of them, so nobody mistakes "Jl. Lorem Ipsum" for an address.

The tax is the regional PBJT makanan dan minuman (formerly PB1), at most 10%,
set per regency; small cafés may be under the threshold. 10% is a placeholder
too, until the owner confirms with her Bapenda. It is kept *inclusive* (menu
prices are what the customer pays), the existing default, so the placeholder
does not change anything a customer is charged.
"""
from __future__ import annotations

from decimal import Decimal

RECEIPT_PLACEHOLDERS: dict[str, str] = {
    "address": "Jl. Lorem Ipsum No. 1, Kota Dolor",
    "contact_phone": "0812-0000-0000",
    "instagram": "@poernama.cafe",
}

TAX_LABEL_PLACEHOLDER = "PBJT"
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


def apply_tax_placeholder(pricing_row) -> None:
    pricing_row.tax_label = TAX_LABEL_PLACEHOLDER
    pricing_row.tax_rate = TAX_RATE_PLACEHOLDER
    pricing_row.tax_inclusive = True


def tax_line_label(label: str | None, rate, inclusive: bool) -> str:
    """"PBJT 10% (termasuk)": the label the café chose, the rate, and whether
    it is inside the price or added to it."""
    pct = (Decimal(rate or 0) * 100).normalize()
    pct_text = f"{pct:f}".rstrip("0").rstrip(".") if "." in f"{pct:f}" else f"{pct:f}"
    base = f"{(label or 'Pajak').strip()} {pct_text}%"
    return f"{base} (termasuk)" if inclusive else base
