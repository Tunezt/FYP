"""P2-6: a stock-book photo updates the stock of items the cafe already has. A
line that names no existing item is listed in the read-back and left out on a
plain YA; only "YA SEMUA" adds it as a new item. Before, YA added every unknown
line to the catalogue (the audit saw "Mie instan" and "Rokok Surya" appear), so
any random photo could fill the menu.

Real path: the photo and the replies go through process_webhook_payload against
the local database; the vision model, the image upload, the composer and the
receipt embedding are stubbed, and the gateway's HTTP call is captured.
"""
import base64
import json
import os
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models import Business, Item
from app.services.receipts import classify_reply_keyword
from app.services.stock import open_item_stock
from app.whatsapp import processor
from app.whatsapp.gateway import GatewayMessageIn, to_cloud_payload
from tests.conftest import seed_books

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")
BOOK = {
    "document_type": "stock_ledger", "supplier": "", "date": "", "confidence": "high", "ambiguities": [],
    "total_amount": 0,
    "items": [
        {"name": "Gula Aren", "quantity": 3, "unit": "kg", "unit_price": 0, "line_total": 0},
        {"name": "Mie instan", "quantity": 20, "unit": "bungkus", "unit_price": 0, "line_total": 0},
        {"name": "Rokok Surya", "quantity": 4, "unit": "bungkus", "unit_price": 0, "line_total": 0},
    ],
}


def test_ya_semua_is_its_own_keyword():
    for said in ("YA SEMUA", "ya, semua", "Ya semuanya!", "tambah semua", "yes all"):
        assert classify_reply_keyword(said) == "confirm_all", said
    for said in ("ya", "YA", "oke", "ok!"):
        assert classify_reply_keyword(said) == "confirm", said
    assert classify_reply_keyword("batal") == "deny"
    assert classify_reply_keyword("gulanya 2 kg") == "other"


@pytest.fixture
async def session_factory():
    if not DB_URL:
        pytest.fail("INTEGRATION_DATABASE_URL unset — local Postgres is not configured (roadmap §2)")
    engine = create_async_engine(DB_URL, connect_args={"statement_cache_size": 0})
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


async def _tenant(s, business_id):
    await s.execute(text("select set_config('app.current_business_id', :b, true)"), {"b": str(business_id)})


@pytest.fixture
async def cafe(session_factory):
    """A cafe with books and one item, Gula Aren at 7 kg."""
    async with session_factory() as s:
        biz = Business(name="Kopi Buku Stok", owner_phone=f"62894{uuid.uuid4().int % 10**8:08d}")
        s.add(biz)
        await s.commit()
    async with session_factory() as s:
        await _tenant(s, biz.id)
        await seed_books(s, biz.id)
        item = Item(business_id=biz.id, name="Gula Aren", unit="kg", current_stock=Decimal(7),
                    cost_price=Decimal("25000"), sell_price=Decimal(0))
        s.add(item)
        await s.flush()
        await open_item_stock(s, item, reason="opname", source_type="test", unit_cost=Decimal("25000"))
        await s.commit()
    yield biz
    async with session_factory() as s:
        await s.delete(await s.get(Business, biz.id))
        await s.commit()


@pytest.fixture
def bot(monkeypatch):
    """(sent texts, composer mock). Vision returns BOOK for every photo."""
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content)["text"])
        return httpx.Response(200, json={"id": "sent-1"})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    settings = get_settings()
    compose = AsyncMock(return_value="✅ Stok sudah diperbarui.")
    with (
        patch.object(settings, "whatsapp_provider", "gateway"),
        patch.object(settings, "whatsapp_gateway_token", "gw-test-token-0123456789"),
        patch.object(settings, "whatsapp_gateway_url", "https://gateway.example.test"),
        patch("app.ai.vision.parse_business_document", new=AsyncMock(return_value=BOOK)),
        patch("app.whatsapp.storage.upload_receipt_image", new=AsyncMock(return_value="r/book.jpg")),
        patch("app.ai.composer.compose_reply", new=compose),
        patch("app.services.rag.embed_receipt", new=AsyncMock()),
    ):
        yield sent, compose


async def _send(sender: str, **fields) -> None:
    msg = GatewayMessageIn.model_validate({"id": f"gw-{uuid.uuid4().hex}", "from": sender, **fields})
    await processor.process_webhook_payload(to_cloud_payload(msg))


async def _photo(sender: str) -> None:
    await _send(sender, type="image", mime_type="image/jpeg", media_b64=base64.b64encode(uuid.uuid4().bytes).decode())


async def _items(session_factory, business_id) -> dict[str, Decimal]:
    async with session_factory() as s:
        await _tenant(s, business_id)
        return {i.name: i.current_stock for i in (await s.execute(select(Item))).scalars().all()}


async def test_ya_updates_existing_items_only_and_names_the_rest(session_factory, cafe, bot):
    sent, compose = bot
    await _photo(cafe.owner_phone)
    readback = sent[-1]
    assert "*Belum ada di daftar barang:* Mie instan, Rokok Surya" in readback
    assert "*YA SEMUA*" in readback

    await _send(cafe.owner_phone, type="text", text="YA")
    assert await _items(session_factory, cafe.id) == {"Gula Aren": Decimal("3.000")}   # counted 7 -> 3, nothing added
    facts = compose.await_args.args[3]
    assert facts["not_saved_not_in_item_list"] == ["Mie instan", "Rokok Surya"]
    assert "halaman Stok" in facts["to_add_them"]


async def test_ya_semua_adds_the_new_items_too(session_factory, cafe, bot):
    sent, compose = bot
    await _photo(cafe.owner_phone)
    await _send(cafe.owner_phone, type="text", text="ya semua")
    assert await _items(session_factory, cafe.id) == {
        "Gula Aren": Decimal("3.000"), "Mie instan": Decimal("20.000"), "Rokok Surya": Decimal("4.000"),
    }
    assert "not_saved_not_in_item_list" not in compose.await_args.args[3]


async def test_a_book_of_known_items_keeps_the_plain_question(cafe, bot):
    sent, _ = bot
    known = {**BOOK, "items": BOOK["items"][:1]}
    with patch("app.ai.vision.parse_business_document", new=AsyncMock(return_value=known)):
        await _photo(cafe.owner_phone)
    assert "Belum ada di daftar barang" not in sent[-1] and "Balas *YA* untuk simpan" in sent[-1]
