"""P2-2: one voice on every WhatsApp reply. The model is told who it is and how
to address the owner (composer and the classifier's clarify text share the rule),
and the formatter in front of every send keeps at most one decorative emoji. An
emoji that starts a line is a marker (the cafe header, ✅/❓ on a receipt
read-back, ⚠️ on a warning) and stays. Model mocked; no database."""
import asyncio
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.ai import composer
from app.ai.composer import VOICE_RULE
from app.ai.router import CLARIFY
from app.whatsapp.markup import _EMOJI, _MARKER, to_whatsapp


def _decorations(text: str) -> int:
    return sum(len(_EMOJI.findall(line[m.end():] if (m := _MARKER.match(line)) else line)) for line in text.split("\n"))


def test_the_composer_and_the_clarify_text_share_one_voice():
    b = SimpleNamespace(name="Kafe", business_type="cafe", language_preference="id")
    with patch("app.ai.composer.generate_text", new=AsyncMock(return_value="ok")) as g:
        asyncio.run(composer.compose_reply(b, "stok gula?", "get_stock", {"items": []}))
    system = g.await_args.kwargs["system"]
    assert VOICE_RULE in system and "at most one in the whole reply" in system
    assert VOICE_RULE in CLARIFY.parameters.properties["reply"].description
    for word in ("aku", "kamu", "Boss", "Kak"):
        assert word in VOICE_RULE


@pytest.mark.parametrize("raw, expected", [
    # The audit's drift: three decorations become one.
    ("Siap Boss! 😄 Penjualan hari ini *Rp 1.250.000* 🎉\n\n- 42 transaksi 🔥",
     "Siap! 😄 Penjualan hari ini *Rp 1.250.000*\n\n- 42 transaksi"),
    # Markers at the start of a line are not decoration.
    ("🏪 Poernama Demo\nOke, sudah kucatat ✅", "🏪 Poernama Demo\nOke, sudah kucatat ✅"),
    ("✅ 5 kg Gula Aren\n✅ 12 liter Susu UHT\n❓ 2 Biji — cek\n\n⚠️ *Nota ini mirip.*\nBalas YA 🙏",
     "✅ 5 kg Gula Aren\n✅ 12 liter Susu UHT\n❓ 2 Biji — cek\n\n⚠️ *Nota ini mirip.*\nBalas YA 🙏"),
    ("- ✅ done 🎉 🎉", "- ✅ done 🎉"),
    # Joined and toned emoji count as one; punctuation closes up behind a removed one.
    ("Mantap 👍🏽 tim 👨‍👩‍👧 hebat ❤️ !", "Mantap 👍🏽 tim hebat!"),
    # Not emoji: arrows, middle dots, ellipsis, dashes.
    ("Harga → naik · 1…2 — ok", "Harga → naik · 1…2 — ok"),
])
def test_one_decorative_emoji_per_message(raw, expected):
    out = to_whatsapp(raw)
    assert out == expected
    assert _decorations(out) <= 1
    assert to_whatsapp(out) == out   # idempotent, like the rest of the formatter


def test_every_fixed_reply_in_the_bot_keeps_to_one_decoration():
    """The fixed texts in app/ are already within the rule; the formatter never has
    to cut them (a cut would read oddly)."""
    from pathlib import Path

    strings = re.compile(r'"([^"\n]*)"')
    for path in Path("app").rglob("*.py"):
        if "eval" in path.parts or path.name == "markup.py":   # markup.py holds the emoji ranges themselves
            continue
        for literal in strings.findall(path.read_text(encoding="utf-8")):
            assert _decorations(literal) <= 1, (path, literal)


@pytest.mark.parametrize("raw, expected", [
    ("Siap Boss! Stok aman.", "Siap! Stok aman."),
    ("Halo Kak, stok gula aman.", "Halo, stok gula aman."),
    ("Kak, stok aman", "stok aman"),
    ("Oke bos.", "Oke."),
    ("Makasih ya bosku!", "Makasih ya!"),
    ("Pagi kak!\nStok aman", "Pagi!\nStok aman"),
    ("Sudah dicatat, kak", "Sudah dicatat"),
    # Names stay: a nickname word inside an item or supplier name is not an address.
    ("Kopi Kakak Tua 2 pcs", "Kopi Kakak Tua 2 pcs"),
    ("Bos Besar hadir", "Bos Besar hadir"),
    ("- Roti Gan", "- Roti Gan"),
])
def test_the_owner_is_never_called_boss_or_kak(raw, expected):
    assert to_whatsapp(raw) == expected
