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
from sqlalchemy import func, select, text
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


# ── M7-T3: close with reconciliation ────────────────────────────────────────


async def _cash(s, c, staff_key, **kw):
    from app.services.cash import record_cash_movement

    return await record_cash_movement(s, c["bid"], staff_id=c[staff_key], **kw)


async def test_close_reconciles_the_whole_drawer_and_posts_the_variance(session_factory, shop):
    """A simulated shift: float, cash and non-cash sales, a refund, cash in,
    petty cash, a bank drop, and a supplier paid by transfer that never touched
    the drawer. Expected = float + cash sales − cash refunds + in − out, and the
    3.500 short lands on the ledger as one entry against the till."""
    from app.models import JournalEntry
    from app.services.ledger import account_balances, entry_lines, trial_balance
    from app.services.statements import balance_sheet
    from app.services.suppliers import create_supplier

    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        supplier = await create_supplier(s, c["bid"], name="Toko Kopi Jaya")
        shift = await open_shift(s, c["bid"], staff_id=c["sari"], opening_float=Decimal(200000))
        sale = await _sell(s, c, "sari", 3, [("cash", 66000)])          # +66.000 cash
        await _sell(s, c, "sari", 1, [("qris", 22000)])                 # not cash: no effect
        await refund_order(s, business_id=c["bid"], order_id=sale.order.id, staff_id=c["sari"], manager_pin="1234")
        await _cash(s, c, "sari", kind="cash_in", amount=Decimal(50000), reason="tambah modal")
        await _cash(s, c, "sari", kind="petty_cash", amount=Decimal(15000), reason="es batu", category="operasional")
        await _cash(s, c, "sari", kind="bank_drop", amount=Decimal(40000), reason="setor bank")
        await _cash(s, c, "sari", kind="supplier_payment", amount=Decimal(30000), reason="bayar kopi",
                    via="transfer", supplier_id=supplier.id)            # never in the drawer
        await s.commit()
        sid = shift.id

    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        shift = await s.get(Shift, sid)
        live = await cash_summary(s, shift)
        assert (live.opening_float, live.cash_sales, live.cash_refunds) == (Decimal("200000.00"), Decimal("66000.00"), Decimal("66000.00"))
        assert (live.cash_in, live.cash_out) == (Decimal("50000.00"), Decimal("55000.00"))   # 15.000 + 40.000
        assert live.expected_cash == Decimal("195000.00")               # 200 + 66 − 66 + 50 − 55

        before = (await s.execute(select(func.count(JournalEntry.id)))).scalar_one()
        await close_shift(s, shift, counted_cash=Decimal(191500), closed_by=c["owner"], notes="kurang 3.500")
        await s.commit()

    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        shift = await s.get(Shift, sid)
        assert shift.expected_cash == Decimal("195000.00") and shift.counted_cash == Decimal("191500.00")
        assert shift.variance == Decimal("-3500.00")

        entries = (await s.execute(select(JournalEntry).where(JournalEntry.source_type == "shift"))).scalars().all()
        assert len(entries) == 1 and (await s.execute(select(func.count(JournalEntry.id)))).scalar_one() == before + 1
        entry = entries[0]
        assert entry.event_type == "ShiftClosed" and entry.source_id == sid
        assert entry.posted_at == shift.closed_at and entry.created_by == c["owner"]
        lines = await entry_lines(s, entry.id)
        assert {(l.memo, l.debit, l.credit) for l in lines} == {
            ("variance_short", Decimal("3500.00"), Decimal("0.00")),
            ("variance_short", Decimal("0.00"), Decimal("3500.00")),
        }

        balances = await account_balances(s)
        assert balances["5800"] == Decimal("3500.00")                   # short is an expense
        # Kas: +66 sale −66 refund +50 in −15 petty −40 drop −3,5 short (the float is not a ledger event)
        assert balances["1100"] == Decimal("-8500.00")
        debit, credit = await trial_balance(s)
        assert debit == credit and (await balance_sheet(s)).balances


async def test_counting_over_posts_the_other_way_and_an_exact_count_posts_nothing(session_factory, shop):
    from app.models import JournalEntry
    from app.services.ledger import account_balances, entry_lines

    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        over = await open_shift(s, c["bid"], staff_id=c["sari"], opening_float=Decimal(100000))
        await _sell(s, c, "sari", 1, [("cash", 22000)])
        await close_shift(s, over, counted_cash=Decimal(124000), closed_by=c["sari"])   # 2.000 over
        assert over.expected_cash == Decimal("122000.00") and over.variance == Decimal("2000.00")

        exact = await open_shift(s, c["bid"], staff_id=c["budi"], opening_float=Decimal(50000))
        await _cash(s, c, "budi", kind="bank_drop", amount=Decimal(10000), reason="setor")
        await close_shift(s, exact, counted_cash=Decimal(40000), closed_by=c["budi"])
        assert exact.expected_cash == Decimal("40000.00") and exact.variance == Decimal("0.00")
        await s.commit()
        ids = {"over": over.id, "exact": exact.id}

    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        entries = (await s.execute(select(JournalEntry).where(JournalEntry.source_type == "shift"))).scalars().all()
        assert [e.source_id for e in entries] == [ids["over"]]           # the exact count posted nothing
        lines = await entry_lines(s, entries[0].id)
        assert {(l.memo, l.debit, l.credit) for l in lines} == {
            ("variance_over", Decimal("2000.00"), Decimal("0.00")),
            ("variance_over", Decimal("0.00"), Decimal("2000.00")),
        }
        balances = await account_balances(s)
        assert balances["5800"] == Decimal("-2000.00")                   # over reduces the same expense
        assert balances["1100"] == Decimal("14000.00")                   # 22 sale − 10 drop + 2 over


async def test_a_refused_posting_leaves_the_shift_open(session_factory, shop):
    """The variance posts in the caller's transaction: if the ledger refuses,
    the close goes with it and the till is still open, unclosed and uncounted."""
    from app.models import JournalEntry
    from app.services.posting_rules import rules_for

    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        shift = await open_shift(s, c["bid"], staff_id=c["sari"], opening_float=Decimal(100000))
        await _sell(s, c, "sari", 1, [("cash", 22000)])
        await s.commit()
        sid = shift.id

    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        rule = (await rules_for(s, "ShiftClosed"))["variance_short"]
        await s.execute(text("update posting_rules set debit_code = '9999' where id = :id"), {"id": str(rule.id)})
        await s.commit()

    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        shift = await s.get(Shift, sid)
        with pytest.raises(Exception) as exc:
            await close_shift(s, shift, counted_cash=Decimal(120000), closed_by=c["owner"])
        assert "account" in str(exc.value) or "9999" in str(exc.value)
        await s.rollback()

    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        shift = await s.get(Shift, sid)
        assert shift.status == "open" and shift.closed_at is None
        assert shift.expected_cash is None and shift.counted_cash is None and shift.variance is None
        assert (await s.execute(select(func.count(JournalEntry.id)).where(JournalEntry.source_type == "shift"))).scalar_one() == 0
        assert await current_shift(s, c["sari"]) is not None


async def test_pos_close_reports_the_reconciliation(session_factory, shop):
    """The kiosk's close sheet gets every line of the sum it is showing."""
    from app.api.pos import pos_close_shift, pos_current_shift, pos_open_shift, pos_record_cash
    from app.schemas.pos import CashMovementIn, ShiftCloseIn, ShiftOpenIn

    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        ctx = SimpleNamespace(session=s, business_id=c["bid"], staff_id=c["sari"])
        await pos_open_shift(ShiftOpenIn(opening_float=Decimal(100000)), ctx)
        await _sell(s, c, "sari", 2, [("cash", 44000)])
        await pos_record_cash(CashMovementIn(kind="cash_in", amount=Decimal(20000), reason="tambah modal"), ctx)
        await pos_record_cash(
            CashMovementIn(kind="petty_cash", amount=Decimal(9000), reason="plastik", category="operasional"), ctx
        )
        live = await pos_current_shift(ctx)
        assert (live.cash_sales, live.cash_in, live.cash_out) == (Decimal("44000.00"), Decimal("20000.00"), Decimal("9000.00"))
        assert live.expected_cash == Decimal("155000.00")
        closed = await pos_close_shift(ShiftCloseIn(counted_cash=Decimal(155000)), ctx)
        assert closed.variance == Decimal("0.00") and closed.expected_cash == Decimal("155000.00")
        await s.commit()
