"""M4-T3 — units and conversion: stock in kg, consume in g.

Done-when (roadmap): consuming 250 g from 5 kg leaves 4.750, proven by a test
crossing the unit boundary, with numeric(12,3) precision respected.

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Business, Item, StockMovement, Uom, UomConversion
from app.services.sales import InsufficientStock
from app.services.stock import open_item_stock
from app.services.units import (
    ItemHasNoUom,
    UnitConversionMissing,
    UomInvalid,
    consume_stock,
    convert_quantity,
    create_conversion,
    create_uom,
    ensure_standard_uoms,
    uom_by_code,
)

from tests.conftest import seed_books

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
async def pantry(session_factory):
    """A new business (created after migration 0009, so it gets its units from
    ensure_standard_uoms) with 5 kg of coffee beans."""
    async with session_factory() as s:
        biz = Business(name="Unit Test", owner_phone=f"62991{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await seed_books(s, bid)
        uoms = await ensure_standard_uoms(s, bid)
        beans = Item(business_id=bid, name="Biji Arabica", unit="kg", current_stock=Decimal(5),
                     cost_price=Decimal(145000), uom_id=uoms["kg"].id)
        s.add(beans)
        await s.flush()
        await open_item_stock(s, beans, unit_cost=beans.cost_price)
        await s.commit()
        ids = {"bid": bid, "beans": beans.id, "kg": uoms["kg"].id, "g": uoms["g"].id, "ml": uoms["ml"].id, "pcs": uoms["pcs"].id}
    yield ids
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


async def test_consuming_250g_from_5kg_leaves_4_750(session_factory, pantry):
    p = pantry
    async with session_factory() as s:
        await _set_tenant(s, p["bid"])
        beans = await s.get(Item, p["beans"])
        removed = await consume_stock(s, beans, Decimal(250), p["g"], reason="production_out", source_type="test")
        await s.commit()
        assert removed == Decimal("0.250")
    async with session_factory() as s:
        await _set_tenant(s, p["bid"])
        beans = await s.get(Item, p["beans"])
        assert beans.current_stock == Decimal("4.750")
        rows = (await s.execute(select(StockMovement).where(StockMovement.item_id == p["beans"]).order_by(StockMovement.created_at, StockMovement.id))).scalars().all()
        assert [(r.reason, r.qty_delta) for r in rows] == [("opname", Decimal("5.000")), ("production_out", Decimal("-0.250"))]
        # Ledger and cache agree (M2-T3 invariant holds across the unit boundary).
        assert sum(r.qty_delta for r in rows) == beans.current_stock


async def test_one_gram_is_representable_and_half_grams_round(session_factory, pantry):
    p = pantry
    async with session_factory() as s:
        await _set_tenant(s, p["bid"])
        beans = await s.get(Item, p["beans"])
        assert await consume_stock(s, beans, Decimal(1), p["g"]) == Decimal("0.001")
        # 0.4 g rounds to 0.000 kg → nothing moves, no row written.
        assert await consume_stock(s, beans, Decimal("0.4"), p["g"]) == Decimal(0)
        # 1.5 g → 0.0015 kg → 0.002 (half up).
        assert await consume_stock(s, beans, Decimal("1.5"), p["g"]) == Decimal("0.002")
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, p["bid"])
        assert (await s.get(Item, p["beans"])).current_stock == Decimal("4.997")
        n = (await s.execute(select(StockMovement).where(StockMovement.item_id == p["beans"]))).scalars().all()
        assert len(n) == 3  # opening + two real consumptions, none for the 0.4 g


async def test_conversion_both_directions_and_missing(session_factory, pantry):
    p = pantry
    async with session_factory() as s:
        await _set_tenant(s, p["bid"])
        assert await convert_quantity(s, Decimal("2.5"), p["kg"], p["g"]) == Decimal("2500")
        assert await convert_quantity(s, Decimal(750), p["g"], p["kg"]) == Decimal("0.75")
        assert await convert_quantity(s, Decimal(3), p["kg"], p["kg"]) == Decimal(3)
        with pytest.raises(UnitConversionMissing) as exc:
            await convert_quantity(s, Decimal(1), p["ml"], p["kg"])
        assert (exc.value.from_code, exc.value.to_code) == ("ml", "kg")


async def test_consume_in_wrong_unit_is_refused_not_guessed(session_factory, pantry):
    p = pantry
    async with session_factory() as s:
        await _set_tenant(s, p["bid"])
        beans = await s.get(Item, p["beans"])
        with pytest.raises(UnitConversionMissing):
            await consume_stock(s, beans, Decimal(100), p["ml"])
        await s.rollback()
    async with session_factory() as s:
        await _set_tenant(s, p["bid"])
        assert (await s.get(Item, p["beans"])).current_stock == Decimal("5.000")


async def test_item_without_uom_cannot_be_consumed_in_another_unit(session_factory, pantry):
    p = pantry
    async with session_factory() as s:
        await _set_tenant(s, p["bid"])
        loose = Item(business_id=p["bid"], name="Sedotan", unit="pcs", current_stock=Decimal(100))
        s.add(loose)
        await s.flush()
        await open_item_stock(s, loose)
        with pytest.raises(ItemHasNoUom):
            await consume_stock(s, loose, Decimal(1), p["g"])
        # Consuming in its own (unspecified) unit still works.
        assert await consume_stock(s, loose, Decimal(10)) == Decimal("10.000")
        await s.commit()


async def test_consume_respects_the_atomic_guard(session_factory, pantry):
    p = pantry
    async with session_factory() as s:
        await _set_tenant(s, p["bid"])
        beans = await s.get(Item, p["beans"])
        with pytest.raises(InsufficientStock):
            await consume_stock(s, beans, Decimal(6000), p["g"])  # 6 kg from 5
        await s.rollback()


async def test_custom_uom_and_conversion(session_factory, pantry):
    """A business adds 'karung' (sack) = 25 kg; consuming 1 karung takes 25 kg,
    and 500 g still works through the standard pair."""
    p = pantry
    async with session_factory() as s:
        await _set_tenant(s, p["bid"])
        karung = await create_uom(s, p["bid"], code="karung", name="karung 25 kg")
        kg = await s.get(Uom, p["kg"])
        await create_conversion(s, p["bid"], from_uom=karung, to_uom=kg, factor=Decimal(25))
        with pytest.raises(UomInvalid) as exc:
            await create_uom(s, p["bid"], code="KARUNG")
        assert exc.value.code == "duplicate"
        with pytest.raises(UomInvalid):
            await create_conversion(s, p["bid"], from_uom=kg, to_uom=kg, factor=Decimal(1))
        assert (await uom_by_code(s, "Kilogram")).id == p["kg"]
        beans = await s.get(Item, p["beans"])
        beans.current_stock = Decimal(30)  # top up for the test (opname row below keeps the ledger honest)
        from app.services.stock import record_movement
        await record_movement(s, business_id=p["bid"], item_id=beans.id, qty_delta=Decimal(25), reason="purchase", source_type="test")
        assert await consume_stock(s, beans, Decimal(1), karung.id) == Decimal("25.000")
        assert await consume_stock(s, beans, Decimal(500), p["g"]) == Decimal("0.500")
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, p["bid"])
        assert (await s.get(Item, p["beans"])).current_stock == Decimal("4.500")
        # reverse conversion was created automatically: 1 kg = 0.04 karung
        rev = (await s.execute(select(UomConversion.factor).where(UomConversion.from_uom_id == p["kg"]).where(UomConversion.to_uom_id != p["g"]))).scalars().all()
        assert Decimal("0.04") in [Decimal(f) for f in rev]


async def test_ensure_standard_uoms_is_idempotent(session_factory, pantry):
    p = pantry
    async with session_factory() as s:
        await _set_tenant(s, p["bid"])
        before = (await s.execute(select(Uom))).scalars().all()
        await ensure_standard_uoms(s, p["bid"])
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, p["bid"])
        after = (await s.execute(select(Uom))).scalars().all()
        assert len(after) == len(before) == 16
        assert len((await s.execute(select(UomConversion))).scalars().all()) == 4
