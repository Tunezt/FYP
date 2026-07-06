from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.rag import receipt_to_document_text


def _receipt(**overrides):
    base = dict(
        parsed_data={
            "document_type": "receipt",
            "items": [
                {"name": "Gula Aren", "quantity": 2, "unit": "kg", "line_total": 76000},
                {"name": "Biji Arabica", "quantity": 5, "unit": "kg", "unit_price": 145000},
            ],
        },
        supplier="Toko Sinar Jaya",
        occurred_at=datetime(2026, 6, 20, tzinfo=timezone.utc),
        total_amount=Decimal("801000"),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_document_text_contains_supplier_date_items_total():
    text = receipt_to_document_text(_receipt())
    assert "Toko Sinar Jaya" in text
    assert "2026-06-20" in text
    assert "2 kg Gula Aren" in text
    assert "5 kg Biji Arabica" in text
    assert "801000" in text


def test_document_text_stock_ledger_wording():
    text = receipt_to_document_text(
        _receipt(parsed_data={"document_type": "stock_ledger", "items": []}, supplier=None,
                 total_amount=None)
    )
    assert text.startswith("Catatan stok")


def test_document_text_survives_empty_parse():
    text = receipt_to_document_text(
        _receipt(parsed_data={}, supplier=None, occurred_at=None, total_amount=None)
    )
    assert "Nota pembelian" in text


async def test_search_history_tool_shapes_no_match_answer():
    from app.ai.tools import TOOL_EXECUTORS

    with patch("app.services.rag.search_receipts", new=AsyncMock(return_value=[])):
        business = SimpleNamespace(id="b-1")
        facts = await TOOL_EXECUTORS["search_history"](None, business, {"query": "gula"})
    assert facts["matches"] == []
    assert "note" in facts


async def test_router_maps_search_history_to_rag_intent():
    from app.ai.router import handle_text

    call = SimpleNamespace(name="search_history", args={"query": "gula"})
    business = SimpleNamespace(name="X", business_type="cafe", language_preference="id")
    with (
        patch("app.ai.router.force_tool_call", new=AsyncMock(return_value=call)),
        patch.dict(
            "app.ai.tools.TOOL_EXECUTORS",
            {"search_history": AsyncMock(return_value={"matches": []})},
        ),
        patch("app.ai.router.compose_reply", new=AsyncMock(return_value="Belum pernah 🙂")),
    ):
        routed = await handle_text(session=None, business=business, text="pernah beli gula?")
    assert routed.intent == "rag"
