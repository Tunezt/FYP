"""M6-T4 — the posting engine: single writer, same transaction as the change.

Done-when (roadmap): a test forces journal writing to fail and asserts the
sale rolled back too. Proved, not asserted by inspection.

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import os
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import hash_pin
from app.models import Business, Item, JournalEntry, JournalLine, Order, OrderLine, Payment, Staff, StockMovement
from app.services.accounts import ensure_standard_chart
from app.services.catalog import ensure_default_variant
from app.services.ledger import account_balances, trial_balance
from app.services.orders import OrderLineSpec, PaymentSpec, create_order, refund_order, void_order
from app.services.posting import PostingFailed, post_event
from app.services.posting_rules import ensure_standard_rules, rules_for, update_rule
from app.services.receiving import GrLineSpec, receive_goods
from app.services.stock import open_item_stock, set_absolute_stock
from app.services.suppliers import create_supplier
from app.services.units import consume_stock, ensure_standard_uoms

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
    """Chart + rules seeded; owner (PIN 1234) and cashier; coffee 10 @ cost 8.000 sells 22.000."""
    async with session_factory() as s:
        biz = Business(name="Posting Test", owner_phone=f"62979{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        uoms = await ensure_standard_uoms(s, bid)
        await ensure_standard_chart(s, bid)
        await ensure_standard_rules(s, bid)
        owner = Staff(business_id=bid, name="Bu Ratna", role="owner", pin_hash=hash_pin("1234"))
        staff = Staff(business_id=bid, name="Kasir", pin_hash=hash_pin("1111"))
        kopi = Item(business_id=bid, name="Kopi", unit="cup", current_stock=Decimal(10), cost_price=Decimal(8000), sell_price=Decimal(22000), uom_id=uoms["cup"].id)
        beans = Item(business_id=bid, name="Biji", unit="kg", current_stock=Decimal(2), cost_price=Decimal(100000), uom_id=uoms["kg"].id)
        s.add_all([owner, staff, kopi, beans])
        await s.flush()
        for it in (kopi, beans):
            await open_item_stock(s, it, unit_cost=it.cost_price)
            await ensure_default_variant(s, it)
        supplier = await create_supplier(s, bid, name="Grosir")
        await s.commit()
        ids = {"bid": bid, "staff": staff.id, "kopi": kopi.id, "beans": beans.id, "supplier": supplier.id, "kg": uoms["kg"].id}
    yield ids
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


async def _counts(s):
    out = []
    for m in (Order, OrderLine, Payment, StockMovement, JournalEntry, JournalLine):
        out.append((await s.execute(select(func.count(m.id)))).scalar_one())
    return tuple(out)


async def test_sale_posts_money_revenue_and_cogs_in_one_entry(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        created = await create_order(
            s, business_id=c["bid"], staff_id=c["staff"],
            lines=[OrderLineSpec(item_id=c["kopi"], quantity=Decimal(2))],
            payments=[PaymentSpec(method="cash", amount=Decimal(24000)), PaymentSpec(method="qris", amount=Decimal(20000))],
        )
        await s.commit()
        order_id = created.order.id
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        entry = (await s.execute(select(JournalEntry).where(JournalEntry.source_id == order_id))).scalar_one()
        assert entry.event_type == "OrderCompleted" and entry.entry_no == 1
        lines = (await s.execute(select(JournalLine).where(JournalLine.entry_id == entry.id).order_by(JournalLine.line_no))).scalars().all()
        assert [(l.memo, l.debit, l.credit) for l in lines] == [
            ("payment:cash", Decimal("24000.00"), Decimal("0.00")), ("payment:cash", Decimal("0.00"), Decimal("24000.00")),
            ("payment:qris", Decimal("20000.00"), Decimal("0.00")), ("payment:qris", Decimal("0.00"), Decimal("20000.00")),
            ("cogs", Decimal("16000.00"), Decimal("0.00")), ("cogs", Decimal("0.00"), Decimal("16000.00")),
        ]
        balances = await account_balances(s)
        assert balances["1100"] == Decimal("24000.00") and balances["1120"] == Decimal("20000.00")
        assert balances["4100"] == Decimal("44000.00") and balances["5100"] == Decimal("16000.00")
        assert balances["1300"] == Decimal("-16000.00")   # inventory down by cost (opening stock was never capitalised)
        d, cr = await trial_balance(s)
        assert d == cr


async def test_journal_failure_rolls_the_sale_back(session_factory, shop):
    """Force the journal write to fail three different ways; every time the
    order, its lines, payments and stock movements are gone and stock is back."""
    c = shop

    async def boom(*a, **k):
        raise RuntimeError("journal down")

    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        before = await _counts(s)

    # 1. The ledger raises (simulated outage / bug).
    with patch("app.services.posting.post_entry", new=AsyncMock(side_effect=boom)):
        async with session_factory() as s:
            await _set_tenant(s, c["bid"])
            with pytest.raises(RuntimeError):
                await create_order(
                    s, business_id=c["bid"], staff_id=c["staff"],
                    lines=[OrderLineSpec(item_id=c["kopi"], quantity=Decimal(3))],
                    payments=[PaymentSpec(method="cash", amount=Decimal(66000))],
                )
            await s.rollback()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert await _counts(s) == before
        assert (await s.get(Item, c["kopi"])).current_stock == Decimal("10.000")

    # 2. A rule points at an account that does not exist for this business →
    #    LedgerInvalid from the service, before anything is written.
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        cogs = (await rules_for(s, "OrderCompleted"))["cogs"]
        await s.execute(text("update posting_rules set credit_code = '9999' where id = :id"), {"id": str(cogs.id)})
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        with pytest.raises(Exception) as exc:
            await create_order(
                s, business_id=c["bid"], staff_id=c["staff"],
                lines=[OrderLineSpec(item_id=c["kopi"], quantity=Decimal(1))],
                payments=[PaymentSpec(method="cash", amount=Decimal(22000))],
            )
        assert "account" in str(exc.value) or "9999" in str(exc.value)
        await s.rollback()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert await _counts(s) == before
        await s.execute(text("update posting_rules set credit_code = '1300' where id = :id"), {"id": str(cogs.id)})
        await s.commit()

    # 3. The database's own balance constraint: make the posted lines unbalanced
    #    by breaking one line after the engine wrote it, inside the same
    #    transaction — the commit is refused and everything rolls back.
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        await create_order(
            s, business_id=c["bid"], staff_id=c["staff"],
            lines=[OrderLineSpec(item_id=c["kopi"], quantity=Decimal(1))],
            payments=[PaymentSpec(method="cash", amount=Decimal(22000))],
        )
        await s.execute(text("update journal_lines set credit = credit + 1 where credit > 0 and memo = 'cogs'"))
        with pytest.raises(Exception) as exc:
            await s.commit()
        assert "unbalanced" in str(exc.value)
        await s.rollback()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert await _counts(s) == before                     # the sale is gone too
        assert (await s.get(Item, c["kopi"])).current_stock == Decimal("10.000")


async def test_void_reverses_and_refund_posts_returns(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        first = await create_order(s, business_id=c["bid"], staff_id=c["staff"],
                                   lines=[OrderLineSpec(item_id=c["kopi"], quantity=Decimal(1))],
                                   payments=[PaymentSpec(method="cash", amount=Decimal(22000))])
        second = await create_order(s, business_id=c["bid"], staff_id=c["staff"],
                                    lines=[OrderLineSpec(item_id=c["kopi"], quantity=Decimal(2))],
                                    payments=[PaymentSpec(method="qris", amount=Decimal(44000))])
        await s.commit()
        first_id, second_id = first.order.id, second.order.id
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        await void_order(s, business_id=c["bid"], order_id=first_id, staff_id=c["staff"], manager_pin="1234")
        await refund_order(s, business_id=c["bid"], order_id=second_id, staff_id=c["staff"], manager_pin="1234", restock=False)
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        entries = (await s.execute(select(JournalEntry).order_by(JournalEntry.entry_no))).scalars().all()
        assert [e.event_type for e in entries] == ["OrderCompleted", "OrderCompleted", "OrderVoided", "OrderRefunded"]
        b = await account_balances(s)
        assert b["1100"] == Decimal(0)                         # cash sale voided → cash back to zero
        assert b["1120"] == Decimal("0.00")                    # QRIS refunded in full
        assert b["4300"] == Decimal("-44000.00")               # returns: a contra-revenue debit, revenue kept gross
        assert b["4100"] == Decimal("44000.00")                # only the refunded sale's revenue remains (void flipped the first)
        assert b["5100"] == Decimal("16000.00")                # not restocked: COGS of the refunded sale stays
        d, cr = await trial_balance(s)
        assert d == cr


async def test_goods_receipt_waste_and_count_post(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        await receive_goods(s, c["bid"], supplier_id=c["supplier"],
                            lines=[GrLineSpec(item_id=c["beans"], quantity=Decimal(3), unit_cost=Decimal(120000), uom_id=c["kg"])])
        beans = await s.get(Item, c["beans"])
        await consume_stock(s, beans, Decimal(1), reason="waste", source_type="test")     # 1 kg @ average 112.000
        kopi = await s.get(Item, c["kopi"])
        await set_absolute_stock(s, kopi, Decimal(8), reason="opname", source_type="test")  # 2 cups short @ 8.000
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        events = [e.event_type for e in (await s.execute(select(JournalEntry).order_by(JournalEntry.entry_no))).scalars()]
        assert events == ["GoodsReceived", "StockWasted", "StockCounted"]
        b = await account_balances(s)
        assert b["2100"] == Decimal("360000.00")                       # payable to the supplier
        assert b["5700"] == Decimal("112000.00") + Decimal("16000.00")  # waste at average cost + count variance
        assert b["1300"] == Decimal("360000.00") - Decimal("112000.00") - Decimal("16000.00")
        d, cr = await trial_balance(s)
        assert d == cr


async def test_engine_holds_no_accounts_and_refuses_unknown_components(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        with pytest.raises(PostingFailed) as exc:
            await post_event(s, c["bid"], "OrderCompleted", {"mystery": Decimal(10)})
        assert exc.value.code == "no_rule"
        assert await post_event(s, c["bid"], "OrderCompleted", {"cogs": Decimal(0)}) is None   # nothing to post
        # Re-pointing a rule changes where the next posting lands — no code change.
        qris = (await rules_for(s, "OrderCompleted"))["payment:qris"]
        await update_rule(s, qris, debit_code="1110")
        entry = await post_event(s, c["bid"], "OrderCompleted", {"payment:qris": Decimal(5000)})
        lines = (await s.execute(select(JournalLine).where(JournalLine.entry_id == entry.id))).scalars().all()
        debit_line = next(l for l in lines if l.debit > 0)
        from app.models import Account
        assert (await s.get(Account, debit_line.account_id)).code == "1110"
        await s.rollback()
