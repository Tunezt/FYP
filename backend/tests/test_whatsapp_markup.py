"""fin-wa-format: every outgoing WhatsApp text is WhatsApp markup, never Markdown
that the phone would show literally, and Rupiah is written the Indonesian way."""
import json
import re

import httpx
import pytest

from app.whatsapp.markup import to_whatsapp

LITERAL_MARKDOWN = re.compile(r"\*\*|^#{1,6} |\]\(https?://|^\s*\|.*\|\s*$", re.M)


@pytest.mark.parametrize("raw, expected", [
    ("Untung **Rp 4.612.000** bulan ini", "Untung *Rp 4.612.000* bulan ini"),
    ("__Penting__: stok habis", "*Penting*: stok habis"),
    ("***Total***", "*Total*"),
    ("~~Rp 20.000~~ Rp 15.000", "~Rp 20.000~ Rp 15.000"),
    ("## Ringkasan hari ini", "*Ringkasan hari ini*"),
    ("# **Laporan**", "*Laporan*"),
    ("Cek [dashboard](https://poernama.app/stok)", "Cek dashboard: https://poernama.app/stok"),
    ("[https://a.id/x](https://a.id/x)", "https://a.id/x"),
    ("* Croissant 12 pcs", "- Croissant 12 pcs"),
    ("• Gula Aren 3 kg", "- Gula Aren 3 kg"),
    ("   - nested", "- nested"),
    ("1) Es Kopi Susu", "1. Es Kopi Susu"),
    ("*bold* di awal baris", "*bold* di awal baris"),   # WhatsApp bold, not a bullet
    ("   + Extra shot (Rp 5.000)", "   + Extra shot (Rp 5.000)"),   # receipt modifier line stays
])
def test_markdown_becomes_whatsapp_markup(raw, expected):
    assert to_whatsapp(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("Rp1250000", "Rp 1.250.000"),
    ("Rp 1250000", "Rp 1.250.000"),
    ("Rp 1,250,000", "Rp 1.250.000"),
    ("Rp. 25.232", "Rp 25.232"),
    ("Rp 25.232,00", "Rp 25.232"),
    ("Rp 12.500,50", "Rp 12.500,50"),
    ("Rp 684.000", "Rp 684.000"),
    ("Rp 500", "Rp 500"),
    ("sekitar Rp 1,5 juta", "sekitar Rp 1,5 juta"),       # a decimal, not thousands: left as written
    ("Rp 145.000 → Rp 165.000 per kg.", "Rp 145.000 → Rp 165.000 per kg."),
])
def test_rupiah_is_written_the_indonesian_way(raw, expected):
    assert to_whatsapp(raw) == expected


def test_a_table_becomes_lines():
    raw = "| Menu | Terjual |\n|---|---:|\n| Es Kopi Susu | 26 |\n| Croissant | 9 |"
    assert to_whatsapp(raw) == "Menu · Terjual\nEs Kopi Susu · 26\nCroissant · 9"


def test_spacing_is_tidied_and_rules_dropped():
    assert to_whatsapp("Halo  \n\n\n\nStok aman\n---\nSelesai\n") == "Halo\n\nStok aman\n\nSelesai"


def test_code_fences_are_left_alone():
    raw = "Kode:\n```**tetap** # sama```\n**ini** berubah"
    assert to_whatsapp(raw) == "Kode:\n```**tetap** # sama```\n*ini* berubah"


def test_a_full_model_answer_has_no_literal_markdown_and_is_idempotent():
    raw = (
        "## Laba bulan ini\n\n**Untung Rp4612000**, Bos.\n\n"
        "* Pendapatan: **Rp 12,816,000**\n* Pengeluaran: Rp 8.204.000\n\n"
        "| Item | Qty |\n|---|---|\n| Kopi | 2 |\n\nDetail di [dashboard](https://x.id)."
    )
    once = to_whatsapp(raw)
    assert not LITERAL_MARKDOWN.search(once), once
    assert "*Untung Rp 4.612.000*" in once and "- Pendapatan: *Rp 12.816.000*" in once
    assert to_whatsapp(once) == once


def test_fixed_texts_pass_through_unchanged():
    from app.whatsapp.receipts import NOT_FOUND

    for text in (
        "⚠️ Peringatan stok menipis untuk Kopi Senja: Arabica tinggal ±2 hari lagi. Balas pesan ini untuk detail.",
        "482913 adalah kode verifikasi Anda.",
        "Oke, sekarang pakai data *Poernama* ✅\nKetik *ganti toko* kapan saja untuk pindah.",
        "1× Kopi Susu — Rp 22.000\n   _tanpa gula_\n*Total: Rp 24.200*",
        NOT_FOUND,
    ):
        assert to_whatsapp(text) == text


def test_empty_text_is_left_empty():
    assert to_whatsapp("") == ""


async def test_the_gateway_send_applies_it(monkeypatch):
    """The one shared place: whatever a caller hands send_text is fixed before it leaves."""
    from app.core.config import get_settings
    from app.whatsapp import client as wa

    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    real = httpx.AsyncClient
    monkeypatch.setattr(wa.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    settings = get_settings()
    monkeypatch.setattr(settings, "whatsapp_provider", "gateway")
    monkeypatch.setattr(settings, "whatsapp_gateway_url", "http://gw.test")
    monkeypatch.setattr(settings, "whatsapp_gateway_token", "real-token")
    await wa.send_text("6281234567890", "## Stok\n**Croissant** Rp12000")
    assert sent[0]["text"] == "*Stok*\n*Croissant* Rp 12.000"


async def test_the_cloud_send_applies_it(monkeypatch):
    from app.whatsapp import client as wa

    payloads: list[dict] = []

    async def fake_post(payload, log_hint):
        payloads.append(payload)

    monkeypatch.setattr(wa, "_gateway", lambda: False)
    monkeypatch.setattr(wa, "_post_message", fake_post)
    await wa.send_text("6281234567890", "**Rp 1,250,000**")
    assert payloads[0]["text"]["body"] == "*Rp 1.250.000*"


def test_several_nightly_alerts_become_a_list_on_the_gateway():
    from app.whatsapp.client import _alert_text

    text = _alert_text("id", "anomali penjualan & stok menipis", "Kopi Senja",
                       "Penjualan Senin Rp 1.413.000 — jauh di atas normal\n"
                       "Budi membatalkan 19 dari 113 transaksi (17%); kasir lain 0%. Perlu dicek.")
    assert text == (
        "⚠️ *2 peringatan untuk Kopi Senja*\n\n"
        "- Penjualan Senin *Rp 1.413.000* — jauh di atas normal\n"
        "- Budi membatalkan 19 dari 113 transaksi (*17%*); kasir lain 0%. Perlu dicek\n\n"   # its own "; " stays
        "Balas pesan ini untuk detail."
    )
    # one key figure per line: days left first, then a percentage, then an amount
    from app.whatsapp.client import _bold_key_figure

    assert _bold_key_figure("Margin kotor 7 hari terakhir 47.0% — turun") == "Margin kotor 7 hari terakhir *47.0%* — turun"
    assert _bold_key_figure("Croissant: stok habis dalam ±4 hari (12 pcs)") == "Croissant: stok habis dalam *±4 hari* (12 pcs)"
    assert _bold_key_figure("Harga naik 13.8%: Rp 145.000 → Rp 165.000") == "Harga naik *13.8%*: Rp 145.000 → Rp 165.000"
    assert _bold_key_figure("Backup otomatis database GAGAL") == "Backup otomatis database GAGAL"
    # the overflow line counts the alerts it stands for
    assert _alert_text("id", "x", "Kopi Senja", "A\nB\n(+3 peringatan lain)").startswith("⚠️ *5 peringatan untuk Kopi Senja*")
    # one alert keeps the approved one-line wording
    assert _alert_text("id", "stok menipis", "Kopi Senja", "Arabica tinggal ±2 hari lagi") == (
        "⚠️ Peringatan stok menipis untuk Kopi Senja: Arabica tinggal ±2 hari lagi. Balas pesan ini untuk detail."
    )


async def test_cloud_template_parameters_carry_no_newlines(monkeypatch):
    """Delivery hands several alerts over one per line; Meta refuses newlines in parameters."""
    from app.whatsapp import client as wa

    payloads: list[dict] = []

    async def fake_post(payload, log_hint):
        payloads.append(payload)

    monkeypatch.setattr(wa, "_gateway", lambda: False)
    monkeypatch.setattr(wa, "_post_message", fake_post)
    await wa.send_template("6281234567890", "business_alert", ["a & b", "Kopi Senja", "Satu\nDua"])
    params = payloads[0]["template"]["components"][0]["parameters"]
    assert [p["text"] for p in params] == ["a & b", "Kopi Senja", "Satu; Dua"]


def test_the_composer_asks_for_whatsapp_markup_not_markdown():
    from app.ai.composer import _SYSTEM

    prompt = _SYSTEM.lower()
    assert "*bold*" in prompt and "never **" in prompt and "first line" in prompt
