"""M3-T4 — void and refund are reversals, gated by the manager (owner) PIN.

Done-when (roadmap): voiding returns stock to its prior level VIA A NEW MOVEMENT
ROW, and the original order is still readable in full. Nothing deleted, nothing
updated in place (the order's status is the one state transition).

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import os
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.pos import pos_void_order
from app.core.security import hash_pin
from app.models import Business, Item, Order, OrderLine, Payment, Sale, Staff, StockMovement
from app.schemas.pos import ReversalIn
from app.services.orders import (
    ManagerPinRejected,
    OrderLineSpec,
    OrderNotReversible,
    PaymentSpec,
    create_order,
    refund_order,
    void_order,
)
from app.services.stock import open_item_stock

from tests.conftest import seed_books

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")
OWNER_PIN, STAFF_PIN = "1234", "5678"


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
async def sold(session_factory):
    """A completed order: 2 coffee (10 → 8) + 1 toast (3 → 2), 68.000 split cash/QRIS."""
    async with session_factory() as s:
        biz = Business(name="Reversal Test", owner_phone=f"62994{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await seed_books(s, bid)
        owner = Staff(business_id=bid, name="Bu Ratna", role="owner", pin_hash=hash_pin(OWNER_PIN))
        cashier = Staff(business_id=bid, name="Kasir", pin_hash=hash_pin(STAFF_PIN))
        s.add_all([owner, cashier])
        await s.flush()
        kopi = Item(business_id=bid, name="Kopi", unit="cup", current_stock=Decimal(10), cost_price=Decimal(8000), sell_price=Decimal(22000))
        roti = Item(business_id=bid, name="Roti", unit="pcs", current_stock=Decimal(3), cost_price=Decimal(9000), sell_price=Decimal(24000))
        s.add_all([kopi, roti])
        await s.flush()
        await open_item_stock(s, kopi, unit_cost=kopi.cost_price)
        await open_item_stock(s, roti, unit_cost=roti.cost_price)
        created = await create_order(
            s, business_id=bid, staff_id=cashier.id,
            lines=[OrderLineSpec(item_id=kopi.id, quantity=Decimal(2)), OrderLineSpec(item_id=roti.id, quantity=Decimal(1))],
            payments=[PaymentSpec(method="cash", amount=Decimal(34000)), PaymentSpec(method="qris", amount=Decimal(34000))],
        )
        await s.commit()
        ids = {"bid": bid, "cashier": cashier.id, "kopi": kopi.id, "roti": roti.id, "order": created.order.id,
               "lines": [cl.line.id for cl in created.lines]}
    yield ids
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


async def _reconciled(s) -> bool:
    return (await s.execute(text(
        "select i.id from items i left join stock_movements m on m.item_id = i.id "
        "group by i.id, i.current_stock having i.current_stock <> coalesce(sum(m.qty_delta), 0)"
    ))).all() == []


async def test_void_restocks_via_new_movement_rows_and_keeps_original(session_factory, sold):
    async with session_factory() as s:
        await _set_tenant(s, sold["bid"])
        rev = await void_order(s, business_id=sold["bid"], order_id=sold["order"], staff_id=sold["cashier"],
                               manager_pin=OWNER_PIN, note="salah pesan")
        await s.commit()
        assert rev.restocked == {sold["kopi"]: Decimal("10.000"), sold["roti"]: Decimal("3.000")}

    async with session_factory() as s:
        await _set_tenant(s, sold["bid"])
        # Stock back to prior level …
        kopi, roti = await s.get(Item, sold["kopi"]), await s.get(Item, sold["roti"])
        assert (kopi.current_stock, roti.current_stock) == (Decimal("10.000"), Decimal("3.000"))
        # … via NEW rows: opening, sale, sale_void per item — nothing removed.
        for item_id in (sold["kopi"], sold["roti"]):
            reasons = (await s.execute(
                select(StockMovement.reason).where(StockMovement.item_id == item_id).order_by(StockMovement.created_at, StockMovement.id)
            )).scalars().all()
            assert reasons == ["opname", "sale", "sale_void"]
        voids = (await s.execute(select(StockMovement).where(StockMovement.reason == "sale_void"))).scalars().all()
        assert sorted(m.qty_delta for m in voids) == [Decimal("1.000"), Decimal("2.000")]
        assert {m.unit_cost for m in voids} == {Decimal("8000.00"), Decimal("9000.00")}  # cost as sold, not today's
        assert await _reconciled(s)

        # The original order is readable in full: same totals, original lines intact.
        order = await s.get(Order, sold["order"])
        assert order.status == "voided" and order.total == Decimal("68000.00")
        lines = (await s.execute(select(OrderLine).where(OrderLine.order_id == sold["order"]).order_by(OrderLine.created_at, OrderLine.id))).scalars().all()
        assert len(lines) == 4
        originals = [l for l in lines if l.id in sold["lines"]]
        assert sorted(l.quantity for l in originals) == [Decimal("1.000"), Decimal("2.000")]
        reversing = [l for l in lines if l.id not in sold["lines"]]
        assert sorted(l.quantity for l in reversing) == [Decimal("-2.000"), Decimal("-1.000")]
        assert sum(l.line_total for l in lines) == Decimal(0)
        assert all("void oleh Bu Ratna" in l.notes and "salah pesan" in l.notes for l in reversing)
        assert {m.source_id for m in voids} == {l.id for l in reversing}

        pays = (await s.execute(select(Payment).where(Payment.order_id == sold["order"]))).scalars().all()
        assert sorted(p.amount for p in pays) == [Decimal("-34000.00"), Decimal("-34000.00"), Decimal("34000.00"), Decimal("34000.00")]
        assert sum(p.amount for p in pays) == Decimal(0)

        # Through the compatibility view the order nets to zero revenue.
        view_total = (await s.execute(select(func.sum(Sale.total_price)).where(Sale.item_id.in_([sold["kopi"], sold["roti"]])))).scalar_one()
        assert view_total == Decimal(0)


async def test_wrong_manager_pin_writes_nothing(session_factory, sold):
    async with session_factory() as s:
        await _set_tenant(s, sold["bid"])
        with pytest.raises(ManagerPinRejected):
            await void_order(s, business_id=sold["bid"], order_id=sold["order"], staff_id=sold["cashier"], manager_pin=STAFF_PIN)
        await s.rollback()
    async with session_factory() as s:
        await _set_tenant(s, sold["bid"])
        assert (await s.get(Order, sold["order"])).status == "completed"
        assert (await s.execute(select(func.count(OrderLine.id)))).scalar_one() == 2
        assert (await s.get(Item, sold["kopi"])).current_stock == Decimal("8.000")


async def test_void_twice_is_refused(session_factory, sold):
    async with session_factory() as s:
        await _set_tenant(s, sold["bid"])
        await void_order(s, business_id=sold["bid"], order_id=sold["order"], staff_id=sold["cashier"], manager_pin=OWNER_PIN)
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, sold["bid"])
        with pytest.raises(OrderNotReversible) as exc:
            await void_order(s, business_id=sold["bid"], order_id=sold["order"], staff_id=sold["cashier"], manager_pin=OWNER_PIN)
        assert exc.value.status == "voided"
        await s.rollback()
    async with session_factory() as s:
        await _set_tenant(s, sold["bid"])
        assert (await s.execute(select(func.count(OrderLine.id)))).scalar_one() == 4  # still one reversal only
        assert await _reconciled(s)


async def test_refund_without_restock_reverses_money_not_stock(session_factory, sold):
    async with session_factory() as s:
        await _set_tenant(s, sold["bid"])
        rev = await refund_order(s, business_id=sold["bid"], order_id=sold["order"], staff_id=sold["cashier"],
                                 manager_pin=OWNER_PIN, restock=False, note="sudah dimakan, komplain rasa")
        await s.commit()
        assert rev.restocked == {}
    async with session_factory() as s:
        await _set_tenant(s, sold["bid"])
        assert (await s.get(Order, sold["order"])).status == "refunded"
        assert (await s.get(Item, sold["kopi"])).current_stock == Decimal("8.000")  # goods not back
        reasons = (await s.execute(select(StockMovement.reason))).scalars().all()
        assert "refund" not in reasons and len(reasons) == 4
        pays = (await s.execute(select(Payment).where(Payment.order_id == sold["order"]))).scalars().all()
        assert sum(p.amount for p in pays) == Decimal(0)
        assert await _reconciled(s)


async def test_refund_with_restock_writes_refund_movements(session_factory, sold):
    async with session_factory() as s:
        await _set_tenant(s, sold["bid"])
        await refund_order(s, business_id=sold["bid"], order_id=sold["order"], staff_id=sold["cashier"], manager_pin=OWNER_PIN)
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, sold["bid"])
        assert (await s.get(Item, sold["kopi"])).current_stock == Decimal("10.000")
        refunds = (await s.execute(select(StockMovement).where(StockMovement.reason == "refund"))).scalars().all()
        assert sorted(m.qty_delta for m in refunds) == [Decimal("1.000"), Decimal("2.000")]
        assert await _reconciled(s)


async def test_pos_void_endpoint_gates_on_pin_with_indonesian_errors(session_factory, sold):
    async with session_factory() as s:
        await _set_tenant(s, sold["bid"])
        ctx = SimpleNamespace(session=s, business_id=sold["bid"], staff_id=sold["cashier"])
        with pytest.raises(HTTPException) as exc:
            await pos_void_order(sold["order"], ReversalIn(manager_pin="0000"), ctx)
        assert exc.value.status_code == 403 and "PIN manajer" in exc.value.detail
        await s.rollback()
    async with session_factory() as s:
        await _set_tenant(s, sold["bid"])
        ctx = SimpleNamespace(session=s, business_id=sold["bid"], staff_id=sold["cashier"])
        out = await pos_void_order(sold["order"], ReversalIn(manager_pin=OWNER_PIN, note="batal"), ctx)
        await s.commit()
    assert out.status == "voided"
    assert sorted(l.quantity for l in out.reversing_lines) == [Decimal("-2.000"), Decimal("-1.000")]
    assert {l.stock_after for l in out.reversing_lines} == {Decimal("10.000"), Decimal("3.000")}
    assert sorted(p.amount for p in out.reversing_payments) == [Decimal("-34000.00"), Decimal("-34000.00")]
    async with session_factory() as s:
        await _set_tenant(s, sold["bid"])
        ctx = SimpleNamespace(session=s, business_id=sold["bid"], staff_id=sold["cashier"])
        with pytest.raises(HTTPException) as exc:
            await pos_void_order(sold["order"], ReversalIn(manager_pin=OWNER_PIN), ctx)
        assert exc.value.status_code == 409 and "sudah dibatalkan" in exc.value.detail
