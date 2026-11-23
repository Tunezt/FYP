"""fin-n2-photo-before-pick: a photo sent from a number on several cafes before a
cafe is picked used to be dropped (the list went out and the owner resent it).
Now one photo per number is held for 15 minutes and read right after the pick,
for the cafe picked. The real pipeline runs (DB lookup, pick, pending row); the
vision call is mocked and outbound HTTP is captured."""
import base64
import json
import os
import uuid
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models import Business, PendingConfirmation
from app.whatsapp import processor
from app.whatsapp.gateway import GatewayMessageIn, to_cloud_payload

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")
NOTA = {
    "document_type": "receipt", "supplier": "Toko Manis", "date": "2026-11-20", "confidence": "high",
    "ambiguities": [], "total_amount": 100000,
    "items": [{"name": "Gula Aren", "quantity": 4, "unit": "kg", "unit_price": 25000, "line_total": 100000}],
}


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
async def two_cafes(session_factory):
    mine = _number()
    async with session_factory() as s:
        resto = Business(name="A Resto Ibu", owner_phone=_number(),
                         extra_owner_numbers=[{"name": "Anak", "phone": mine, "added_at": "2026-11-23T00:00:00+00:00"}])
        demo = Business(name="B Demo", owner_phone=mine)
        s.add_all([resto, demo])
        await s.commit()
        ids = {"resto": resto.id, "demo": demo.id}
    yield mine, ids
    async with session_factory() as s:
        for bid in ids.values():
            await s.delete(await s.get(Business, bid))
        await s.commit()
    processor._held_photos.clear()


@pytest.fixture
def sent(monkeypatch):
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "sent-1"})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return bodies


@pytest.fixture
def vision():
    settings = get_settings()
    parse = AsyncMock(return_value=NOTA)
    with (
        patch.object(settings, "whatsapp_provider", "gateway"),
        patch.object(settings, "whatsapp_gateway_token", "gw-test-token-0123456789"),
        patch.object(settings, "whatsapp_gateway_url", "https://gateway.example.test"),
        patch("app.ai.vision.parse_business_document", new=parse),
        patch("app.whatsapp.storage.upload_receipt_image", new=AsyncMock(return_value="r/x.jpg")),
    ):
        processor._held_photos.clear()
        yield parse


async def _send(sender: str, **fields) -> None:
    msg = GatewayMessageIn.model_validate({"id": f"gw-{uuid.uuid4().hex}", "from": sender, **fields})
    await processor.process_webhook_payload(to_cloud_payload(msg))


async def _photo(sender: str, data: bytes = b"jpg-1") -> None:
    await _send(sender, type="image", mime_type="image/jpeg", media_b64=base64.b64encode(data).decode())


async def _pending_for(session_factory, business_id):
    async with session_factory() as s:
        await s.execute(text("select set_config('app.current_business_id', :b, true)"), {"b": str(business_id)})
        return (await s.execute(select(PendingConfirmation))).scalars().all()


async def test_photo_before_the_pick_is_read_for_the_cafe_picked(session_factory, two_cafes, sent, vision):
    mine, ids = two_cafes
    await _photo(mine)
    assert vision.await_count == 0                                   # no cafe's data before the pick
    assert "1. A Resto Ibu" in sent[-1]["text"] and "Fotonya kusimpan" in sent[-1]["text"]

    await _send(mine, type="text", text="2")
    assert vision.await_count == 1
    texts = [b["text"] for b in sent]
    assert texts[-3].startswith("Oke, sekarang pakai data *B Demo*")
    assert "Fotonya sudah kuterima" in texts[-2]
    assert texts[-1].startswith("🏪 B Demo\n") and "Ini nota belanja" in texts[-1]
    assert len(await _pending_for(session_factory, ids["demo"])) == 1
    assert await _pending_for(session_factory, ids["resto"]) == []

    await _send(mine, type="text", text="halo")                      # nothing left to replay
    assert vision.await_count == 1


async def test_an_unclear_reply_keeps_the_photo_waiting_and_the_list_is_sent_once(two_cafes, sent, vision):
    mine, _ids = two_cafes
    await _photo(mine)
    await _send(mine, type="text", text="e")
    assert vision.await_count == 0
    assert [b["text"].startswith("Belum jelas") for b in sent[-2:]] == [False, True]   # photo's list, then the unclear one
    n = len(sent)
    await _send(mine, type="text", text="demo")
    assert vision.await_count == 1 and len(sent) > n


async def test_only_the_latest_photo_is_kept(two_cafes, sent, vision):
    mine, _ids = two_cafes
    await _photo(mine, b"first")
    await _photo(mine, b"second")
    await _send(mine, type="text", text="1")
    assert vision.await_count == 1 and vision.await_args.args[0] == b"second"


async def test_a_photo_expires_after_fifteen_minutes(two_cafes, sent, vision):
    mine, _ids = two_cafes
    await _photo(mine)
    with patch.object(processor, "_PHOTO_WAIT_SECONDS", -1):
        await _send(mine, type="text", text="2")
    assert vision.await_count == 0 and sent[-1]["text"].startswith("Oke, sekarang pakai data")
