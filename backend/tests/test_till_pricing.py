"""M7-T4b — the pricing engine applied at the till.

The table in test_pricing.py says what a bill should come to; this file proves
the sale actually writes those figures, refuses a discount without the
manager's PIN when the settings say so, demands payment of the rounded total,
posts the reclassifications so revenue is exactly the goods at list price
(ex-tax), and that a refund undoes precisely what the sale posted.

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
from app.models import Business, Item, JournalEntry, JournalLine, Order, OrderLine, Payment, Staff, StockMovement
from app.services.catalog import ensure_default_variant
from app.services.ledger import account_balances, trial_balance
from app.services.orders import (
    DiscountNeedsManager, ManagerPinRejected, OrderLineSpec, PaymentMismatch, PaymentSpec, create_order,
    refund_order, void_order,
)
from app.services.pricing import PricingInvalid, ensure_pricing_settings
from app.services.statements import balance_sheet
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
    """Owner (PIN 1234), cashier Sari; Kopi 18.000 (cost 8.000) and Roti 27.500 (cost 12.000) — the
    bill from the pricing table: 3 Kopi + 1 Roti = 81.500."""
    async with session_factory() as s:
        biz = Business(name="Till Pricing", owner_phone=f"62973{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await seed_books(s, bid)
        await ensure_pricing_settings(s, bid)
        owner = Staff(business_id=bid, name="Bu Ratna", role="owner", pin_hash=hash_pin("1234"))
        sari = Staff(business_id=bid, name="Sari", pin_hash=hash_pin("2345"))
        kopi = Item(business_id=bid, name="Kopi", unit="cup", current_stock=D(50), cost_price=D(8000), sell_price=D(18000))
        roti = Item(business_id=bid, name="Roti", unit="pcs", current_stock=D(50), cost_price=D(12000), sell_price=D(27500))
        s.add_all([owner, sari, kopi, roti])
        await s.flush()
        for it in (kopi, roti):
            await open_item_stock(s, it, unit_cost=it.cost_price)
            await ensure_default_variant(s, it)
        await s.commit()
        ids = {"bid": bid, "owner": owner.id, "sari": sari.id, "kopi": kopi.id, "roti": roti.id}
    yield ids
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


async def _settings(s, c, **values):
    row = await ensure_pricing_settings(s, c["bid"])
    for k, v in values.items():
        setattr(row, k, v)
    await s.flush()


def _bill(c, line_discount=D(0)):
    return [
        OrderLineSpec(item_id=c["kopi"], quantity=D(3), line_discount=line_discount),
        OrderLineSpec(item_id=c["roti"], quantity=D(1)),
    ]


async def _sale_lines(s, order_id):
    entry = (await s.execute(select(JournalEntry).where(JournalEntry.source_id == order_id, JournalEntry.event_type == "OrderCompleted"))).scalar_one()
    lines = (await s.execute(select(JournalLine).where(JournalLine.entry_id == entry.id).order_by(JournalLine.line_no))).scalars().all()
    return {(l.memo, "dr" if l.debit > 0 else "cr"): (l.debit if l.debit > 0 else l.credit) for l in lines}


async def test_exclusive_tax_sale_writes_the_table_row_and_posts_it(session_factory, shop):
    """Row 'excl · service taxed · line discount' of the table: 4.500 off the
    Kopi line → expected 89.700 after rounding down 43,50. The order row, the
    lines, the journal and the balances all say the same thing."""
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        await _settings(s, c, tax_rate=D("0.11"), tax_inclusive=False, service_charge_rate=D("0.05"),
                        service_before_tax=True, rounding_unit=D(100), rounding_mode="nearest")
        created = await create_order(
            s, business_id=c["bid"], staff_id=c["sari"], lines=_bill(c, D(4500)),
            payments=[PaymentSpec(method="cash", amount=D(50000)), PaymentSpec(method="qris", amount=D(39700))],
            manager_pin="1234",
        )
        await s.commit()
        oid = created.order.id

    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        o = await s.get(Order, oid)
        assert (o.subtotal, o.discount_total, o.service_charge, o.tax_total, o.rounding, o.total) == (
            D("81500.00"), D("4500.00"), D("3850.00"), D("8893.50"), D("-43.50"), D("89700.00"),
        )
        lines = (await s.execute(select(OrderLine).where(OrderLine.order_id == oid).order_by(OrderLine.created_at, OrderLine.unit_price))).scalars().all()
        assert [(l.unit_price, l.quantity, l.line_discount, l.line_total) for l in lines] == [
            (D("18000.00"), D("3.000"), D("4500.00"), D("49500.00")),
            (D("27500.00"), D("1.000"), D("0.00"), D("27500.00")),
        ]
        posted = await _sale_lines(s, oid)
        assert posted[("discount", "dr")] == D("4500.00") and posted[("tax", "cr")] == D("8893.50")
        assert posted[("service_charge", "cr")] == D("3850.00") and posted[("rounding_down", "dr")] == D("43.50")
        assert ("rounding_up", "dr") not in posted
        assert posted[("cogs", "dr")] == D("36000.00")   # 3 × 8.000 + 12.000
        b = await account_balances(s)
        assert b["4100"] == D("81500.00")                 # revenue = the goods at list price, nothing else
        assert b["4200"] == D("-4500.00")               # contra revenue reads negative in the credit-normal convention
        assert b["2200"] == D("8893.50") and b["4900"] == D("3850.00") - D("43.50")
        assert b["1100"] == D("50000.00") and b["1120"] == D("39700.00")
        dr, cr = await trial_balance(s)
        assert dr == cr and (await balance_sheet(s)).balances


async def test_inclusive_tax_posts_revenue_ex_tax_and_service_ex_tax(session_factory, shop):
    """Row 'incl · service taxed · no discount': the customer pays 85.600; the
    ledger must show revenue 73.423,42 (goods ex-tax), other income 3.671,17
    (service ex-tax) and tax 8.480,41 — the service charge's own tax is never
    in two places."""
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        await _settings(s, c, tax_rate=D("0.11"), tax_inclusive=True, service_charge_rate=D("0.05"),
                        service_before_tax=True, rounding_unit=D(100), rounding_mode="nearest")
        created = await create_order(
            s, business_id=c["bid"], staff_id=c["sari"], lines=_bill(c),
            payments=[PaymentSpec(method="cash", amount=D(85600))],
        )
        await s.commit()
        oid = created.order.id
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        o = await s.get(Order, oid)
        assert (o.service_charge, o.tax_total, o.rounding, o.total) == (D("4075.00"), D("8480.41"), D("25.00"), D("85600.00"))
        posted = await _sale_lines(s, oid)
        assert posted[("service_charge", "cr")] == D("3671.17")   # ex-tax, not the 4.075 the customer sees
        assert posted[("tax", "cr")] == D("8480.41") and posted[("rounding_up", "cr")] == D("25.00")
        b = await account_balances(s)
        assert b["4100"] == D("73423.42")                          # 81.500 − 8.076,58 contained tax
        assert b["4900"] == D("3671.17") + D("25.00") and b["2200"] == D("8480.41")
        assert b["1100"] == D("85600.00")
        dr, cr = await trial_balance(s)
        assert dr == cr


async def test_discounts_are_behind_the_manager_pin(session_factory, shop):
    c = shop

    async def counts(s):
        out = []
        for m in (Order, Payment, StockMovement, JournalEntry):
            out.append((await s.execute(select(func.count(m.id)))).scalar_one())
        return tuple(out)

    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        before = await counts(s)
        kopi_before = (await s.get(Item, c["kopi"])).current_stock
        # No PIN → refused, and nothing moved.
        with pytest.raises(DiscountNeedsManager):
            await create_order(s, business_id=c["bid"], staff_id=c["sari"], lines=_bill(c),
                               payments=[PaymentSpec(method="cash", amount=D(75500))], bill_discount=D(6000))
        await s.rollback()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        with pytest.raises(ManagerPinRejected):
            await create_order(s, business_id=c["bid"], staff_id=c["sari"], lines=_bill(c, D(500)),
                               payments=[PaymentSpec(method="cash", amount=D(81000))], manager_pin="9999")
        await s.rollback()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert await counts(s) == before and (await s.get(Item, c["kopi"])).current_stock == kopi_before
        # The owner's PIN opens the gate.
        created = await create_order(s, business_id=c["bid"], staff_id=c["sari"], lines=_bill(c),
                                     payments=[PaymentSpec(method="cash", amount=D(75500))],
                                     bill_discount=D(6000), manager_pin="1234")
        assert created.order.discount_total == D("6000.00") and created.order.total == D("75500.00")
        await s.commit()
    # With the gate off, a cashier may discount alone.
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        await _settings(s, c, discount_requires_pin=False)
        created = await create_order(s, business_id=c["bid"], staff_id=c["sari"], lines=_bill(c, D(1500)),
                                     payments=[PaymentSpec(method="cash", amount=D(80000))])
        assert created.order.discount_total == D("1500.00")
        await s.commit()
    # No discount at all never asks for a PIN, whatever the setting.
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        await _settings(s, c, discount_requires_pin=True)
        created = await create_order(s, business_id=c["bid"], staff_id=c["sari"], lines=_bill(c),
                                     payments=[PaymentSpec(method="cash", amount=D(81500))])
        assert created.order.discount_total == D("0.00")
        await s.commit()


async def test_payment_must_equal_the_rounded_total_and_bad_discounts_are_refused(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        await _settings(s, c, rounding_unit=D(500), rounding_mode="up", discount_requires_pin=False)
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        # 81.500 − 1.250 = 80.250 → up to 80.500. Paying the unrounded figure is a mismatch.
        with pytest.raises(PaymentMismatch) as exc:
            await create_order(s, business_id=c["bid"], staff_id=c["sari"], lines=_bill(c),
                               payments=[PaymentSpec(method="cash", amount=D(80250))], bill_discount=D(1250))
        assert (exc.value.total, exc.value.paid) == (D("80500.00"), D("80250.00"))
        await s.rollback()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        with pytest.raises(PricingInvalid) as exc:
            await create_order(s, business_id=c["bid"], staff_id=c["sari"], lines=_bill(c, D(60000)),
                               payments=[PaymentSpec(method="cash", amount=D(21500))])
        assert exc.value.code == "line_discount"
        await s.rollback()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        with pytest.raises(PricingInvalid) as exc:
            await create_order(s, business_id=c["bid"], staff_id=c["sari"], lines=_bill(c),
                               payments=[PaymentSpec(method="cash", amount=D(0))], bill_discount=D(90000))
        assert exc.value.code == "discount"
        await s.rollback()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert (await s.execute(select(func.count(Order.id)))).scalar_one() == 0
        assert (await s.get(Item, c["kopi"])).current_stock == D("50.000")   # the refused sales took nothing
        created = await create_order(s, business_id=c["bid"], staff_id=c["sari"], lines=_bill(c),
                                     payments=[PaymentSpec(method="cash", amount=D(80500))], bill_discount=D(1250))
        assert (created.order.rounding, created.order.total) == (D("250.00"), D("80500.00"))
        await s.commit()


async def test_refund_undoes_exactly_what_the_sale_posted_even_after_settings_change(session_factory, shop):
    """After a full refund of a discounted, taxed, service-charged, rounded
    bill: no discount, no tax owed, no service income, no rounding left on the
    books, and returns equal net revenue. The rates are changed between the
    sale and the refund to prove the refund reads the sale, not the settings."""
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        await _settings(s, c, tax_rate=D("0.11"), tax_inclusive=False, service_charge_rate=D("0.05"),
                        service_before_tax=False, rounding_unit=D(100), rounding_mode="nearest", discount_requires_pin=False)
        created = await create_order(s, business_id=c["bid"], staff_id=c["sari"], lines=_bill(c),
                                     payments=[PaymentSpec(method="cash", amount=D(88000))], bill_discount=D(6000))
        assert created.order.total == D("88000.00")   # row 'excl · service after tax · bill discount'
        await s.commit()
        oid = created.order.id
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        await _settings(s, c, tax_rate=D("0.10"), service_charge_rate=D(0), rounding_unit=D(0))
        await refund_order(s, business_id=c["bid"], order_id=oid, staff_id=c["sari"], manager_pin="1234")
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        refund = (await s.execute(select(JournalEntry).where(JournalEntry.source_id == oid, JournalEntry.event_type == "OrderRefunded"))).scalar_one()
        memos = {l.memo for l in (await s.execute(select(JournalLine).where(JournalLine.entry_id == refund.id))).scalars()}
        assert memos == {"refund:cash", "cogs_reversal", "discount_reversal", "tax_reversal", "service_charge_reversal", "rounding_up_reversal"}
        b = await account_balances(s)
        assert b["4200"] == D("0.00") and b["2200"] == D("0.00") and b["4900"] == D("0.00")
        assert b["1100"] == D("0.00") and b["1300"] == D("0.00") and b["5100"] == D("0.00")
        # The sale left 4100 at 81.500; the refund removes the discount and adds back tax,
        # service and rounding: 81.500 - 6.000 + 8.305 + 4.190,25 + 4,75 = 88.000 = what was returned.
        assert b["4100"] == D("88000.00") and b["4300"] == D("-88000.00")   # returns are contra revenue: net revenue is zero
        dr, cr = await trial_balance(s)
        assert dr == cr and (await balance_sheet(s)).balances


async def test_void_flips_the_whole_priced_entry(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        await _settings(s, c, tax_rate=D("0.11"), tax_inclusive=True, service_charge_rate=D("0.05"),
                        service_before_tax=False, rounding_unit=D(100), discount_requires_pin=False)
        created = await create_order(s, business_id=c["bid"], staff_id=c["sari"], lines=_bill(c, D(4500)),
                                     payments=[PaymentSpec(method="qris", amount=D(80900))])
        await void_order(s, business_id=c["bid"], order_id=created.order.id, staff_id=c["sari"], manager_pin="1234")
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        b = await account_balances(s)
        assert all(b.get(code, D(0)) == D(0) for code in ("1120", "4100", "4200", "2200", "4900", "5100", "1300"))
        dr, cr = await trial_balance(s)
        assert dr == cr


async def test_pos_quote_order_and_receipt_agree(session_factory, shop):
    from app.api.pos import pos_create_order, pos_quote, pos_receipt
    from app.schemas.pos import OrderIn, OrderLineIn, PaymentIn, QuoteIn

    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        await _settings(s, c, tax_rate=D("0.11"), tax_inclusive=False, service_charge_rate=D("0.05"),
                        service_before_tax=True, rounding_unit=D(100))
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        ctx = SimpleNamespace(session=s, business_id=c["bid"], staff_id=c["sari"])
        lines = [OrderLineIn(item_id=c["kopi"], quantity=D(3), line_discount=D(4500)), OrderLineIn(item_id=c["roti"], quantity=D(1))]
        quote = await pos_quote(QuoteIn(lines=lines), ctx)
        assert (quote.subtotal, quote.discount_total, quote.service_charge, quote.tax_total, quote.rounding, quote.total) == (
            D("81500.00"), D("4500.00"), D("3850.00"), D("8893.50"), D("-43.50"), D("89700.00"),
        )
        assert quote.discount_requires_pin and not quote.tax_inclusive
        assert [(l.gross, l.line_discount, l.line_total) for l in quote.lines] == [
            (D("54000.00"), D("4500.00"), D("49500.00")), (D("27500.00"), D("0.00"), D("27500.00")),
        ]
        # Same cart, no PIN → 403 in Indonesian; nothing sold.
        with pytest.raises(HTTPException) as exc:
            await pos_create_order(OrderIn(lines=lines, payments=[PaymentIn(method="cash", amount=D(89700))]), ctx)
        assert exc.value.status_code == 403 and "PIN" in exc.value.detail
        await s.rollback()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        ctx = SimpleNamespace(session=s, business_id=c["bid"], staff_id=c["sari"])
        lines = [OrderLineIn(item_id=c["kopi"], quantity=D(3), line_discount=D(4500)), OrderLineIn(item_id=c["roti"], quantity=D(1))]
        out = await pos_create_order(
            OrderIn(lines=lines, payments=[PaymentIn(method="cash", amount=D(89700))], manager_pin="1234"), ctx
        )
        assert (out.subtotal, out.discount_total, out.service_charge, out.tax_total, out.rounding, out.total) == (
            D("81500.00"), D("4500.00"), D("3850.00"), D("8893.50"), D("-43.50"), D("89700.00"),
        )
        receipt = await pos_receipt(out.id, ctx)
        assert (receipt.subtotal, receipt.discount_total, receipt.tax_total, receipt.rounding, receipt.total) == (
            D("81500.00"), D("4500.00"), D("8893.50"), D("-43.50"), D("89700.00"),
        )
        assert receipt.tax_inclusive is False
        # A discount bigger than the line is refused with an Indonesian message.
        with pytest.raises(HTTPException) as exc:
            await pos_quote(QuoteIn(lines=[OrderLineIn(item_id=c["kopi"], quantity=D(1), line_discount=D(99999))]), ctx)
        assert exc.value.status_code == 422 and "Diskon" in exc.value.detail
        await s.commit()


async def test_owner_reads_and_changes_the_settings(session_factory, shop):
    from app.api.dashboard import get_pricing_settings, update_pricing_settings
    from app.schemas.dashboard import PricingSettingsPatch

    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        ctx = SimpleNamespace(session=s, business_id=c["bid"])
        current = await get_pricing_settings(ctx)
        assert current.tax_rate == D("0.0000") and current.discount_requires_pin and current.rounding_mode == "nearest"
        changed = await update_pricing_settings(
            PricingSettingsPatch(tax_rate=D("0.11"), tax_inclusive=False, rounding_unit=D(100), rounding_mode="up"), ctx
        )
        assert (changed.tax_rate, changed.tax_inclusive, changed.rounding_unit, changed.rounding_mode) == (D("0.1100"), False, D("100.00"), "up")
        assert changed.service_charge_rate == D("0.0000")   # untouched fields stay
        await s.commit()
    with pytest.raises(Exception):
        PricingSettingsPatch(tax_rate=D("1.2"))
    with pytest.raises(Exception):
        PricingSettingsPatch(rounding_mode="sideways")
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        # The next sale uses them: 81.500 + 11% = 90.465 → up to 90.500.
        created = await create_order(s, business_id=c["bid"], staff_id=c["sari"], lines=_bill(c),
                                     payments=[PaymentSpec(method="cash", amount=D(90500))])
        assert (created.order.tax_total, created.order.rounding, created.order.total) == (D("8965.00"), D("35.00"), D("90500.00"))
        await s.commit()
