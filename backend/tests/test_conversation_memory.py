"""P2-9: the last few turns of one owner number with one cafe are shown to the
classifier, so a follow-up ("kalau minggu lalu?", "kalau croissant?") is routed
with what it leaves out. The model is stubbed; the real path runs through
process_webhook_payload against the local database with the gateway captured.
"""
import json
import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai import memory
from app.ai.router import handle_text
from app.core.config import get_settings
from app.models import Business
from app.whatsapp import processor
from app.whatsapp.gateway import GatewayMessageIn, to_cloud_payload

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")


def _call(name, args=None):
    return SimpleNamespace(name=name, args=args or {})


def test_memory_keeps_the_last_three_fresh_turns_per_number_and_cafe(monkeypatch):
    key, other = memory.chat_key("6281", "cafe-a"), memory.chat_key("6281", "cafe-b")
    clock = [1000.0]
    monkeypatch.setattr(memory.time, "monotonic", lambda: clock[0])
    for i in range(5):
        memory.remember(key, f"q{i}", "get_stock", {"item_name": f"i{i}"}, f"Stok i{i} *{i} kg*.\nDetail")
    turns = memory.recent(key)
    assert [t["said"] for t in turns] == ["q2", "q3", "q4"] and turns[-1]["reply"] == "Stok i4 *4 kg*."
    assert memory.recent(other) == []                       # another cafe on the same number starts empty
    clock[0] += memory.TTL_SECONDS + 1
    assert memory.recent(key) == []                         # a long pause starts fresh


def test_the_classifier_sees_earlier_turns_only_when_there_are_some():
    assert memory.with_history("halo", []) == "halo"
    text = memory.with_history("kalau minggu lalu?", [
        {"said": "penjualan hari ini?", "tool": "get_sales_summary", "args": {"period": "today"}, "reply": "Penjualan hari ini *Rp 1.250.000*."},
    ])
    assert text.startswith("Earlier in this chat") and 'get_sales_summary {"period": "today"}' in text
    assert text.endswith("Current message: kalau minggu lalu?")


async def test_the_router_passes_history_and_reports_the_tool_and_args():
    business = SimpleNamespace(name="Kafe", business_type="cafe", language_preference="id")
    history = [{"said": "stok gula aren?", "tool": "get_stock", "args": {"item_name": "gula aren"}, "reply": "Stok gula aren *3 kg*."}]
    force = AsyncMock(return_value=_call("get_stock", {"item_name": "croissant"}))
    with (
        patch("app.ai.router.force_tool_call", new=force),
        patch.dict("app.ai.tools.TOOL_EXECUTORS", {"get_stock": AsyncMock(return_value={"items": []})}),
        patch("app.ai.router.compose_reply", new=AsyncMock(return_value="Stok croissant *12 pcs*.")),
    ):
        routed = await handle_text(None, business, "kalau croissant?", history=history)
        assert "Current message: kalau croissant?" in force.await_args.kwargs["message"]
        assert "follow-up" in force.await_args.kwargs["system"]
        await handle_text(None, business, "stok croissant?")
        assert force.await_args.kwargs["message"] == "stok croissant?"       # no history: unchanged input
        assert "follow-up" not in force.await_args.kwargs["system"]
    assert (routed.tool, routed.args) == ("get_stock", {"item_name": "croissant"})


# ── real path ────────────────────────────────────────────────────────────────

@pytest.fixture
async def cafe():
    if not DB_URL:
        pytest.fail("INTEGRATION_DATABASE_URL unset — local Postgres is not configured (roadmap §2)")
    engine = create_async_engine(DB_URL, connect_args={"statement_cache_size": 0})
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        biz = Business(name="Kopi Ingat", owner_phone=f"62893{uuid.uuid4().int % 10**8:08d}")
        s.add(biz)
        await s.commit()
    yield biz
    async with factory() as s:
        await s.delete(await s.get(Business, biz.id))
        await s.commit()
    await engine.dispose()


@pytest.fixture
def sent(monkeypatch):
    bodies: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content)["text"])
        return httpx.Response(200, json={"id": "sent-1"})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    settings = get_settings()
    with (
        patch.object(settings, "whatsapp_provider", "gateway"),
        patch.object(settings, "whatsapp_gateway_token", "gw-test-token-0123456789"),
        patch.object(settings, "whatsapp_gateway_url", "https://gateway.example.test"),
    ):
        yield bodies


async def _say(sender, body):
    msg = GatewayMessageIn.model_validate({"id": f"gw-{uuid.uuid4().hex}", "from": sender, "type": "text", "text": body})
    await processor.process_webhook_payload(to_cloud_payload(msg))


async def test_a_follow_up_reaches_the_classifier_with_the_turn_before_it(cafe, sent):
    calls = [_call("get_sales_summary", {"period": "today"}), _call("get_sales_summary", {"period": "last_week"})]
    force = AsyncMock(side_effect=calls)
    with (
        patch("app.ai.router.force_tool_call", new=force),
        patch.dict("app.ai.tools.TOOL_EXECUTORS", {"get_sales_summary": AsyncMock(return_value={"revenue": 1})}),
        patch("app.ai.router.compose_reply", new=AsyncMock(side_effect=["Penjualan hari ini *Rp 1.250.000*.", "Minggu lalu *Rp 8.400.000*."])),
    ):
        await _say(cafe.owner_phone, "penjualan hari ini?")
        await _say(cafe.owner_phone, "kalau minggu lalu?")
        await _say(f"62893{uuid.uuid4().int % 10**8:08d}", "halo")   # a stranger: ignored, and no memory for them
    first, second = (c.kwargs["message"] for c in force.await_args_list)
    assert first == "penjualan hari ini?"
    assert "- Owner: penjualan hari ini?" in second and "Answer began: Penjualan hari ini *Rp 1.250.000*." in second
    assert second.endswith("Current message: kalau minggu lalu?")
    assert sent == ["Penjualan hari ini *Rp 1.250.000*.", "Minggu lalu *Rp 8.400.000*."]
