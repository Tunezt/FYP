"""M5-T4 — supplier invoice photo → draft goods receipt, confirmed with one reply.

Done-when (roadmap): an invoice photo produces a draft confirmed with one
reply, with a test covering the unmatched-line path. Never write stock from a
photo without confirmation; unmatched lines are questions, never guesses.

The model call is mocked with a fixed parse (the real vision path is measured
in M1); everything after the parse runs for real against the local Postgres.
"""
import os
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import engine as app_engine
from app.models import Business, Expense, GoodsReceipt, GoodsReceiptLine, Item, PendingConfirmation, Receipt, StockMovement
from app.services.catalog import ensure_default_variant
from app.services.invoice_draft import build_draft, confirm_draft, draft_summary, match_item
from app.services.ledger import account_balances
from app.services.stock import open_item_stock
from app.services.suppliers import create_supplier
from app.services.units import ensure_standard_uoms
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
    """Gula Aren 4 kg @30.000, Susu UHT 10 l @17.000, Biji Arabica 2 kg; supplier Toko Manis."""
    async with session_factory() as s:
        biz = Business(name="Invoice Draft Test", owner_phone=f"62984{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await seed_books(s, bid)
        uoms = await ensure_standard_uoms(s, bid)
        toko = await create_supplier(s, bid, name="Toko Manis")
        gula = Item(business_id=bid, name="Gula Aren", unit="kg", current_stock=Decimal(4), cost_price=Decimal(30000), uom_id=uoms["kg"].id)
        susu = Item(business_id=bid, name="Susu UHT", unit="liter", current_stock=Decimal(10), cost_price=Decimal(17000), uom_id=uoms["liter"].id)
        beans = Item(business_id=bid, name="Biji Arabica", unit="kg", current_stock=Decimal(2), cost_price=Decimal(140000), uom_id=uoms["kg"].id)
        s.add_all([gula, susu, beans])
        await s.flush()
        for it in (gula, susu, beans):
            await open_item_stock(s, it, unit_cost=it.cost_price)
            await ensure_default_variant(s, it)
        await s.commit()
        ids = {"bid": bid, "supplier": toko.id, "gula": gula.id, "susu": susu.id, "beans": beans.id}
    yield ids
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


INVOICE = {
    "document_type": "receipt", "supplier": "toko manis", "date": "2026-09-01",
    "items": [
        {"name": "Gula Aren", "quantity": 3, "unit": "kg", "unit_written": True, "unit_price": 38000, "unit_price_written": True, "line_total": 114000},
        {"name": "susu uht", "quantity": 2500, "unit": "ml", "unit_written": True, "unit_price": 20, "unit_price_written": True, "line_total": 50000},
        {"name": "Kecap BH", "quantity": 1, "unit": "pcs", "unit_written": False, "unit_price": 17000, "unit_price_written": True, "line_total": 17000},
    ],
    "total_amount": 181000, "confidence": "high", "ambiguities": [],
}


async def _gaps(s):
    return (await s.execute(text(
        "select i.name from items i left join stock_movements m on m.item_id = i.id "
        "group by i.id, i.name, i.current_stock having i.current_stock <> coalesce(sum(m.qty_delta), 0)"
    ))).scalars().all()


async def test_draft_matches_lines_and_surfaces_the_unmatched_one_as_a_question(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        biz = await s.get(Business, c["bid"])
        draft = await build_draft(s, biz, INVOICE)
    assert draft["supplier_id"] == str(c["supplier"]) and draft["supplier_name"] == "Toko Manis"
    assert [(m["name_read"], m["item_name"], m["uom_id"] is not None) for m in draft["matched"]] == [
        ("Gula Aren", "Gula Aren", False),      # same unit as stock
        ("susu uht", "Susu UHT", True),         # ml → converted at receipt time
    ]
    assert [q["name_read"] for q in draft["questions"]] == ["Kecap BH"]
    assert "belum ada di daftar barang" in draft["questions"][0]["reason"]
    text_out = draft_summary(draft)
    assert "❓ 1 pcs Kecap BH" in text_out and "Akan dilewati" in text_out
    assert "✅ 2500 ml susu uht" in text_out and "✅ 3 kg Gula Aren" in text_out
    assert "Supplier: Toko Manis" in text_out and "Balas *YA*" in text_out and "2 baris" in text_out


async def test_fuzzy_match_and_ambiguity_ask_rather_than_guess(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        item, cands = await match_item(s, "biji arabika")   # typo → still Biji Arabica
        assert item is not None and item.name == "Biji Arabica"
        item, cands = await match_item(s, "gula")            # substring of one item → match
        assert item is not None and item.name == "Gula Aren"
        s.add(Item(business_id=c["bid"], name="Gula Pasir", unit="kg"))
        await s.flush()
        item, cands = await match_item(s, "gula")            # now two plausible items → question with candidates
        assert item is None and sorted(cands) == ["Gula Aren", "Gula Pasir"]
        item, cands = await match_item(s, "Sabun Cuci Piring")
        assert item is None and cands == []
        await s.rollback()


async def test_confirming_creates_goods_receipt_for_matched_lines_only(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        biz = await s.get(Business, c["bid"])
        draft = await build_draft(s, biz, INVOICE)
        with patch("app.services.rag.embed_receipt", new=AsyncMock()):
            facts = await confirm_draft(s, biz, draft, "receipts/inv.jpg", INVOICE)
        await s.commit()
    assert facts["saved"] and facts["skipped"] == ["Kecap BH"] and facts["goods_receipt_number"] == 1
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        gula, susu = await s.get(Item, c["gula"]), await s.get(Item, c["susu"])
        assert gula.current_stock == Decimal("7.000") and gula.cost_price == Decimal("33428.57")   # moving average
        assert susu.current_stock == Decimal("12.500") and susu.cost_price == Decimal("17600.00")  # 2.5 l @20.000/l
        assert (await s.execute(select(func.count(Item.id)))).scalar_one() == 3                    # no item invented for Kecap
        gr = (await s.execute(select(GoodsReceipt))).scalar_one()
        assert gr.supplier_id == c["supplier"] and gr.subtotal == Decimal("164000.00")
        assert len((await s.execute(select(GoodsReceiptLine).where(GoodsReceiptLine.receipt_id == gr.id))).scalars().all()) == 2
        photo = (await s.execute(select(Receipt))).scalar_one()
        assert photo.supplier_id == c["supplier"] and photo.total_amount == Decimal("181000.00")
        expense = (await s.execute(select(Expense))).scalar_one()
        assert expense.amount == Decimal("181000.00") and expense.receipt_id == photo.id
        assert [m.reason for m in (await s.execute(select(StockMovement).where(StockMovement.source_type == "goods_receipt"))).scalars()] == ["purchase", "purchase"]
        assert await _gaps(s) == []
        # Books (M6-T6): matched goods are inventory owed to the supplier; only
        # the 17.000 the receipt has beyond them (Kecap BH) is an expense.
        balances = await account_balances(s)
        assert balances["1300"] == Decimal("164000.00") and balances["2100"] == Decimal("164000.00")
        assert balances["5200"] == Decimal("17000.00") and balances["1100"] == Decimal("-17000.00")


async def test_photo_never_writes_stock_without_a_reply_and_one_ya_confirms(session_factory, shop):
    """End to end through the WhatsApp handlers with the model mocked: a
    high-confidence invoice is parked, stock untouched; 'ya' creates the goods
    receipt; the unmatched line is named as skipped."""
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        biz = await s.get(Business, c["bid"])
    with (
        patch("app.whatsapp.processor.send_text", new=AsyncMock()),
        patch("app.whatsapp.client.download_media", new=AsyncMock(return_value=(b"jpg", "image/jpeg"))),
        patch("app.whatsapp.storage.upload_receipt_image", new=AsyncMock(return_value="receipts/x.jpg")),
        patch("app.ai.vision.parse_business_document", new=AsyncMock(return_value=INVOICE)),
    ):
        intent, reply = await _handle_image(biz, {"image": {"id": "media-1"}})
    assert intent == "vision" and "❓ 1 pcs Kecap BH" in reply and "Balas *YA*" in reply
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        pending = (await s.execute(select(PendingConfirmation))).scalar_one()
        assert pending.kind == "goods_receipt" and pending.payload["draft"]["matched"][0]["item_name"] == "Gula Aren"
        assert (await s.get(Item, c["gula"])).current_stock == Decimal("4.000")  # nothing written yet
        assert (await s.execute(select(GoodsReceipt))).scalars().all() == []

    with (
        patch("app.ai.composer.compose_reply", new=AsyncMock(return_value="Sip, penerimaan barang tercatat.")) as compose,
    ):
        intent, reply = await _handle_text(biz, "ya")
    assert intent == "vision_confirm" and "tercatat" in reply
    facts = compose.await_args.args[3]
    assert facts["skipped"] == ["Kecap BH"] and len(facts["stock_effects"]) == 2
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert (await s.get(Item, c["gula"])).current_stock == Decimal("7.000")
        assert (await s.execute(select(func.count(GoodsReceipt.id)))).scalar_one() == 1
        assert (await s.execute(select(PendingConfirmation))).scalars().all() == []   # consumed
        assert await _gaps(s) == []


async def test_tidak_discards_the_draft(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        biz = await s.get(Business, c["bid"])
    with (
        patch("app.whatsapp.processor.send_text", new=AsyncMock()),
        patch("app.whatsapp.client.download_media", new=AsyncMock(return_value=(b"jpg", "image/jpeg"))),
        patch("app.whatsapp.storage.upload_receipt_image", new=AsyncMock(return_value="receipts/y.jpg")),
        patch("app.ai.vision.parse_business_document", new=AsyncMock(return_value=INVOICE)),
    ):
        await _handle_image(biz, {"image": {"id": "media-2"}})
    intent, reply = await _handle_text(biz, "tidak")
    assert intent == "vision_deny" and "nggak ada yang disimpan" in reply
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert (await s.get(Item, c["gula"])).current_stock == Decimal("4.000")
        assert (await s.execute(select(GoodsReceipt))).scalars().all() == []
