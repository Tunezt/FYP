"""M8-T3 — the promo engine: discount or bonus product, conditions ANDed at the
moment of sale, no activate/expire job.

Done when: a BOGO applies at the till, posts its cost to the ledger, and stops
applying the moment its window closes. The engine is pure, so most of this
needs no database; the till tests do.
"""
import os
import uuid
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import hash_pin
from app.models import Business, Item, JournalEntry, JournalLine, Order, OrderLine, PromoApplication, Staff
from app.services.catalog import ensure_default_variant
from app.services.ledger import account_balances, trial_balance
from app.services.orders import OrderLineSpec, PaymentSpec, create_order, refund_order
from app.services.pricing import PricingConfig, ensure_pricing_settings
from app.services.promos import CartLine, PromoInvalid, PromoRule, apply_promos, create_promo, is_open, load_rules, update_promo
from app.services.stock import open_item_stock

from tests.conftest import seed_books

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")
D = Decimal
WIB = "Asia/Jakarta"
KOPI, ROTI, TEH = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def wib(y, mo, d, h, mi=0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=ZoneInfo(WIB)).astimezone(timezone.utc)


def cart():
    return [CartLine(KOPI, D(20000), D(2)), CartLine(ROTI, D(15000), D(1))]   # 55.000


# ── the pure engine ─────────────────────────────────────────────────────────


def test_conditions_are_anded_and_evaluated_at_the_moment():
    happy_hour = PromoRule(
        id=uuid.uuid4(), name="Happy hour", kind="percent_off", value=D("0.5"), item_id=KOPI,
        days_of_week=frozenset({0, 1, 2, 3, 4}), time_start=time(14, 0), time_end=time(17, 0),
        starts_at=wib(2026, 9, 1, 0), ends_at=wib(2026, 10, 1, 0),
    )
    at = lambda *a: (wib(*a), wib(*a).astimezone(ZoneInfo(WIB)))
    assert is_open(happy_hour, *at(2026, 9, 7, 14, 0))       # Monday 14:00 — opens exactly on the minute
    assert is_open(happy_hour, *at(2026, 9, 7, 16, 59))
    assert not is_open(happy_hour, *at(2026, 9, 7, 17, 0))   # closes the moment the window ends
    assert not is_open(happy_hour, *at(2026, 9, 7, 13, 59))
    assert not is_open(happy_hour, *at(2026, 9, 6, 15, 0))   # Sunday
    assert not is_open(happy_hour, *at(2026, 8, 31, 15, 0))  # before the date range
    assert not is_open(happy_hour, *at(2026, 10, 1, 15, 0))  # ends_at is exclusive
    # Local time is the business's, not UTC: 15:00 WIB is 08:00 UTC.
    assert wib(2026, 9, 7, 15).hour == 8
    late = PromoRule(id=uuid.uuid4(), name="Late", kind="percent_off", value=D("0.1"), time_start=time(22, 0), time_end=time(2, 0))
    assert is_open(late, *at(2026, 9, 7, 23, 30)) and is_open(late, *at(2026, 9, 8, 1, 30)) and not is_open(late, *at(2026, 9, 8, 2, 0))


def test_bogo_adds_a_free_bonus_line_per_multiple_up_to_the_cap():
    bogo = PromoRule(id=uuid.uuid4(), name="BOGO Kopi", kind="bonus_item", item_id=KOPI, buy_quantity=D(1), bonus_quantity=D(1), max_per_order=2)
    r = apply_promos(cart(), [bogo], at_utc=wib(2026, 9, 7, 10), tz=WIB)
    assert r.line_discounts == [D("0.00"), D("0.00")]
    assert [(b.item_id, b.quantity, b.unit_price) for b in r.bonus_lines] == [(KOPI, D("2.000"), D("20000.00"))]
    assert r.total == D("40000.00")
    assert [(a.amount, a.bonus_quantity, a.bonus_index) for a in r.applications] == [(D("40000.00"), D("2.000"), 0)]
    # Buy 3 → capped at 2 applications.
    r = apply_promos([CartLine(KOPI, D(20000), D(3))], [bogo], at_utc=wib(2026, 9, 7, 10), tz=WIB)
    assert r.bonus_lines[0].quantity == D("2.000")
    # Buy 2 get 1, on a different item, at that item's list price.
    b2g1 = PromoRule(id=uuid.uuid4(), name="Beli 2 Kopi gratis Teh", kind="bonus_item", item_id=KOPI, bonus_item_id=TEH, buy_quantity=D(2), bonus_quantity=D(1))
    r = apply_promos(cart(), [b2g1], at_utc=wib(2026, 9, 7, 10), tz=WIB, price_of={TEH: D(8000)})
    assert [(b.item_id, b.quantity, b.unit_price) for b in r.bonus_lines] == [(TEH, D("1.000"), D("8000.00"))]
    # Only 1 Kopi → not enough for a multiple → nothing.
    r = apply_promos([CartLine(KOPI, D(20000), D(1))], [b2g1], at_utc=wib(2026, 9, 7, 10), tz=WIB, price_of={TEH: D(8000)})
    assert r.bonus_lines == [] and r.total == D("0.00")


def test_percent_and_amount_off_item_and_bill_never_below_zero():
    ten = PromoRule(id=uuid.uuid4(), name="Kopi 10%", kind="percent_off", value=D("0.10"), item_id=KOPI)
    r = apply_promos(cart(), [ten], at_utc=wib(2026, 9, 7, 10), tz=WIB)
    assert r.line_discounts == [D("4000.00"), D("0.00")] and r.total == D("4000.00")
    # Percent off is on what remains after the cashier's line discount.
    r = apply_promos([CartLine(KOPI, D(20000), D(2), line_discount=D(10000))], [ten], at_utc=wib(2026, 9, 7, 10), tz=WIB)
    assert r.line_discounts == [D("3000.00")]
    # Amount off per multiple: 5.000 off per 2 Kopi, capped at the line.
    five = PromoRule(id=uuid.uuid4(), name="5rb tiap 2 Kopi", kind="amount_off", value=D(5000), item_id=KOPI, buy_quantity=D(2))
    r = apply_promos([CartLine(KOPI, D(20000), D(5))], [five], at_utc=wib(2026, 9, 7, 10), tz=WIB)
    assert r.line_discounts == [D("10000.00")]
    huge = PromoRule(id=uuid.uuid4(), name="Terlalu besar", kind="amount_off", value=D(999999), item_id=ROTI)
    r = apply_promos(cart(), [huge], at_utc=wib(2026, 9, 7, 10), tz=WIB)
    assert r.line_discounts == [D("0.00"), D("15000.00")]                      # the whole line, not more
    # Bill-level: 10% of what remains after item promos and the cashier's bill discount.
    bill10 = PromoRule(id=uuid.uuid4(), name="Semua 10%", kind="percent_off", value=D("0.10"))
    r = apply_promos(cart(), [ten, bill10], at_utc=wib(2026, 9, 7, 10), tz=WIB, bill_discount=D(1000))
    assert r.line_discounts == [D("4000.00"), D("0.00")] and r.bill_discount == D("5000.00")   # (55.000 − 4.000 − 1.000) × 10%
    assert r.total == D("9000.00")


def test_min_spend_is_net_of_the_cashiers_discounts_before_promos():
    gate = PromoRule(id=uuid.uuid4(), name="Belanja 50rb diskon 5rb", kind="amount_off", value=D(5000), min_spend=D(50000))
    assert apply_promos(cart(), [gate], at_utc=wib(2026, 9, 7, 10), tz=WIB).bill_discount == D("5000.00")             # 55.000 ≥ 50.000
    assert apply_promos(cart(), [gate], at_utc=wib(2026, 9, 7, 10), tz=WIB, bill_discount=D(6000)).bill_discount == D("0.00")   # 49.000 < 50.000
    assert apply_promos([CartLine(KOPI, D(20000), D(2), line_discount=D(6000)), CartLine(ROTI, D(15000), D(1))], [gate],
                        at_utc=wib(2026, 9, 7, 10), tz=WIB).bill_discount == D("0.00")


def test_two_tills_agree_on_the_order_promos_apply_in():
    a = PromoRule(id=uuid.UUID(int=2), name="B", kind="percent_off", value=D("0.10"), item_id=KOPI)
    b = PromoRule(id=uuid.UUID(int=1), name="A", kind="percent_off", value=D("0.10"), item_id=KOPI)
    r1 = apply_promos(cart(), [a, b], at_utc=wib(2026, 9, 7, 10), tz=WIB)
    r2 = apply_promos(cart(), [b, a], at_utc=wib(2026, 9, 7, 10), tz=WIB)
    assert [x.promo_name for x in r1.applications] == [x.promo_name for x in r2.applications] == ["A", "B"]
    assert r1.line_discounts == r2.line_discounts == [D("7600.00"), D("0.00")]   # 4.000 then 10% of 36.000


# ── the till (needs the local Postgres, roadmap §2) ─────────────────────────


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
    """Kopi 20.000 (cost 8.000), Teh 8.000 (cost 3.000); a BOGO on Kopi open weekdays 14:00–17:00 WIB, max 2 per bill."""
    async with session_factory() as s:
        biz = Business(name="Promo Test", owner_phone=f"62970{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await seed_books(s, bid)
        await ensure_pricing_settings(s, bid)
        owner = Staff(business_id=bid, name="Bu Ratna", role="owner", pin_hash=hash_pin("1234"))
        sari = Staff(business_id=bid, name="Sari", pin_hash=hash_pin("2345"))
        kopi = Item(business_id=bid, name="Kopi", unit="cup", current_stock=D(20), cost_price=D(8000), sell_price=D(20000))
        teh = Item(business_id=bid, name="Teh", unit="cup", current_stock=D(20), cost_price=D(3000), sell_price=D(8000))
        s.add_all([owner, sari, kopi, teh])
        await s.flush()
        for it in (kopi, teh):
            await open_item_stock(s, it, unit_cost=it.cost_price)
            await ensure_default_variant(s, it)
        bogo = await create_promo(
            s, bid, name="BOGO Kopi sore", kind="bonus_item", item_id=kopi.id, max_per_order=2,
            conditions=[{"kind": "day_of_week", "days_of_week": [0, 1, 2, 3, 4]},
                        {"kind": "time_window", "time_start": time(14, 0), "time_end": time(17, 0)}],
        )
        await s.commit()
        ids = {"bid": bid, "owner": owner.id, "sari": sari.id, "kopi": kopi.id, "teh": teh.id, "bogo": bogo.id}
    yield ids
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


async def _sell(s, c, qty, paid, at, **kw):
    return await create_order(
        s, business_id=c["bid"], staff_id=c["sari"],
        lines=[OrderLineSpec(item_id=c["kopi"], quantity=D(qty))],
        payments=[PaymentSpec(method="cash", amount=D(paid))], sold_at=at, **kw,
    )


async def test_bogo_applies_at_the_till_posts_its_cost_and_stops_when_the_window_closes(session_factory, shop):
    c = shop
    monday_1530, monday_1700 = wib(2026, 9, 7, 15, 30), wib(2026, 9, 7, 17, 0)
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        sale = await _sell(s, c, 1, 20000, monday_1530)                 # pays for 1, gets 2
        await s.commit()
        oid = sale.order.id
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        o = await s.get(Order, oid)
        assert (o.subtotal, o.discount_total, o.promo_total, o.total) == (D("40000.00"), D("0.00"), D("20000.00"), D("20000.00"))
        lines = sorted((await s.execute(select(OrderLine).where(OrderLine.order_id == oid))).scalars().all(), key=lambda l: l.notes is not None)
        assert [(l.quantity, l.unit_price, l.line_total, l.notes) for l in lines] == [
            (D("1.000"), D("20000.00"), D("20000.00"), None), (D("1.000"), D("20000.00"), D("20000.00"), "promo: BOGO Kopi sore"),
        ]
        assert (await s.get(Item, c["kopi"])).current_stock == D("18.000")   # both cups left the shelf
        apps = (await s.execute(select(PromoApplication).where(PromoApplication.order_id == oid))).scalars().all()
        assert [(a.promo_id, a.amount, a.bonus_quantity, a.order_line_id) for a in apps] == [(c["bogo"], D("20000.00"), D("1.000"), lines[1].id)]
        # The books: revenue gross for both cups, the free one in 4250, cost of goods for both.
        entry = (await s.execute(select(JournalEntry).where(JournalEntry.source_id == oid, JournalEntry.event_type == "OrderCompleted"))).scalar_one()
        posted = {(l.memo, "dr" if l.debit > 0 else "cr"): (l.debit or l.credit) for l in (await s.execute(select(JournalLine).where(JournalLine.entry_id == entry.id))).scalars()}
        assert posted[("promo", "dr")] == D("20000.00") and posted[("cogs", "dr")] == D("16000.00")
        b = await account_balances(s)
        assert b["4100"] == D("40000.00") and b["4250"] == D("-20000.00") and b["1100"] == D("20000.00") and b["5100"] == D("16000.00")
        dr, cr = await trial_balance(s)
        assert dr == cr

    # 17:00:00 — the window has closed; the same sale gets nothing.
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        closed = await _sell(s, c, 1, 20000, monday_1700)
        assert closed.order.promo_total == D("0.00") and len(closed.lines) == 1
        # Saturday afternoon: wrong day.
        sat = await _sell(s, c, 1, 20000, wib(2026, 9, 12, 15))
        assert sat.order.promo_total == D("0.00")
        # Buy 3 inside the window: capped at 2 free.
        three = await _sell(s, c, 3, 60000, monday_1530)
        assert three.order.promo_total == D("40000.00") and [cl.line.quantity for cl in three.lines] == [D("3.000"), D("2.000")]
        await s.commit()


async def test_payment_is_for_the_promo_price_and_bonus_stock_is_guarded(session_factory, shop):
    c = shop
    at = wib(2026, 9, 7, 15)
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        from app.services.orders import PaymentMismatch
        with pytest.raises(PaymentMismatch) as exc:
            await _sell(s, c, 1, 40000, at)          # the customer does not pay for the free one
        assert exc.value.total == D("20000.00")
        await s.rollback()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        kopi = await s.get(Item, c["kopi"])
        kopi.current_stock = D(1)                    # one left: the paid cup takes it, the bonus cannot
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        from app.services.sales import InsufficientStock
        with pytest.raises(InsufficientStock):
            await _sell(s, c, 1, 20000, at)
        await s.rollback()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert (await s.get(Item, c["kopi"])).current_stock == D("1.000")   # nothing sold, nothing taken
        assert (await s.execute(select(func.count(Order.id)))).scalar_one() == 0


async def test_refund_reverses_the_promo_and_percent_promo_with_tax(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        row = await ensure_pricing_settings(s, c["bid"])
        row.tax_rate, row.tax_inclusive = D("0.11"), False
        await create_promo(s, c["bid"], name="Teh 25%", kind="percent_off", value=D("0.25"), item_id=c["teh"])
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        # 2 Teh = 16.000, promo 4.000 → 12.000 taxable → tax 1.320 → 13.320
        sale = await create_order(
            s, business_id=c["bid"], staff_id=c["sari"], lines=[OrderLineSpec(item_id=c["teh"], quantity=D(2))],
            payments=[PaymentSpec(method="cash", amount=D(13320))], sold_at=wib(2026, 9, 8, 10),
        )
        o = sale.order
        assert (o.subtotal, o.promo_total, o.tax_total, o.total) == (D("16000.00"), D("4000.00"), D("1320.00"), D("13320.00"))
        await s.commit()
        oid = o.id
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        await refund_order(s, business_id=c["bid"], order_id=oid, staff_id=c["sari"], manager_pin="1234")
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        refund = (await s.execute(select(JournalEntry).where(JournalEntry.source_id == oid, JournalEntry.event_type == "OrderRefunded"))).scalar_one()
        memos = {l.memo for l in (await s.execute(select(JournalLine).where(JournalLine.entry_id == refund.id))).scalars()}
        assert {"refund:cash", "promo_reversal", "tax_reversal", "cogs_reversal"} <= memos
        b = await account_balances(s)
        assert b["4250"] == D("0.00") and b["2200"] == D("0.00") and b["1100"] == D("0.00")
        assert b["4100"] == -b["4300"]
        dr, cr = await trial_balance(s)
        assert dr == cr


async def test_owner_crud_and_the_kiosk_quote(session_factory, shop):
    from app.api.dashboard import add_promo, edit_promo, list_promos
    from app.api.pos import pos_quote
    from app.schemas.dashboard import PromoConditionIn, PromoCreateIn, PromoUpdateIn
    from app.schemas.pos import OrderLineIn, QuoteIn

    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        owner = SimpleNamespace(session=s, business_id=c["bid"], staff_id=None)
        listed = await list_promos(owner, include_inactive=True)
        assert [p.name for p in listed] == ["BOGO Kopi sore"] and listed[0].conditions[0].kind in ("day_of_week", "time_window")
        created = await add_promo(PromoCreateIn(
            name="Belanja 30rb diskon 3rb", kind="amount_off", value=D(3000),
            conditions=[PromoConditionIn(kind="min_spend", amount=D(30000)),
                        PromoConditionIn(kind="date_range", starts_at=wib(2026, 9, 1, 0), ends_at=wib(2026, 12, 31, 0))],
        ), owner)
        assert created.kind == "amount_off" and len(created.conditions) == 2 and created.applications == 0
        with pytest.raises(HTTPException) as exc:
            await add_promo(PromoCreateIn(name="Salah", kind="percent_off", value=D("1.5")), owner)
        assert exc.value.status_code == 422
        with pytest.raises(HTTPException) as exc:
            await add_promo(PromoCreateIn(name="Tanpa barang", kind="bonus_item"), owner)
        assert exc.value.status_code == 422
        with pytest.raises(HTTPException) as exc:
            await add_promo(PromoCreateIn(name="Syarat kosong", kind="amount_off", value=D(1), conditions=[PromoConditionIn(kind="day_of_week", days_of_week=[])]), owner)
        assert exc.value.status_code == 422
        # Replace the condition set and switch off.
        edited = await edit_promo(created.id, PromoUpdateIn(conditions=[PromoConditionIn(kind="min_spend", amount=D(50000))]), owner)
        assert [cd.kind for cd in edited.conditions] == ["min_spend"] and edited.conditions[0].amount == D("50000.00")
        off = await edit_promo(c["bogo"], PromoUpdateIn(is_active=False), owner)
        assert off.is_active is False
        assert [r.name for r in await load_rules(s)] == ["Belanja 30rb diskon 3rb"]
        await edit_promo(c["bogo"], PromoUpdateIn(is_active=True), owner)
        await s.commit()

    # The kiosk quote runs the same engine at "now": make the BOGO open right now.
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        owner = SimpleNamespace(session=s, business_id=c["bid"], staff_id=None)
        await edit_promo(c["bogo"], PromoUpdateIn(conditions=[]), owner)   # always on
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        pos = SimpleNamespace(session=s, business_id=c["bid"], staff_id=c["sari"])
        quote = await pos_quote(QuoteIn(lines=[OrderLineIn(item_id=c["kopi"], quantity=D(1)), OrderLineIn(item_id=c["teh"], quantity=D(2))]), pos)
        assert (quote.subtotal, quote.promo_total, quote.total) == (D("56000.00"), D("20000.00"), D("36000.00"))
        assert [(l.is_bonus, l.quantity, l.promo_discount) for l in quote.lines] == [
            (False, D("1.000"), D("0.00")), (False, D("2.000"), D("0.00")), (True, D("1.000"), D("20000.00")),
        ]
        assert quote.lines[2].promo_name == "BOGO Kopi sore" and [p.name for p in quote.promos] == ["BOGO Kopi sore"]
        sale = await create_order(
            s, business_id=c["bid"], staff_id=c["sari"],
            lines=[OrderLineSpec(item_id=c["kopi"], quantity=D(1)), OrderLineSpec(item_id=c["teh"], quantity=D(2))],
            payments=[PaymentSpec(method="cash", amount=D(36000))],
        )
        assert sale.order.total == quote.total == D("36000.00")   # the screen and the sale agree
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        owner = SimpleNamespace(session=s, business_id=c["bid"], staff_id=None)
        bogo = next(p for p in await list_promos(owner, include_inactive=True) if p.id == c["bogo"])
        assert bogo.applications == 1 and bogo.given_away == D("20000.00")
