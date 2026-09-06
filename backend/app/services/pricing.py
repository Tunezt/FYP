"""How a bill is built (roadmap M7-T4): discounts, tax, service charge, rounding.

`price_order` is a **pure function**. It takes the lines, the business's
settings and an optional bill discount, and returns every figure an order row
carries. It touches no session, so the arithmetic can be pinned down by a table
of cases rather than by running sales — which is the point, because this is
where being off by one rupiah compounds into a broken ledger.

The four knobs and what they mean:

  tax_inclusive        menu prices already contain the tax (the usual warung),
                       so tax is *extracted* from the line totals rather than
                       added on top.
  service_before_tax   the service charge sits inside the taxable base, so it
                       is taxed too. Otherwise tax is worked out first and the
                       service charge is taken on the tax-inclusive amount.
  rounding_unit        rupiah rounding at the total (100 = to the nearest
                       hundred). 0 turns it off.
  rounding_mode        nearest (half up), up, or down.

The identity every result satisfies, and which `test_pricing` asserts on all
twelve combinations:

  exclusive tax:  total = subtotal − discount − promo − voucher + service_charge + tax + rounding
  inclusive tax:  total = subtotal − discount − promo − voucher + service_charge + rounding
                  (tax_total is the tax *contained* in those figures, and is
                  reported so the ledger can move it out of revenue)

Applied at the till by `services/orders.create_order` (M7-T4b): the manager-PIN
gate on discounts, the order rows, and the journal entry through
`fiscal_components`. Nothing here writes anything.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PricingSettings

MONEY = Decimal("0.01")
ROUNDING_MODES = ("nearest", "up", "down")


def q(amount: Decimal) -> Decimal:
    """To the rupiah cent, half up. Every intermediate figure goes through this,
    so a total is always the sum of figures that were themselves rounded — the
    same order the receipt prints them in."""
    return Decimal(amount).quantize(MONEY, rounding=ROUND_HALF_UP)


class PricingInvalid(Exception):
    """`code`: quantity, price, discount, line_discount, promo, voucher, mode, rate, unit."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


ALL_ORDER_TYPES = ("dine_in", "takeaway", "delivery", "pickup")


@dataclass(frozen=True)
class PricingConfig:
    """The business's settings, detached from the ORM row so `price_order`
    stays pure and a test can state a case in one literal."""

    tax_rate: Decimal = Decimal(0)
    tax_inclusive: bool = True
    service_charge_rate: Decimal = Decimal(0)
    service_before_tax: bool = True
    rounding_unit: Decimal = Decimal(0)
    rounding_mode: str = "nearest"
    discount_requires_pin: bool = True
    # Order type routing (M11-T3): the service charge only on these types; a
    # flat fee on `delivery`, added after tax, never taxed.
    service_applies_to: tuple[str, ...] = ALL_ORDER_TYPES
    delivery_fee: Decimal = Decimal(0)

    @classmethod
    def from_row(cls, row: PricingSettings) -> "PricingConfig":
        return cls(
            tax_rate=Decimal(row.tax_rate),
            tax_inclusive=bool(row.tax_inclusive),
            service_charge_rate=Decimal(row.service_charge_rate),
            service_before_tax=bool(row.service_before_tax),
            rounding_unit=Decimal(row.rounding_unit),
            rounding_mode=row.rounding_mode,
            discount_requires_pin=bool(row.discount_requires_pin),
            service_applies_to=tuple(row.service_applies_to or ALL_ORDER_TYPES),
            delivery_fee=Decimal(row.delivery_fee or 0),
        )


@dataclass(frozen=True)
class LineInput:
    unit_price: Decimal
    quantity: Decimal
    line_discount: Decimal = Decimal(0)   # an amount off this line, not a rate
    promo_discount: Decimal = Decimal(0)  # what the promo engine gave on this line (M8-T3)


@dataclass(frozen=True)
class PricedLine:
    unit_price: Decimal
    quantity: Decimal
    gross: Decimal          # unit_price × quantity
    line_discount: Decimal
    line_total: Decimal     # gross − line_discount (the promo is order-level: see PricedOrder.promo_total)
    promo_discount: Decimal = Decimal("0.00")


@dataclass(frozen=True)
class PricedOrder:
    """Exactly the figures an `orders` row carries, plus the priced lines."""

    subtotal: Decimal          # Σ gross, before any discount
    discount_total: Decimal    # line discounts + the bill discount (the cashier's)
    service_charge: Decimal
    tax_total: Decimal         # added on top (exclusive) or contained (inclusive)
    rounding: Decimal          # total − the figure before rounding
    total: Decimal
    tax_inclusive: bool
    lines: tuple[PricedLine, ...]
    service_tax: Decimal = Decimal("0.00")   # the part of service_charge that is tax (inclusive + taxed only)
    promo_total: Decimal = Decimal("0.00")   # what promos gave away (M8-T3), kept apart from discount_total
    voucher_total: Decimal = Decimal("0.00") # what a voucher code took off (M8-T4)
    delivery_fee: Decimal = Decimal("0.00")  # the flat fee a delivery carries (M11-T3); inside `total`, outside `net`

    @property
    def net(self) -> Decimal:
        """What the goods cost after discounts, promos and the voucher, at menu prices."""
        return q(self.subtotal - self.discount_total - self.promo_total - self.voucher_total)

    def fiscal_components(self) -> dict[str, Decimal]:
        """What the sale reclassifies out of revenue, for the posting engine
        (M7-T4b). `service_charge` is posted **ex-tax**: with inclusive prices
        and a taxed service charge the customer-facing figure carries its own
        tax, and that tax already sits in `tax_total` — posting it twice would
        overstate other income by exactly the amount revenue was understated.
        Rounding goes up or down as its own component because the engine
        never posts a negative amount."""
        return {
            "discount": self.discount_total,
            "promo": self.promo_total,
            "voucher": self.voucher_total,
            "tax": self.tax_total,
            "service_charge": q(self.service_charge - self.service_tax),
            "delivery_fee": self.delivery_fee,
            "rounding_up": self.rounding if self.rounding > 0 else Decimal("0.00"),
            "rounding_down": -self.rounding if self.rounding < 0 else Decimal("0.00"),
        }


def round_total(amount: Decimal, unit: Decimal, mode: str) -> Decimal:
    """Rupiah rounding at the total. `unit` 0 means none."""
    unit = Decimal(unit)
    if unit < 0:
        raise PricingInvalid("unit")
    if mode not in ROUNDING_MODES:
        raise PricingInvalid("mode")
    if unit == 0:
        return q(amount)
    steps = Decimal(amount) / unit
    if mode == "nearest":
        steps = steps.quantize(Decimal(1), rounding=ROUND_HALF_UP)
    elif mode == "up":
        steps = steps.to_integral_value(rounding=ROUND_CEILING)
    else:
        steps = steps.to_integral_value(rounding=ROUND_FLOOR)
    return q(steps * unit)


def price_order(
    lines: list[LineInput],
    config: PricingConfig,
    *,
    bill_discount: Decimal = Decimal(0),
    promo_bill_discount: Decimal = Decimal(0),
    voucher_discount: Decimal = Decimal(0),
    order_type: str = "takeaway",
) -> PricedOrder:
    """Price one bill. Raises rather than silently clamping: a discount larger
    than the bill, a negative quantity or an unknown rounding mode is a bug in
    the caller, and a till that quietly charges something else is worse than a
    till that refuses."""
    if config.tax_rate < 0 or config.tax_rate >= 1 or config.service_charge_rate < 0 or config.service_charge_rate >= 1:
        raise PricingInvalid("rate")

    priced: list[PricedLine] = []
    subtotal = Decimal(0)
    line_discounts = Decimal(0)
    promo_lines = Decimal(0)
    for line in lines:
        quantity, unit_price = Decimal(line.quantity), Decimal(line.unit_price)
        discount = q(line.line_discount or 0)
        promo = q(line.promo_discount or 0)
        if quantity <= 0:
            raise PricingInvalid("quantity")
        if unit_price < 0:
            raise PricingInvalid("price")
        gross = q(unit_price * quantity)
        if discount < 0 or discount > gross:
            raise PricingInvalid("line_discount")
        if promo < 0 or promo > gross - discount:
            raise PricingInvalid("promo")
        priced.append(PricedLine(q(unit_price), quantity, gross, discount, q(gross - discount), promo))
        subtotal += gross
        line_discounts += discount
        promo_lines += promo

    subtotal = q(subtotal)
    bill_discount = q(bill_discount or 0)
    if bill_discount < 0:
        raise PricingInvalid("discount")
    discount_total = q(line_discounts + bill_discount)
    if discount_total > subtotal:
        raise PricingInvalid("discount")
    promo_bill_discount = q(promo_bill_discount or 0)
    if promo_bill_discount < 0:
        raise PricingInvalid("promo")
    promo_total = q(promo_lines + promo_bill_discount)
    if discount_total + promo_total > subtotal:
        raise PricingInvalid("promo")
    voucher_total = q(voucher_discount or 0)
    if voucher_total < 0 or discount_total + promo_total + voucher_total > subtotal:
        raise PricingInvalid("voucher")

    net = q(subtotal - discount_total - promo_total - voucher_total)
    tax_rate = Decimal(config.tax_rate)
    # Routing by order type (M11-T3): the service charge only where the
    # settings put it (a café: dine-in only); a flat delivery fee on delivery,
    # added after tax and not taxed.
    sc_rate = Decimal(config.service_charge_rate) if order_type in config.service_applies_to else Decimal(0)
    delivery_fee = q(config.delivery_fee) if order_type == "delivery" else Decimal("0.00")
    if delivery_fee < 0:
        raise PricingInvalid("rate")

    service_tax = Decimal("0.00")
    if not config.tax_inclusive:
        # Menu prices exclude tax: service and tax are both added on top.
        if config.service_before_tax:
            service = q(net * sc_rate)
            tax = q((net + service) * tax_rate)
        else:
            tax = q(net * tax_rate)
            service = q((net + tax) * sc_rate)
        before_rounding = q(net + service + tax)
    else:
        # Menu prices include tax: the tax already inside `net` is extracted,
        # never added, or the customer would be charged it twice.
        net_ex = q(net / (1 + tax_rate))
        contained = q(net - net_ex)
        if config.service_before_tax:
            # The service charge is taxed too, so it is taken on the ex-tax
            # amount and carries its own tax into what the customer pays.
            service_ex = q(net_ex * sc_rate)
            service_tax = q(service_ex * tax_rate)
            service = q(service_ex + service_tax)
            tax = q(contained + service_tax)
        else:
            service = q(net * sc_rate)
            tax = contained
        before_rounding = q(net + service)

    before_rounding = q(before_rounding + delivery_fee)
    total = round_total(before_rounding, config.rounding_unit, config.rounding_mode)
    return PricedOrder(
        subtotal=subtotal,
        discount_total=discount_total,
        service_charge=service,
        tax_total=tax,
        rounding=q(total - before_rounding),
        total=total,
        tax_inclusive=config.tax_inclusive,
        lines=tuple(priced),
        service_tax=service_tax,
        promo_total=promo_total,
        voucher_total=voucher_total,
        delivery_fee=delivery_fee,
    )


# ── the business's settings ─────────────────────────────────────────────────


async def ensure_pricing_settings(session: AsyncSession, business_id: uuid.UUID) -> PricingSettings:
    """Every business has exactly one row. Created at registration; migration
    0019 backfills the ones that existed before."""
    row = (await session.execute(select(PricingSettings))).scalar_one_or_none()
    if row is None:
        row = PricingSettings(business_id=business_id)
        session.add(row)
        await session.flush()
    return row


async def pricing_config(session: AsyncSession, business_id: uuid.UUID) -> PricingConfig:
    """Read-only: the sale path must never write the settings row (two
    concurrent first sales would race on the unique key). Registration, the
    seed and migration 0019 guarantee the row; a business somehow without one
    is priced with the defaults, which is what the row would have said."""
    row = (await session.execute(select(PricingSettings))).scalar_one_or_none()
    return PricingConfig.from_row(row) if row is not None else PricingConfig()
