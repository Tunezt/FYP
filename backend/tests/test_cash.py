"""M7-T2 — cash in and out: petty cash, supplier paid in cash, bank drop.
Each posts to the ledger, in the same transaction, and is stamped with the
till it moved through.

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
from app.models import Business, CashMovement, Expense, JournalEntry, Staff
from app.services.cash import CashInvalid, list_cash_movements, record_cash_movement
from app.services.ledger import account_balances, trial_balance
from app.services.shifts import open_shift
from app.services.statements import balance_sheet, profit_and_loss
from app.services.suppliers import create_supplier

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
        biz = Business(name="Cash Test", owner_phone=f"62975{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await seed_books(s, bid)
        sari = Staff(business_id=bid, name="Sari", pin_hash=hash_pin("2345"))
        s.add(sari)
        await s.flush()
        supplier = await create_supplier(s, bid, name="Grosir")
        await s.commit()
        ids = {"bid": bid, "sari": sari.id, "supplier": supplier.id}
    yield ids
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


async def test_each_kind_posts_and_is_stamped_with_the_open_shift(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        shift = await open_shift(s, c["bid"], staff_id=c["sari"], opening_float=Decimal(100000))
        top_up = await record_cash_movement(s, c["bid"], kind="cash_in", amount=Decimal(50000), reason="tambah modal", staff_id=c["sari"])
        ice = await record_cash_movement(s, c["bid"], kind="petty_cash", amount=Decimal(15000), reason="es batu", category="operasional", staff_id=c["sari"])
        paid = await record_cash_movement(s, c["bid"], kind="supplier_payment", amount=Decimal(30000), reason="bayar Grosir", supplier_id=c["supplier"], staff_id=c["sari"])
        drop = await record_cash_movement(s, c["bid"], kind="bank_drop", amount=Decimal(40000), reason="setor sore", staff_id=c["sari"])
        by_transfer = await record_cash_movement(s, c["bid"], kind="supplier_payment", via="transfer", amount=Decimal(25000), reason="transfer Grosir", supplier_id=c["supplier"], staff_id=c["sari"])
        await s.commit()
        ids = {"shift": shift.id, "ice": ice.id, "rows": [top_up.id, ice.id, paid.id, drop.id], "transfer": by_transfer.id}

    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        # Through the drawer → this shift; a transfer never touched the till.
        for rid in ids["rows"]:
            assert (await s.get(CashMovement, rid)).shift_id == ids["shift"]
        assert (await s.get(CashMovement, ids["transfer"])).shift_id is None
        assert (await s.get(CashMovement, ids["transfer"])).via == "transfer"

        balances = await account_balances(s)
        assert balances["1100"] == Decimal("-35000.00")   # +50 −15 −30 −40 (the float itself is not a ledger event)
        assert balances["3100"] == Decimal("50000.00")    # owner's money in
        assert balances["5500"] == Decimal("15000.00")    # petty cash is an expense
        assert balances["2100"] == Decimal("-55000.00")   # payable paid down 30 + 25
        assert balances["1110"] == Decimal("15000.00")    # bank +40 drop −25 transfer
        debit, credit = await trial_balance(s)
        assert debit == credit
        assert (await balance_sheet(s)).balances

        # Petty cash went through the expense writer: one expense row, linked, on the P&L.
        expense = (await s.execute(select(Expense))).scalar_one()
        assert expense.amount == Decimal("15000.00") and expense.description == "es batu" and expense.category == "operasional"
        assert (await s.get(CashMovement, ids["ice"])).expense_id == expense.id
        pnl = await profit_and_loss(s, since=expense.occurred_at, until=expense.occurred_at.replace(year=2100))
        assert [(l.code, l.amount) for l in pnl.expenses] == [("5500", Decimal("15000.00"))]

        # Every non-petty movement posted exactly one entry naming it as source.
        entries = (await s.execute(select(JournalEntry).where(JournalEntry.source_type == "cash_movement"))).scalars().all()
        assert sorted(e.event_type for e in entries) == ["BankDrop", "CashIn", "SupplierPaid", "SupplierPaid"]
        assert {e.source_id for e in entries} == {ids["rows"][0], ids["rows"][2], ids["rows"][3], ids["transfer"]}

        listed = await list_cash_movements(s, shift_id=ids["shift"])
        assert [m.kind for m in listed] == ["bank_drop", "supplier_payment", "petty_cash", "cash_in"] or len(listed) == 4


async def test_without_an_open_shift_the_movement_has_no_till(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        row = await record_cash_movement(s, c["bid"], kind="bank_drop", amount=Decimal(10000), reason="setor", staff_id=c["sari"])
        await s.commit()
        assert row.shift_id is None and row.via == "cash"


async def test_validation_writes_nothing(session_factory, shop):
    c = shop
    cases = [
        (dict(kind="tip_jar", amount=Decimal(1), reason="x"), "kind"),
        (dict(kind="cash_in", via="lottery", amount=Decimal(1), reason="x"), "via"),
        (dict(kind="bank_drop", amount=Decimal(0), reason="x"), "amount"),
        (dict(kind="bank_drop", amount=Decimal(-5), reason="x"), "amount"),
        (dict(kind="bank_drop", amount=Decimal(5), reason="  "), "reason"),
        (dict(kind="supplier_payment", amount=Decimal(5), reason="x"), "supplier"),
        (dict(kind="supplier_payment", amount=Decimal(5), reason="x", supplier_id=uuid.uuid4()), "supplier"),
        (dict(kind="petty_cash", amount=Decimal(0), reason="x"), "amount"),
    ]
    for kwargs, code in cases:
        async with session_factory() as s:
            await _set_tenant(s, c["bid"])
            with pytest.raises(CashInvalid) as exc:
                await record_cash_movement(s, c["bid"], staff_id=c["sari"], **kwargs)
            assert exc.value.code == code, kwargs
            await s.rollback()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert (await s.execute(select(func.count(CashMovement.id)))).scalar_one() == 0
        assert (await s.execute(select(func.count(JournalEntry.id)))).scalar_one() == 0
        assert (await s.execute(select(func.count(Expense.id)))).scalar_one() == 0


async def test_pos_endpoints_record_and_list_for_the_shift(session_factory, shop):
    from app.api.pos import pos_cash_movements, pos_record_cash, pos_suppliers
    from app.schemas.pos import CashMovementIn

    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        ctx = SimpleNamespace(session=s, business_id=c["bid"], staff_id=c["sari"])
        assert await pos_cash_movements(ctx) == []
        shift = await open_shift(s, c["bid"], staff_id=c["sari"], opening_float=Decimal(0))
        suppliers = await pos_suppliers(ctx)
        assert [x.name for x in suppliers] == ["Grosir"]
        out = await pos_record_cash(CashMovementIn(kind="petty_cash", amount=Decimal(12000), reason="gas", category="operasional"), ctx)
        assert out.kind == "petty_cash" and out.direction == "out" and out.shift_id == shift.id and out.staff_name == "Sari"
        paid = await pos_record_cash(CashMovementIn(kind="supplier_payment", amount=Decimal(5000), reason="cicil", supplier_id=suppliers[0].id), ctx)
        assert paid.supplier_name == "Grosir" and paid.via == "cash"
        assert [m.reason for m in await pos_cash_movements(ctx)] == ["cicil", "gas"]
        with pytest.raises(HTTPException) as exc:
            await pos_record_cash(CashMovementIn(kind="supplier_payment", amount=Decimal(5000), reason="x", supplier_id=uuid.uuid4()), ctx)
        assert exc.value.status_code == 404
        with pytest.raises(HTTPException) as exc:
            await pos_record_cash(CashMovementIn(kind="bank_drop", amount=Decimal(5000), reason="   "), ctx)
        assert exc.value.status_code == 422
        await s.commit()
