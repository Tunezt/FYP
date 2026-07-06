"""The Section 4 confirmation gate is a correctness requirement: a parse is
only committed without asking when confidence is high, nothing is ambiguous,
and there is actually something to save."""
from app.services.receipts import classify_reply_keyword, needs_confirmation, summarize_parse


def _parse(**overrides):
    base = {
        "document_type": "receipt",
        "supplier": "Toko Sinar",
        "date": "2026-07-06",
        "items": [{"name": "Gula Aren", "quantity": 2, "unit": "kg", "line_total": 76000}],
        "total_amount": 76000,
        "confidence": "high",
        "ambiguities": [],
    }
    base.update(overrides)
    return base


def test_high_confidence_clean_parse_skips_confirmation():
    assert not needs_confirmation(_parse())


def test_medium_confidence_requires_confirmation():
    assert needs_confirmation(_parse(confidence="medium"))


def test_low_confidence_requires_confirmation():
    assert needs_confirmation(_parse(confidence="low"))


def test_any_ambiguity_requires_confirmation_even_at_high_confidence():
    assert needs_confirmation(_parse(ambiguities=["qty for gula could be 2 or 7"]))


def test_empty_items_requires_confirmation():
    assert needs_confirmation(_parse(items=[]))


def test_affirmative_keywords_multilingual():
    for word in ["YA", "ya", "yes", "Betul", "ok", "Oke", "simpan", "boleh", "ya!"]:
        assert classify_reply_keyword(word) == "confirm", word


def test_negative_keywords_multilingual():
    for word in ["salah", "No", "batal", "gak", "tak", "bukan"]:
        assert classify_reply_keyword(word) == "deny", word


def test_longer_replies_are_not_keyword_matched():
    # Real corrections must go to the revision flow, not keyword matching.
    assert classify_reply_keyword("ya tapi gulanya 3 kg bukan 2") == "other"
    assert classify_reply_keyword("stok arabica berapa?") == "other"


def test_summary_lists_items_supplier_and_total():
    text = summarize_parse(_parse())
    assert "2 kg Gula Aren" in text
    assert "Toko Sinar" in text
    assert "76.000" in text


def test_summary_handles_empty_parse():
    assert "tidak ada item" in summarize_parse({"items": []})
