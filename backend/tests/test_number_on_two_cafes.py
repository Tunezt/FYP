"""wa-gw-5: one WhatsApp number on two cafes.

The owner's number stays an extra assistant number on the family restaurant
and also owns the presentation demo. The bot used to pick one of the two with
an unordered LIMIT 1. Now it asks once with a numbered list, remembers the
pick (businesses.active_owner_numbers), names the cafe on every answer, and
"ganti toko" / "switch business" shows the list again. A number on one cafe,
a stranger and `STRUK <kode>` behave as before. The real pipeline runs (DB
lookup included); the AI handler is stubbed and outbound HTTP is captured.
"""
import json
import os
import uuid
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models import Business
from app.whatsapp import processor
from app.whatsapp.gateway import GatewayMessageIn, to_cloud_payload

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")


def _number() -> str:
    return f"62897{uuid.uuid4().int % 10**8:08d}"


@pytest.fixture
async def session_factory():
    if not DB_URL:
        pytest.fail("INTEGRATION_DATABASE_URL unset — local Postgres is not configured (roadmap §2)")
    engine = create_async_engine(DB_URL, connect_args={"statement_cache_size": 0})
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def two_cafes(session_factory):
    """`mine` is extra on "A Resto Ibu" and owns "B Demo" (names sort A, B)."""
    mine = _number()
    async with session_factory() as s:
        resto = Business(name="A Resto Ibu", owner_phone=_number(),
                         extra_owner_numbers=[{"name": "Anak", "phone": mine, "added_at": "2026-11-23T00:00:00+00:00"}])
        demo = Business(name="B Demo", owner_phone=mine)
        single = Business(name="C Satu Toko", owner_phone=_number())
        s.add_all([resto, demo, single])
        await s.commit()
        ids = {"resto": resto.id, "demo": demo.id, "single": single.id}
    yield mine, ids, single.owner_phone
    async with session_factory() as s:
        for bid in ids.values():
            await s.delete(await s.get(Business, bid))
        await s.commit()


@pytest.fixture
def sent(monkeypatch):
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "sent-1"})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return bodies


@contextmanager
def gateway():
    settings = get_settings()
    handler = AsyncMock(return_value=("smalltalk", "Halo! Ada yang bisa kubantu?"))
    with (
        patch.object(settings, "whatsapp_provider", "gateway"),
        patch.object(settings, "whatsapp_gateway_token", "gw-test-token-0123456789"),
        patch.object(settings, "whatsapp_gateway_url", "https://gateway.example.test"),
        patch.object(processor, "_handle_text", new=handler),
    ):
        yield handler


async def _say(sender: str, text: str) -> None:
    msg = GatewayMessageIn.model_validate({"id": f"gw-{uuid.uuid4().hex}", "from": sender, "type": "text", "text": text})
    await processor.process_webhook_payload(to_cloud_payload(msg))


async def test_a_number_on_one_cafe_is_answered_as_before(two_cafes, sent):
    _mine, _ids, single_owner = two_cafes
    with gateway() as handler:
        await _say(single_owner, "halo")
    assert [b["text"] for b in sent] == ["Halo! Ada yang bisa kubantu?"]   # no list, no header
    assert handler.await_args.args[0].name == "C Satu Toko"


async def test_two_cafes_ask_once_then_remember(session_factory, two_cafes, sent):
    mine, ids, _ = two_cafes
    with gateway() as handler:
        await _say(mine, "halo")
        assert handler.await_count == 0   # no cafe's data before the pick
        listing = sent[-1]["text"]
        assert "1. A Resto Ibu" in listing and "2. B Demo" in listing and "ganti toko" in listing

        await _say(mine, "2")
        assert "B Demo" in sent[-1]["text"] and handler.await_count == 0

        await _say(mine, "berapa penjualan hari ini?")
        await _say(mine, "3")   # a number after the pick is a normal message, not a pick
    assert handler.await_count == 2
    assert all(call.args[0].id == ids["demo"] for call in handler.await_args_list)
    assert sent[-1]["text"] == "🏪 B Demo\nHalo! Ada yang bisa kubantu?"
    async with session_factory() as s:
        assert mine in (await s.get(Business, ids["demo"])).active_owner_numbers
        assert mine not in (await s.get(Business, ids["resto"])).active_owner_numbers


@pytest.mark.parametrize("command", ["ganti toko", "Switch Business"])
async def test_switching_shows_the_list_and_moves_to_the_other_cafe(session_factory, two_cafes, sent, command):
    mine, ids, _ = two_cafes
    with gateway() as handler:
        await _say(mine, "2")
        await _say(mine, command)
        assert "1. A Resto Ibu" in sent[-1]["text"]
        await _say(mine, "halo")   # still no pick: the list again, no data read
        assert "1. A Resto Ibu" in sent[-1]["text"] and handler.await_count == 0
        await _say(mine, "1")
        await _say(mine, "halo")
    assert handler.await_args.args[0].id == ids["resto"]
    assert sent[-1]["text"].startswith("🏪 A Resto Ibu\n")
    async with session_factory() as s:
        assert mine in (await s.get(Business, ids["resto"])).active_owner_numbers
        assert mine not in (await s.get(Business, ids["demo"])).active_owner_numbers


async def test_a_stranger_still_gets_silence(two_cafes, sent):
    with gateway():
        await _say(_number(), "halo")
        await _say(_number(), "ganti toko")
    assert sent == []


async def test_struk_is_answered_before_any_list(two_cafes, sent):
    mine, _ids, _ = two_cafes
    with gateway() as handler:
        await _say(mine, "STRUK ZZZZ9999")
    assert handler.await_count == 0
    assert len(sent) == 1 and "tidak ditemukan" in sent[0]["text"]


# wa-gw-6: the picker also takes the business name (owner typed "demo" and got the list again).
@pytest.mark.parametrize(
    "reply, expected",
    [
        ("2", "demo"),                # the list number
        ("  B   DEMO ", "demo"),      # exact name, any case, extra spaces
        ("Demo", "demo"),             # unique partial word
        ("ibu.", "resto"),            # a trailing full stop is ignored
    ],
)
async def test_picker_takes_number_name_or_unique_partial(session_factory, two_cafes, sent, reply, expected):
    mine, ids, _ = two_cafes
    with gateway() as handler:
        await _say(mine, reply)
        assert handler.await_count == 0 and sent[-1]["text"].startswith("Oke, sekarang pakai data")
        await _say(mine, "halo")
    assert handler.await_args.args[0].id == ids[expected]


@pytest.mark.parametrize("reply", ["e", "toko lain", "3"])   # "e" is in both names; the others in none
async def test_picker_unclear_reply_shows_the_list_again(session_factory, two_cafes, sent, reply):
    mine, ids, _ = two_cafes
    with gateway() as handler:
        await _say(mine, reply)
        assert handler.await_count == 0
        text = sent[-1]["text"]
        assert text.startswith("Belum jelas") and "1. A Resto Ibu" in text and "2. B Demo" in text
        await _say(mine, "demo")   # and it still works afterwards
        assert sent[-1]["text"].startswith("Oke, sekarang pakai data *B Demo*")
    async with session_factory() as s:
        assert mine in (await s.get(Business, ids["demo"])).active_owner_numbers


async def test_switch_command_shows_the_list_without_the_unclear_line(two_cafes, sent):
    mine, _ids, _ = two_cafes
    with gateway():
        await _say(mine, "ganti toko")
    assert not sent[-1]["text"].startswith("Belum jelas") and "1. A Resto Ibu" in sent[-1]["text"]
