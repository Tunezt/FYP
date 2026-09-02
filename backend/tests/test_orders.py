"""M3-T3 — the POS writes multi-line orders with split payment, all-or-nothing.

Done-when (roadmap): a two-item sale paid half cash half QRIS produces one
order, two lines, two payments, two stock movements, and the reconciliation
invariant holds. Plus the failure modes that make "all-or-nothing" real.

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

from app.api.pos import pos_create_order
from app.core.security import hash_pin
from app.models import Business, Item, Order, OrderLine, Payment, Sale, Staff, StockMovement
from app.schemas.pos import OrderIn
from app.services.orders import OrderLineSpec, PaymentMismatch, PaymentSpec, create_order
from app.services.sales import InsufficientStock
from app.services.stock import open_item_stock

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
    """A business with a cashier, coffee (10 @ 22.000, cost 8.000) and toast (3 @ 24.000, cost 9.000)."""
    async with session_factory() as s:
        biz = Business(name="Order Test", owner_phone=f"62995{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        staff = Staff(business_id=bid, name="Kasir", pin_hash=hash_pin("1234"))
        s.add(staff)
        await s.flush()
        kopi = Item(business_id=bid, name="Es Kopi Susu", unit="cup", current_stock=Decimal(10),
                    cost_price=Decimal(8000), sell_price=Decimal(22000))
        roti = Item(business_id=bid, name="Roti Bakar", unit="pcs", current_stock=Decimal(3),
                    cost_price=Decimal(9000), sell_price=Decimal(24000))
        s.add_all([kopi, roti])
        await s.flush()
        await open_item_stock(s, kopi, unit_cost=kopi.cost_price)
        await open_item_stock(s, roti, unit_cost=roti.cost_price)
        await s.commit()
        ids = {"bid": bid, "staff": staff.id, "kopi": kopi.id, "roti": roti.id}
    yield ids
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


async def _counts(s):
    """(orders, lines, payments, movements) visible to the pinned tenant."""
    out = []
    for model in (Order, OrderLine, Payment, StockMovement):
        out.append((await s.execute(select(func.count(model.id)))).scalar_one())
    return tuple(out)


async def _reconciled(s) -> bool:
    gaps = (await s.execute(text(
        "select i.id from items i left join stock_movements m on m.item_id = i.id "
        "group by i.id, i.current_stock having i.current_stock <> coalesce(sum(m.qty_delta), 0)"
    ))).all()
    return gaps == []


async def test_two_items_half_cash_half_qris(session_factory, shop):
    """2 coffees (44.000) + 1 toast (24.000) = 68.000, paid 34.000 cash + 34.000 QRIS."""
    async with session_factory() as s:
        await _set_tenant(s, shop["bid"])
        created = await create_order(
            s, business_id=shop["bid"], staff_id=shop["staff"], order_type="dine_in",
            lines=[OrderLineSpec(item_id=shop["kopi"], quantity=Decimal(2)),
                   OrderLineSpec(item_id=shop["roti"], quantity=Decimal(1), notes="tanpa gula")],
            payments=[PaymentSpec(method="cash", amount=Decimal(34000)),
                      PaymentSpec(method="qris", amount=Decimal(34000), reference="QR-0001")],
        )
        await s.commit()
        order_id = created.order.id

    async with session_factory() as s:
        await _set_tenant(s, shop["bid"])
        assert await _counts(s) == (1, 2, 2, 2 + 2)  # one order, two lines, two payments, 2 openings + 2 sales
        order = await s.get(Order, order_id)
        assert order.status == "completed" and order.order_type == "dine_in"
        assert order.subtotal == Decimal("68000.00") and order.total == Decimal("68000.00")

        lines = (await s.execute(select(OrderLine).where(OrderLine.order_id == order_id).order_by(OrderLine.created_at))).scalars().all()
        by_item = {l.item_id: l for l in lines}
        assert by_item[shop["kopi"]].line_total == Decimal("44000.00")
        assert by_item[shop["kopi"]].unit_cost_at_sale == Decimal("8000.00")   # cost snapshot
        assert by_item[shop["roti"]].unit_cost_at_sale == Decimal("9000.00")
        assert by_item[shop["roti"]].notes == "tanpa gula"

        pays = (await s.execute(select(Payment).where(Payment.order_id == order_id))).scalars().all()
        assert sorted((p.method, p.amount) for p in pays) == [("cash", Decimal("34000.00")), ("qris", Decimal("34000.00"))]
        assert sum(p.amount for p in pays) == order.total

        moves = (await s.execute(select(StockMovement).where(StockMovement.reason == "sale"))).scalars().all()
        assert sorted((m.item_id, m.qty_delta) for m in moves) == sorted([(shop["kopi"], Decimal("-2.000")), (shop["roti"], Decimal("-1.000"))])
        assert {m.source_id for m in moves} == {l.id for l in lines}
        assert all(m.unit_cost in (Decimal("8000.00"), Decimal("9000.00")) for m in moves)

        kopi = await s.get(Item, shop["kopi"]); roti = await s.get(Item, shop["roti"])
        assert (kopi.current_stock, roti.current_stock) == (Decimal("8.000"), Decimal("2.000"))
        assert await _reconciled(s)

        # Every line is a row of the compatibility view, so the 8 tools see the sale.
        view_rows = (await s.execute(select(Sale).where(Sale.staff_id == shop["staff"]))).scalars().all()
        assert sorted(r.total_price for r in view_rows) == [Decimal("24000.00"), Decimal("44000.00")]


async def test_out_of_stock_line_rolls_back_the_whole_order(session_factory, shop):
    """Coffee is decremented first, then toast (3 left) is asked for 4: the
    whole order fails and the coffee stock is untouched."""
    async with session_factory() as s:
        await _set_tenant(s, shop["bid"])
        with pytest.raises(InsufficientStock) as exc:
            await create_order(
                s, business_id=shop["bid"], staff_id=shop["staff"],
                lines=[OrderLineSpec(item_id=shop["kopi"], quantity=Decimal(5)),
                       OrderLineSpec(item_id=shop["roti"], quantity=Decimal(4))],
                payments=[PaymentSpec(method="cash", amount=Decimal(206000))],
            )
        assert exc.value.item_name == "Roti Bakar"
        await s.rollback()

    async with session_factory() as s:
        await _set_tenant(s, shop["bid"])
        assert await _counts(s) == (0, 0, 0, 2)  # only the two opening rows
        kopi = await s.get(Item, shop["kopi"])
        assert kopi.current_stock == Decimal("10.000")
        assert await _reconciled(s)


async def test_payments_must_match_total_exactly(session_factory, shop):
    async with session_factory() as s:
        await _set_tenant(s, shop["bid"])
        with pytest.raises(PaymentMismatch) as exc:
            await create_order(
                s, business_id=shop["bid"], staff_id=shop["staff"],
                lines=[OrderLineSpec(item_id=shop["kopi"], quantity=Decimal(1))],
                payments=[PaymentSpec(method="cash", amount=Decimal(20000))],
            )
        assert (exc.value.total, exc.value.paid) == (Decimal("22000.00"), Decimal("20000.00"))
        await s.rollback()
    async with session_factory() as s:
        await _set_tenant(s, shop["bid"])
        assert await _counts(s) == (0, 0, 0, 2)
        assert (await s.get(Item, shop["kopi"])).current_stock == Decimal("10.000")


async def test_pos_endpoint_returns_order_and_translates_errors(session_factory, shop):
    async with session_factory() as s:
        await _set_tenant(s, shop["bid"])
        ctx = SimpleNamespace(session=s, business_id=shop["bid"], staff_id=shop["staff"])
        out = await pos_create_order(OrderIn.model_validate({
            "lines": [{"item_id": str(shop["kopi"]), "quantity": "2"}, {"item_id": str(shop["roti"]), "quantity": "1"}],
            "payments": [{"method": "cash", "amount": "34000"}, {"method": "qris", "amount": "34000", "reference": "QR-9"}],
            "order_type": "takeaway",
        }), ctx)
        await s.commit()
    assert out.total == Decimal("68000.00") and len(out.lines) == 2 and len(out.payments) == 2
    assert {l.item_name for l in out.lines} == {"Es Kopi Susu", "Roti Bakar"}
    assert {l.remaining_stock for l in out.lines} == {Decimal("8.000"), Decimal("2.000")}

    async with session_factory() as s:  # Indonesian error text, HTTP 422, nothing written
        await _set_tenant(s, shop["bid"])
        ctx = SimpleNamespace(session=s, business_id=shop["bid"], staff_id=shop["staff"])
        with pytest.raises(HTTPException) as exc:
            await pos_create_order(OrderIn.model_validate({
                "lines": [{"item_id": str(shop["kopi"]), "quantity": "1"}],
                "payments": [{"method": "cash", "amount": "1000"}],
            }), ctx)
        assert exc.value.status_code == 422 and "tidak sama dengan total" in exc.value.detail
        await s.rollback()

    async with session_factory() as s:
        await _set_tenant(s, shop["bid"])
        assert await _counts(s) == (1, 2, 2, 4)
        assert await _reconciled(s)
