"""M4-T5 — moving-average COGS.

Done-when (roadmap): a test buys at two prices, sells, asserts exact COGS, and
proves gross margin on an old order does not move when today's cost changes.

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai.tools import get_profit
from app.core.security import hash_pin
from app.models import Business, Item, ItemVariant, OrderLine, Staff, StockMovement
from app.services.catalog import create_variant, ensure_default_variant, set_recipe_line
from app.services.orders import OrderLineSpec, PaymentSpec, create_order
from app.services.receipts import commit_parse
from app.services.stock import add_stock, moving_average, open_item_stock
from app.services.units import ensure_standard_uoms

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")


def test_moving_average_arithmetic():
    assert moving_average(Decimal(0), Decimal(0), Decimal(5), Decimal(100000)) == Decimal("100000.00")
    assert moving_average(Decimal(5), Decimal(100000), Decimal(5), Decimal(140000)) == Decimal("120000.00")
    assert moving_average(Decimal(3), Decimal(20000), Decimal(1), Decimal(30000)) == Decimal("22500.00")
    # Uneven quantities: (2 × 10.000 + 1 × 16.000) / 3 = 12.000
    assert moving_average(Decimal(2), Decimal(10000), Decimal(1), Decimal(16000)) == Decimal("12000.00")
    # Rounds half-up to rupiah cents: (1 × 10 + 2 × 11) / 3 = 10.666… → 10.67
    assert moving_average(Decimal(1), Decimal(10), Decimal(2), Decimal(11)) == Decimal("10.67")
    # Negative or zero stock carries nothing: the new price is the cost.
    assert moving_average(Decimal(-2), Decimal(999), Decimal(4), Decimal(50)) == Decimal("50.00")


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
        biz = Business(name="COGS Test", owner_phone=f"62989{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        uoms = await ensure_standard_uoms(s, bid)
        staff = Staff(business_id=bid, name="Kasir", pin_hash=hash_pin("1111"))
        # A resold item: bought in, sold as is.
        bubuk = Item(business_id=bid, name="Kopi Bubuk 250g", unit="pcs", current_stock=Decimal(0),
                     cost_price=Decimal(0), sell_price=Decimal(45000), uom_id=uoms["pcs"].id)
        # Raw materials for a made-to-order latte.
        beans = Item(business_id=bid, name="Biji Arabica", unit="kg", current_stock=Decimal(0), cost_price=Decimal(0), uom_id=uoms["kg"].id)
        milk = Item(business_id=bid, name="Susu UHT", unit="liter", current_stock=Decimal(10), cost_price=Decimal(17000), uom_id=uoms["liter"].id)
        latte = Item(business_id=bid, name="Latte", unit="cup", current_stock=Decimal(0), cost_price=Decimal(0), sell_price=Decimal(28000), uom_id=uoms["cup"].id)
        s.add_all([staff, bubuk, beans, milk, latte])
        await s.flush()
        await open_item_stock(s, milk, unit_cost=milk.cost_price)
        for it in (bubuk, beans, milk, latte):
            await ensure_default_variant(s, it)
        large = await create_variant(s, latte, name="Large", sell_price=Decimal(28000), cost_price=Decimal(0))
        await set_recipe_line(s, large, beans, quantity=Decimal(24), uom_id=uoms["g"].id)
        await set_recipe_line(s, large, milk, quantity=Decimal(180), uom_id=uoms["ml"].id)
        await s.commit()
        ids = {"bid": bid, "staff": staff.id, "bubuk": bubuk.id, "beans": beans.id, "milk": milk.id,
               "latte": latte.id, "large": large.id}
    yield ids
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


async def _margin(s, line_id) -> Decimal:
    """Gross margin of a line from its own snapshot — the only correct way."""
    line = await s.get(OrderLine, line_id)
    return ((line.unit_price - line.unit_cost_at_sale) * line.quantity).quantize(Decimal("0.01"))


async def test_buy_at_two_prices_sell_and_old_margin_does_not_move(session_factory, shop):
    c = shop
    async with session_factory() as s:  # buy 2 @ 20.000, then 2 @ 30.000 → average 25.000
        await _set_tenant(s, c["bid"])
        bubuk = await s.get(Item, c["bubuk"])
        await add_stock(s, bubuk, Decimal(2), reason="purchase", source_type="test", unit_cost=Decimal(20000))
        assert bubuk.cost_price == Decimal("20000.00")
        await add_stock(s, bubuk, Decimal(2), reason="purchase", source_type="test", unit_cost=Decimal(30000))
        assert bubuk.cost_price == Decimal("25000.00")
        from app.services.catalog import sync_default_from_item
        await sync_default_from_item(s, bubuk)
        await s.commit()

    async with session_factory() as s:  # sell one: exact COGS 25.000 snapshotted
        await _set_tenant(s, c["bid"])
        created = await create_order(
            s, business_id=c["bid"], staff_id=c["staff"],
            lines=[OrderLineSpec(item_id=c["bubuk"], quantity=Decimal(1))],
            payments=[PaymentSpec(method="cash", amount=Decimal(45000))],
        )
        await s.commit()
        line_id = created.lines[0].line.id
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        line = await s.get(OrderLine, line_id)
        assert line.unit_cost_at_sale == Decimal("25000.00")
        move = (await s.execute(select(StockMovement).where(StockMovement.source_id == line_id))).scalar_one()
        assert move.unit_cost == Decimal("25000.00")
        assert await _margin(s, line_id) == Decimal("20000.00")
        biz = await s.get(Business, c["bid"])
        profit = await get_profit(s, biz, {"period": "today"})
        assert profit["cost_of_goods_estimate"] == 25000.0 and profit["cost_of_goods_lines_without_snapshot"] == 0

    async with session_factory() as s:  # today's cost changes: buy 3 @ 60.000 → new average
        await _set_tenant(s, c["bid"])
        bubuk = await s.get(Item, c["bubuk"])
        await add_stock(s, bubuk, Decimal(3), reason="purchase", source_type="test", unit_cost=Decimal(60000))
        assert bubuk.cost_price == Decimal("42500.00")  # (3 on hand × 25.000 + 3 × 60.000) / 6
        await s.commit()

    async with session_factory() as s:  # the old order's margin has not moved
        await _set_tenant(s, c["bid"])
        assert (await s.get(OrderLine, line_id)).unit_cost_at_sale == Decimal("25000.00")
        assert await _margin(s, line_id) == Decimal("20000.00")
        biz = await s.get(Business, c["bid"])
        profit = await get_profit(s, biz, {"period": "today"})
        assert profit["cost_of_goods_estimate"] == 25000.0  # not 42.500

    async with session_factory() as s:  # a new sale snapshots the new average
        await _set_tenant(s, c["bid"])
        created = await create_order(
            s, business_id=c["bid"], staff_id=c["staff"],
            lines=[OrderLineSpec(item_id=c["bubuk"], quantity=Decimal(2))],
            payments=[PaymentSpec(method="cash", amount=Decimal(90000))],
        )
        await s.commit()
        assert created.lines[0].line.unit_cost_at_sale == Decimal("42500.00")


async def test_made_to_order_cogs_is_the_recipe_at_component_averages(session_factory, shop):
    c = shop
    async with session_factory() as s:  # beans 5 kg @100.000 then 5 kg @140.000 → 120.000/kg
        await _set_tenant(s, c["bid"])
        beans = await s.get(Item, c["beans"])
        await add_stock(s, beans, Decimal(5), reason="purchase", source_type="test", unit_cost=Decimal(100000))
        await add_stock(s, beans, Decimal(5), reason="purchase", source_type="test", unit_cost=Decimal(140000))
        assert beans.cost_price == Decimal("120000.00")
        await s.commit()
    async with session_factory() as s:  # one large latte: 24 g × 120.000/kg + 180 ml × 17.000/l
        await _set_tenant(s, c["bid"])
        created = await create_order(
            s, business_id=c["bid"], staff_id=c["staff"],
            lines=[OrderLineSpec(item_id=c["latte"], quantity=Decimal(1), variant_id=c["large"])],
            payments=[PaymentSpec(method="qris", amount=Decimal(28000))],
        )
        await s.commit()
        line = created.lines[0].line
        assert line.unit_cost_at_sale == Decimal("5940.00")  # 2.880 + 3.060
        line_id = line.id
    async with session_factory() as s:  # beans get expensive: the sold latte's COGS stays 5.940
        await _set_tenant(s, c["bid"])
        beans = await s.get(Item, c["beans"])
        await add_stock(s, beans, Decimal(10), reason="purchase", source_type="test", unit_cost=Decimal(200000))
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert (await s.get(OrderLine, line_id)).unit_cost_at_sale == Decimal("5940.00")
        assert await _margin(s, line_id) == Decimal("22060.00")


async def test_receipt_photo_purchase_moves_the_average(session_factory, shop):
    """A receipt photo committing 3 kg @38.000 on top of 4 kg carried @30.000 → 33.428,57."""
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        gula = Item(business_id=c["bid"], name="Gula Aren", unit="kg", current_stock=Decimal(4), cost_price=Decimal(30000))
        s.add(gula)
        await s.flush()
        await open_item_stock(s, gula, unit_cost=gula.cost_price)
        await ensure_default_variant(s, gula)
        await s.commit()
        gula_id = gula.id
    parsed = {
        "document_type": "receipt", "supplier": "Toko Manis", "date": "2026-09-01",
        "items": [{"name": "Gula Aren", "quantity": 3, "unit": "kg", "unit_price": 38000, "line_total": 114000}],
        "total_amount": 114000, "confidence": "high", "ambiguities": [],
    }
    with patch("app.services.rag.embed_receipt", new=AsyncMock()):
        async with session_factory() as s:
            await _set_tenant(s, c["bid"])
            biz = await s.get(Business, c["bid"])
            await commit_parse(s, biz, parsed, "receipts/z.jpg")
            await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        gula = await s.get(Item, gula_id)
        assert gula.current_stock == Decimal("7.000")
        assert gula.cost_price == Decimal("33428.57")  # (4 × 30.000 + 3 × 38.000) / 7, half-up
        default = (await s.execute(select(ItemVariant).where(ItemVariant.item_id == gula_id, ItemVariant.is_default.is_(True)))).scalar_one()
        assert default.cost_price == Decimal("33428.57")  # default variant mirrors the item
