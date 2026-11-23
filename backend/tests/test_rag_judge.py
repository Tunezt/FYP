"""fin-rag-judge: a receipt is cited when the question's words are in it, or when one
cheap model call says it answers the question. A judge failure cites nothing and the
owner is told the receipts could not be checked (never a silent "not found")."""
from unittest.mock import AsyncMock, patch

import pytest

from app.services import rag

R1 = "Nota pembelian dari Toko Sinar Jaya tanggal 2026-10-02.\n10 kg gula pasir Rp 140000\n24 liter susu UHT Rp 456000"
R4 = "Nota pembelian dari Toko Sinar Jaya tanggal 2026-10-16.\n5 kg gula aren Rp 125000\n24 liter susu UHT Rp 456000"


@pytest.mark.asyncio
async def test_word_matched_receipts_need_no_model_call():
    judge = AsyncMock()
    with patch("app.services.rag.judge", new=judge):
        assert await rag.verify("pernah beli gula aren?", [R4]) == [True]
    judge.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_rest_go_to_one_judge_call_and_its_answer_decides():
    judge = AsyncMock(return_value=[True, False])
    with patch("app.services.rag.judge", new=judge):
        # "palm sugar" is in no receipt: both go to the judge, in one call
        assert await rag.verify("yg beli palm sugar 5 kilo di Sinar Jaya?", [R4, R1]) == [True, False]
    judge.assert_awaited_once()
    assert judge.await_args.args[1] == [R4, R1]


@pytest.mark.asyncio
async def test_only_the_unmatched_ones_are_judged():
    judge = AsyncMock(return_value=[False])
    with patch("app.services.rag.judge", new=judge):
        assert await rag.verify("pernah beli gula aren?", [R4, R1]) == [True, False]
    assert judge.await_args.args[1] == [R1]


@pytest.mark.asyncio
async def test_no_candidates_no_call():
    judge = AsyncMock()
    with patch("app.services.rag.judge", new=judge):
        assert await rag.verify("pernah beli salmon?", []) == []
    judge.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("model_says", [RuntimeError("503"), {"answers": [True]}, {"answers": "yes"}, {}])
async def test_a_failed_or_malformed_judge_is_a_failure_not_a_no(model_says):
    mock = AsyncMock(side_effect=model_says) if isinstance(model_says, Exception) else AsyncMock(return_value=model_says)
    with patch("app.ai.gemini.generate_json", new=mock):
        with pytest.raises(rag.ReceiptCheckFailed):
            await rag.judge("palm sugar?", [R4, R1])


@pytest.mark.asyncio
async def test_an_exhausted_quota_passes_through_to_the_quota_reply():
    from app.ai.gemini import QuotaExhausted

    with patch("app.ai.gemini.generate_json", new=AsyncMock(side_effect=QuotaExhausted(False))):
        with pytest.raises(QuotaExhausted):
            await rag.judge("palm sugar?", [R4, R1])


@pytest.mark.asyncio
async def test_the_tool_flags_a_failed_check_and_the_router_says_so_plainly():
    from types import SimpleNamespace

    from app.ai import tools
    from app.ai.router import RECEIPT_CHECK_FAILED_REPLY, handle_text

    with patch("app.services.rag.search_receipts", new=AsyncMock(side_effect=rag.ReceiptCheckFailed("503"))):
        facts = await tools.search_history(None, SimpleNamespace(id=None), {"query": "palm sugar"})
    assert facts == {"matches": [], "receipt_check_failed": True}

    biz = SimpleNamespace(name="Poernama Demo", business_type="cafe", language_preference="id")
    with (
        patch("app.ai.router.force_tool_call", new=AsyncMock(return_value=SimpleNamespace(name="search_history", args={"query": "x"}))),
        patch.dict("app.ai.tools.TOOL_EXECUTORS", {"search_history": AsyncMock(return_value=facts)}),
        patch("app.ai.router.compose_reply", new=AsyncMock()) as composer,
    ):
        routed = await handle_text(session=None, business=biz, text="pernah beli palm sugar?")
    composer.assert_not_awaited()
    assert routed.intent == "rag_unavailable" and routed.reply == RECEIPT_CHECK_FAILED_REPLY
    assert "belum bisa kucek" in routed.reply and "try again" in routed.reply


@pytest.mark.asyncio
async def test_the_judge_asks_the_cheap_model_first_and_reads_its_answer():
    mock = AsyncMock(return_value={"answers": [True, False]})
    with patch("app.ai.gemini.generate_json", new=mock):
        assert await rag.judge("palm sugar?", [R4, R1]) == [True, False]
    assert mock.await_args.kwargs["first_model"] == rag.JUDGE_MODEL == "gemini-3.5-flash-lite"
    assert "Receipt 1:" in mock.await_args.kwargs["message"] and "Receipt 2:" in mock.await_args.kwargs["message"]
