"""Lexical guard: a match counts only if the question's key words occur in the receipt text."""
import pytest

from app.services.rag import key_words, mentions

R5 = "Nota pembelian dari PT Dairy Fresh tanggal 2026-10-20.\n30 liter susu segar Rp 540000\n5 kg krimer Rp 200000"
R7 = "Nota pembelian dari Pasar Induk.\n3 kg selada Rp 60000\n4 kg tomat Rp 80000"
R1 = "Nota pembelian dari Toko Sinar Jaya.\n10 kg gula pasir\n24 liter susu UHT"


def test_key_words_drop_the_question_around_the_item():
    assert key_words("kapan terakhir beli susu UHT?") == ["susu", "uht"]
    assert key_words("Have I bought cheese before") == ["cheese"]
    assert key_words("kapan terakhir?") == []


@pytest.mark.parametrize("q,text,ok", [
    ("pernah beli ikan salmon?", R5, False),
    ("kapan terakhir beli susu uht", R1, True),
    ("kapan terakhir beli susu uht", R5, False),  # susu segar, not UHT
    ("nota dari Dairy Fresh ada ga", R5, True),
    ("nota dari Toko Bangunan Jaya ada ga", R1, False),  # shares "Toko" and "Jaya" only
    ("beli sayur di mana ya", R7, True),  # category word -> selada / tomat
    ("have I bought cheese before", "1 kg keju", True),  # English -> Indonesian
    ("pernah beli gula aren?", R1, False),
    ("beli arabika di mana", "5 kg kopi arabica", True),  # one-letter spelling slip
    ("KÉJU ada?", "2 kg keju", True),  # case and accents ignored
    ("kapan terakhir?", R1, True),  # names nothing: nothing to check
])
def test_mentions(q, text, ok):
    assert mentions(text, key_words(q)) is ok
