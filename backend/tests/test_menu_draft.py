"""M5-T5 — menu photo → reviewable draft catalogue → items and variants on YA.

The model call is mocked with a fixed menu parse; everything after it runs for
real against the local Postgres.
"""
import os
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import engine as app_engine
from app.models import Business, Item, ItemVariant, PendingConfirmation
from app.services.catalog import ensure_default_variant
from app.services.menu_draft import build_menu_draft, confirm_menu_draft, menu_draft_summary
from app.whatsapp.processor import _handle_image, _handle_text

from tests.conftest import seed_books

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")


@pytest.fixture
async def engine():
    if not DB_URL:
        pytest.fail("INTEGRATION_DATABASE_URL unset — local Postgres is not configured (roadmap §2)")
    engine = create_async_engine(DB_URL, connect_args={"statement_cache_size": 0})
    yield engine
    await engine.dispose()
    await app_engine.dispose()


@pytest.fixture
def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _set_tenant(session, business_id):
    await session.execute(
        text("select set_config('app.current_business_id', :bid, true)"), {"bid": str(business_id)}
    )


@pytest.fixture
async def shop(session_factory):
    async with session_factory() as s:
        biz = Business(name="Menu Draft Test", owner_phone=f"62983{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await seed_books(s, bid)
        existing = Item(business_id=bid, name="Es Teh Manis", unit="cup", sell_price=Decimal(8000))
        s.add(existing)
        await s.flush()
        await ensure_default_variant(s, existing)
        await s.commit()
    yield bid
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


MENU = {
    "is_menu": True,
    "products": [
        {"name": "Es Kopi Susu", "category": "Kopi", "variants": [
            {"name": "Regular", "price": 22000, "price_written": True},
            {"name": "Large", "price": 27000, "price_written": True}]},
        {"name": "Roti Bakar Coklat", "category": "Makanan", "variants": [{"name": "Standar", "price": 24000, "price_written": True}]},
        {"name": "es teh manis", "category": "Minuman", "variants": [{"name": "Standar", "price": 9000, "price_written": True}]},
        {"name": "Pisang Goreng", "category": "Makanan", "variants": [{"name": "Standar", "price": 0, "price_written": False}]},
    ],
    "confidence": "medium",
    "ambiguities": ["Harga Pisang Goreng tertutup stiker"],
}


async def test_draft_separates_new_existing_and_unpriced(session_factory, shop):
    async with session_factory() as s:
        await _set_tenant(s, shop)
        biz = await s.get(Business, shop)
        draft = await build_menu_draft(s, biz, MENU)
    assert [p["name"] for p in draft["new_products"]] == ["Es Kopi Susu", "Roti Bakar Coklat", "Pisang Goreng"]
    assert [e["item_name"] for e in draft["existing"]] == ["Es Teh Manis"]       # case-insensitive, not repriced
    assert [q["name"] for q in draft["questions"]] == ["Pisang Goreng"]
    summary = menu_draft_summary(draft)
    assert "🆕 Es Kopi Susu — Regular Rp 22.000, Large Rp 27.000" in summary
    assert "🆕 Roti Bakar Coklat — Rp 24.000" in summary
    assert "↩️ es teh manis — sudah ada" in summary
    assert "❓ Pisang Goreng: harga tidak terbaca" in summary
    assert "⚠️ Harga Pisang Goreng tertutup stiker" in summary
    assert "membuat 3 barang baru" in summary and "Balas *YA*" in summary


async def test_confirm_creates_items_with_variants_and_leaves_existing_alone(session_factory, shop):
    async with session_factory() as s:
        await _set_tenant(s, shop)
        biz = await s.get(Business, shop)
        draft = await build_menu_draft(s, biz, MENU)
        facts = await confirm_menu_draft(s, biz, draft)
        await s.commit()
    assert facts["created_count"] == 3 and facts["existing_skipped"] == ["Es Teh Manis"] and facts["unpriced"] == ["Pisang Goreng"]
    async with session_factory() as s:
        await _set_tenant(s, shop)
        assert (await s.execute(select(func.count(Item.id)))).scalar_one() == 4
        kopi = (await s.execute(select(Item).where(Item.name == "Es Kopi Susu"))).scalar_one()
        assert kopi.sell_price == Decimal("22000.00") and kopi.unit == "porsi" and kopi.current_stock == Decimal(0)
        variants = (await s.execute(select(ItemVariant).where(ItemVariant.item_id == kopi.id).order_by(ItemVariant.sell_price))).scalars().all()
        assert [(v.name, v.sell_price, v.is_default) for v in variants] == [
            ("Regular", Decimal("22000.00"), True), ("Large", Decimal("27000.00"), False)]
        teh = (await s.execute(select(Item).where(Item.name == "Es Teh Manis"))).scalar_one()
        assert teh.sell_price == Decimal("8000.00")                      # the photo's 9.000 did not reprice it
        pisang = (await s.execute(select(Item).where(Item.name == "Pisang Goreng"))).scalar_one()
        assert pisang.sell_price == Decimal(0)                            # confirmed at 0, said so, never guessed
        roti_default = (await s.execute(select(ItemVariant).join(Item).where(Item.name == "Roti Bakar Coklat"))).scalar_one()
        assert roti_default.name == "Standar" and roti_default.is_default and roti_default.sell_price == Decimal("24000.00")


async def test_menu_caption_parks_a_draft_and_one_ya_creates_the_catalogue(session_factory, shop):
    async with session_factory() as s:
        await _set_tenant(s, shop)
        biz = await s.get(Business, shop)
    with (
        patch("app.whatsapp.processor.send_text", new=AsyncMock()),
        patch("app.whatsapp.client.download_media", new=AsyncMock(return_value=(b"jpg", "image/jpeg"))),
        patch("app.whatsapp.storage.upload_receipt_image", new=AsyncMock(return_value="receipts/menu.jpg")),
        patch("app.ai.vision.parse_menu_photo", new=AsyncMock(return_value=MENU)) as parse_menu,
        patch("app.ai.vision.parse_business_document", new=AsyncMock()) as parse_receipt,
    ):
        intent, reply = await _handle_image(biz, {"image": {"id": "media-9", "caption": "Ini foto MENU kami"}})
    assert intent == "vision_menu" and "🆕 Es Kopi Susu" in reply
    parse_menu.assert_awaited_once()
    parse_receipt.assert_not_awaited()                                   # the caption routed it to the menu path
    async with session_factory() as s:
        await _set_tenant(s, shop)
        pending = (await s.execute(select(PendingConfirmation))).scalar_one()
        assert pending.kind == "menu_draft"
        assert (await s.execute(select(func.count(Item.id)))).scalar_one() == 1    # nothing created yet

    with patch("app.ai.composer.compose_reply", new=AsyncMock(return_value="Siap, 3 menu baru sudah masuk.")) as compose:
        intent, reply = await _handle_text(biz, "ya")
    assert intent == "vision_menu_confirm" and "3 menu" in reply
    assert compose.await_args.args[3]["created_count"] == 3
    async with session_factory() as s:
        await _set_tenant(s, shop)
        assert (await s.execute(select(func.count(Item.id)))).scalar_one() == 4
        assert (await s.execute(select(PendingConfirmation))).scalars().all() == []


async def test_not_a_menu_is_refused(session_factory, shop):
    async with session_factory() as s:
        await _set_tenant(s, shop)
        biz = await s.get(Business, shop)
    with (
        patch("app.whatsapp.processor.send_text", new=AsyncMock()),
        patch("app.whatsapp.client.download_media", new=AsyncMock(return_value=(b"jpg", "image/jpeg"))),
        patch("app.whatsapp.storage.upload_receipt_image", new=AsyncMock(return_value="receipts/cat.jpg")),
        patch("app.ai.vision.parse_menu_photo", new=AsyncMock(return_value={"is_menu": False, "products": [], "confidence": "low", "ambiguities": []})),
    ):
        intent, reply = await _handle_image(biz, {"image": {"id": "media-10", "caption": "menu"}})
    assert intent == "vision_menu" and "nggak nemu daftar produk" in reply
    async with session_factory() as s:
        await _set_tenant(s, shop)
        assert (await s.execute(select(PendingConfirmation))).scalars().all() == []
