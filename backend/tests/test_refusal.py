"""M9-T5 — explicit refusal: when no metric or tool matches, the assistant says
so in the owner's language and offers what it can answer. It never improvises a
number. Done when a set of out-of-scope questions produces refusals and zero
fabricated figures. The model is mocked throughout; nothing here needs a
database.
"""
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.ai import tools as tools_module
from app.ai.refusal import CAPABILITIES, LANGUAGES, capability_menu, detect_language, looks_like_a_figure, refusal_reply
from app.ai.router import OUT_OF_SCOPE, _declarations, handle_text

DIGITS = re.compile(r"\d")


def _business(lang="id"):
    return SimpleNamespace(name="Kopi Kenangan Senja", business_type="cafe", language_preference=lang)


def _call(name, args=None):
    return SimpleNamespace(name=name, args=args or {})


# The out-of-scope set: things the data does not hold, in the three languages
# the owner writes, with the typos and mixing real owners produce.
OUT_OF_SCOPE_QUESTIONS = [
    ("berapa penjualan bulan depan kira-kira?", "prediksi penjualan bulan depan", "id"),
    ("cuaca besok hujan ga? mau stok es batu", "cuaca besok", "id"),
    ("berapa pajak UMKM yg harus sy bayar tahun ini", "pajak UMKM tahun ini", "id"),
    ("harga kopi di warung sebelah brp?", "harga di warung lain", "id"),
    ("karyawan sy si Budi absen brp kali bulan ini", "absensi karyawan", "id"),
    ("berapa banyak jualan bulan depan agaknya?", "ramalan jualan bulan depan", "ms"),
    ("harga kopi kat kedai sebelah berapa?", "harga di kedai lain", "ms"),
    ("what will my sales be next month?", "next month's sales forecast", "en"),
    ("how much tax do I owe this year", "this year's tax", "en"),
    ("is it going to rain tomorrow", "tomorrow's weather", "en"),
]


@pytest.mark.parametrize("question,topic,lang", OUT_OF_SCOPE_QUESTIONS, ids=[q[0][:28] for q in OUT_OF_SCOPE_QUESTIONS])
async def test_out_of_scope_questions_are_refused_with_zero_fabricated_figures(question, topic, lang):
    composer = AsyncMock(return_value="SHOULD NOT BE CALLED")
    with (
        patch("app.ai.router.force_tool_call", new=AsyncMock(return_value=_call("out_of_scope", {"topic": topic}))),
        patch("app.ai.router.compose_reply", new=composer),
    ):
        routed = await handle_text(session=None, business=_business(), text=question)
    assert routed.intent == "refuse"
    composer.assert_not_awaited()                      # no model wrote this reply
    assert not DIGITS.search(routed.reply)             # zero figures, of any kind
    assert topic in routed.reply                       # it says what it cannot answer …
    assert capability_menu(lang) in routed.reply       # … and what it can
    opener = {"id": "Maaf, data usaha", "ms": "Maaf, data perniagaan", "en": "Sorry"}[lang]
    assert routed.reply.startswith(opener)             # in the owner's language


async def test_a_clarify_reply_that_invents_a_figure_is_replaced_by_a_refusal():
    """The classifier may write the clarify text itself. A figure in it has no
    facts behind it, so it never reaches the owner."""
    for invented in ("Penjualan hari ini sekitar Rp 500.000 ya!", "Kira-kira 1.250.000 bu", "Untung 12% kok", "Stok tinggal 30 ribu"):
        with (
            patch("app.ai.router.force_tool_call", new=AsyncMock(return_value=_call("clarify", {"reply": invented}))),
            patch("app.ai.router.compose_reply", new=AsyncMock()),
        ):
            routed = await handle_text(session=None, business=_business(), text="halo gimana hari ini")
        assert routed.intent == "refuse" and not DIGITS.search(routed.reply), invented
    # A plain greeting still passes through untouched.
    with (
        patch("app.ai.router.force_tool_call", new=AsyncMock(return_value=_call("clarify", {"reply": "Halo! Mau cek apa hari ini?"}))),
        patch("app.ai.router.compose_reply", new=AsyncMock()),
    ):
        routed = await handle_text(session=None, business=_business(), text="halo")
    assert routed.intent == "clarify" and routed.reply == "Halo! Mau cek apa hari ini?"


def test_the_figure_detector_knows_money_and_percentages_but_not_years_or_counts_in_words():
    for yes in ("Rp 500.000", "RM 12", "1.250.000", "30 ribu", "2,5 juta", "15rb", "12%", "sekitar 500k"):
        assert looks_like_a_figure(yes), yes
    for no in ("Halo! Mau cek apa hari ini?", "Selamat pagi bu", "Boleh, mau lihat penjualan minggu ini atau bulan ini?", "ok siap"):
        assert not looks_like_a_figure(no), no


def test_every_tool_is_offered_in_every_language_so_the_menu_cannot_fall_behind():
    """A tool added to tools.py without a phrase here breaks this test; the
    owner is always told the whole of what the assistant can do."""
    declared = {d.name for d in tools_module.TOOL_DECLARATIONS}
    assert declared <= set(CAPABILITIES), declared - set(CAPABILITIES)
    for name, phrases in CAPABILITIES.items():
        assert set(phrases) == set(LANGUAGES), name
        assert all(p.strip() and not DIGITS.search(p) for p in phrases.values()), name
    for lang in LANGUAGES:
        menu = capability_menu(lang, limit=100)
        assert all(CAPABILITIES[n][lang] in menu for n in declared)
    # The escape hatches are declared alongside the tools; `out_of_scope` never carries a reply text.
    names = [d.name for d in _declarations()]
    assert names[-2:] == ["out_of_scope", "clarify"] and "reply" not in (OUT_OF_SCOPE.parameters.properties or {})


def test_language_detection_follows_the_message_then_the_business():
    assert detect_language("berapa stok gula?", "en") == "id"
    assert detect_language("what's the stock of sugar", "id") == "en"
    assert detect_language("baki stok gula berapa banyak?", "id") == "ms"
    assert detect_language("???", "ms") == "ms"
    assert detect_language("", "xx") == "id"
    # Malay-preferring business, a word shared by id and ms: the business wins the tie.
    assert detect_language("berapa?", "ms") == "ms"
    reply = refusal_reply(_business("en"), "???", topic=None)
    assert reply.startswith("Sorry") and "What I can answer" in reply
    # A topic that itself smuggles a number is dropped, not echoed.
    assert not DIGITS.search(refusal_reply(_business(), "berapa?", topic="omzet 50 juta bulan depan"))
