"""M2-T4 — the ledger backfill makes pre-ledger data satisfy the M2-T3 invariant.

Builds a business the way the database looked before migration 0003 existed —
items with a running stock figure, 30 days of `sales` rows, a receipt photo
whose parsed lines added stock, a stock-book photo — with NO stock_movements
rows, then runs the exact statements migration 0004 runs (as app_role pinned
to the tenant; the migration runs them as the elevated role across tenants).

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import hash_pin
from app.models import Business, Item, Order, OrderLine, Receipt, Staff, StockMovement
from app.services.stock_backfill import BACKFILL_STATEMENTS, ROLLBACK_STATEMENTS

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
async def legacy_business(session_factory):
    """Pre-migration shape: stock figures and sales history, zero ledger rows."""
    now = datetime.now(timezone.utc)
    async with session_factory() as s:
        biz = Business(name="Legacy Warung", owner_phone=f"62996{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        staff = Staff(business_id=bid, name="Lama", pin_hash=hash_pin("9999"))
        s.add(staff)
        await s.flush()
        kopi = Item(business_id=bid, name="Kopi Susu", unit="cup", current_stock=Decimal(40),
                    cost_price=Decimal(8000), sell_price=Decimal(22000), created_at=now - timedelta(days=40))
        gula = Item(business_id=bid, name="Gula Aren", unit="kg", current_stock=Decimal("4.500"),
                    cost_price=Decimal(38000), sell_price=Decimal(0), created_at=now - timedelta(days=40))
        idle = Item(business_id=bid, name="Sedotan", unit="pcs", current_stock=Decimal(0))
        s.add_all([kopi, gula, idle])
        await s.flush()
        for d in range(30, 0, -1):  # 30 days × 2 cups, no ledger rows (pre-M2 code)
            # Sales live in the order model since M3-T2 (the `sales` view reads them);
            # the backfill's `from sales` query sees these lines exactly like legacy rows.
            order = Order(business_id=bid, staff_id=staff.id, subtotal=Decimal(44000), total=Decimal(44000),
                          sold_at=now - timedelta(days=d))
            s.add(order)
            await s.flush()
            s.add(OrderLine(business_id=bid, order_id=order.id, item_id=kopi.id, quantity=Decimal(2),
                            unit_price=Decimal(22000), line_total=Decimal(44000)))
        s.add(Receipt(  # a purchase photo whose commit added 5 kg of gula at 38.000
            business_id=bid, image_url="receipts/legacy.jpg", supplier="Toko Manis",
            total_amount=Decimal(190000), created_at=now - timedelta(days=10),
            parsed_data={"document_type": "receipt", "items": [
                {"name": "Gula Aren", "quantity": 5, "unit": "kg", "unit_price": 38000, "line_total": 190000},
                {"name": "Barang Tak Dikenal", "quantity": 1, "unit": "pcs", "unit_price": 0, "line_total": 0},
            ]},
        ))
        s.add(Receipt(  # a stock-book count: absolute, prior state unknown → must be skipped
            business_id=bid, image_url="receipts/legacy-count.jpg", created_at=now - timedelta(days=5),
            parsed_data={"document_type": "stock_ledger", "items": [
                {"name": "Kopi Susu", "quantity": 99, "unit": "cup", "unit_price": 0, "line_total": 0},
            ]},
        ))
        await s.commit()
        ids = {"kopi": kopi.id, "gula": gula.id, "idle": idle.id, "staff": staff.id}
    yield bid, ids
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


async def _run_backfill(session):
    for statement in BACKFILL_STATEMENTS:
        await session.execute(text(statement))


async def _gaps(session):
    return (await session.execute(text(
        """
        select i.name, i.current_stock, coalesce(sum(m.qty_delta), 0)
        from items i left join stock_movements m on m.item_id = i.id
        group by i.id, i.name, i.current_stock
        having i.current_stock <> coalesce(sum(m.qty_delta), 0)
        """
    ))).all()


async def test_backfill_reconciles_legacy_data(session_factory, legacy_business):
    bid, ids = legacy_business
    async with session_factory() as s:
        await _set_tenant(s, bid)
        assert len(await _gaps(s)) == 2  # kopi and gula are unreconciled before; idle is 0 == 0
        await _run_backfill(s)
        await s.commit()

    async with session_factory() as s:
        await _set_tenant(s, bid)
        assert await _gaps(s) == []  # the M2-T3 invariant now holds for pre-migration data

        kopi_rows = (await s.execute(
            select(StockMovement).where(StockMovement.item_id == ids["kopi"]).order_by(StockMovement.created_at)
        )).scalars().all()
        assert [r.reason for r in kopi_rows] == ["opname"] + ["sale"] * 30
        assert kopi_rows[0].source_type == "backfill_opening" and kopi_rows[0].qty_delta == Decimal(100)  # 40 + 60 sold
        assert all(r.unit_cost is None for r in kopi_rows)  # cost at the time unknown → NULL, never a guess
        assert all(r.source_type == "backfill_sale" and r.staff_id == ids["staff"] for r in kopi_rows[1:])
        assert {r.qty_delta for r in kopi_rows[1:]} == {Decimal("-2.000")}
        # The stock-book count (99) was NOT turned into a movement.
        assert not any(r.source_type == "backfill_receipt" for r in kopi_rows)

        gula_rows = (await s.execute(
            select(StockMovement).where(StockMovement.item_id == ids["gula"]).order_by(StockMovement.created_at)
        )).scalars().all()
        assert [(r.reason, r.source_type) for r in gula_rows] == [("opname", "backfill_opening"), ("purchase", "backfill_receipt")]
        assert gula_rows[1].qty_delta == Decimal("5.000") and gula_rows[1].unit_cost == Decimal("38000.00")
        # The opening balance is whatever the history implies, even when that is odd:
        # the photo added 5 kg and 4.5 kg remain with no sale ever recorded, so the
        # implied opening figure is −0.5 kg. The backfill records exactly that rather
        # than inventing a waste row. The invariant holds and the discrepancy stays
        # visible in the ledger instead of being hidden.
        assert gula_rows[0].qty_delta == Decimal("-0.500")

        idle_rows = (await s.execute(select(func.count(StockMovement.id)).where(StockMovement.item_id == ids["idle"]))).scalar_one()
        assert idle_rows == 0  # nothing to reconstruct for a zero-stock item with no history

        # The unmatched receipt line ("Barang Tak Dikenal") created nothing.
        names = (await s.execute(select(Item.name))).scalars().all()
        assert "Barang Tak Dikenal" not in names


async def test_backfill_is_idempotent_and_skips_items_with_history(session_factory, legacy_business):
    bid, ids = legacy_business
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await _run_backfill(s)
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, bid)
        before = (await s.execute(select(func.count(StockMovement.id)))).scalar_one()
        assert before == 31 + 2
        # A live row written after M2-T2 for gula must protect gula from any re-backfill.
        s.add(StockMovement(business_id=bid, item_id=ids["gula"], qty_delta=Decimal("-0.250"),
                            reason="waste", source_type="test"))
        item = await s.get(Item, ids["gula"])
        item.current_stock = Decimal("4.250")
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await _run_backfill(s)
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, bid)
        after = (await s.execute(select(func.count(StockMovement.id)))).scalar_one()
        assert after == before + 1  # only the live waste row; the backfill added nothing
        assert await _gaps(s) == []


async def test_rollback_removes_only_backfilled_rows(session_factory, legacy_business):
    bid, ids = legacy_business
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await _run_backfill(s)
        s.add(StockMovement(business_id=bid, item_id=ids["kopi"], qty_delta=Decimal("-1"),
                            reason="sale", source_type="sale", source_id=uuid.uuid4()))
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, bid)
        for statement in ROLLBACK_STATEMENTS:
            await s.execute(text(statement))
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, bid)
        rows = (await s.execute(select(StockMovement.source_type))).scalars().all()
        assert rows == ["sale"]  # the live row survives, every backfill_* row is gone
