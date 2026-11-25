"""Explicit refusal (roadmap M9-T5).

When no tool or metric matches, the assistant says so — in the owner's
language — and lists what it *can* answer. It never improvises a number. Two
mechanisms make that a property of the code rather than of a prompt:

  1. The `out_of_scope` function is handled here, deterministically, with no
     model call at all: the reply is assembled from a phrase table keyed by the
     fixed tool set, so it cannot contain a figure that was not in the data.
  2. Any reply the model writes without facts behind it (the `clarify` path,
     where the classifier supplies the text itself) is checked for anything
     that looks like a figure — rupiah, ringgit, thousands separators,
     "ribu"/"juta" — and replaced with the refusal if one is found.

The capability menu is derived from the tool declarations, so a tool added in
tools.py must be given a phrase here or `test_refusal` fails: the menu can
never quietly fall behind the assistant.
"""
from __future__ import annotations

import re

from app.ai import tools as tools_module
from app.models import Business

LANGUAGES = ("id", "ms", "en")

# One line per reading tool, per language. Write tools (recording an expense,
# correcting stock, drafting a purchase order) are offered too — they are
# things the owner can ask for. `search_history` is the RAG path.
CAPABILITIES: dict[str, dict[str, str]] = {
    "get_sales_summary": {"id": "omzet per hari / minggu / bulan", "ms": "jualan harian / mingguan / bulanan", "en": "totals by day, week or month"},
    "compare_periods": {"id": "banding antar periode", "ms": "banding antara tempoh", "en": "period comparisons"},
    "get_profit": {"id": "untung rugi", "ms": "untung rugi", "en": "profit and loss"},
    "get_stock": {"id": "sisa stok", "ms": "baki stok", "en": "stock on hand"},
    "get_low_stock": {"id": "yang hampir habis", "ms": "yang hampir habis", "en": "items running low"},
    "correct_stock": {"id": "koreksi stok", "ms": "betulkan stok", "en": "stock corrections"},
    "record_expense": {"id": "catat pengeluaran", "ms": "catat perbelanjaan", "en": "recording an expense"},
    "search_history": {"id": "cari nota lama", "ms": "cari resit lama", "en": "searching past receipts"},
    "get_purchase_history": {"id": "riwayat belanja", "ms": "sejarah belian", "en": "purchase history"},
    "get_supplier_prices": {"id": "harga supplier", "ms": "harga pembekal", "en": "supplier prices"},
    "get_recipe_cost": {"id": "modal per porsi", "ms": "kos setiap hidangan", "en": "cost per serving"},
    "get_shift_summary": {"id": "shift kasir & selisih kas", "ms": "syif juruwang & beza tunai", "en": "till shifts and cash gaps"},
    "get_customer_summary": {"id": "pelanggan & poin", "ms": "pelanggan & mata", "en": "customers and points"},
    "get_promo_performance": {"id": "promo", "ms": "promosi", "en": "promos"},
    "draft_purchase_order": {"id": "draf pesanan ke supplier", "ms": "draf pesanan kepada pembekal", "en": "purchase order drafts"},
}

# P2-1: the menu is grouped, one line per group, so all of it fits a phone screen.
# A tool in no group still shows, under the last heading (the test keeps it complete).
_GROUPS: tuple[tuple[dict[str, str], tuple[str, ...]], ...] = (
    ({"id": "Penjualan", "ms": "Jualan", "en": "Sales"},
     ("get_sales_summary", "compare_periods", "get_profit", "get_promo_performance", "get_customer_summary", "get_shift_summary")),
    ({"id": "Stok", "ms": "Stok", "en": "Stock"},
     ("get_stock", "get_low_stock", "correct_stock", "get_recipe_cost")),
    ({"id": "Belanja", "ms": "Belian", "en": "Purchasing"},
     ("get_purchase_history", "get_supplier_prices", "draft_purchase_order", "record_expense", "search_history")),
)
# Not tools but things the owner can send (processor.py: photos and the stock Excel).
_SEND = {
    "id": "- *Kirim:* foto nota belanja, foto buku stok, file Excel stok, atau foto menu dengan keterangan _menu_",
    "ms": "- *Hantar:* foto resit belian, foto buku stok, fail Excel stok, atau foto menu dengan kapsyen _menu_",
    "en": "- *Send:* a purchase receipt photo, a stock-book photo, the stock Excel file, or a menu photo captioned _menu_",
}

_INTRO = {
    "id": "Maaf, data usaha yang aku pegang belum mencakup {topic}, jadi aku nggak bisa jawab itu pakai angka.",
    "ms": "Maaf, data perniagaan yang saya ada tidak merangkumi {topic}, jadi saya tidak boleh menjawabnya dengan angka.",
    "en": "Sorry, the business data I hold does not cover {topic}, so I can't give you a figure for that.",
}
_INTRO_NO_TOPIC = {
    "id": "Maaf, itu di luar data usaha yang aku pegang, jadi aku nggak bisa jawab pakai angka.",
    "ms": "Maaf, itu di luar data perniagaan yang saya ada, jadi saya tidak boleh menjawabnya dengan angka.",
    "en": "Sorry, that is outside the business data I hold, so I can't give you a figure for it.",
}
_OFFER = {
    "id": "Yang bisa kubantu:",
    "ms": "Yang boleh saya bantu:",
    "en": "What I can answer:",
}

# Marker words that belong to ONE language. Words shared by Indonesian and
# Malay (berapa, stok, harga, bulan, minggu, hari ini) are deliberately absent:
# they cannot tell the two apart, and a tie goes to the business's preference.
_MS_MARKERS = re.compile(r"\b(baki|jualan|pembekal|belian|syif|tempoh|boleh|awak|nak|tak|agaknya|kedai|kat|macam mana|ramalan|perbelanjaan|juruwang)\b", re.I)
_EN_MARKERS = re.compile(r"\b(how|what|much|many|the|sales|stock|profit|today|week|month|customer|price|is|are|do|can|please|will|my)\b", re.I)
_ID_MARKERS = re.compile(r"\b(penjualan|pelanggan|bisa|gimana|kenapa|udah|belum|tolong|dong|brp|sy|yg|ga|nggak|kira-kira|sebelah|karyawan|pengeluaran|kasir)\b", re.I)

# Words Indonesian and Malay share: a signal that the message is not English,
# but not which of the two it is.
_SHARED_MARKERS = re.compile(r"\b(berapa|stok|harga|bulan|minggu|hari ini|kemarin|sisa|jual|beli|gula|kopi|pelanggan|untung|rugi)\b", re.I)

# What a fabricated figure looks like in a WhatsApp reply.
_FIGURE = re.compile(
    r"(\bRp\s?\d|\bRM\s?\d|\d{1,3}(?:[.,]\d{3})+\b|\b\d+(?:[.,]\d+)?\s?(?:ribu|rb|juta|jt|k)\b|\b\d+(?:[.,]\d+)?\s?%)",
    re.I,
)


def detect_language(text: str, default: str = "id") -> str:
    """The owner's language for this message. Exclusive marker words decide;
    words Indonesian and Malay share only say "not English", and the business's
    preference then picks between the two; no signal at all means the business's
    preference."""
    default = default if default in LANGUAGES else "id"
    scores = {
        "ms": len(_MS_MARKERS.findall(text or "")),
        "en": len(_EN_MARKERS.findall(text or "")),
        "id": len(_ID_MARKERS.findall(text or "")),
    }
    best = max(scores, key=lambda k: scores[k])
    if scores[best] > 0:
        tied = [k for k, v in scores.items() if v == scores[best]]
        return default if len(tied) > 1 and default in tied else best
    if _SHARED_MARKERS.search(text or ""):
        return default if default in ("id", "ms") else "id"
    return default


def looks_like_a_figure(reply: str) -> bool:
    return bool(_FIGURE.search(reply or ""))


def capability_menu(language: str) -> str:
    """Everything the assistant can do: one line per group of tools, then what
    the owner can send. Every declared tool with a phrase is listed."""
    language = language if language in LANGUAGES else "id"
    declared = [d.name for d in tools_module.TOOL_DECLARATIONS if d.name in CAPABILITIES]
    grouped = {n for _label, names in _GROUPS for n in names}
    lines = []
    for i, (label, names) in enumerate(_GROUPS):
        tools = [n for n in names if n in declared]
        if i == len(_GROUPS) - 1:
            tools += [n for n in declared if n not in grouped]
        lines.append(f"- *{label[language]}:* " + ", ".join(CAPABILITIES[n][language] for n in tools))
    return "\n".join([*lines, _SEND[language]])


def refusal_reply(business: Business, user_message: str, topic: str | None = None) -> str:
    """Assembled, never generated: a refusal in the owner's language that says
    what the data does not cover and offers what it does."""
    language = detect_language(user_message, default=getattr(business, "language_preference", "id") or "id")
    topic = (topic or "").strip().rstrip("?.!")
    if topic and not looks_like_a_figure(topic):
        intro = _INTRO[language].format(topic=topic)
    else:
        intro = _INTRO_NO_TOPIC[language]
    return f"{intro}\n\n{_OFFER[language]}\n{capability_menu(language)}"
