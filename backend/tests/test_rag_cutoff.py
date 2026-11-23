"""The distance cut-off keeps close receipts and drops far ones (value chosen by the sweep in docs/evaluation.md)."""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import rag


def _receipt(supplier):
    return SimpleNamespace(id=uuid.uuid4(), supplier=supplier, occurred_at=None, total_amount=None, parsed_data={})


async def _search(distances, **kw):
    rows = [(_receipt(f"s{i}"), d) for i, d in enumerate(distances)]
    session = MagicMock()
    session.execute = AsyncMock(return_value=SimpleNamespace(all=lambda: rows))
    with patch("app.services.rag.embed_text", new=AsyncMock(return_value=[0.0] * 768)):
        return await rag.search_receipts(session, uuid.uuid4(), "pernah beli salmon?", lexical=False, **kw)


def test_cutoff_value_is_the_swept_one():
    assert rag.MAX_DISTANCE == 0.45


@pytest.mark.asyncio
async def test_only_receipts_within_the_cutoff_come_back():
    docs = await _search([0.20, 0.45, 0.46, 0.60])
    assert [d.metadata["supplier"] for d in docs] == ["s0", "s1"]


@pytest.mark.asyncio
async def test_nothing_close_means_no_receipt_is_cited():
    assert await _search([0.48, 0.50, 0.60]) == []


@pytest.mark.asyncio
async def test_a_caller_can_widen_the_cutoff_for_the_sweep():
    assert len(await _search([0.20, 0.60], max_distance=2.0)) == 2


@pytest.mark.asyncio
async def test_a_close_receipt_without_the_asked_word_is_not_cited():
    # distance 0.20 is well inside the cut-off, but "salmon" is nowhere in the receipt text
    with patch("app.services.rag.embed_text", new=AsyncMock(return_value=[0.0] * 768)):
        session = MagicMock()
        session.execute = AsyncMock(return_value=SimpleNamespace(all=lambda: [(_receipt("Dairy Fresh"), 0.20)]))
        assert await rag.search_receipts(session, uuid.uuid4(), "pernah beli ikan salmon?") == []
        assert len(await rag.search_receipts(session, uuid.uuid4(), "nota dari Dairy Fresh ada ga")) == 1
