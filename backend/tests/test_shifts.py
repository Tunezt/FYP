"""M7-T1 — shifts: staff, opening float, open/close times, expected cash,
counted cash, variance.

Expected cash here is float + cash payments attributed to the shift (M7-T2
adds cash in/out, M7-T3 posts the variance). Attribution is by the till the
money actually moved through: a refund paid out in a later shift is that
shift's cash, not the original sale's.

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

from app.core.security import hash_pin
from app.models import Business, Item, Order, Payment, Shift, Staff
from app.services.catalog import ensure_default_variant
from app.services.orders import OrderLineSpec, PaymentSpec, create_order, refund_order
from app.services.shifts import ShiftInvalid, cash_summary, close_shift, current_shift, open_shift
from app.services.stock import open_item_stock

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
    """Owner (PIN 1234), cashiers Sari and Budi; Kopi 10 @ cost 8.000 sells 22.000."""
    async with session_factory() as s:
        biz = Business(name="Shift Test", owner_phone=f"62976{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await seed_books(s, bid)
        owner = Staff(business_id=bid, name="Bu Ratna", role="owner", pin_hash=hash_pin("1234"))
        sari = Staff(business_id=bid, name="Sari", pin_hash=hash_pin("2345"))
        budi = Staff(business_id=bid, name="Budi", pin_hash=hash_pin("3456"))
        kopi = Item(business_id=bid, name="Kopi", unit="cup", current_stock=Decimal(10), cost_price=Decimal(8000), sell_price=Decimal(22000))
        s.add_all([owner, sari, budi, kopi])
        await s.flush()
        await open_item_stock(s, kopi, unit_cost=kopi.cost_price)
        await ensure_default_variant(s, kopi)
        await s.commit()
        ids = {"bid": bid, "owner": owner.id, "sari": sari.id, "budi": budi.id, "kopi": kopi.id}
    yield ids
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


async def _sell(s, c, staff_key, qty, payments):
    return await create_order(
        s, business_id=c["bid"], staff_id=c[staff_key],
        lines=[OrderLineSpec(item_id=c["kopi"], quantity=Decimal(qty))],
        payments=[PaymentSpec(method=m, amount=Decimal(a)) for m, a in payments],
    )


async def test_open_sell_close_with_expected_cash_and_variance(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        shift = await open_shift(s, c["bid"], staff_id=c["sari"], opening_float=Decimal(100000))
        assert shift.status == "open" and shift.opening_float == Decimal("100000.00")
        assert shift.expected_cash is None and shift.counted_cash is None and shift.variance is None
        sari_cash = await _sell(s, c, "sari", 2, [("cash", 44000)])
        sari_qris = await _sell(s, c, "sari", 1, [("qris", 22000)])
        budi_cash = await _sell(s, c, "budi", 1, [("cash", 22000)])   # Budi has no shift open
        await s.commit()
        ids = {"shift": shift.id, "sari_cash": sari_cash.order.id, "sari_qris": sari_qris.order.id, "budi": budi_cash.order.id}

    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        # Attribution: Sari's orders and payments carry her shift; Budi's carry none.
        for key in ("sari_cash", "sari_qris"):
            assert (await s.get(Order, ids[key])).shift_id == ids["shift"]
        assert (await s.get(Order, ids["budi"])).shift_id is None
        payments = (await s.execute(select(Payment).order_by(Payment.created_at))).scalars().all()
        assert [(p.method, p.shift_id == ids["shift"]) for p in payments] == [("cash", True), ("qris", True), ("cash", False)]

        shift = await s.get(Shift, ids["shift"])
        live = await cash_summary(s, shift)
        assert (live.cash_sales, live.cash_refunds, live.expected_cash) == (Decimal("44000.00"), Decimal("0.00"), Decimal("144000.00"))

        closed = await close_shift(s, shift, counted_cash=Decimal(140000), closed_by=c["owner"], notes="kurang 4rb")
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        shift = await s.get(Shift, ids["shift"])
        assert shift.status == "closed" and shift.closed_at is not None and shift.closed_by == c["owner"]
        assert shift.expected_cash == Decimal("144000.00") and shift.counted_cash == Decimal("140000.00")
        assert shift.variance == Decimal("-4000.00") and shift.notes == "kurang 4rb"
        assert await current_shift(s, c["sari"]) is None


async def test_refund_paid_out_in_a_later_shift_is_that_shifts_cash(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        first = await open_shift(s, c["bid"], staff_id=c["sari"], opening_float=Decimal(50000))
        sale = await _sell(s, c, "sari", 1, [("cash", 22000)])
        await close_shift(s, first, counted_cash=Decimal(72000), closed_by=c["sari"])
        assert first.variance == Decimal("0.00")
        second = await open_shift(s, c["bid"], staff_id=c["sari"], opening_float=Decimal(50000))
        await refund_order(s, business_id=c["bid"], order_id=sale.order.id, staff_id=c["sari"], manager_pin="1234")
        await s.commit()
        ids = {"first": first.id, "second": second.id, "order": sale.order.id}
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        payments = (await s.execute(select(Payment).where(Payment.order_id == ids["order"]).order_by(Payment.created_at))).scalars().all()
        assert [(p.amount, p.shift_id) for p in payments] == [(Decimal("22000.00"), ids["first"]), (Decimal("-22000.00"), ids["second"])]
        assert (await s.get(Order, ids["order"])).shift_id == ids["first"]   # the sale stays where it was made
        first, second = await s.get(Shift, ids["first"]), await s.get(Shift, ids["second"])
        assert first.expected_cash == Decimal("72000.00")                    # closed figures never move
        live = await cash_summary(s, second)
        assert (live.cash_sales, live.cash_refunds, live.expected_cash) == (Decimal("0.00"), Decimal("22000.00"), Decimal("28000.00"))
        await close_shift(s, second, counted_cash=Decimal(28000), closed_by=c["owner"])
        assert second.variance == Decimal("0.00")
        await s.commit()


async def test_one_open_shift_per_cashier_and_close_rules(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        with pytest.raises(ShiftInvalid) as exc:
            await open_shift(s, c["bid"], staff_id=c["sari"], opening_float=Decimal(-1))
        assert exc.value.code == "float"
        sari = await open_shift(s, c["bid"], staff_id=c["sari"], opening_float=Decimal(0))
        with pytest.raises(ShiftInvalid) as exc:
            await open_shift(s, c["bid"], staff_id=c["sari"], opening_float=Decimal(0))
        assert exc.value.code == "already_open"
        budi = await open_shift(s, c["bid"], staff_id=c["budi"], opening_float=Decimal(10000))  # another till is fine
        assert budi.id != sari.id
        with pytest.raises(ShiftInvalid) as exc:
            await close_shift(s, sari, counted_cash=Decimal(-5), closed_by=c["sari"])
        assert exc.value.code == "counted"
        await close_shift(s, sari, counted_cash=Decimal(0), closed_by=c["sari"])
        with pytest.raises(ShiftInvalid) as exc:
            await close_shift(s, sari, counted_cash=Decimal(0), closed_by=c["sari"])
        assert exc.value.code == "closed"
        again = await open_shift(s, c["bid"], staff_id=c["sari"], opening_float=Decimal(5000))  # after closing, a new one
        assert again.id != sari.id and again.status == "open"
        await s.commit()
    # The database enforces the one-open-shift rule too, not just the service.
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        s.add(Shift(business_id=c["bid"], staff_id=c["sari"], status="open", opening_float=Decimal(0)))
        with pytest.raises(Exception):
            await s.commit()


async def test_pos_endpoints_open_report_and_close(session_factory, shop):
    from app.api.pos import pos_close_shift, pos_current_shift, pos_open_shift
    from app.schemas.pos import ShiftCloseIn, ShiftOpenIn

    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        ctx = SimpleNamespace(session=s, business_id=c["bid"], staff_id=c["sari"])
        assert await pos_current_shift(ctx) is None
        opened = await pos_open_shift(ShiftOpenIn(opening_float=Decimal(100000)), ctx)
        assert opened.status == "open" and opened.staff_name == "Sari" and opened.expected_cash == Decimal("100000.00")
        with pytest.raises(HTTPException) as exc:
            await pos_open_shift(ShiftOpenIn(opening_float=Decimal(0)), ctx)
        assert exc.value.status_code == 409
        await _sell(s, c, "sari", 1, [("cash", 22000)])
        live = await pos_current_shift(ctx)
        assert live.id == opened.id and live.cash_sales == Decimal("22000.00") and live.expected_cash == Decimal("122000.00")
        closed = await pos_close_shift(ShiftCloseIn(counted_cash=Decimal(125000), notes="lebih"), ctx)
        assert closed.status == "closed" and closed.expected_cash == Decimal("122000.00")
        assert closed.counted_cash == Decimal("125000.00") and closed.variance == Decimal("3000.00") and closed.notes == "lebih"
        assert await pos_current_shift(ctx) is None
        with pytest.raises(HTTPException) as exc:
            await pos_close_shift(ShiftCloseIn(counted_cash=Decimal(0)), ctx)
        assert exc.value.status_code == 409
        await s.commit()
