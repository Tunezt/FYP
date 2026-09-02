"""M5-T3 — goods receipts: receiving emits purchase movements and updates the
moving-average cost; partial and over-receipt are explicit.

Done-when (roadmap): receiving 8 of 10 leaves the PO partially received and
M2-T3 holds.

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import os
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.dashboard import create_goods_receipt, get_goods_receipt, list_goods_receipts, supplier_history
from app.models import Business, GoodsReceipt, GoodsReceiptLine, Item, ItemVariant, PoLine, PurchaseOrder, StockMovement
from app.schemas.dashboard import GoodsReceiptCreateIn, GrLineIn
from app.services.catalog import ensure_default_variant
from app.services.purchasing import PoLineSpec, cancel, create_purchase_order, mark_ordered
from app.services.receiving import GrLineSpec, ReceivingInvalid, receive_goods
from app.services.stock import open_item_stock
from app.services.suppliers import create_supplier
from app.services.units import ensure_standard_uoms

from tests.conftest import seed_books

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")


@pytest.fixture
async def engine():
    if not DB_URL:
        pytest.fail("INTEGRATION_DATABASE_URL unset — local Postgres is not configured (roadmap §2)")
    engine = create_async_engine(DB_URL, connect_args={"statement_cache_size": 0})
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _set_tenant(session, business_id):
    await session.execute(
        text("select set_config('app.current_business_id', :bid, true)"), {"bid": str(business_id)}
    )


async def _gaps(s):
    return (await s.execute(text(
        "select i.name from items i left join stock_movements m on m.item_id = i.id "
        "group by i.id, i.name, i.current_stock having i.current_stock <> coalesce(sum(m.qty_delta), 0)"
    ))).scalars().all()


@pytest.fixture
async def shop(session_factory):
    """Beans 2 kg @140.000 on hand; a PO for 10 kg @150.000 (ordered)."""
    async with session_factory() as s:
        biz = Business(name="GR Test", owner_phone=f"62985{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await seed_books(s, bid)
        uoms = await ensure_standard_uoms(s, bid)
        supplier = await create_supplier(s, bid, name="Grosir Barokah")
        beans = Item(business_id=bid, name="Biji Arabica", unit="kg", current_stock=Decimal(2), cost_price=Decimal(140000), uom_id=uoms["kg"].id)
        milk = Item(business_id=bid, name="Susu UHT", unit="liter", current_stock=Decimal(0), cost_price=Decimal(0), uom_id=uoms["liter"].id)
        loose = Item(business_id=bid, name="Gelas", unit="pcs", current_stock=Decimal(0))
        s.add_all([beans, milk, loose])
        await s.flush()
        await open_item_stock(s, beans, unit_cost=beans.cost_price)
        for it in (beans, milk, loose):
            await ensure_default_variant(s, it)
        po = await create_purchase_order(
            s, bid, supplier_id=supplier.id,
            lines=[PoLineSpec(item_id=beans.id, quantity=Decimal(10), unit_cost=Decimal(150000), uom_id=uoms["kg"].id)],
        )
        await mark_ordered(s, po)
        po_line = (await s.execute(select(PoLine).where(PoLine.po_id == po.id))).scalar_one()
        await s.commit()
        ids = {"bid": bid, "supplier": supplier.id, "beans": beans.id, "milk": milk.id, "loose": loose.id,
               "po": po.id, "po_line": po_line.id, "kg": uoms["kg"].id, "g": uoms["g"].id, "ml": uoms["ml"].id}
    yield ids
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


async def test_receiving_8_of_10_leaves_po_partially_received(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        received = await receive_goods(
            s, c["bid"], po_id=c["po"],
            lines=[GrLineSpec(item_id=c["beans"], quantity=Decimal(8), unit_cost=Decimal(150000), uom_id=c["kg"], po_line_id=c["po_line"])],
        )
        await s.commit()
        assert received.po.status == "partially_received"
        assert received.receipt.number == 1 and received.receipt.subtotal == Decimal("1200000.00")
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        beans = await s.get(Item, c["beans"])
        assert beans.current_stock == Decimal("10.000")                 # 2 + 8
        assert beans.cost_price == Decimal("148000.00")                  # (2 × 140.000 + 8 × 150.000) / 10
        default = (await s.execute(select(ItemVariant).where(ItemVariant.item_id == c["beans"], ItemVariant.is_default.is_(True)))).scalar_one()
        assert default.cost_price == Decimal("148000.00")
        po_line = await s.get(PoLine, c["po_line"])
        assert po_line.received_quantity == Decimal("8.000")
        move = (await s.execute(select(StockMovement).where(StockMovement.reason == "purchase"))).scalar_one()
        assert (move.qty_delta, move.unit_cost, move.source_type) == (Decimal("8.000"), Decimal("150000.00"), "goods_receipt")
        assert await _gaps(s) == []                                       # M2-T3 holds

    async with session_factory() as s:  # the remaining 2 close the PO
        await _set_tenant(s, c["bid"])
        received = await receive_goods(
            s, c["bid"], po_id=c["po"],
            lines=[GrLineSpec(item_id=c["beans"], quantity=Decimal(2), unit_cost=Decimal(150000), uom_id=c["kg"], po_line_id=c["po_line"])],
        )
        await s.commit()
        assert received.po.status == "received" and received.receipt.number == 2
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert (await s.get(Item, c["beans"])).current_stock == Decimal("12.000")
        assert (await s.get(PurchaseOrder, c["po"])).status == "received"
        with pytest.raises(Exception):  # a received PO cannot be cancelled
            await cancel(s, await s.get(PurchaseOrder, c["po"]))
        await s.rollback()


async def test_over_receipt_is_refused_unless_explicitly_allowed(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        with pytest.raises(ReceivingInvalid) as exc:
            await receive_goods(
                s, c["bid"], po_id=c["po"],
                lines=[GrLineSpec(item_id=c["beans"], quantity=Decimal(11), unit_cost=Decimal(150000), uom_id=c["kg"], po_line_id=c["po_line"])],
            )
        assert exc.value.code == "over_receipt" and "dipesan 10" in exc.value.detail
        await s.rollback()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert (await s.get(Item, c["beans"])).current_stock == Decimal("2.000")   # nothing moved
        assert (await s.execute(select(GoodsReceipt))).scalars().all() == []
        received = await receive_goods(
            s, c["bid"], po_id=c["po"], allow_over_receipt=True,
            lines=[GrLineSpec(item_id=c["beans"], quantity=Decimal(11), unit_cost=Decimal(150000), uom_id=c["kg"], po_line_id=c["po_line"])],
        )
        await s.commit()
        assert received.po.status == "received"
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert (await s.get(PoLine, c["po_line"])).received_quantity == Decimal("11.000")
        assert (await s.get(Item, c["beans"])).current_stock == Decimal("13.000")
        assert await _gaps(s) == []


async def test_receiving_in_grams_against_a_kg_po_line(session_factory, shop):
    """The PO says 10 kg; the delivery note says 2.500 g at 150/g."""
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        received = await receive_goods(
            s, c["bid"], po_id=c["po"],
            lines=[GrLineSpec(item_id=c["beans"], quantity=Decimal(2500), unit_cost=Decimal(150), uom_id=c["g"], po_line_id=c["po_line"])],
        )
        await s.commit()
        line = received.lines[0]
        assert line.quantity_item_unit == Decimal("2.500") and line.unit_cost_item_unit == Decimal("150000.00")
        assert line.line_total == Decimal("375000.00")
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert (await s.get(PoLine, c["po_line"])).received_quantity == Decimal("2.500")
        assert (await s.get(PurchaseOrder, c["po"])).status == "partially_received"
        beans = await s.get(Item, c["beans"])
        assert beans.current_stock == Decimal("4.500")
        assert beans.cost_price == Decimal("145555.56")   # (2 × 140.000 + 2.5 × 150.000) / 4.5
        assert await _gaps(s) == []


async def test_free_receipt_without_po_and_validation(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        received = await receive_goods(
            s, c["bid"], supplier_id=c["supplier"], notes="beli langsung",
            lines=[GrLineSpec(item_id=c["milk"], quantity=Decimal(12), unit_cost=Decimal(17000)),
                   GrLineSpec(item_id=c["loose"], quantity=Decimal(100), unit_cost=Decimal(500))],
        )
        await s.commit()
        assert received.po is None and received.receipt.subtotal == Decimal("254000.00")
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        milk = await s.get(Item, c["milk"])
        assert milk.current_stock == Decimal("12.000") and milk.cost_price == Decimal("17000.00")  # first cost sets the average
        assert await _gaps(s) == []
        for spec, kwargs, code in (
            (GrLineSpec(item_id=c["loose"], quantity=Decimal(1), uom_id=c["g"]), {}, "no_uom"),
            (GrLineSpec(item_id=c["milk"], quantity=Decimal(1), uom_id=c["g"]), {}, "conversion"),
            (GrLineSpec(item_id=c["milk"], quantity=Decimal(1), po_line_id=c["po_line"]), {"po_id": c["po"]}, "po_line_mismatch"),
            (GrLineSpec(item_id=uuid.uuid4(), quantity=Decimal(1)), {}, "item"),
        ):
            with pytest.raises(ReceivingInvalid) as exc:
                await receive_goods(s, c["bid"], lines=[spec], **kwargs)
            assert exc.value.code == code
            await s.rollback()
            await _set_tenant(s, c["bid"])
        with pytest.raises(ReceivingInvalid) as exc:
            await receive_goods(s, c["bid"], lines=[])
        assert exc.value.code == "empty"


async def test_endpoints_and_supplier_history(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        ctx = SimpleNamespace(session=s, business_id=c["bid"], staff_id=None)
        with pytest.raises(HTTPException) as exc:
            await create_goods_receipt(GoodsReceiptCreateIn(po_id=c["po"], lines=[
                GrLineIn(item_id=c["beans"], quantity=Decimal(12), unit_cost=Decimal(150000), uom_id=c["kg"], po_line_id=c["po_line"])]), ctx)
        assert exc.value.status_code == 409 and "melebihi" in exc.value.detail and "dipesan 10" in exc.value.detail
        await s.rollback()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        ctx = SimpleNamespace(session=s, business_id=c["bid"], staff_id=None)
        out = await create_goods_receipt(GoodsReceiptCreateIn(po_id=c["po"], lines=[
            GrLineIn(item_id=c["beans"], quantity=Decimal(8), unit_cost=Decimal(150000), uom_id=c["kg"], po_line_id=c["po_line"])]), ctx)
        assert out.po_status == "partially_received" and out.supplier_name == "Grosir Barokah"
        assert out.lines[0].stock_after == Decimal("10.000") and out.lines[0].avg_cost_after == Decimal("148000.00")
        assert (await get_goods_receipt(out.id, ctx)).number == 1
        assert [r.number for r in await list_goods_receipts(ctx, limit=50)] == [1]
        h = await supplier_history(c["supplier"], ctx)
        assert h.purchase_count == 1 and h.total_spent == Decimal("1200000.00") and h.receipts[0].kind == "goods_receipt"
        await s.commit()
