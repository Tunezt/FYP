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
    "get_sales_summary": {"id": "penjualan hari ini / minggu ini", "ms": "jualan hari ini / minggu ini", "en": "today's or this week's sales"},
    "compare_periods": {"id": "banding penjualan antar periode", "ms": "banding jualan antara tempoh", "en": "compare sales between periods"},
    "get_profit": {"id": "untung rugi", "ms": "untung rugi", "en": "profit and loss"},
    "get_stock": {"id": "sisa stok", "ms": "baki stok", "en": "stock on hand"},
    "get_low_stock": {"id": "stok yang hampir habis", "ms": "stok yang hampir habis", "en": "items about to run out"},
    "correct_stock": {"id": "koreksi stok setelah hitung fisik", "ms": "betulkan stok selepas kiraan", "en": "correct a stock count"},
    "record_expense": {"id": "catat pengeluaran", "ms": "catat perbelanjaan", "en": "record an expense"},
    "search_history": {"id": "cari catatan lama", "ms": "cari rekod lama", "en": "search past records"},
    "get_purchase_history": {"id": "riwayat belanja ke supplier", "ms": "sejarah belian dari pembekal", "en": "purchase history by supplier"},
    "get_supplier_prices": {"id": "harga beli terakhir per supplier", "ms": "harga belian terakhir ikut pembekal", "en": "last purchase prices by supplier"},
    "get_recipe_cost": {"id": "modal per porsi dari resep", "ms": "kos setiap hidangan dari resipi", "en": "recipe cost per serving"},
    "get_shift_summary": {"id": "rekap shift kasir dan selisih kas", "ms": "ringkasan syif juruwang dan beza tunai", "en": "till shifts and cash variance"},
    "get_customer_summary": {"id": "ringkasan pelanggan dan poin", "ms": "ringkasan pelanggan dan mata", "en": "customer summary and points"},
    "get_promo_performance": {"id": "kinerja promo", "ms": "prestasi promosi", "en": "promo performance"},
    "draft_purchase_order": {"id": "buat draf pesanan ke supplier", "ms": "sediakan draf pesanan kepada pembekal", "en": "draft a purchase order"},
}

_INTRO = {
    "id": "Maaf, data usaha yang saya pegang tidak mencakup {topic}, jadi saya tidak bisa menjawab itu dengan angka.",
    "ms": "Maaf, data perniagaan yang saya ada tidak merangkumi {topic}, jadi saya tidak boleh menjawabnya dengan angka.",
    "en": "Sorry — the business data I hold does not cover {topic}, so I can't give you a figure for that.",
}
_INTRO_NO_TOPIC = {
    "id": "Maaf, itu di luar data usaha yang saya pegang, jadi saya tidak bisa menjawabnya dengan angka.",
    "ms": "Maaf, itu di luar data perniagaan yang saya ada, jadi saya tidak boleh menjawabnya dengan angka.",
    "en": "Sorry — that is outside the business data I hold, so I can't give you a figure for it.",
}
_OFFER = {
    "id": "Yang bisa saya jawab: {menu}.",
    "ms": "Yang boleh saya jawab: {menu}.",
    "en": "What I can answer: {menu}.",
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


def capability_menu(language: str, limit: int = 8) -> str:
    """The things the assistant can do, in the order the tools are declared
    (so the most-used come first), for every declared tool with a phrase."""
    language = language if language in LANGUAGES else "id"
    names = [d.name for d in tools_module.TOOL_DECLARATIONS]
    phrases = [CAPABILITIES[n][language] for n in names if n in CAPABILITIES]
    return ", ".join(phrases[:limit])


def refusal_reply(business: Business, user_message: str, topic: str | None = None) -> str:
    """Assembled, never generated: a refusal in the owner's language that says
    what the data does not cover and offers what it does."""
    language = detect_language(user_message, default=getattr(business, "language_preference", "id") or "id")
    topic = (topic or "").strip().rstrip("?.!")
    if topic and not looks_like_a_figure(topic):
        intro = _INTRO[language].format(topic=topic)
    else:
        intro = _INTRO_NO_TOPIC[language]
    return f"{intro} {_OFFER[language].format(menu=capability_menu(language))}"
