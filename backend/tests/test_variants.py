"""M4-T1 — variants: an item with three sizes sells at three prices and
reporting rolls up to the parent.

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

from app.api.dashboard import add_variant, edit_variant, list_variants, update_item
from app.core.security import hash_pin
from app.models import Business, Item, ItemVariant, OrderLine, Sale, Staff, StockMovement
from app.schemas.dashboard import ItemUpdateIn, VariantCreateIn, VariantUpdateIn
from app.services.catalog import VariantInvalid, create_variant, ensure_default_variant, update_variant
from app.services.orders import OrderLineSpec, PaymentSpec, VariantNotFound, create_order
from app.services.stock import open_item_stock

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
async def latte(session_factory):
    """A café with one item, 'Latte', 10 in stock, default price 22.000 / cost 8.000."""
    async with session_factory() as s:
        biz = Business(name="Variant Test", owner_phone=f"62993{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        staff = Staff(business_id=bid, name="Kasir", pin_hash=hash_pin("1111"))
        item = Item(business_id=bid, name="Latte", unit="cup", current_stock=Decimal(10),
                    cost_price=Decimal(8000), sell_price=Decimal(22000))
        s.add_all([staff, item])
        await s.flush()
        await open_item_stock(s, item, unit_cost=item.cost_price)
        default = await ensure_default_variant(s, item)
        await s.commit()
        ids = {"bid": bid, "staff": staff.id, "item": item.id, "default": default.id}
    yield ids
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


async def test_three_sizes_sell_at_three_prices_and_roll_up(session_factory, latte):
    async with session_factory() as s:
        await _set_tenant(s, latte["bid"])
        item = await s.get(Item, latte["item"])
        regular = await update_variant(s, await s.get(ItemVariant, latte["default"]), item, name="Regular")
        large = await create_variant(s, item, name="Large", sell_price=Decimal(28000), cost_price=Decimal(10000))
        jumbo = await create_variant(s, item, name="Jumbo", sell_price=Decimal(32000), cost_price=Decimal(12000), sku="LAT-XL")
        await s.commit()
        ids = {"regular": regular.id, "large": large.id, "jumbo": jumbo.id}

    async with session_factory() as s:
        await _set_tenant(s, latte["bid"])
        created = await create_order(
            s, business_id=latte["bid"], staff_id=latte["staff"],
            lines=[OrderLineSpec(item_id=latte["item"], quantity=Decimal(1), variant_id=ids["regular"]),
                   OrderLineSpec(item_id=latte["item"], quantity=Decimal(1), variant_id=ids["large"]),
                   OrderLineSpec(item_id=latte["item"], quantity=Decimal(1), variant_id=ids["jumbo"])],
            payments=[PaymentSpec(method="cash", amount=Decimal(82000))],
        )
        await s.commit()
        assert created.order.total == Decimal("82000.00")

    async with session_factory() as s:
        await _set_tenant(s, latte["bid"])
        lines = (await s.execute(select(OrderLine).where(OrderLine.item_id == latte["item"]).order_by(OrderLine.unit_price))).scalars().all()
        assert [(l.variant_id, l.unit_price, l.unit_cost_at_sale) for l in lines] == [
            (ids["regular"], Decimal("22000.00"), Decimal("8000.00")),
            (ids["large"], Decimal("28000.00"), Decimal("10000.00")),
            (ids["jumbo"], Decimal("32000.00"), Decimal("12000.00")),
        ]
        # Stock is the parent's: three cups gone, one movement per line at the variant's cost.
        assert (await s.get(Item, latte["item"])).current_stock == Decimal("7.000")
        costs = (await s.execute(select(StockMovement.unit_cost).where(StockMovement.reason == "sale"))).scalars().all()
        assert sorted(costs) == [Decimal("8000.00"), Decimal("10000.00"), Decimal("12000.00")]
        # Reporting rolls up to the parent item through the unchanged `sales` view.
        n, revenue = (await s.execute(
            select(func.count(Sale.id), func.sum(Sale.total_price)).where(Sale.item_id == latte["item"])
        )).one()
        assert (n, revenue) == (3, Decimal("82000.00"))


async def test_line_without_variant_uses_the_default(session_factory, latte):
    async with session_factory() as s:
        await _set_tenant(s, latte["bid"])
        created = await create_order(
            s, business_id=latte["bid"], staff_id=latte["staff"],
            lines=[OrderLineSpec(item_id=latte["item"], quantity=Decimal(2))],
            payments=[PaymentSpec(method="qris", amount=Decimal(44000))],
        )
        await s.commit()
        line = created.lines[0].line
        assert line.variant_id == latte["default"] and line.unit_price == Decimal("22000.00")


async def test_inactive_or_foreign_variant_is_refused(session_factory, latte):
    async with session_factory() as s:
        await _set_tenant(s, latte["bid"])
        item = await s.get(Item, latte["item"])
        old = await create_variant(s, item, name="Small", sell_price=Decimal(18000))
        await update_variant(s, old, item, is_active=False)
        other = Item(business_id=latte["bid"], name="Teh", unit="cup", current_stock=Decimal(5), sell_price=Decimal(10000))
        s.add(other)
        await s.flush()
        await open_item_stock(s, other)
        teh_default = await ensure_default_variant(s, other)
        await s.commit()
        ids = {"inactive": old.id, "teh_default": teh_default.id}

    for bad in ("inactive", "teh_default"):
        async with session_factory() as s:
            await _set_tenant(s, latte["bid"])
            with pytest.raises(VariantNotFound):
                await create_order(
                    s, business_id=latte["bid"], staff_id=latte["staff"],
                    lines=[OrderLineSpec(item_id=latte["item"], quantity=Decimal(1), variant_id=ids[bad])],
                    payments=[PaymentSpec(method="cash", amount=Decimal(1))],
                )
            await s.rollback()
    async with session_factory() as s:
        await _set_tenant(s, latte["bid"])
        assert (await s.get(Item, latte["item"])).current_stock == Decimal("10.000")


async def test_default_variant_and_item_prices_stay_in_sync_both_ways(session_factory, latte):
    async with session_factory() as s:  # item edited on the dashboard → default variant follows
        await _set_tenant(s, latte["bid"])
        ctx = SimpleNamespace(session=s, business_id=latte["bid"], staff_id=None)
        await update_item(latte["item"], ItemUpdateIn(sell_price=Decimal(25000), cost_price=Decimal(9000)), ctx)
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, latte["bid"])
        v = await s.get(ItemVariant, latte["default"])
        assert (v.sell_price, v.cost_price) == (Decimal("25000.00"), Decimal("9000.00"))

    async with session_factory() as s:  # default variant edited → item follows
        await _set_tenant(s, latte["bid"])
        ctx = SimpleNamespace(session=s, business_id=latte["bid"], staff_id=None)
        await edit_variant(latte["default"], VariantUpdateIn(sell_price=Decimal(26000)), ctx)
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, latte["bid"])
        assert (await s.get(Item, latte["item"])).sell_price == Decimal("26000.00")


async def test_owner_variant_endpoints_and_rules(session_factory, latte):
    async with session_factory() as s:
        await _set_tenant(s, latte["bid"])
        ctx = SimpleNamespace(session=s, business_id=latte["bid"], staff_id=None)
        large = await add_variant(latte["item"], VariantCreateIn(name="Large", sell_price=Decimal(28000), cost_price=Decimal(10000)), ctx)
        assert large.is_default is False and large.sell_price == Decimal("28000.00")
        with pytest.raises(HTTPException) as exc:
            await add_variant(latte["item"], VariantCreateIn(name="large", sell_price=Decimal(1)), ctx)
        assert exc.value.status_code == 409 and "sudah dipakai" in exc.value.detail
        with pytest.raises(HTTPException) as exc:
            await edit_variant(latte["default"], VariantUpdateIn(is_active=False), ctx)
        assert exc.value.status_code == 409 and "utama" in exc.value.detail
        # Promote Large to default: exactly one default remains, item price follows.
        await edit_variant(large.id, VariantUpdateIn(is_default=True), ctx)
        rows = await list_variants(latte["item"], ctx)
        assert [(r.name, r.is_default) for r in rows] == [("Large", True), ("Standar", False)]
        assert (await s.get(Item, latte["item"])).sell_price == Decimal("28000.00")
        await s.commit()


async def test_create_variant_rejects_blank_name(session_factory, latte):
    async with session_factory() as s:
        await _set_tenant(s, latte["bid"])
        item = await s.get(Item, latte["item"])
        with pytest.raises(VariantInvalid) as exc:
            await create_variant(s, item, name="   ", sell_price=Decimal(1))
        assert exc.value.code == "name"
