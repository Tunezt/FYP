"""M1-T3 — server-side guards on the vision parse (app/ai/vision.py::normalize_parse).

The M1-T2 baseline showed the model's own confidence never flagged a fabricated
unit or unit price: every silent error was a field the document did not show,
returned as `high` with no ambiguity. These tests pin the deterministic guards
that make such a parse either harmless (the guess is dropped before storage) or
gated (an arithmetic contradiction becomes an ambiguity the owner is asked about).

Pure functions, no model call, no database.
"""
from app.ai.vision import normalize_parse
from app.services.receipts import needs_confirmation


def _item(**over):
    base = {
        "name": "Gula pasir", "quantity": 2, "unit": "kg", "unit_written": True,
        "unit_price": 14000, "unit_price_written": True, "line_total": 28000,
    }
    base.update(over)
    return base


def _parse(items, total=None, **over):
    parsed = {
        "document_type": "receipt", "supplier": "", "date": "",
        "items": items,
        "total_amount": sum(i.get("line_total", 0) for i in items) if total is None else total,
        "confidence": "high", "ambiguities": [],
    }
    parsed.update(over)
    return parsed


def test_clean_parse_is_untouched_and_passes_gate():
    parsed = _parse([_item(), _item(name="Telur", quantity=1, unit_price=28000, line_total=28000)])
    out = normalize_parse(parsed)
    assert out["items"][0]["unit"] == "kg"
    assert out["items"][0]["unit_price"] == 14000
    assert out["ambiguities"] == []
    assert not needs_confirmation(out)


def test_unit_not_written_is_dropped():
    """The glare page: model wrote 'pack' / 'bottle' on lines with no unit."""
    parsed = _parse([_item(unit="bottle", unit_written=False)])
    out = normalize_parse(parsed)
    assert out["items"][0]["unit"] == ""
    # Not a gate trigger by itself — nothing wrong is stored (commit falls back to 'pcs').
    assert not needs_confirmation(out)


def test_derived_unit_price_is_kept_when_consistent_with_line_total():
    """line_total ÷ quantity is arithmetic, not a guess; keep it, flagged as derived."""
    parsed = _parse([_item(unit_price=14000, unit_price_written=False, line_total=28000)])
    out = normalize_parse(parsed)
    assert out["items"][0]["unit_price"] == 14000
    assert out["items"][0]["unit_price_written"] is False
    assert out["ambiguities"] == []


def test_unit_price_with_no_written_basis_is_zeroed():
    """No unit price written AND no line total to derive from: a pure guess."""
    parsed = _parse([_item(unit_price=14000, unit_price_written=False, line_total=0)], total=0)
    out = normalize_parse(parsed)
    assert out["items"][0]["unit_price"] == 0


def test_inconsistent_line_arithmetic_becomes_ambiguity_and_gates():
    parsed = _parse([_item(quantity=2, unit_price=14000, line_total=30000)], total=30000)
    out = normalize_parse(parsed)
    assert len(out["ambiguities"]) == 1
    assert "Gula pasir" in out["ambiguities"][0]
    assert needs_confirmation(out)


def test_total_not_matching_lines_becomes_ambiguity_and_gates():
    """The neat page writes 645.500 while its lines sum to 635.500."""
    parsed = _parse([_item(), _item(name="Telur", quantity=1, unit_price=28000, line_total=28000)],
                    total=66000)
    out = normalize_parse(parsed)
    assert any("tidak sama dengan total" in a for a in out["ambiguities"])
    assert needs_confirmation(out)


def test_sum_check_skipped_when_no_line_totals():
    """A stock count with quantities only must not be gated for lacking money."""
    parsed = _parse([_item(unit_price=0, unit_price_written=False, line_total=0)], total=0,
                    document_type="stock_ledger")
    out = normalize_parse(parsed)
    assert out["ambiguities"] == []
    assert not needs_confirmation(out)


def test_one_rupiah_tolerance():
    parsed = _parse([_item(quantity=3, unit_price=3333, line_total=10000)], total=10000)
    assert normalize_parse(parsed)["ambiguities"] == []


def test_legacy_parse_without_written_flags_is_treated_as_written():
    """Pending payloads and revisions from before M1-T3 carry no *_written keys."""
    legacy = _parse([{"name": "Beras", "quantity": 2, "unit": "kg", "unit_price": 14000, "line_total": 28000}])
    out = normalize_parse(legacy)
    assert out["items"][0]["unit"] == "kg"
    assert out["items"][0]["unit_price"] == 14000
    assert not needs_confirmation(out)


def test_normalize_is_idempotent():
    parsed = _parse([_item(quantity=2, unit_price=14000, line_total=30000)], total=30000)
    once = normalize_parse(parsed)
    twice = normalize_parse(once)
    assert twice["ambiguities"] == once["ambiguities"]
    assert len(twice["ambiguities"]) == 1


def test_missing_sections_get_defaults():
    out = normalize_parse({})
    assert out["items"] == [] and out["ambiguities"] == []
    assert out["confidence"] == "low" and out["document_type"] == "other"
    assert needs_confirmation(out)
