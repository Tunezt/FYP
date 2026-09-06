"""M8-T4 — vouchers: single and bulk codes, expiry, single-use under concurrency.

Done when: two simultaneous redemptions of one code produce exactly one
success — the same test shape as the stock race.

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import hash_pin
from app.models import Business, Item, JournalEntry, JournalLine, Order, Staff, Voucher, VoucherRedemption
from app.services.catalog import ensure_default_variant
from app.services.customers import create_customer
from app.services.ledger import account_balances, trial_balance
from app.services.orders import OrderLineSpec, PaymentMismatch, PaymentSpec, create_order, refund_order, void_order
from app.services.pricing import ensure_pricing_settings
from app.services.stock import open_item_stock
from app.services.vouchers import (
    VoucherInvalid, check_voucher, create_vouchers, generate_code, normalize_code, update_voucher, voucher_amount,
    voucher_by_code,
)

from tests.conftest import seed_books

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")
D = Decimal
NOW = datetime(2026, 9, 7, 8, 0, tzinfo=timezone.utc)


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
    """Kopi 20.000 (cost 8.000). Voucher HEMAT5: Rp 5.000 off, single use, min spend 15.000, valid Sept 2026."""
    async with session_factory() as s:
        biz = Business(name="Voucher Test", owner_phone=f"62969{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await seed_books(s, bid)
        await ensure_pricing_settings(s, bid)
        owner = Staff(business_id=bid, name="Bu Ratna", role="owner", pin_hash=hash_pin("1234"))
        sari = Staff(business_id=bid, name="Sari", pin_hash=hash_pin("2345"))
        kopi = Item(business_id=bid, name="Kopi", unit="cup", current_stock=D(50), cost_price=D(8000), sell_price=D(20000))
        s.add_all([owner, sari, kopi])
        await s.flush()
        await open_item_stock(s, kopi, unit_cost=kopi.cost_price)
        await ensure_default_variant(s, kopi)
        (hemat,) = await create_vouchers(
            s, bid, kind="amount_off", value=D(5000), code="hemat5", min_spend=D(15000),
            starts_at=datetime(2026, 9, 1, tzinfo=timezone.utc), expires_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
        )
        await s.commit()
        ids = {"bid": bid, "owner": owner.id, "sari": sari.id, "kopi": kopi.id, "hemat": hemat.id}
    yield ids
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


async def _sell(s, c, qty, paid, at=NOW, **kw):
    return await create_order(
        s, business_id=c["bid"], staff_id=c["sari"],
        lines=[OrderLineSpec(item_id=c["kopi"], quantity=D(qty))],
        payments=[PaymentSpec(method="cash", amount=D(paid))], sold_at=at, **kw,
    )


def test_codes_are_normalised_and_generated_readably():
    assert normalize_code("  hemat 5 ") == "HEMAT5"
    code = generate_code("SENJA")
    assert code.startswith("SENJA-") and len(code) == 14
    assert not any(ch in code for ch in "0O1I")


def test_the_amount_is_capped_and_never_more_than_the_bill():
    pct = SimpleNamespace(kind="percent_off", value=D("0.20"), max_discount=D(15000))
    assert voucher_amount(pct, D(50000)) == D("10000.00")
    assert voucher_amount(pct, D(100000)) == D("15000.00")       # the cap
    flat = SimpleNamespace(kind="amount_off", value=D(5000), max_discount=None)
    assert voucher_amount(flat, D(3000)) == D("3000.00")           # never below zero
    assert voucher_amount(flat, D(0)) == D("0.00")


async def test_single_and_bulk_creation_and_validation(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        batch = await create_vouchers(s, c["bid"], kind="percent_off", value=D("0.20"), count=25, prefix="senja", batch_name="Flyer")
        codes = [v.code for v in batch]
        assert len(set(codes)) == 25 and all(cd.startswith("SENJA-") for cd in codes)
        assert len({v.batch_id for v in batch}) == 1 and all(v.batch_name == "Flyer" and v.max_uses == 1 for v in batch)
        with pytest.raises(VoucherInvalid) as exc:
            await create_vouchers(s, c["bid"], kind="amount_off", value=D(1000), code="HEMAT5")
        assert exc.value.code == "duplicate"
        for kw, code in [
            (dict(kind="percent_off", value=D("1.5")), "value"),
            (dict(kind="amount_off", value=D(0)), "value"),
            (dict(kind="amount_off", value=D(1), code="AB"), "code"),
            (dict(kind="amount_off", value=D(1), code="X1", count=2), "count"),   # a named code is made once
            (dict(kind="amount_off", value=D(1), count=0), "count"),
            (dict(kind="amount_off", value=D(1), max_uses=0), "max_uses"),
            (dict(kind="amount_off", value=D(1), starts_at=NOW, expires_at=NOW), "dates"),
            (dict(kind="percent_off", value=D("0.1"), max_discount=D(0)), "max_discount"),
        ]:
            with pytest.raises(VoucherInvalid) as exc:
                await create_vouchers(s, c["bid"], **kw)
            assert exc.value.code == code, kw
        await s.commit()
    # The database keeps the code unique too.
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        s.add(Voucher(business_id=c["bid"], code="HEMAT5", kind="amount_off", value=D(1)))
        with pytest.raises(Exception):
            await s.commit()


async def test_check_explains_every_refusal(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        v = await voucher_by_code(s, "hemat 5")
        assert check_voucher(v, base=D(20000), at=NOW).amount == D("5000.00")
        with pytest.raises(VoucherInvalid) as exc:
            check_voucher(None, base=D(20000), at=NOW)
        assert exc.value.code == "not_found"
        with pytest.raises(VoucherInvalid) as exc:
            check_voucher(v, base=D(10000), at=NOW)
        assert exc.value.code == "min_spend_not_met"
        with pytest.raises(VoucherInvalid) as exc:
            check_voucher(v, base=D(20000), at=datetime(2026, 8, 31, 23, 59, tzinfo=timezone.utc))
        assert exc.value.code == "not_started"
        with pytest.raises(VoucherInvalid) as exc:
            check_voucher(v, base=D(20000), at=datetime(2026, 10, 1, tzinfo=timezone.utc))   # expires_at is exclusive
        assert exc.value.code == "expired"
        await update_voucher(s, v, is_active=False)
        with pytest.raises(VoucherInvalid) as exc:
            check_voucher(v, base=D(20000), at=NOW)
        assert exc.value.code == "inactive"
        await update_voucher(s, v, is_active=True)
        v.uses = 1
        with pytest.raises(VoucherInvalid) as exc:
            check_voucher(v, base=D(20000), at=NOW)
        assert exc.value.code == "used_up"
        await s.rollback()


async def test_a_sale_redeems_posts_and_the_second_use_is_refused(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        with pytest.raises(PaymentMismatch) as exc:
            await _sell(s, c, 1, 20000, voucher_code="HEMAT5")       # the voucher price is 15.000
        assert exc.value.total == D("15000.00")
        await s.rollback()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        sale = await _sell(s, c, 1, 15000, voucher_code="hemat5")
        assert (sale.order.subtotal, sale.order.voucher_total, sale.order.total) == (D("20000.00"), D("5000.00"), D("15000.00"))
        await s.commit()
        oid = sale.order.id
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        v = await s.get(Voucher, c["hemat"])
        assert v.uses == 1
        reds = (await s.execute(select(VoucherRedemption).where(VoucherRedemption.order_id == oid))).scalars().all()
        assert [(r.voucher_id, r.amount, r.reversal_of) for r in reds] == [(c["hemat"], D("5000.00"), None)]
        entry = (await s.execute(select(JournalEntry).where(JournalEntry.source_id == oid, JournalEntry.event_type == "OrderCompleted"))).scalar_one()
        posted = {(l.memo, "dr" if l.debit > 0 else "cr"): (l.debit or l.credit) for l in (await s.execute(select(JournalLine).where(JournalLine.entry_id == entry.id))).scalars()}
        assert posted[("voucher", "dr")] == D("5000.00") and posted[("payment:cash", "dr")] == D("15000.00")
        b = await account_balances(s)
        assert b["4100"] == D("20000.00") and b["4250"] == D("-5000.00") and b["1100"] == D("15000.00")
        dr, cr = await trial_balance(s)
        assert dr == cr
        # Single use: the second sale with the same code is refused and sells nothing.
        with pytest.raises(VoucherInvalid) as exc:
            await _sell(s, c, 1, 15000, voucher_code="HEMAT5")
        assert exc.value.code == "used_up"
        await s.rollback()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert (await s.execute(select(func.count(Order.id)))).scalar_one() == 1
        assert (await s.get(Item, c["kopi"])).current_stock == D("49.000")


async def test_two_simultaneous_redemptions_of_one_code_produce_exactly_one_success(session_factory, shop):
    """The done-when. Same shape as the stock race: both tills pass the checks,
    the atomic UPDATE lets one through."""
    c = shop

    async def try_redeem():
        async with session_factory() as s:
            await _set_tenant(s, c["bid"])
            try:
                await _sell(s, c, 1, 15000, voucher_code="HEMAT5")
                await s.commit()
                return "sold"
            except VoucherInvalid as exc:
                await s.rollback()
                return exc.code

    results = await asyncio.gather(try_redeem(), try_redeem())
    assert sorted(results) == ["sold", "used_up"]
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert (await s.get(Voucher, c["hemat"])).uses == 1
        assert (await s.execute(select(func.count(Order.id)))).scalar_one() == 1
        assert (await s.execute(select(func.count(VoucherRedemption.id)))).scalar_one() == 1
        assert (await s.get(Item, c["kopi"])).current_stock == D("49.000")   # the loser took no stock either


async def test_refund_gives_the_use_back_and_reverses_the_posting(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        sale = await _sell(s, c, 1, 15000, voucher_code="HEMAT5")
        await s.commit()
        oid = sale.order.id
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        await refund_order(s, business_id=c["bid"], order_id=oid, staff_id=c["sari"], manager_pin="1234")
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert (await s.get(Voucher, c["hemat"])).uses == 0
        reds = (await s.execute(select(VoucherRedemption).where(VoucherRedemption.order_id == oid).order_by(VoucherRedemption.created_at))).scalars().all()
        assert [r.amount for r in reds] == [D("5000.00"), D("-5000.00")] and reds[1].reversal_of == reds[0].id
        refund = (await s.execute(select(JournalEntry).where(JournalEntry.source_id == oid, JournalEntry.event_type == "OrderRefunded"))).scalar_one()
        memos = {l.memo for l in (await s.execute(select(JournalLine).where(JournalLine.entry_id == refund.id))).scalars()}
        assert "voucher_reversal" in memos
        b = await account_balances(s)
        assert b["4250"] == D("0.00") and b["1100"] == D("0.00")
        # The code can be used again — once.
        again = await _sell(s, c, 1, 15000, voucher_code="HEMAT5")
        assert again.order.voucher_total == D("5000.00")
        await void_order(s, business_id=c["bid"], order_id=again.order.id, staff_id=c["sari"], manager_pin="1234")
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert (await s.get(Voucher, c["hemat"])).uses == 0                  # a void gives it back too
        dr, cr = await trial_balance(s)
        assert dr == cr


async def test_multi_use_and_percent_with_customer(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        (dua,) = await create_vouchers(s, c["bid"], kind="percent_off", value=D("0.25"), code="DUAKALI", max_uses=2, max_discount=D(4000))
        andi = await create_customer(s, c["bid"], name="Andi", phone="081200001111")
        await s.commit()
        ids = {"dua": dua.id, "andi": andi.id}
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        first = await _sell(s, c, 1, 16000, voucher_code="DUAKALI", customer_id=ids["andi"])      # 25% of 20.000 = 5.000 → capped 4.000
        assert first.order.voucher_total == D("4000.00")
        second = await _sell(s, c, 2, 36000, voucher_code="duakali")                              # 25% of 40.000 = 10.000 → 4.000
        assert second.order.voucher_total == D("4000.00")
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        with pytest.raises(VoucherInvalid) as exc:
            await _sell(s, c, 1, 16000, voucher_code="DUAKALI")
        assert exc.value.code == "used_up"
        await s.rollback()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        v = await s.get(Voucher, ids["dua"])
        assert v.uses == 2
        red = (await s.execute(select(VoucherRedemption).where(VoucherRedemption.customer_id == ids["andi"]))).scalar_one()
        assert red.amount == D("4000.00")
        with pytest.raises(VoucherInvalid) as exc:
            await update_voucher(s, v, max_uses=1)                    # cannot go below what is already used
        assert exc.value.code == "max_uses"
        await update_voucher(s, v, max_uses=3)
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        third = await _sell(s, c, 1, 16000, voucher_code="DUAKALI")
        assert third.order.voucher_total == D("4000.00")
        await s.commit()


async def test_pos_quote_and_owner_endpoints(session_factory, shop):
    from app.api.dashboard import add_vouchers, edit_voucher, list_vouchers_endpoint
    from app.api.pos import pos_create_order, pos_quote, pos_receipt
    from app.schemas.dashboard import VoucherCreateIn, VoucherUpdateIn
    from app.schemas.pos import OrderIn, OrderLineIn, PaymentIn, QuoteIn

    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        pos = SimpleNamespace(session=s, business_id=c["bid"], staff_id=c["sari"])
        owner = SimpleNamespace(session=s, business_id=c["bid"], staff_id=None)
        good = await pos_quote(QuoteIn(lines=[OrderLineIn(item_id=c["kopi"], quantity=D(1))], voucher_code=" hemat5 "), pos)
        assert (good.voucher_code, good.voucher_total, good.total, good.voucher_error) == ("HEMAT5", D("5000.00"), D("15000.00"), None)
        bad = await pos_quote(QuoteIn(lines=[OrderLineIn(item_id=c["kopi"], quantity=D(1))], voucher_code="NGAWUR"), pos)
        assert bad.voucher_code is None and bad.voucher_total == D("0.00") and bad.total == D("20000.00")
        assert bad.voucher_error == "Kode voucher tidak dikenali"
        small = await pos_quote(QuoteIn(lines=[OrderLineIn(item_id=c["kopi"], quantity=D(1), line_discount=D(6000))], voucher_code="HEMAT5"), pos)
        assert "belanja minimal Rp 15.000" in (small.voucher_error or "")
        out = await pos_create_order(
            OrderIn(lines=[OrderLineIn(item_id=c["kopi"], quantity=D(1))], payments=[PaymentIn(method="cash", amount=D(15000))], voucher_code="HEMAT5"),
            pos,
        )
        assert out.voucher_total == D("5000.00")
        receipt = await pos_receipt(out.id, pos)
        assert (receipt.voucher_code, receipt.voucher_total) == ("HEMAT5", D("5000.00"))
        with pytest.raises(HTTPException) as exc:
            await pos_create_order(
                OrderIn(lines=[OrderLineIn(item_id=c["kopi"], quantity=D(1))], payments=[PaymentIn(method="cash", amount=D(15000))], voucher_code="HEMAT5"),
                pos,
            )
        assert exc.value.status_code == 409 and "sudah terpakai" in exc.value.detail
        await s.rollback()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        owner = SimpleNamespace(session=s, business_id=c["bid"], staff_id=None)
        made = await add_vouchers(VoucherCreateIn(kind="percent_off", value=D("0.10"), count=5, prefix="PROMO", batch_name="IG"), owner)
        assert len(made) == 5 and len({m.batch_id for m in made}) == 1
        one = await add_vouchers(VoucherCreateIn(kind="amount_off", value=D(2000), code="kopi-pagi", max_uses=50), owner)
        assert one[0].code == "KOPI-PAGI" and one[0].max_uses == 50
        with pytest.raises(HTTPException) as exc:
            await add_vouchers(VoucherCreateIn(kind="amount_off", value=D(1), code="KOPI-PAGI"), owner)
        assert exc.value.status_code == 409
        listed = await list_vouchers_endpoint(owner, q="promo", batch_id=None, include_inactive=True, limit=200)
        assert len(listed) == 5
        by_batch = await list_vouchers_endpoint(owner, q="", batch_id=made[0].batch_id, include_inactive=True, limit=200)
        assert len(by_batch) == 5
        edited = await edit_voucher(one[0].id, VoucherUpdateIn(is_active=False), owner)
        assert edited.is_active is False
        with pytest.raises(HTTPException) as exc:
            await edit_voucher(uuid.uuid4(), VoucherUpdateIn(is_active=True), owner)
        assert exc.value.status_code == 404
        await s.commit()
