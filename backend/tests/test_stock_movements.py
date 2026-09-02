"""M2-T2 — every write to items.current_stock writes a stock_movements row in the
same transaction, and after each path SUM(qty_delta) == items.current_stock.

Paths covered (all of them — grep for `current_stock` writes in app/):
  * services/sales.record_sale                 sale            (atomic UPDATE kept)
  * services/receipts.commit_parse             purchase / opname (create, add, set)
  * services/stock_import.apply_stock_template opname          (create, update)
  * ai/tools.correct_stock                     correction      (WhatsApp)
  * api/dashboard.create_item / update_item    opname / correction
  * app/seed                                   opname + sale   (checked by M2-T3's invariant)

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import os
import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai.tools import correct_stock
from app.api.dashboard import create_item, update_item
from app.core.security import hash_pin
from app.models import Business, Item, Staff, StockMovement
from app.schemas.dashboard import ItemCreateIn, ItemUpdateIn
from app.services.receipts import commit_parse
from app.services.sales import record_sale
from app.services.stock import open_item_stock
from app.services.stock_import import apply_stock_template

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
async def business(session_factory):
    async with session_factory() as session:
        biz = Business(name="Ledger Test", owner_phone=f"62998{uuid.uuid4().hex[:9]}")
        session.add(biz)
        await session.commit()
    yield biz
    async with session_factory() as session:
        row = await session.get(Business, biz.id)
        if row:
            await session.delete(row)
        await session.commit()


async def _ledger_sum(session, item_id) -> Decimal:
    total = (
        await session.execute(
            select(func.coalesce(func.sum(StockMovement.qty_delta), 0)).where(StockMovement.item_id == item_id)
        )
    ).scalar_one()
    return Decimal(total)


async def _movements(session, item_id) -> list[StockMovement]:
    return (
        await session.execute(
            select(StockMovement).where(StockMovement.item_id == item_id).order_by(StockMovement.created_at, StockMovement.id)
        )
    ).scalars().all()


async def _assert_reconciled(session, item_id):
    item = await session.get(Item, item_id)
    await session.refresh(item)
    assert await _ledger_sum(session, item_id) == Decimal(item.current_stock), item.name


async def _new_item(session, business_id, name, stock, cost=Decimal("10000"), unit="pcs") -> Item:
    item = Item(business_id=business_id, name=name, unit=unit, current_stock=Decimal(stock),
                cost_price=cost, sell_price=Decimal("20000"))
    session.add(item)
    await session.flush()
    await open_item_stock(session, item, reason="opname", source_type="test", unit_cost=cost)
    return item


async def test_sale_writes_sale_movement_with_cost_snapshot(session_factory, business):
    async with session_factory() as session:
        await _set_tenant(session, business.id)
        staff = Staff(business_id=business.id, name="Kasir", pin_hash=hash_pin("1111"))
        session.add(staff)
        await session.flush()
        item = await _new_item(session, business.id, "Es Kopi", 5, cost=Decimal("8000"))
        recorded = await record_sale(
            session, business_id=business.id, staff_id=staff.id, item_id=item.id, quantity=Decimal(2)
        )
        await session.commit()
        item_id, sale_id, staff_id = item.id, recorded.line.id, staff.id  # sale id == line id (M3-T2)

    async with session_factory() as session:
        await _set_tenant(session, business.id)
        rows = await _movements(session, item_id)
        assert [r.reason for r in rows] == ["opname", "sale"]
        sale_row = rows[1]
        assert sale_row.qty_delta == Decimal("-2.000")
        assert sale_row.unit_cost == Decimal("8000.00")  # today's cost, snapshotted
        assert sale_row.source_type == "sale" and sale_row.source_id == sale_id
        assert sale_row.staff_id == staff_id
        await _assert_reconciled(session, item_id)  # 5 - 2 == 3


async def test_receipt_commit_creates_adds_and_sets(session_factory, business):
    async with session_factory() as session:
        await _set_tenant(session, business.id)
        existing = await _new_item(session, business.id, "Gula Aren", 4, cost=Decimal("30000"), unit="kg")
        await session.commit()
        existing_id = existing.id

    parsed_receipt = {
        "document_type": "receipt", "supplier": "Toko Manis", "date": "2026-09-01",
        "items": [
            {"name": "Gula Aren", "quantity": 3, "unit": "kg", "unit_price": 38000, "line_total": 114000},
            {"name": "Susu UHT", "quantity": 12, "unit": "liter", "unit_price": 17000, "line_total": 204000},
        ],
        "total_amount": 318000, "confidence": "high", "ambiguities": [],
    }
    with patch("app.services.rag.embed_receipt", new=AsyncMock()):
        async with session_factory() as session:
            await _set_tenant(session, business.id)
            biz = await session.get(Business, business.id)
            facts = await commit_parse(session, biz, parsed_receipt, "receipts/x.jpg")
            await session.commit()
    assert facts["saved"]

    async with session_factory() as session:
        await _set_tenant(session, business.id)
        rows = await _movements(session, existing_id)
        assert [r.reason for r in rows] == ["opname", "purchase"]
        assert rows[1].qty_delta == Decimal("3.000") and rows[1].unit_cost == Decimal("38000.00")
        assert rows[1].source_type == "receipt" and rows[1].source_id is not None
        await _assert_reconciled(session, existing_id)  # 4 + 3 == 7

        created = (await session.execute(select(Item).where(Item.name == "Susu UHT"))).scalar_one()
        crows = await _movements(session, created.id)
        assert [r.reason for r in crows] == ["purchase"]
        assert crows[0].qty_delta == Decimal("12.000") and crows[0].unit_cost == Decimal("17000.00")
        await _assert_reconciled(session, created.id)
        created_id = created.id

    # A stock book photo is a count: the existing 7 kg becomes 5 kg via an opname row
    # carrying the difference and no cost (a count knows no price).
    parsed_ledger = {
        "document_type": "stock_ledger", "supplier": "", "date": "",
        "items": [{"name": "Gula Aren", "quantity": 5, "unit": "kg", "unit_price": 0, "line_total": 0}],
        "total_amount": 0, "confidence": "high", "ambiguities": [],
    }
    with patch("app.services.rag.embed_receipt", new=AsyncMock()):
        async with session_factory() as session:
            await _set_tenant(session, business.id)
            biz = await session.get(Business, business.id)
            await commit_parse(session, biz, parsed_ledger, "receipts/y.jpg")
            await session.commit()

    async with session_factory() as session:
        await _set_tenant(session, business.id)
        rows = await _movements(session, existing_id)
        assert [r.reason for r in rows] == ["opname", "purchase", "opname"]
        assert rows[2].qty_delta == Decimal("-2.000") and rows[2].unit_cost is None
        await _assert_reconciled(session, existing_id)  # 5
        await _assert_reconciled(session, created_id)


async def test_excel_import_creates_and_updates_with_opname_rows(session_factory, business):
    async with session_factory() as session:
        await _set_tenant(session, business.id)
        existing = await _new_item(session, business.id, "Biji Arabica", 8, cost=Decimal("145000"), unit="kg")
        await session.commit()
        existing_id = existing.id

    rows = [
        {"name": "Biji Arabica", "quantity": Decimal("6.5"), "unit": "kg", "cost_price": Decimal("150000"),
         "sell_price": Decimal(0), "reorder_threshold": Decimal(3)},
        {"name": "Gula Pasir", "quantity": Decimal("10"), "unit": "kg", "cost_price": Decimal(0),
         "sell_price": Decimal(0), "reorder_threshold": Decimal(2)},
    ]
    async with session_factory() as session:
        await _set_tenant(session, business.id)
        biz = await session.get(Business, business.id)
        result = await apply_stock_template(session, biz, rows)
        await session.commit()
    assert result["saved"]

    async with session_factory() as session:
        await _set_tenant(session, business.id)
        erows = await _movements(session, existing_id)
        assert [r.reason for r in erows] == ["opname", "opname"]
        assert erows[1].qty_delta == Decimal("-1.500") and erows[1].source_type == "stock_import"
        assert erows[1].unit_cost == Decimal("150000.00")
        await _assert_reconciled(session, existing_id)  # 6.5

        new = (await session.execute(select(Item).where(Item.name == "Gula Pasir"))).scalar_one()
        nrows = await _movements(session, new.id)
        assert [r.reason for r in nrows] == ["opname"]
        assert nrows[0].qty_delta == Decimal("10.000") and nrows[0].unit_cost is None  # no cost given
        await _assert_reconciled(session, new.id)


async def test_whatsapp_correction_writes_correction_row(session_factory, business):
    async with session_factory() as session:
        await _set_tenant(session, business.id)
        item = await _new_item(session, business.id, "Croissant", 12)
        await session.commit()
        item_id = item.id

    async with session_factory() as session:
        await _set_tenant(session, business.id)
        biz = await session.get(Business, business.id)
        facts = await correct_stock(session, biz, {"item_name": "croissant", "new_quantity": 9})
        await session.commit()
    assert facts["ok"] and facts["old_stock"] == 12 and facts["new_stock"] == 9

    async with session_factory() as session:
        await _set_tenant(session, business.id)
        rows = await _movements(session, item_id)
        assert [r.reason for r in rows] == ["opname", "correction"]
        assert rows[1].qty_delta == Decimal("-3.000") and rows[1].source_type == "whatsapp"
        assert rows[1].unit_cost is None
        await _assert_reconciled(session, item_id)


async def test_dashboard_create_and_edit_stock(session_factory, business):
    async with session_factory() as session:
        await _set_tenant(session, business.id)
        ctx = SimpleNamespace(session=session, business_id=business.id, staff_id=None)
        out = await create_item(
            ItemCreateIn(name="Matcha Latte", unit="cup", current_stock=Decimal(25),
                         cost_price=Decimal(11000), sell_price=Decimal(28000)),
            ctx,
        )
        await session.commit()
        item_id = out.id

    async with session_factory() as session:
        await _set_tenant(session, business.id)
        rows = await _movements(session, item_id)
        assert [r.reason for r in rows] == ["opname"]
        assert rows[0].qty_delta == Decimal("25.000") and rows[0].source_type == "dashboard"
        assert rows[0].unit_cost == Decimal("11000.00")
        await _assert_reconciled(session, item_id)

    async with session_factory() as session:  # edit the figure → correction row
        await _set_tenant(session, business.id)
        ctx = SimpleNamespace(session=session, business_id=business.id, staff_id=None)
        await update_item(item_id, ItemUpdateIn(current_stock=Decimal(30)), ctx)
        await session.commit()

    async with session_factory() as session:  # edit only the name → no ledger row
        await _set_tenant(session, business.id)
        ctx = SimpleNamespace(session=session, business_id=business.id, staff_id=None)
        await update_item(item_id, ItemUpdateIn(name="Matcha Latte Besar"), ctx)
        await session.commit()

    async with session_factory() as session:
        await _set_tenant(session, business.id)
        rows = await _movements(session, item_id)
        assert [r.reason for r in rows] == ["opname", "correction"]
        assert rows[1].qty_delta == Decimal("5.000")
        await _assert_reconciled(session, item_id)  # 30


async def test_zero_stock_item_has_no_opening_row(session_factory, business):
    async with session_factory() as session:
        await _set_tenant(session, business.id)
        ctx = SimpleNamespace(session=session, business_id=business.id, staff_id=None)
        out = await create_item(ItemCreateIn(name="Baru", unit="pcs"), ctx)
        await session.commit()
        item_id = out.id

    async with session_factory() as session:  # SET LOCAL ends with the commit above
        await _set_tenant(session, business.id)
        assert await _movements(session, item_id) == []
        await _assert_reconciled(session, item_id)  # 0 == 0
