"""wa-gw-4: more WhatsApp numbers for the owner's assistant.

The owner adds numbers in Pengaturan (businesses.extra_owner_numbers). Each one
gets the assistant exactly like owner_phone; a removed number and a stranger
get silence; international numbers (+60) are normalised per country. The real
pipeline runs here (DB lookup included); only the AI handler is stubbed and
outbound HTTP is answered by httpx.MockTransport.
"""
import json
import os
import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models import Business
from app.services.owner_numbers import OwnerNumberInvalid, clean_owner_number
from app.whatsapp import processor
from app.whatsapp.gateway import GatewayMessageIn, to_cloud_payload

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")
GATEWAY_URL = "https://gateway.example.test"


@pytest.mark.parametrize(
    "typed,stored",
    [
        ("+60 12-345 6789", "60123456789"),
        ("60 11 2345 6789", "601123456789"),
        ("012-345 6789", "60123456789"),      # Malaysian local mobile
        ("0812 3456 7890", "6281234567890"),  # Indonesian local mobile
        ("+62 812-3456-7890", "6281234567890"),
        ("+1 415 555 0100", "14155550100"),   # other countries pass on length
    ],
)
def test_numbers_are_normalised_per_country(typed, stored):
    assert clean_owner_number(typed) == stored


@pytest.mark.parametrize("bad", ["", "abc", "081", "+60 3-1234 5678", "+62 21 555 1234", "6012345", "1234567890123456"])
def test_an_unusable_number_is_refused(bad):
    with pytest.raises(OwnerNumberInvalid) as exc:
        clean_owner_number(bad)
    assert exc.value.code == "phone"


# ── Real pipeline ────────────────────────────────────────────────────────────


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
        biz = Business(name="Extra Numbers Test", owner_phone=f"62897{uuid.uuid4().int % 10**8:08d}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    yield bid
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


@pytest.fixture
def captured(monkeypatch):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "sent-1"})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return requests


@contextmanager
def gateway():
    settings = get_settings()
    with (
        patch.object(settings, "whatsapp_provider", "gateway"),
        patch.object(settings, "whatsapp_gateway_token", "gw-test-token-0123456789"),
        patch.object(settings, "whatsapp_gateway_url", GATEWAY_URL),
        patch.object(processor, "_handle_text", new=AsyncMock(return_value=("smalltalk", "Halo! Ada yang bisa kubantu?"))),
    ):
        yield


async def _say_halo(sender: str) -> None:
    msg = GatewayMessageIn.model_validate({"id": f"gw-{uuid.uuid4().hex}", "from": sender, "type": "text", "text": "halo"})
    await processor.process_webhook_payload(to_cloud_payload(msg))


async def test_an_added_number_is_answered_and_a_removed_one_is_not(session_factory, cafe, captured):
    from app.api.dashboard import add_business_owner_number, remove_business_owner_number
    from app.schemas.dashboard import OwnerNumberIn

    my = f"6012{uuid.uuid4().int % 10**7:07d}"   # random, so no other cafe in the DB holds it
    typed = f"+60 {my[2:4]}-{my[4:7]} {my[7:]}"
    async with session_factory() as s:
        owner = SimpleNamespace(session=s, business_id=cafe, staff_id=None)
        out = await add_business_owner_number(OwnerNumberIn(name="Firaz", phone=typed), owner)
        await s.commit()
    assert [(n.name, n.phone) for n in out.extra_owner_numbers] == [("Firaz", my)]

    with gateway():
        await _say_halo(my)
    assert len(captured) == 1
    body = json.loads(captured[0].content)
    assert body["to"] == my and body["text"].startswith("Halo!")

    async with session_factory() as s:
        owner = SimpleNamespace(session=s, business_id=cafe, staff_id=None)
        out = await remove_business_owner_number(my, owner)
        await s.commit()
    assert out.extra_owner_numbers == []

    captured.clear()
    with gateway():
        await _say_halo(my)
    assert captured == []


async def test_a_stranger_still_gets_silence(cafe, captured):
    with gateway():
        await _say_halo("60" + str(uuid.uuid4().int)[:9])
    assert captured == []


async def test_the_owner_still_gets_answers(session_factory, cafe, captured):
    async with session_factory() as s:
        phone = (await s.get(Business, cafe)).owner_phone
    with gateway():
        await _say_halo(phone)
    assert [json.loads(r.content)["to"] for r in captured] == [phone]


async def test_bad_taken_and_unknown_numbers_are_refused_in_indonesian(session_factory, cafe):
    from app.api.dashboard import add_business_owner_number, remove_business_owner_number
    from app.schemas.dashboard import OwnerNumberIn

    async with session_factory() as s:
        owner = SimpleNamespace(session=s, business_id=cafe, staff_id=None)
        own_phone = (await s.get(Business, cafe)).owner_phone
        with pytest.raises(HTTPException) as exc:
            await add_business_owner_number(OwnerNumberIn(name="X", phone="12345"), owner)
        assert exc.value.status_code == 422 and "tidak valid" in exc.value.detail
        with pytest.raises(HTTPException) as exc:
            await add_business_owner_number(OwnerNumberIn(name="Saya", phone="+" + own_phone), owner)
        assert exc.value.status_code == 409 and "sudah dipakai" in exc.value.detail
        my = f"6011{uuid.uuid4().int % 10**8:08d}"
        await add_business_owner_number(OwnerNumberIn(name="Firaz", phone="0" + my[2:]), owner)
        with pytest.raises(HTTPException) as exc:
            await add_business_owner_number(OwnerNumberIn(name="Lagi", phone="+" + my), owner)
        assert exc.value.status_code == 409
        with pytest.raises(HTTPException) as exc:
            await remove_business_owner_number("60199999999", owner)
        assert exc.value.status_code == 404
        await s.rollback()
