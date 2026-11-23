"""fin-n1-dup-receipt: the same nota confirmed twice used to double the expense
and the stock. Before asking for YA the read-back now says so when the photo is
a saved one (same image) or the same supplier + date + total (another photo of
it), and still lets the owner say YA for a real second purchase. The vision
call and the composer are mocked; everything else runs against local Postgres."""
import os
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import engine as app_engine
from app.models import Business, Expense, GoodsReceipt, Item, Receipt
from app.services.catalog import ensure_default_variant
from app.services.stock import open_item_stock
from app.services.suppliers import create_supplier
from app.services.units import ensure_standard_uoms
from app.whatsapp.processor import _handle_image, _handle_text
from tests.conftest import seed_books

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")
WARNING = "Nota ini mirip nota Toko Manis 20 Nov Rp 684.000 yang sudah tersimpan"


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
    await session.execute(text("select set_config('app.current_business_id', :bid, true)"), {"bid": str(business_id)})


@pytest.fixture
async def shop(session_factory):
    async with session_factory() as s:
        biz = Business(name="Dup Receipt Test", owner_phone=f"62985{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await seed_books(s, bid)
        uoms = await ensure_standard_uoms(s, bid)
        await create_supplier(s, bid, name="Toko Manis")
        gula = Item(business_id=bid, name="Gula Aren", unit="kg", current_stock=Decimal(4), cost_price=Decimal(30000), uom_id=uoms["kg"].id)
        s.add(gula)
        await s.flush()
        await open_item_stock(s, gula, unit_cost=gula.cost_price)
        await ensure_default_variant(s, gula)
        await s.commit()
    yield bid
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


def _nota(total=684000, supplier="Toko Manis", date="2026-11-20"):
    return {
        "document_type": "receipt", "supplier": supplier, "date": date, "confidence": "high", "ambiguities": [],
        "items": [{"name": "Gula Aren", "quantity": 4, "unit": "kg", "unit_written": True,
                   "unit_price": total // 4, "unit_price_written": True, "line_total": total}],
        "total_amount": total,
    }


async def _photo(session_factory, bid, parsed, image=b"photo-1"):
    async with session_factory() as s:
        biz = await s.get(Business, bid)
    with (
        patch("app.whatsapp.processor.send_text", new=AsyncMock()),
        patch("app.whatsapp.client.download_media", new=AsyncMock(return_value=(image, "image/jpeg"))),
        patch("app.whatsapp.storage.upload_receipt_image", new=AsyncMock(return_value=f"r/{uuid.uuid4()}.jpg")),
        patch("app.ai.vision.parse_business_document", new=AsyncMock(return_value=parsed)),
    ):
        _intent, reply = await _handle_image(biz, {"image": {"id": "m"}})
    return biz, reply


async def _ya(biz):
    with patch("app.ai.composer.compose_reply", new=AsyncMock(return_value="tercatat")):
        return await _handle_text(biz, "ya")


async def _saved(session_factory, bid):
    async with session_factory() as s:
        await _set_tenant(s, bid)
        return [(await s.execute(select(func.count(t.id)))).scalar_one() for t in (Receipt, Expense, GoodsReceipt)]


async def test_first_receipt_and_a_different_one_get_no_warning(session_factory, shop):
    biz, reply = await _photo(session_factory, shop, _nota())
    assert "mirip" not in reply
    await _ya(biz)
    _, other = await _photo(session_factory, shop, _nota(total=500000), image=b"photo-2")   # other total
    _, other_day = await _photo(session_factory, shop, _nota(date="2026-11-21"), image=b"photo-3")
    _, other_shop = await _photo(session_factory, shop, _nota(supplier="Pasar Induk"), image=b"photo-4")
    assert "mirip" not in other and "mirip" not in other_day and "mirip" not in other_shop


async def test_the_same_photo_twice_warns_and_a_real_second_purchase_can_still_be_saved(session_factory, shop):
    biz, _ = await _photo(session_factory, shop, _nota())
    await _ya(biz)
    # Same bytes: caught by the image hash even if the read of supplier/total differs this time.
    biz, reply = await _photo(session_factory, shop, _nota(total=608000, supplier="Toko Manis 2"))
    assert WARNING in reply and "Balas *YA* kalau ini pembelian baru, atau *BATAL*" in reply
    assert "already saved" in reply and reply.index("Balas *YA* untuk mencatat") < reply.index("mirip")
    assert await _saved(session_factory, shop) == [1, 1, 1]            # warned, nothing written
    intent, _ = await _ya(biz)                                         # it really was a second purchase
    assert intent == "vision_confirm" and await _saved(session_factory, shop) == [2, 2, 2]


async def test_batal_after_the_warning_saves_nothing(session_factory, shop):
    biz, _ = await _photo(session_factory, shop, _nota())
    await _ya(biz)
    biz, reply = await _photo(session_factory, shop, _nota())
    assert "mirip" in reply
    intent, _ = await _handle_text(biz, "batal")
    assert intent == "vision_deny" and await _saved(session_factory, shop) == [1, 1, 1]


async def test_same_supplier_date_total_with_another_photo_warns(session_factory, shop):
    biz, _ = await _photo(session_factory, shop, _nota(), image=b"photo-1")
    await _ya(biz)
    _, reply = await _photo(session_factory, shop, _nota(supplier=" toko  MANIS "), image=b"photo-other")
    assert WARNING in reply


async def test_a_correction_keeps_the_image_hash_so_the_warning_survives_it(session_factory, shop):
    biz, _ = await _photo(session_factory, shop, _nota())
    await _ya(biz)
    biz, _ = await _photo(session_factory, shop, _nota(total=500000, date="2026-11-22"))   # same bytes
    revised = {"unrelated": False, "parsed": _nota(total=684000, date="2026-11-20", supplier="toko manis")}
    with patch("app.ai.vision.revise_parse", new=AsyncMock(return_value=revised)):
        intent, reply = await _handle_text(biz, "totalnya 684.000, tanggal 20 Nov")
    assert intent == "vision_revise" and WARNING in reply
