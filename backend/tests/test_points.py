"""M8-T2 — the points ledger: append-only, same pattern as stock. Earn rules,
redemption as a payment method, the balance a derived sum that the cache must
match, and the redemption race resolved at the database.

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import asyncio
import os
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import hash_pin
from app.models import Business, Customer, Item, JournalEntry, JournalLine, Order, Payment, PointsMovement, Staff
from app.services.catalog import ensure_default_variant
from app.services.customers import create_customer
from app.services.ledger import account_balances, trial_balance
from app.services.orders import OrderLineSpec, PaymentSpec, create_order, refund_order, void_order
from app.services.points import (
    InsufficientPoints, LoyaltyConfig, PointsInvalid, adjust_points, ensure_loyalty_settings, points_for_amount,
    points_for_rupiah,
)
from app.services.stock import open_item_stock

from tests.conftest import seed_books

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")
D = Decimal


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
    """Programme on: 1 poin per Rp 1.000, poin = Rp 100, min tukar 10. Kopi 20.000, cost 8.000. Andi is a customer."""
    async with session_factory() as s:
        biz = Business(name="Points Test", owner_phone=f"62971{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await seed_books(s, bid)
        loyalty = await ensure_loyalty_settings(s, bid)
        loyalty.is_active, loyalty.rupiah_per_point, loyalty.point_value, loyalty.min_redeem_points = True, D(1000), D(100), 10
        owner = Staff(business_id=bid, name="Bu Ratna", role="owner", pin_hash=hash_pin("1234"))
        sari = Staff(business_id=bid, name="Sari", pin_hash=hash_pin("2345"))
        kopi = Item(business_id=bid, name="Kopi", unit="cup", current_stock=D(50), cost_price=D(8000), sell_price=D(20000))
        s.add_all([owner, sari, kopi])
        await s.flush()
        await open_item_stock(s, kopi, unit_cost=kopi.cost_price)
        await ensure_default_variant(s, kopi)
        andi = await create_customer(s, bid, name="Andi", phone="081211112222")
        await s.commit()
        ids = {"bid": bid, "owner": owner.id, "sari": sari.id, "kopi": kopi.id, "andi": andi.id}
    yield ids
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


async def _sell(s, c, qty, payments, **kw):
    return await create_order(
        s, business_id=c["bid"], staff_id=c["sari"],
        lines=[OrderLineSpec(item_id=c["kopi"], quantity=D(qty))],
        payments=[PaymentSpec(method=m, amount=D(a)) for m, a in payments], **kw,
    )


async def _balance_and_sum(s, customer_id):
    cached = (await s.get(Customer, customer_id)).points_balance
    summed = (await s.execute(select(func.coalesce(func.sum(PointsMovement.points_delta), 0)).where(PointsMovement.customer_id == customer_id))).scalar_one()
    return int(cached), int(summed)


def test_earn_rule_floors_and_needs_an_active_programme():
    cfg = LoyaltyConfig(is_active=True, rupiah_per_point=D(1000), point_value=D(100))
    assert points_for_amount(D(20000), cfg) == 20
    assert points_for_amount(D(20999), cfg) == 20
    assert points_for_amount(D(999), cfg) == 0
    assert points_for_amount(D(20000), LoyaltyConfig(is_active=False)) == 0
    assert points_for_rupiah(D(1500), cfg) == 15
    with pytest.raises(PointsInvalid) as exc:
        points_for_rupiah(D(1550), cfg)
    assert exc.value.code == "whole"


async def test_a_sale_earns_points_on_what_was_paid_with_money_and_posts_the_cost(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        anon = await _sell(s, c, 1, [("cash", 20000)])                                # no customer: nothing
        sale = await _sell(s, c, 2, [("cash", 40000)], customer_id=c["andi"])         # 40 points
        await s.commit()
        ids = {"anon": anon.order.id, "sale": sale.order.id}
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert await _balance_and_sum(s, c["andi"]) == (40, 40)
        rows = (await s.execute(select(PointsMovement).order_by(PointsMovement.created_at))).scalars().all()
        assert [(r.reason, r.points_delta, r.amount, r.source_id) for r in rows] == [("earn", 40, D("4000.00"), ids["sale"])]
        entry = (await s.execute(select(JournalEntry).where(JournalEntry.source_id == ids["sale"], JournalEntry.event_type == "PointsEarned"))).scalar_one()
        lines = (await s.execute(select(JournalLine).where(JournalLine.entry_id == entry.id).order_by(JournalLine.line_no))).scalars().all()
        assert [(l.debit, l.credit) for l in lines] == [(D("4000.00"), D("0.00")), (D("0.00"), D("4000.00"))]
        b = await account_balances(s)
        assert b["5600"] == D("4000.00") and b["2300"] == D("4000.00")            # marketing expense, points liability
        assert (await s.execute(select(func.count(JournalEntry.id)).where(JournalEntry.source_id == ids["anon"], JournalEntry.event_type == "PointsEarned"))).scalar_one() == 0
        dr, cr = await trial_balance(s)
        assert dr == cr


async def test_redeeming_is_a_payment_method_and_earns_only_on_the_cash_part(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        await _sell(s, c, 3, [("cash", 60000)], customer_id=c["andi"])              # 60 points = Rp 6.000
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        # 20.000 bill: 5.000 in points (50 points), 15.000 cash → earns 15 points on the cash part only.
        sale = await _sell(s, c, 1, [("points", 5000), ("cash", 15000)], customer_id=c["andi"])
        await s.commit()
        oid = sale.order.id
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert await _balance_and_sum(s, c["andi"]) == (25, 25)                   # 60 − 50 + 15
        rows = (await s.execute(select(PointsMovement).where(PointsMovement.source_id == oid).order_by(PointsMovement.points_delta))).scalars().all()
        assert [(r.reason, r.points_delta, r.amount) for r in rows] == [("redeem", -50, D("5000.00")), ("earn", 15, D("1500.00"))]
        payments = (await s.execute(select(Payment).where(Payment.order_id == oid).order_by(Payment.amount))).scalars().all()
        assert [(p.method, p.amount) for p in payments] == [("points", D("5000.00")), ("cash", D("15000.00"))]
        # The sale's own entry posts the redemption: Dr points liability / Cr revenue.
        entry = (await s.execute(select(JournalEntry).where(JournalEntry.source_id == oid, JournalEntry.event_type == "OrderCompleted"))).scalar_one()
        memos = {(l.memo, l.debit, l.credit) for l in (await s.execute(select(JournalLine).where(JournalLine.entry_id == entry.id))).scalars()}
        assert ("payment:points", D("5000.00"), D("0.00")) in memos and ("payment:points", D("0.00"), D("5000.00")) in memos
        b = await account_balances(s)
        assert b["2300"] == D("6000.00") - D("5000.00") + D("1500.00")           # accrued 6.000, redeemed 5.000, accrued 1.500
        assert b["4100"] == D("60000.00") + D("20000.00")                         # revenue is the full bill either way
        dr, cr = await trial_balance(s)
        assert dr == cr


async def test_the_redemption_guard_rolls_the_whole_sale_back(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        await _sell(s, c, 1, [("cash", 20000)], customer_id=c["andi"])              # 20 points
        await s.commit()

    async def counts(s):
        return [(await s.execute(select(func.count(m.id)))).scalar_one() for m in (Order, Payment, PointsMovement, JournalEntry)]

    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        before = await counts(s)
        stock_before = (await s.get(Item, c["kopi"])).current_stock
        with pytest.raises(InsufficientPoints) as exc:                             # 30 points needed, has 20
            await _sell(s, c, 1, [("points", 3000), ("cash", 17000)], customer_id=c["andi"])
        assert (exc.value.needed, exc.value.available) == (30, 20)
        await s.rollback()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert await counts(s) == before
        assert (await s.get(Item, c["kopi"])).current_stock == stock_before       # the stock decrement rolled back too
        assert await _balance_and_sum(s, c["andi"]) == (20, 20)
        # Validation: no customer, a fraction of a point, below the minimum.
        for payments, kw, code in [
            ([("points", 2000), ("cash", 18000)], {}, "customer"),
            ([("points", 1550), ("cash", 18450)], {"customer_id": c["andi"]}, "whole"),
            ([("points", 500), ("cash", 19500)], {"customer_id": c["andi"]}, "min"),
        ]:
            with pytest.raises(PointsInvalid) as exc:
                await _sell(s, c, 1, payments, **kw)
            assert exc.value.code == code
            await s.rollback()
            await _set_tenant(s, c["bid"])
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        loyalty = await ensure_loyalty_settings(s, c["bid"])
        loyalty.is_active = False
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        with pytest.raises(PointsInvalid) as exc:
            await _sell(s, c, 1, [("points", 1000), ("cash", 19000)], customer_id=c["andi"])
        assert exc.value.code == "inactive"
        await s.rollback()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        await _sell(s, c, 1, [("cash", 20000)], customer_id=c["andi"])             # programme off: no earn either
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert await _balance_and_sum(s, c["andi"]) == (20, 20)


async def test_void_and_refund_take_back_earned_points_and_return_spent_ones(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        first = await _sell(s, c, 3, [("cash", 60000)], customer_id=c["andi"])      # +60
        second = await _sell(s, c, 1, [("points", 2000), ("cash", 18000)], customer_id=c["andi"])   # −20 +18 → 58
        await s.commit()
        ids = {"first": first.order.id, "second": second.order.id}
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert await _balance_and_sum(s, c["andi"]) == (58, 58)
        await refund_order(s, business_id=c["bid"], order_id=ids["second"], staff_id=c["sari"], manager_pin="1234")
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert await _balance_and_sum(s, c["andi"]) == (60, 60)                   # +20 back, −18 taken
        reversals = (await s.execute(select(PointsMovement).where(PointsMovement.source_id == ids["second"], PointsMovement.reason == "reversal"))).scalars().all()
        assert sorted(r.points_delta for r in reversals) == [-18, 20]
        released = (await s.execute(select(JournalEntry).where(JournalEntry.source_id == ids["second"], JournalEntry.event_type == "PointsReversed"))).scalar_one()
        assert {(l.debit, l.credit) for l in (await s.execute(select(JournalLine).where(JournalLine.entry_id == released.id))).scalars()} == {(D("1800.00"), D("0.00")), (D("0.00"), D("1800.00"))}
        refund = (await s.execute(select(JournalEntry).where(JournalEntry.source_id == ids["second"], JournalEntry.event_type == "OrderRefunded"))).scalar_one()
        memos = {l.memo for l in (await s.execute(select(JournalLine).where(JournalLine.entry_id == refund.id))).scalars()}
        assert "refund:points" in memos and "refund:cash" in memos
        b = await account_balances(s)
        assert b["2300"] == D("6000.00")                                          # only the first sale's accrual remains
        # Void the first sale: its 60 earned points go, even though 60 > current balance? No — balance is 60, fine.
        await void_order(s, business_id=c["bid"], order_id=ids["first"], staff_id=c["sari"], manager_pin="1234")
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert await _balance_and_sum(s, c["andi"]) == (0, 0)
        b = await account_balances(s)
        assert b["2300"] == D("0.00") and b["5600"] == D("0.00")                  # nothing accrued, nothing spent
        dr, cr = await trial_balance(s)
        assert dr == cr


async def test_taking_back_points_already_spent_goes_negative_but_never_spendable(session_factory, shop):
    """Earn 20, spend them, then void the earning sale: the ledger must still be
    written (−20 → balance −20). The next redemption is refused."""
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        earning = await _sell(s, c, 1, [("cash", 20000)], customer_id=c["andi"])    # +20
        await _sell(s, c, 1, [("points", 2000), ("cash", 18000)], customer_id=c["andi"])   # −20 +18 = 18
        await void_order(s, business_id=c["bid"], order_id=earning.order.id, staff_id=c["sari"], manager_pin="1234")
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert await _balance_and_sum(s, c["andi"]) == (-2, -2)
        with pytest.raises(InsufficientPoints):
            await _sell(s, c, 1, [("points", 1000), ("cash", 19000)], customer_id=c["andi"])
        await s.rollback()


async def test_two_simultaneous_redemptions_of_one_balance_produce_exactly_one_success(session_factory, shop):
    """Same shape as the stock race: 30 points, two tills each try to spend all 30.
    (Each sale also earns 17 on its cash part, so the loser must be refused on
    the balance as it stood when the winner committed: 17 < 30.)"""
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        await _sell(s, c, 1, [("cash", 20000)], customer_id=c["andi"])          # 20 points
        await adjust_points(s, c["bid"], await s.get(Customer, c["andi"]), 10, staff_id=c["owner"], notes="bonus")   # +10
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert await _balance_and_sum(s, c["andi"]) == (30, 30)

    async def try_redeem():
        async with session_factory() as s:
            await _set_tenant(s, c["bid"])
            try:
                await _sell(s, c, 1, [("points", 3000), ("cash", 17000)], customer_id=c["andi"])
                await s.commit()
                return "sold"
            except InsufficientPoints:
                await s.rollback()
                return "rejected"

    results = await asyncio.gather(try_redeem(), try_redeem())
    assert sorted(results) == ["rejected", "sold"]
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert await _balance_and_sum(s, c["andi"]) == (17, 17)                   # 30 − 30 + 17 earned on the cash part
        assert (await s.execute(select(func.count(Order.id)))).scalar_one() == 2


async def test_manual_adjustment_is_a_row_and_posts(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        andi = await s.get(Customer, c["andi"])
        up = await adjust_points(s, c["bid"], andi, 50, staff_id=c["owner"], notes="kompensasi")
        assert (up.reason, up.points_delta, up.amount, up.notes) == ("adjust", 50, D("5000.00"), "kompensasi")
        with pytest.raises(InsufficientPoints):
            await adjust_points(s, c["bid"], andi, -80, staff_id=c["owner"])
        down = await adjust_points(s, c["bid"], andi, -20, staff_id=c["owner"])
        assert down.points_delta == -20
        with pytest.raises(PointsInvalid):
            await adjust_points(s, c["bid"], andi, 0, staff_id=c["owner"])
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert await _balance_and_sum(s, c["andi"]) == (30, 30)
        b = await account_balances(s)
        assert b["2300"] == D("3000.00") and b["5600"] == D("3000.00")
        # The cache is never edited directly: the ledger is the truth the invariant checks.
        assert (await s.execute(select(func.count(PointsMovement.id)))).scalar_one() == 2


async def test_pos_and_owner_endpoints(session_factory, shop):
    from app.api.dashboard import adjust_customer_points, customer_points, get_loyalty_settings, update_loyalty_settings
    from app.api.pos import pos_create_order, pos_loyalty, pos_receipt, pos_search_customers
    from app.schemas.dashboard import LoyaltySettingsPatch, PointsAdjustIn
    from app.schemas.pos import OrderIn, OrderLineIn, PaymentIn

    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        pos = SimpleNamespace(session=s, business_id=c["bid"], staff_id=c["sari"])
        owner = SimpleNamespace(session=s, business_id=c["bid"], staff_id=c["owner"])
        prog = await pos_loyalty(pos)
        assert prog.is_active and prog.point_value == D("100.00") and prog.min_redeem_points == 10
        out = await pos_create_order(
            OrderIn(lines=[OrderLineIn(item_id=c["kopi"], quantity=D(2))], payments=[PaymentIn(method="cash", amount=D(40000))],
                    customer_id=c["andi"]),
            pos,
        )
        assert (out.points_earned, out.points_redeemed) == (40, 0)
        found = await pos_search_customers(pos, q="Andi")
        assert found[0].points_balance == 40 and found[0].points_value == D("4000.00")
        paid = await pos_create_order(
            OrderIn(lines=[OrderLineIn(item_id=c["kopi"], quantity=D(1))],
                    payments=[PaymentIn(method="points", amount=D(3000)), PaymentIn(method="cash", amount=D(17000))],
                    customer_id=c["andi"]),
            pos,
        )
        assert (paid.points_earned, paid.points_redeemed) == (17, 30)
        receipt = await pos_receipt(paid.id, pos)
        assert (receipt.points_earned, receipt.points_redeemed, receipt.customer_name) == (17, 30, "Andi")
        with pytest.raises(HTTPException) as exc:
            await pos_create_order(
                OrderIn(lines=[OrderLineIn(item_id=c["kopi"], quantity=D(1))],
                        payments=[PaymentIn(method="points", amount=D(9000)), PaymentIn(method="cash", amount=D(11000))],
                        customer_id=c["andi"]),
                pos,
            )
        assert exc.value.status_code == 409 and "Poin tidak cukup" in exc.value.detail
        await s.rollback()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        pos = SimpleNamespace(session=s, business_id=c["bid"], staff_id=c["sari"])
        owner = SimpleNamespace(session=s, business_id=c["bid"], staff_id=c["owner"])
        with pytest.raises(HTTPException) as exc:
            await pos_create_order(
                OrderIn(lines=[OrderLineIn(item_id=c["kopi"], quantity=D(1))],
                        payments=[PaymentIn(method="points", amount=D(1000)), PaymentIn(method="cash", amount=D(19000))]),
                pos,
            )
        assert exc.value.status_code == 422 and "pelanggan" in exc.value.detail.lower()
        await s.rollback()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        owner = SimpleNamespace(session=s, business_id=c["bid"], staff_id=c["owner"])
        current = await get_loyalty_settings(owner)
        assert current.is_active and current.rupiah_per_point == D("1000.00")
        changed = await update_loyalty_settings(LoyaltySettingsPatch(rupiah_per_point=D(500), min_redeem_points=5), owner)
        assert changed.rupiah_per_point == D("500.00") and changed.min_redeem_points == 5 and changed.point_value == D("100.00")
        with pytest.raises(Exception):
            LoyaltySettingsPatch(rupiah_per_point=D(0))
        adjusted = await adjust_customer_points(c["andi"], PointsAdjustIn(points_delta=5, notes="ulang tahun"), owner)
        assert adjusted.points_balance == 5
        history = await customer_points(c["andi"], owner, limit=50)
        assert [(m.reason, m.points_delta) for m in history] == [("adjust", 5)]
        with pytest.raises(HTTPException) as exc:
            await adjust_customer_points(c["andi"], PointsAdjustIn(points_delta=-50), owner)
        assert exc.value.status_code == 409
        with pytest.raises(HTTPException) as exc:
            await customer_points(uuid.uuid4(), owner, limit=10)
        assert exc.value.status_code == 404
        await s.commit()
