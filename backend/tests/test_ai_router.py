"""Routing logic with the Gemini call mocked — proves classification outcomes
map to the right executor / fallback without a live model."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.ai.router import handle_text


def _business():
    return SimpleNamespace(
        name="Kopi Kenangan Senja", business_type="cafe", language_preference="id"
    )


def _call(name, args=None):
    return SimpleNamespace(name=name, args=args or {})


async def test_tool_intent_executes_and_composes():
    facts = {"found": True, "items": [{"name": "Biji Arabica", "current_stock": 8.0}]}
    executor = AsyncMock(return_value=facts)
    with (
        patch("app.ai.router.force_tool_call", new=AsyncMock(return_value=_call("get_stock", {"item_name": "arabica"}))),
        patch.dict("app.ai.tools.TOOL_EXECUTORS", {"get_stock": executor}),
        patch("app.ai.router.compose_reply", new=AsyncMock(return_value="Sisa 8 kg ✅")) as composer,
    ):
        routed = await handle_text(session=None, business=_business(), text="stok arabica berapa?")

    assert routed.intent == "get_stock"
    assert routed.reply == "Sisa 8 kg ✅"
    executor.assert_awaited_once()
    assert executor.await_args.args[2] == {"item_name": "arabica"}
    composer.assert_awaited_once()
    assert composer.await_args.args[3] == facts


async def test_clarify_uses_model_supplied_reply_without_composer():
    with (
        patch("app.ai.router.force_tool_call", new=AsyncMock(return_value=_call("clarify", {"reply": "Halo! Mau cek apa hari ini?"}))),
        patch("app.ai.router.compose_reply", new=AsyncMock()) as composer,
    ):
        routed = await handle_text(session=None, business=_business(), text="halo")

    assert routed.intent == "clarify"
    assert "Halo" in routed.reply
    composer.assert_not_awaited()


async def test_no_function_call_falls_back_to_clarify():
    with (
        patch("app.ai.router.force_tool_call", new=AsyncMock(return_value=None)),
        patch("app.ai.router.compose_reply", new=AsyncMock(return_value="Maaf, maksudnya gimana?")),
    ):
        routed = await handle_text(session=None, business=_business(), text="???")

    assert routed.intent == "clarify"


async def test_unknown_tool_name_falls_back_to_clarify():
    with (
        patch("app.ai.router.force_tool_call", new=AsyncMock(return_value=_call("hallucinated_tool"))),
        patch("app.ai.router.compose_reply", new=AsyncMock(return_value="Bisa dijelaskan lagi?")),
    ):
        routed = await handle_text(session=None, business=_business(), text="xyz")

    assert routed.intent == "clarify"
