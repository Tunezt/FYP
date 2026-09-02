"""M5-T2 — purchase orders: draft, ordered, partially received, received, cancelled.

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import os
import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.dashboard import add_po_line, cancel_po, create_po, delete_po_line, edit_po_line, get_po, list_purchase_orders, order_po
from app.models import Business, Item, PoLine, PurchaseOrder, StockMovement, Supplier
from app.schemas.dashboard import PoLineIn, PoLineUpdateIn, PurchaseOrderCreateIn
from app.services.purchasing import (
    PoLineSpec,
    PurchaseOrderInvalid,
    add_line,
    cancel,
    create_purchase_order,
    mark_ordered,
    refresh_status_from_lines,
    remove_line,
)
from app.services.suppliers import create_supplier, update_supplier
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


@pytest.fixture
async def shop(session_factory):
    async with session_factory() as s:
        biz = Business(name="PO Test", owner_phone=f"62986{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await seed_books(s, bid)
        uoms = await ensure_standard_uoms(s, bid)
        supplier = await create_supplier(s, bid, name="Grosir Barokah")
        beans = Item(business_id=bid, name="Biji Arabica", unit="kg", current_stock=Decimal(2), cost_price=Decimal(140000), uom_id=uoms["kg"].id)
        milk = Item(business_id=bid, name="Susu UHT", unit="liter", current_stock=Decimal(5), cost_price=Decimal(17000), uom_id=uoms["liter"].id)
        s.add_all([beans, milk])
        await s.commit()
        ids = {"bid": bid, "supplier": supplier.id, "beans": beans.id, "milk": milk.id, "kg": uoms["kg"].id, "g": uoms["g"].id}
    yield ids
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


async def test_draft_lifecycle_and_totals(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        po = await create_purchase_order(
            s, c["bid"], supplier_id=c["supplier"], notes="minggu depan", expected_at=date(2026, 9, 10),
            lines=[PoLineSpec(item_id=c["beans"], quantity=Decimal(10), unit_cost=Decimal(140000), uom_id=c["kg"])],
        )
        assert po.status == "draft" and po.number == 1 and po.subtotal == Decimal("1400000.00")
        milk_line = await add_line(s, po, PoLineSpec(item_id=c["milk"], quantity=Decimal(12), unit_cost=Decimal(17000)))
        assert po.subtotal == Decimal("1604000.00")
        await remove_line(s, po, milk_line)
        assert po.subtotal == Decimal("1400000.00")
        po2 = await create_purchase_order(s, c["bid"], supplier_id=c["supplier"], lines=[])
        assert po2.number == 2
        with pytest.raises(PurchaseOrderInvalid) as exc:
            await mark_ordered(s, po2)
        assert exc.value.code == "empty"
        await mark_ordered(s, po)
        assert po.status == "ordered" and po.ordered_at is not None
        with pytest.raises(PurchaseOrderInvalid) as exc:  # frozen once ordered
            await add_line(s, po, PoLineSpec(item_id=c["milk"], quantity=Decimal(1)))
        assert exc.value.code == "not_draft"
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert (await s.execute(select(StockMovement))).scalars().all() == []  # a PO never touches stock
        assert (await s.get(Item, c["beans"])).current_stock == Decimal("2.000")


async def test_cancel_rules(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        po = await create_purchase_order(s, c["bid"], supplier_id=c["supplier"],
                                         lines=[PoLineSpec(item_id=c["beans"], quantity=Decimal(1), unit_cost=Decimal(1))])
        await mark_ordered(s, po)
        await cancel(s, po)
        assert po.status == "cancelled" and po.cancelled_at is not None
        with pytest.raises(PurchaseOrderInvalid) as exc:
            await cancel(s, po)
        assert exc.value.code == "not_open"
        # Simulate a partial receipt (M5-T3 does this for real): cancel is refused.
        po2 = await create_purchase_order(s, c["bid"], supplier_id=c["supplier"],
                                          lines=[PoLineSpec(item_id=c["beans"], quantity=Decimal(10), unit_cost=Decimal(1))])
        await mark_ordered(s, po2)
        line = (await s.execute(select(PoLine).where(PoLine.po_id == po2.id))).scalar_one()
        line.received_quantity = Decimal(4)
        po2.status = refresh_status_from_lines(po2, [line])
        assert po2.status == "partially_received"
        with pytest.raises(PurchaseOrderInvalid) as exc:
            await cancel(s, po2)
        assert exc.value.code in ("not_open", "already_received")
        line.received_quantity = Decimal(10)
        assert refresh_status_from_lines(po2, [line]) == "received"
        await s.commit()


async def test_validation(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        with pytest.raises(PurchaseOrderInvalid) as exc:
            await create_purchase_order(s, c["bid"], supplier_id=uuid.uuid4(), lines=[])
        assert exc.value.code == "supplier"
        sup = await s.get(Supplier, c["supplier"])
        await update_supplier(s, sup, is_active=False)
        with pytest.raises(PurchaseOrderInvalid) as exc:
            await create_purchase_order(s, c["bid"], supplier_id=c["supplier"], lines=[])
        assert exc.value.code == "supplier_inactive"
        await update_supplier(s, sup, is_active=True)
        po = await create_purchase_order(s, c["bid"], supplier_id=c["supplier"], lines=[])
        for spec, code in (
            (PoLineSpec(item_id=uuid.uuid4(), quantity=Decimal(1)), "item"),
            (PoLineSpec(item_id=c["beans"], quantity=Decimal(0)), "quantity"),
            (PoLineSpec(item_id=c["beans"], quantity=Decimal(1), uom_id=uuid.uuid4()), "uom"),
        ):
            with pytest.raises(PurchaseOrderInvalid) as exc:
                await add_line(s, po, spec)
            assert exc.value.code == code
        await s.rollback()


async def test_owner_endpoints(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        ctx = SimpleNamespace(session=s, business_id=c["bid"], staff_id=None)
        out = await create_po(PurchaseOrderCreateIn(
            supplier_id=c["supplier"], notes="urgent",
            lines=[PoLineIn(item_id=c["beans"], quantity=Decimal(5), unit_cost=Decimal(140000), uom_id=c["kg"])],
        ), ctx)
        assert out.status == "draft" and out.supplier_name == "Grosir Barokah" and out.subtotal == Decimal("700000.00")
        assert out.lines[0].item_name == "Biji Arabica" and out.lines[0].uom_code == "kg" and out.lines[0].received_quantity == Decimal(0)
        out = await add_po_line(out.id, PoLineIn(item_id=c["milk"], quantity=Decimal(6), unit_cost=Decimal(17000)), ctx)
        assert len(out.lines) == 2 and out.subtotal == Decimal("802000.00")
        milk_line = next(l for l in out.lines if l.item_id == c["milk"])
        out = await edit_po_line(out.id, milk_line.id, PoLineUpdateIn(quantity=Decimal(12)), ctx)
        assert out.subtotal == Decimal("904000.00")
        out = await delete_po_line(out.id, milk_line.id, ctx)
        assert len(out.lines) == 1
        out = await order_po(out.id, ctx)
        assert out.status == "ordered"
        with pytest.raises(HTTPException) as exc:
            await add_po_line(out.id, PoLineIn(item_id=c["milk"], quantity=Decimal(1)), ctx)
        assert exc.value.status_code == 409 and "tidak bisa diubah" in exc.value.detail
        assert [p.number for p in await list_purchase_orders(ctx, status="ordered")] == [1]
        assert (await get_po(out.id, ctx)).status == "ordered"
        out = await cancel_po(out.id, ctx)
        assert out.status == "cancelled"
        with pytest.raises(HTTPException) as exc:
            await get_po(uuid.uuid4(), ctx)
        assert exc.value.status_code == 404
        await s.commit()
