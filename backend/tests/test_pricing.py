"""M7-T4a — the pricing engine: discounts, tax, service charge, rounding.

The roadmap asks for the table of twelve combinations *first*, and this is it:
tax inclusive or exclusive × service charge before or after tax × no discount,
a line discount, or a bill discount. Every expected figure below was worked out
by hand from one bill and one set of rates, so the table is a specification the
code has to meet rather than a recording of whatever the code happened to do.

The bill, in every row:

    3 × 18.000  = 54.000
    1 × 27.500  = 27.500
    subtotal      81.500

    tax 11%, service charge 5%, rounding to the nearest 100.
    line discount = 4.500 off the first line · bill discount = 6.000

`price_order` is pure, so none of this needs a database. The two settings tests
at the end do, because the row and its defaults are the thing being checked.
"""
import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Business, PricingSettings
from app.services.pricing import (
    LineInput,
    PricingConfig,
    PricingInvalid,
    ensure_pricing_settings,
    price_order,
    pricing_config,
    round_total,
)

D = Decimal


def bill(line_discount: Decimal = D(0)) -> list[LineInput]:
    return [
        LineInput(unit_price=D(18000), quantity=D(3), line_discount=line_discount),
        LineInput(unit_price=D("27500"), quantity=D(1)),
    ]


def config(*, inclusive: bool, service_before_tax: bool) -> PricingConfig:
    return PricingConfig(
        tax_rate=D("0.11"),
        tax_inclusive=inclusive,
        service_charge_rate=D("0.05"),
        service_before_tax=service_before_tax,
        rounding_unit=D(100),
        rounding_mode="nearest",
    )


# (id, inclusive, service_before_tax, line discount, bill discount,
#  expected discount_total, service_charge, tax_total, rounding, total)
#
# Exclusive rows: service and tax are added on top, so the total is the same
# whichever order they are applied in (5% then 11% is 11% then 5%) — what
# changes is how much of it is tax and how much is service. That is exactly the
# distinction the ledger cares about, so the split is asserted, not just the total.
#
# Inclusive rows: the tax is extracted from the menu prices, never added, so the
# customer pays 81.500 of goods plus service only. `service_before_tax` then
# decides whether the service charge carries its own tax (raising tax_total)
# or not.
CASES = [
    # ── tax exclusive ───────────────────────────────────────────────────────
    ("excl · service taxed · no discount",
     False, True, D(0), D(0), D("0.00"), D("4075.00"), D("9413.25"), D("11.75"), D("95000.00")),
    ("excl · service taxed · line discount",
     False, True, D(4500), D(0), D("4500.00"), D("3850.00"), D("8893.50"), D("-43.50"), D("89700.00")),
    ("excl · service taxed · bill discount",
     False, True, D(0), D(6000), D("6000.00"), D("3775.00"), D("8720.25"), D("4.75"), D("88000.00")),
    ("excl · service after tax · no discount",
     False, False, D(0), D(0), D("0.00"), D("4523.25"), D("8965.00"), D("11.75"), D("95000.00")),
    ("excl · service after tax · line discount",
     False, False, D(4500), D(0), D("4500.00"), D("4273.50"), D("8470.00"), D("-43.50"), D("89700.00")),
    ("excl · service after tax · bill discount",
     False, False, D(0), D(6000), D("6000.00"), D("4190.25"), D("8305.00"), D("4.75"), D("88000.00")),
    # ── tax inclusive ───────────────────────────────────────────────────────
    ("incl · service taxed · no discount",
     True, True, D(0), D(0), D("0.00"), D("4075.00"), D("8480.41"), D("25.00"), D("85600.00")),
    ("incl · service taxed · line discount",
     True, True, D(4500), D(0), D("4500.00"), D("3850.00"), D("8012.16"), D("50.00"), D("80900.00")),
    ("incl · service taxed · bill discount",
     True, True, D(0), D(6000), D("6000.00"), D("3775.00"), D("7856.08"), D("25.00"), D("79300.00")),
    ("incl · service after tax · no discount",
     True, False, D(0), D(0), D("0.00"), D("4075.00"), D("8076.58"), D("25.00"), D("85600.00")),
    ("incl · service after tax · line discount",
     True, False, D(4500), D(0), D("4500.00"), D("3850.00"), D("7630.63"), D("50.00"), D("80900.00")),
    ("incl · service after tax · bill discount",
     True, False, D(0), D(6000), D("6000.00"), D("3775.00"), D("7481.98"), D("25.00"), D("79300.00")),
]


@pytest.mark.parametrize(
    "label,inclusive,service_before_tax,line_discount,bill_discount,discount_total,service,tax,rounding,total",
    CASES,
    ids=[c[0] for c in CASES],
)
def test_the_twelve_combinations(
    label, inclusive, service_before_tax, line_discount, bill_discount,
    discount_total, service, tax, rounding, total,
):
    result = price_order(
        bill(line_discount),
        config(inclusive=inclusive, service_before_tax=service_before_tax),
        bill_discount=bill_discount,
    )
    assert result.subtotal == D("81500.00")
    assert result.discount_total == discount_total
    assert result.service_charge == service
    assert result.tax_total == tax
    assert result.rounding == rounding
    assert result.total == total

    # The identity the order row has to satisfy. Inclusive tax is already inside
    # the line totals, so it does not appear again on the right-hand side.
    added_tax = D(0) if inclusive else tax
    assert result.total == result.subtotal - result.discount_total + service + added_tax + rounding

    # Rounding is only ever the gap to the nearest 100, never a way to lose money.
    assert abs(result.rounding) <= D(50)
    assert result.total % 100 == 0


def test_the_table_covers_every_combination():
    """Guards the table itself: twelve rows, one per combination, no duplicates."""
    seen = {(inclusive, before, bool(line), bool(bill)) for _, inclusive, before, line, bill, *_ in CASES}
    assert len(CASES) == 12 and len(seen) == 12
    assert {(i, b) for i, b, _l, _bd in seen} == {(True, True), (True, False), (False, True), (False, False)}


def test_lines_carry_their_own_discount_and_total():
    result = price_order(bill(D(4500)), config(inclusive=False, service_before_tax=True))
    assert [(l.gross, l.line_discount, l.line_total) for l in result.lines] == [
        (D("54000.00"), D("4500.00"), D("49500.00")),
        (D("27500.00"), D("0.00"), D("27500.00")),
    ]
    assert result.net == D("77000.00")
    assert sum((l.line_total for l in result.lines), D(0)) == result.net


def test_no_tax_no_service_no_rounding_is_the_plain_warung():
    """The defaults: what the menu says is what the customer pays."""
    result = price_order(bill(), PricingConfig())
    assert (result.subtotal, result.total) == (D("81500.00"), D("81500.00"))
    assert (result.discount_total, result.service_charge, result.tax_total, result.rounding) == (
        D("0.00"), D("0.00"), D("0.00"), D("0.00"),
    )


def test_a_discount_alone_still_rounds_and_totals():
    result = price_order(
        bill(), PricingConfig(rounding_unit=D(500), rounding_mode="down"), bill_discount=D(1250),
    )
    assert result.net == D("80250.00") and result.total == D("80000.00") and result.rounding == D("-250.00")


@pytest.mark.parametrize(
    "amount,unit,mode,expected",
    [
        (D("89743.50"), D(100), "nearest", D("89700.00")),
        (D("89750.00"), D(100), "nearest", D("89800.00")),   # a tie rounds up
        (D("89743.50"), D(100), "up", D("89800.00")),
        (D("89743.50"), D(100), "down", D("89700.00")),
        (D("89700.00"), D(100), "up", D("89700.00")),        # already exact: no free rupiah
        (D("89743.50"), D(1000), "nearest", D("90000.00")),
        (D("89743.50"), D(0), "nearest", D("89743.50")),     # rounding off
        (D("89743.50"), D(50), "nearest", D("89750.00")),
    ],
)
def test_rounding_modes(amount, unit, mode, expected):
    assert round_total(amount, unit, mode) == expected


@pytest.mark.parametrize(
    "kwargs,code",
    [
        (dict(lines=[LineInput(D(1000), D(0))]), "quantity"),
        (dict(lines=[LineInput(D(1000), D(-1))]), "quantity"),
        (dict(lines=[LineInput(D(-1), D(1))]), "price"),
        (dict(lines=[LineInput(D(1000), D(1), D(-5))]), "line_discount"),
        (dict(lines=[LineInput(D(1000), D(1), D(1001))]), "line_discount"),
        (dict(lines=[LineInput(D(1000), D(1))], bill_discount=D(-1)), "discount"),
        (dict(lines=[LineInput(D(1000), D(1))], bill_discount=D(1001)), "discount"),
    ],
)
def test_it_refuses_rather_than_charging_something_else(kwargs, code):
    lines = kwargs.pop("lines")
    with pytest.raises(PricingInvalid) as exc:
        price_order(lines, PricingConfig(), **kwargs)
    assert exc.value.code == code


def test_bad_settings_are_refused():
    with pytest.raises(PricingInvalid) as exc:
        price_order(bill(), PricingConfig(tax_rate=D("1.5")))
    assert exc.value.code == "rate"
    with pytest.raises(PricingInvalid) as exc:
        price_order(bill(), PricingConfig(rounding_unit=D(100), rounding_mode="sideways"))
    assert exc.value.code == "mode"
    with pytest.raises(PricingInvalid) as exc:
        round_total(D(100), D(-1), "nearest")
    assert exc.value.code == "unit"


# ── the settings row (needs the local Postgres, roadmap §2) ─────────────────

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")


@pytest.fixture
async def session_factory():
    if not DB_URL:
        pytest.fail("INTEGRATION_DATABASE_URL unset — local Postgres is not configured (roadmap §2)")
    engine = create_async_engine(DB_URL, connect_args={"statement_cache_size": 0})
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def business(session_factory):
    async with session_factory() as s:
        biz = Business(name="Pricing Test", owner_phone=f"62974{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    yield bid
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


async def test_settings_default_to_the_plain_warung_and_are_created_once(session_factory, business):
    async with session_factory() as s:
        await s.execute(
            text("select set_config('app.current_business_id', :bid, true)"), {"bid": str(business)}
        )
        first = await ensure_pricing_settings(s, business)
        assert (first.tax_rate, first.service_charge_rate, first.rounding_unit) == (D("0.0000"), D("0.0000"), D("0.00"))
        assert first.tax_inclusive and first.service_before_tax and first.discount_requires_pin
        assert first.rounding_mode == "nearest"
        again = await ensure_pricing_settings(s, business)
        assert again.id == first.id
        await s.commit()

    async with session_factory() as s:
        await s.execute(
            text("select set_config('app.current_business_id', :bid, true)"), {"bid": str(business)}
        )
        cfg = await pricing_config(s, business)
        assert price_order(bill(), cfg).total == D("81500.00")


async def test_settings_reach_the_pure_function_unchanged(session_factory, business):
    async with session_factory() as s:
        await s.execute(
            text("select set_config('app.current_business_id', :bid, true)"), {"bid": str(business)}
        )
        row = await ensure_pricing_settings(s, business)
        row.tax_rate, row.tax_inclusive = D("0.11"), False
        row.service_charge_rate, row.service_before_tax = D("0.05"), True
        row.rounding_unit, row.rounding_mode = D(100), "nearest"
        await s.commit()

    async with session_factory() as s:
        await s.execute(
            text("select set_config('app.current_business_id', :bid, true)"), {"bid": str(business)}
        )
        cfg = await pricing_config(s, business)
        result = price_order(bill(), cfg)
        # The first row of the table, arrived at through the database.
        assert (result.service_charge, result.tax_total, result.total) == (
            D("4075.00"), D("9413.25"), D("95000.00"),
        )


async def test_the_database_refuses_impossible_settings(session_factory, business):
    """The CHECKs are the last line of defence if a future caller skips the service."""
    bad = [
        ("tax_rate", "1.5"),
        ("service_charge_rate", "-0.01"),
        ("rounding_unit", "-100"),
        ("rounding_mode", "'sideways'"),
    ]
    for column, value in bad:
        async with session_factory() as s:
            await s.execute(
                text("select set_config('app.current_business_id', :bid, true)"), {"bid": str(business)}
            )
            await ensure_pricing_settings(s, business)
            await s.commit()
        async with session_factory() as s:
            await s.execute(
                text("select set_config('app.current_business_id', :bid, true)"), {"bid": str(business)}
            )
            with pytest.raises(Exception):
                await s.execute(
                    text(f"update pricing_settings set {column} = {value} where business_id = :bid"),
                    {"bid": str(business)},
                )
                await s.commit()
            await s.rollback()

    async with session_factory() as s:
        await s.execute(
            text("select set_config('app.current_business_id', :bid, true)"), {"bid": str(business)}
        )
        s.add(PricingSettings(business_id=business))
        with pytest.raises(Exception):
            await s.commit()   # one row per business
