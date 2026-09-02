"""M4-T4 — recipes keyed on the variant: selling one large latte writes stock
movements for beans and milk in the right quantities, and M2-T3 still holds.

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import os
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.dashboard import edit_recipe_line, get_recipe, put_recipe_line
from app.api.pos import pos_items
from app.core.security import hash_pin
from app.models import Business, Item, RecipeLine, Staff, StockMovement
from app.schemas.dashboard import RecipeLineIn, RecipeLineUpdateIn
from app.services.catalog import RecipeInvalid, create_variant, ensure_default_variant, set_recipe_line
from app.services.orders import OrderLineSpec, PaymentSpec, RecipeUnitMismatch, create_order, void_order
from app.services.sales import InsufficientStock
from app.services.stock import open_item_stock
from app.services.units import ensure_standard_uoms

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
async def cafe(session_factory):
    """Latte (Regular 22.000 / Large 28.000) made from beans (5 kg) and milk (10 liter).
    Regular: 18 g beans + 120 ml milk. Large: 24 g beans + 180 ml milk."""
    async with session_factory() as s:
        biz = Business(name="Recipe Test", owner_phone=f"62990{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        uoms = await ensure_standard_uoms(s, bid)
        owner = Staff(business_id=bid, name="Bu Ratna", role="owner", pin_hash=hash_pin("1234"))
        staff = Staff(business_id=bid, name="Kasir", pin_hash=hash_pin("1111"))
        latte = Item(business_id=bid, name="Latte", unit="cup", current_stock=Decimal(0), cost_price=Decimal(8000), sell_price=Decimal(22000), uom_id=uoms["cup"].id)
        beans = Item(business_id=bid, name="Biji Arabica", unit="kg", current_stock=Decimal(5), cost_price=Decimal(145000), uom_id=uoms["kg"].id)
        milk = Item(business_id=bid, name="Susu UHT", unit="liter", current_stock=Decimal(10), cost_price=Decimal(17000), uom_id=uoms["liter"].id)
        s.add_all([owner, staff, latte, beans, milk])
        await s.flush()
        for it in (beans, milk):
            await open_item_stock(s, it, unit_cost=it.cost_price)
        regular = await ensure_default_variant(s, latte)
        large = await create_variant(s, latte, name="Large", sell_price=Decimal(28000), cost_price=Decimal(10000))
        await set_recipe_line(s, regular, beans, quantity=Decimal(18), uom_id=uoms["g"].id)
        await set_recipe_line(s, regular, milk, quantity=Decimal(120), uom_id=uoms["ml"].id)
        await set_recipe_line(s, large, beans, quantity=Decimal(24), uom_id=uoms["g"].id)
        await set_recipe_line(s, large, milk, quantity=Decimal(180), uom_id=uoms["ml"].id)
        await s.commit()
        ids = {"bid": bid, "staff": staff.id, "latte": latte.id, "beans": beans.id, "milk": milk.id,
               "regular": regular.id, "large": large.id, "g": uoms["g"].id, "ml": uoms["ml"].id, "pcs": uoms["pcs"].id}
    yield ids
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


async def _gaps(s):
    return (await s.execute(text(
        "select i.name from items i left join stock_movements m on m.item_id = i.id "
        "group by i.id, i.name, i.current_stock having i.current_stock <> coalesce(sum(m.qty_delta), 0)"
    ))).scalars().all()


async def test_selling_one_large_latte_consumes_beans_and_milk(session_factory, cafe):
    c = cafe
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        created = await create_order(
            s, business_id=c["bid"], staff_id=c["staff"],
            lines=[OrderLineSpec(item_id=c["latte"], quantity=Decimal(1), variant_id=c["large"])],
            payments=[PaymentSpec(method="cash", amount=Decimal(28000))],
        )
        await s.commit()
        line_id = created.lines[0].line.id
        assert created.lines[0].remaining_stock == Decimal(0)  # the latte itself has no stock and needs none

    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        beans, milk, latte = await s.get(Item, c["beans"]), await s.get(Item, c["milk"]), await s.get(Item, c["latte"])
        assert beans.current_stock == Decimal("4.976")   # 5 kg − 24 g
        assert milk.current_stock == Decimal("9.820")    # 10 l − 180 ml
        assert latte.current_stock == Decimal("0.000")   # untouched, no negative
        moves = (await s.execute(select(StockMovement).where(StockMovement.source_id == line_id))).scalars().all()
        assert sorted((m.item_id, m.qty_delta, m.reason, m.unit_cost) for m in moves) == sorted([
            (c["beans"], Decimal("-0.024"), "sale", Decimal("145000.00")),
            (c["milk"], Decimal("-0.180"), "sale", Decimal("17000.00")),
        ])
        assert await _gaps(s) == []  # M2-T3 still holds


async def test_regular_uses_less_and_quantity_scales(session_factory, cafe):
    c = cafe
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        await create_order(
            s, business_id=c["bid"], staff_id=c["staff"],
            lines=[OrderLineSpec(item_id=c["latte"], quantity=Decimal(3))],  # default = Regular
            payments=[PaymentSpec(method="qris", amount=Decimal(66000))],
        )
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert (await s.get(Item, c["beans"])).current_stock == Decimal("4.946")  # 5 − 3 × 18 g
        assert (await s.get(Item, c["milk"])).current_stock == Decimal("9.640")   # 10 − 3 × 120 ml
        assert await _gaps(s) == []


async def test_out_of_beans_rejects_the_order_naming_the_component(session_factory, cafe):
    c = cafe
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        beans = await s.get(Item, c["beans"])
        beans.current_stock = Decimal("0.020")
        from app.services.stock import record_movement
        await record_movement(s, business_id=c["bid"], item_id=beans.id, qty_delta=Decimal("-4.980"), reason="waste", source_type="test")
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        with pytest.raises(InsufficientStock) as exc:
            await create_order(
                s, business_id=c["bid"], staff_id=c["staff"],
                lines=[OrderLineSpec(item_id=c["latte"], quantity=Decimal(1), variant_id=c["large"])],
                payments=[PaymentSpec(method="cash", amount=Decimal(28000))],
            )
        assert exc.value.item_name == "Biji Arabica"
        await s.rollback()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert (await s.get(Item, c["milk"])).current_stock == Decimal("10.000")  # milk untouched: all-or-nothing
        assert await _gaps(s) == []


async def test_void_puts_the_components_back(session_factory, cafe):
    c = cafe
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        created = await create_order(
            s, business_id=c["bid"], staff_id=c["staff"],
            lines=[OrderLineSpec(item_id=c["latte"], quantity=Decimal(2), variant_id=c["large"])],
            payments=[PaymentSpec(method="cash", amount=Decimal(56000))],
        )
        await s.commit()
        order_id = created.order.id
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        rev = await void_order(s, business_id=c["bid"], order_id=order_id, staff_id=c["staff"], manager_pin="1234")
        await s.commit()
        assert rev.restocked == {c["beans"]: Decimal("5.000"), c["milk"]: Decimal("10.000")}
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        voids = (await s.execute(select(StockMovement).where(StockMovement.reason == "sale_void"))).scalars().all()
        assert sorted((m.item_id, m.qty_delta) for m in voids) == sorted([(c["beans"], Decimal("0.048")), (c["milk"], Decimal("0.360"))])
        assert await _gaps(s) == []


async def test_recipe_in_a_unit_the_component_lacks_is_refused(session_factory, cafe):
    c = cafe
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        cup = Item(business_id=c["bid"], name="Gelas plastik", unit="pcs", current_stock=Decimal(100))  # no uom_id
        s.add(cup)
        await s.flush()
        await open_item_stock(s, cup)
        from app.models import ItemVariant
        regular = await s.get(ItemVariant, c["regular"])
        await set_recipe_line(s, regular, cup, quantity=Decimal(1), uom_id=c["pcs"])  # unit given, item has none
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        with pytest.raises(RecipeUnitMismatch) as exc:
            await create_order(
                s, business_id=c["bid"], staff_id=c["staff"],
                lines=[OrderLineSpec(item_id=c["latte"], quantity=Decimal(1))],
                payments=[PaymentSpec(method="cash", amount=Decimal(22000))],
            )
        assert exc.value.component_name == "Gelas plastik"
        await s.rollback()


async def test_recipe_rules_and_owner_endpoints(session_factory, cafe):
    c = cafe
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        from app.models import ItemVariant
        regular = await s.get(ItemVariant, c["regular"])
        latte = await s.get(Item, c["latte"])
        with pytest.raises(RecipeInvalid) as exc:
            await set_recipe_line(s, regular, latte, quantity=Decimal(1))
        assert exc.value.code == "self"
        ctx = SimpleNamespace(session=s, business_id=c["bid"], staff_id=None)
        lines = await get_recipe(c["large"], ctx)
        assert [(l.component_name, l.quantity, l.uom_code) for l in lines] == [("Biji Arabica", Decimal("24.000"), "g"), ("Susu UHT", Decimal("180.000"), "ml")]
        # Upsert: the same component again updates in place, no duplicate.
        updated = await put_recipe_line(c["large"], RecipeLineIn(component_item_id=c["beans"], quantity=Decimal(26), uom_id=c["g"]), ctx)
        assert updated.quantity == Decimal("26.000")
        assert len(await get_recipe(c["large"], ctx)) == 2
        with pytest.raises(HTTPException) as exc:
            await put_recipe_line(c["regular"], RecipeLineIn(component_item_id=c["latte"], quantity=Decimal(1)), ctx)
        assert exc.value.status_code == 422
        # Deactivate milk on Large: a Large now consumes beans only.
        milk_line = next(l for l in await get_recipe(c["large"], ctx) if l.component_item_id == c["milk"])
        await edit_recipe_line(milk_line.id, RecipeLineUpdateIn(is_active=False), ctx)
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        await create_order(
            s, business_id=c["bid"], staff_id=c["staff"],
            lines=[OrderLineSpec(item_id=c["latte"], quantity=Decimal(1), variant_id=c["large"])],
            payments=[PaymentSpec(method="cash", amount=Decimal(28000))],
        )
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert (await s.get(Item, c["beans"])).current_stock == Decimal("4.974")  # 26 g
        assert (await s.get(Item, c["milk"])).current_stock == Decimal("10.000")
        ctx = SimpleNamespace(session=s, business_id=c["bid"], staff_id=c["staff"])
        items = await pos_items(ctx)
        by_name = {i.name: i for i in items}
        assert by_name["Latte"].made_to_order is True and by_name["Biji Arabica"].made_to_order is False
