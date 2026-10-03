"""till-11 — the restaurant tax is added on top of the menu price, as "PB1".

The owner's feedback of 2 October 2026 (point 5): "why is the tax not added to
the total?" Indonesian cafés print Subtotal, PB1 10%, Total. It is the regional
restaurant tax (PBJT since UU 1/2022, formerly PB1), not PPN.

Because it is now *charged*, a rate nobody has confirmed must not reach a real
customer: a real café (bootstrap) starts at PB1, on top, 0%, and charges the
moment the owner types the confirmed rate. The demo seed shows 10%.

Needs the local Postgres (roadmap §2). No skip marker.
"""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.bootstrap import bootstrap
from app.models import Business, Item, Order, PricingSettings, Staff
from app.services.business_profile import TAX_LABEL_PLACEHOLDER, apply_tax_placeholder
from app.services.catalog import ensure_default_variant
from app.services.orders import OrderLineSpec, PaymentSpec, create_order
from app.services.stock import open_item_stock
from tests.test_bootstrap import _phone, _scoped, dispose_app_engine, engine, session_factory  # noqa: F401 (fixtures)

D = Decimal


def test_the_placeholder_is_pb1_added_on_top():
    class Row:
        pass

    row = Row()
    apply_tax_placeholder(row)
    assert (row.tax_label, row.tax_rate, row.tax_inclusive) == ("PB1", D("0.10"), False)
    apply_tax_placeholder(row, rate=D(0))
    assert (row.tax_label, row.tax_rate, row.tax_inclusive) == ("PB1", D(0), False)


@pytest.fixture
async def real_cafe(session_factory):
    result = await bootstrap(name="Kopi Bu Tini", owner_name="Bu Tini", phone=_phone(), pin="4821")
    yield result.business_id
    async with session_factory() as session:
        stale = await session.get(Business, result.business_id)
        if stale:
            await session.delete(stale)
        await session.commit()


async def _sell(session_factory, business_id, amount):
    async with session_factory() as session:
        await _scoped(session, business_id)
        staff = (await session.execute(select(Staff).where(Staff.business_id == business_id))).scalars().one()
        kopi = (await session.execute(select(Item).where(Item.business_id == business_id))).scalars().first()
        if kopi is None:
            kopi = Item(business_id=business_id, name="Kopi Tubruk", unit="cup",
                        current_stock=D(50), cost_price=D(4000), sell_price=D(12000))
            session.add(kopi)
            await session.flush()
            await open_item_stock(session, kopi, unit_cost=kopi.cost_price)
            await ensure_default_variant(session, kopi)
        created = await create_order(
            session, business_id=business_id, staff_id=staff.id,
            lines=[OrderLineSpec(item_id=kopi.id, quantity=D(2))],
            payments=[PaymentSpec(method="cash", amount=amount)],
        )
        order_id = created.order.id
        await session.commit()
    async with session_factory() as session:
        await _scoped(session, business_id)
        return await session.get(Order, order_id)


async def test_a_real_cafe_charges_no_tax_until_the_owner_sets_the_rate(session_factory, real_cafe):
    async with session_factory() as session:
        await _scoped(session, real_cafe)
        pricing = (await session.execute(select(PricingSettings))).scalar_one()
        assert (pricing.tax_label, pricing.tax_rate, pricing.tax_inclusive) == (TAX_LABEL_PLACEHOLDER, D("0.0000"), False)

    first = await _sell(session_factory, real_cafe, D(24000))
    assert (first.subtotal, first.tax_total, first.total) == (D("24000.00"), D("0.00"), D("24000.00"))

    # The owner confirms 10% with Bapenda and types it: now it is added on top.
    async with session_factory() as session:
        await _scoped(session, real_cafe)
        pricing = (await session.execute(select(PricingSettings))).scalar_one()
        pricing.tax_rate = D("0.10")
        await session.commit()
    second = await _sell(session_factory, real_cafe, D(26400))
    assert (second.subtotal, second.tax_total, second.total) == (D("24000.00"), D("2400.00"), D("26400.00"))
