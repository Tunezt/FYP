"""M9-T4 — the tool set grows over the registry: purchase history, supplier
prices, recipe cost, shift summary, customer summary, promo performance, and a
drafted purchase order. Fixed signatures, every figure a metric, no SQL of
their own (the static guard in test_tools_use_registry covers the new tools
too because they live in the same file).

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import os
import uuid
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai.tools import (
    draft_purchase_order, get_customer_summary, get_promo_performance, get_purchase_history, get_recipe_cost,
    get_shift_summary, get_supplier_prices,
)
from app.core.security import hash_pin
from app.metrics import compute
from app.models import Business, Item, PoLine, PurchaseOrder, Staff
from app.services.catalog import ensure_default_variant, set_recipe_line
from app.services.customers import create_customer
from app.services.orders import OrderLineSpec, PaymentSpec, create_order
from app.services.pricing import ensure_pricing_settings
from app.services.promos import create_promo
from app.services.receiving import GrLineSpec, receive_goods
from app.services.shifts import close_shift, open_shift
from app.services.stock import open_item_stock
from app.services.suppliers import create_supplier
from app.services.units import ensure_standard_uoms

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
    """Toko Jaya delivered gula twice (12.000 then 13.500 per kg) and kopi once; Es Kopi Susu is
    20 g kopi (cost 150/g → 3.000) + 150 ml susu (cost 20/ml → 3.000), sells 22.000; Andi bought twice
    today (one BOGO applied); Sari's shift closed 2.000 over."""
    async with session_factory() as s:
        biz = Business(name="New Tools", owner_phone=f"62965{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await seed_books(s, bid)
        await ensure_pricing_settings(s, bid)
        uoms = await ensure_standard_uoms(s, bid)
        owner = Staff(business_id=bid, name="Bu Ratna", role="owner", pin_hash=hash_pin("1234"))
        sari = Staff(business_id=bid, name="Sari", pin_hash=hash_pin("2345"))
        gula = Item(business_id=bid, name="Gula", unit="kg", current_stock=D(10), cost_price=D(12000), sell_price=D(0), uom_id=uoms["kg"].id)
        kopi = Item(business_id=bid, name="Kopi bubuk", unit="g", current_stock=D(1000), cost_price=D(150), sell_price=D(0), uom_id=uoms["g"].id)
        susu = Item(business_id=bid, name="Susu", unit="ml", current_stock=D(5000), cost_price=D(20), sell_price=D(0), uom_id=uoms["ml"].id)
        eskopi = Item(business_id=bid, name="Es Kopi Susu", unit="cup", current_stock=D(0), cost_price=D(0), sell_price=D(22000))
        s.add_all([owner, sari, gula, kopi, susu, eskopi])
        await s.flush()
        variants = {}
        for it in (gula, kopi, susu, eskopi):
            await open_item_stock(s, it, unit_cost=it.cost_price)
            variants[it.id] = await ensure_default_variant(s, it)
        await set_recipe_line(s, variants[eskopi.id], kopi, quantity=D(20), uom_id=uoms["g"].id)
        await set_recipe_line(s, variants[eskopi.id], susu, quantity=D(150), uom_id=uoms["ml"].id)
        jaya = await create_supplier(s, bid, name="Toko Jaya")
        now = datetime.now(timezone.utc)
        await receive_goods(s, bid, supplier_id=jaya.id, lines=[GrLineSpec(item_id=gula.id, quantity=D(5), unit_cost=D(12000))],
                            received_at=now - timedelta(days=10), received_by=owner.id)
        await receive_goods(s, bid, supplier_id=jaya.id,
                            lines=[GrLineSpec(item_id=gula.id, quantity=D(5), unit_cost=D(13500)), GrLineSpec(item_id=kopi.id, quantity=D(500), unit_cost=D(160))],
                            received_at=now - timedelta(days=2), received_by=owner.id)
        andi = await create_customer(s, bid, name="Andi Wijaya", phone="081200001111")
        await create_promo(s, bid, name="BOGO Es Kopi", kind="bonus_item", item_id=eskopi.id, max_per_order=1)
        shift = await open_shift(s, bid, staff_id=sari.id, opening_float=D(50000), opened_at=now - timedelta(hours=8))
        await create_order(s, business_id=bid, staff_id=sari.id, lines=[OrderLineSpec(item_id=eskopi.id, quantity=D(1))],
                           payments=[PaymentSpec(method="cash", amount=D(22000))], sold_at=now - timedelta(hours=6), customer_id=andi.id)
        await create_order(s, business_id=bid, staff_id=sari.id, lines=[OrderLineSpec(item_id=eskopi.id, quantity=D(2))],
                           payments=[PaymentSpec(method="qris", amount=D(44000))], sold_at=now - timedelta(hours=3), customer_id=andi.id)
        await close_shift(s, shift, counted_cash=D(74000), closed_by=owner.id, closed_at=now - timedelta(hours=1))   # expected 72.000
        await s.commit()
        ids = {"bid": bid, "gula": gula.id, "kopi": kopi.id, "eskopi": eskopi.id, "jaya": jaya.id, "andi": andi.id}
    yield ids
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


async def test_purchase_history_and_supplier_prices(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        biz = await s.get(Business, c["bid"])
        history = await get_purchase_history(s, biz, {"supplier_name": "jaya", "period": "last_30_days"})
        assert history["found"] and history["supplier"] == "Toko Jaya"
        assert [(r["total"], r["lines"]) for r in history["receipts"]] == [(147500.0, 2), (60000.0, 1)]   # newest first
        assert history["total_received"] == 207500.0
        assert history["total_received"] == float((await compute(s, biz, "purchase_history", period="last_30_days")).value)
        missing = await get_purchase_history(s, biz, {"supplier_name": "Bu Tini", "period": "last_30_days"})
        assert missing == {"found": False, "query": "Bu Tini", "known_suppliers": ["Toko Jaya"]}
        prices = await get_supplier_prices(s, biz, {"item_name": "gula"})
        assert prices["found"] and len(prices["prices"]) == 1
        gula = prices["prices"][0]
        assert (gula["supplier"], gula["last_price"], gula["previous_price"], gula["change_pct"]) == ("Toko Jaya", 13500.0, 12000.0, 12.5)
        everything = await get_supplier_prices(s, biz, {})
        assert [(r["item"], r["last_price"], r["previous_price"]) for r in everything["prices"]] == [("Gula", 13500.0, 12000.0), ("Kopi bubuk", 160.0, None)]
        assert (await get_supplier_prices(s, biz, {"item_name": "durian"}))["found"] is False


async def test_recipe_cost_shift_customer_and_promo(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        biz = await s.get(Business, c["bid"])
        # The kopi receipt (500 g at 160 on top of 1.000 g at 150) moved the average to 153,33 (M4-T5),
        # so the recipe costs 20 × 153,33 + 150 × 20 = 6.066,60 — the registry reads today's cost, as it should.
        recipe = await get_recipe_cost(s, biz, {"item_name": "es kopi"})
        assert recipe["found"] and recipe["item"] == "Es Kopi Susu" and recipe["cost_per_unit"] == 6066.6
        assert [(r["component"], r["quantity"], r["unit_cost"], r["cost"]) for r in recipe["components"]] == [
            ("Kopi bubuk", 20.0, 153.33, 3066.6), ("Susu", 150.0, 20.0, 3000.0),
        ]
        assert "margin kotor 72.4%" in recipe["note"]
        plain = await get_recipe_cost(s, biz, {"item_name": "gula"})
        assert plain["cost_per_unit"] == 12375.0 and plain["components"] == [] and "tidak ada resep" in plain["note"]   # moving average after the two receipts

        shifts = await get_shift_summary(s, biz, {"period": "today"})
        assert len(shifts["shifts"]) == 1 and shifts["total_variance"] == 2000.0
        sh = shifts["shifts"][0]
        assert (sh["staff"], sh["opening_float"], sh["cash_sales"], sh["expected_cash"], sh["counted_cash"], sh["variance"]) == \
               ("Sari", 50000.0, 22000.0, 72000.0, 74000.0, 2000.0)

        andi = await get_customer_summary(s, biz, {"customer": "0812 0000 1111", "period": "today"})
        assert andi["found"] and andi["customers"][0]["name"] == "Andi Wijaya"
        assert (andi["customers"][0]["visits"], andi["customers"][0]["total_spent"], andi["customers"][0]["period_visits"]) == (2, 66000.0, 2)
        top = await get_customer_summary(s, biz, {"period": "today"})
        assert [x["name"] for x in top["customers"]] == ["Andi Wijaya"] and top["customers"][0]["period_spend"] == 66000.0
        assert (await get_customer_summary(s, biz, {"customer": "nobody"}))["found"] is False

        promos = await get_promo_performance(s, biz, {"period": "today"})
        assert len(promos["promos"]) == 1
        bogo = promos["promos"][0]
        # Applied on both of Andi's orders (1 free each, capped at 1 per order): 2 applications, 2 × 22.000 given away.
        assert (bogo["name"], bogo["applications"], bogo["orders"], bogo["given_away"], bogo["order_revenue"]) == ("BOGO Es Kopi", 2, 2, 44000.0, 66000.0)
        assert promos["total_cost"] == 44000.0


async def test_draft_purchase_order_uses_last_prices_and_creates_a_draft(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        biz = await s.get(Business, c["bid"])
        out = await draft_purchase_order(s, biz, {"supplier_name": "toko jaya", "lines": [
            {"item_name": "gula", "quantity": 10}, {"item_name": "kopi bubuk", "quantity": 250}, {"item_name": "durian", "quantity": 1},
        ]})
        await s.commit()
        await _set_tenant(s, c["bid"])   # a commit ends the SET LOCAL tenant
        assert out["drafted"] and out["status"] == "draft" and out["supplier"] == "Toko Jaya"
        assert [(l["item"], l["quantity"], l["unit_cost"], l["price_known"]) for l in out["lines"]] == [
            ("Gula", 10.0, 13500.0, True), ("Kopi bubuk", 250.0, 160.0, True),
        ]
        assert out["unknown_items"] == ["durian"] and out["subtotal"] == 175000.0   # 10 × 13.500 + 250 × 160
        po = (await s.execute(select(PurchaseOrder))).scalar_one()
        assert po.number == out["po_number"] and po.status == "draft" and po.subtotal == D("175000.00")
        assert len((await s.execute(select(PoLine).where(PoLine.po_id == po.id))).scalars().all()) == 2
        # Nothing was ordered, received or moved: a draft is a draft.
        assert (await s.get(Item, c["gula"])).current_stock == D("20.000")   # 10 opening + 5 + 5 received
        # No supplier, no draft; no usable line, no draft.
        assert (await draft_purchase_order(s, biz, {"supplier_name": "warung sebelah", "lines": [{"item_name": "gula", "quantity": 1}]}))["drafted"] is False
        none = await draft_purchase_order(s, biz, {"supplier_name": "jaya", "lines": [{"item_name": "durian", "quantity": 1}, {"item_name": "gula", "quantity": 0}]})
        assert none["drafted"] is False and none["reason"] == "no_valid_lines"
        assert (await s.execute(select(PurchaseOrder))).scalars().all().__len__() == 1
