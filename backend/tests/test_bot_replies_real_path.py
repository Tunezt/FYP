"""P2 polish of the WhatsApp assistant, checked on the real path: a message goes
through process_webhook_payload (owner lookup, pending check, router, the
formatter in send_text) against the local database, with the model call
stubbed and the gateway's HTTP call captured, never sent.
"""
import asyncio
import json
import os
import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai import tools as tools_module
from app.ai.refusal import CAPABILITIES
from app.core.config import get_settings
from app.models import Business
from app.whatsapp import processor
from app.whatsapp.gateway import GatewayMessageIn, to_cloud_payload

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")


def _number() -> str:
    return f"62896{uuid.uuid4().int % 10**8:08d}"


@pytest.fixture
async def session_factory():
    if not DB_URL:
        pytest.fail("INTEGRATION_DATABASE_URL unset — local Postgres is not configured (roadmap §2)")
    engine = create_async_engine(DB_URL, connect_args={"statement_cache_size": 0})
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def cafe(session_factory):
    async with session_factory() as s:
        business = Business(name="Kopi Uji P2", owner_phone=_number())
        s.add(business)
        await s.commit()
    yield business
    async with session_factory() as s:
        await s.delete(await s.get(Business, business.id))
        await s.commit()


@pytest.fixture
def sent(monkeypatch):
    bodies: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content)["text"])
        return httpx.Response(200, json={"id": "sent-1"})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return bodies


@contextmanager
def gateway():
    settings = get_settings()
    with (
        patch.object(settings, "whatsapp_provider", "gateway"),
        patch.object(settings, "whatsapp_gateway_token", "gw-test-token-0123456789"),
        patch.object(settings, "whatsapp_gateway_url", "https://gateway.example.test"),
    ):
        yield


def _text(sender: str, body: str, message_id: str | None = None) -> dict:
    msg = GatewayMessageIn.model_validate(
        {"id": message_id or f"gw-{uuid.uuid4().hex}", "from": sender, "type": "text", "text": body}
    )
    return to_cloud_payload(msg)


async def test_p2_1_an_out_of_scope_question_gets_the_whole_grouped_menu(cafe, sent):
    call = SimpleNamespace(name="out_of_scope", args={"topic": "cuaca besok"})
    with gateway(), patch("app.ai.router.force_tool_call", new=AsyncMock(return_value=call)):
        await processor.process_webhook_payload(_text(cafe.owner_phone, "besok hujan ga?"))
    assert len(sent) == 1
    reply = sent[0]
    assert reply.startswith("Maaf, data usaha yang aku pegang belum mencakup cuaca besok")
    declared = [d.name for d in tools_module.TOOL_DECLARATIONS]
    assert all(CAPABILITIES[n]["id"] in reply for n in declared)       # 15 of 15, not 8
    assert "- *Penjualan:*" in reply and "- *Stok:*" in reply and "- *Belanja:*" in reply and "- *Kirim:*" in reply


async def test_p2_2_a_drifting_model_answer_reaches_the_phone_with_one_emoji(cafe, sent):
    call = SimpleNamespace(name="get_sales_summary", args={"period": "today"})
    drift = "Siap Boss! Penjualan hari ini *Rp 1.250.000* 🎉🎉\n\n- 42 transaksi 🔥\n⚠️ Stok gula tipis 😅"
    with (
        gateway(),
        patch("app.ai.router.force_tool_call", new=AsyncMock(return_value=call)),
        patch("app.ai.router.compose_reply", new=AsyncMock(return_value=drift)) as compose,
    ):
        await processor.process_webhook_payload(_text(cafe.owner_phone, "penjualan hari ini?"))
    assert compose.await_count == 1
    assert sent == ["Siap! Penjualan hari ini *Rp 1.250.000* 🎉\n\n- 42 transaksi\n⚠️ Stok gula tipis"]


async def test_p2_12_a_redelivered_message_is_answered_once_even_after_a_restart(session_factory, cafe, sent):
    import importlib

    from sqlalchemy import select, text

    from app.models import ProcessedMessage, RequestLog

    message_id = f"wamid.p2-12-{uuid.uuid4().hex}"
    call = SimpleNamespace(name="clarify", args={"reply": "Halo! Mau cek apa hari ini?"})
    with gateway(), patch("app.ai.router.force_tool_call", new=AsyncMock(return_value=call)) as router:
        await processor.process_webhook_payload(_text(cafe.owner_phone, "halo", message_id))
        importlib.reload(processor)   # what a restart or a deploy does to anything kept in memory
        await processor.process_webhook_payload(_text(cafe.owner_phone, "halo", message_id))
        # Two deliveries racing each other: still one answer.
        racing = f"wamid.p2-12-{uuid.uuid4().hex}"
        await asyncio.gather(*(processor.process_webhook_payload(_text(cafe.owner_phone, "halo", racing)) for _ in range(2)))
    assert router.await_count == 2 and len(sent) == 2
    async with session_factory() as s:
        claimed = (await s.execute(select(ProcessedMessage.message_id).where(ProcessedMessage.message_id.in_([message_id, racing])))).scalars().all()
        await s.execute(text("select set_config('app.current_business_id', :b, true)"), {"b": str(cafe.id)})
        logs = (await s.execute(select(RequestLog).where(RequestLog.business_id == cafe.id))).scalars().all()
    assert sorted(claimed) == sorted([message_id, racing])
    assert len(logs) == 2   # one request log per message, not per delivery


async def test_p2_12_a_new_message_id_is_still_answered(cafe, sent):
    call = SimpleNamespace(name="clarify", args={"reply": "Halo!"})
    with gateway(), patch("app.ai.router.force_tool_call", new=AsyncMock(return_value=call)):
        await processor.process_webhook_payload(_text(cafe.owner_phone, "halo"))
        await processor.process_webhook_payload(_text(cafe.owner_phone, "halo"))
    assert sent == ["Halo!", "Halo!"]
