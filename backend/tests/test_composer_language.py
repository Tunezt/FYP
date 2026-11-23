"""fin-p0-7: English questions must be answered in English; the language is detected
in code and handed to the model as a hard instruction."""
import pytest

from app.ai.composer import _language_rule, detect_language


@pytest.mark.parametrize("text", [
    "how much did I sell today?", "what's my best selling item this week?", "show me my profit this month",
])
def test_english_is_detected(text):
    assert detect_language(text) == "English"
    assert "ENGLISH" in _language_rule(text, "id")


@pytest.mark.parametrize("text", ["stok es kopi susu berapa?", "bulan ini untung ga?", "jualan semalam"])
def test_indonesian_and_malay_are_detected(text):
    assert detect_language(text) == "Indonesian"
    assert "ENGLISH" not in _language_rule(text, "id")


@pytest.mark.parametrize("text", ["halo", "ok", "3", ""])
def test_unsure_falls_back_to_the_business_default(text):
    assert detect_language(text) is None
    assert "Default language: id" in _language_rule(text, "id")
