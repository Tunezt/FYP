"""Audit P0-6: a correction must not keep the old total."""
import json

import pytest

from app.ai import vision

BEFORE = {
    "document_type": "receipt", "confidence": "high", "ambiguities": [], "total_amount": 645500,
    "items": [
        {"name": "Gula Aren", "quantity": 5, "unit": "kg", "unit_price": 15000, "line_total": 75000},
        {"name": "Biji Kopi", "quantity": 3, "unit": "kg", "unit_price": 190166.67, "line_total": 570500},
    ],
}


def _revised(total, gula_qty=3, gula_total=45000):
    r = json.loads(json.dumps(BEFORE))
    r["items"][0].update(quantity=gula_qty, line_total=gula_total)
    r["total_amount"] = total
    return {"unrelated": False, "parsed": r}


@pytest.mark.parametrize("model_total", [645500, 0, 615500])
async def test_total_is_sum_of_lines(monkeypatch, model_total):
    async def fake(**_):
        return json.dumps(_revised(model_total))
    monkeypatch.setattr(vision, "generate_text", fake)
    out = await vision.revise_parse(BEFORE, "gulanya 3 kg bukan 5")
    assert out["parsed"]["total_amount"] == 615500
    assert not any("tidak sama dengan total yang tertulis" in a for a in out["parsed"]["ambiguities"])


async def test_unrelated_keeps_total(monkeypatch):
    async def fake(**_):
        return json.dumps({"unrelated": True, "parsed": BEFORE})
    monkeypatch.setattr(vision, "generate_text", fake)
    out = await vision.revise_parse(BEFORE, "omzet hari ini?")
    assert out["parsed"]["total_amount"] == 645500


async def test_flags_when_first_parse_disagreed(monkeypatch):
    before = json.loads(json.dumps(BEFORE))
    before["total_amount"] = 700000  # tax/discount on the page, not in the lines
    async def fake(**_):
        return json.dumps(_revised(700000))
    monkeypatch.setattr(vision, "generate_text", fake)
    out = await vision.revise_parse(before, "gulanya 3 kg")
    assert out["parsed"]["total_amount"] == 615500
    assert any("dihitung ulang" in a for a in out["parsed"]["ambiguities"])
