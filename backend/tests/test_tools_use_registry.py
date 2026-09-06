"""M9-T2 — the assistant's reading tools stop computing and start calling the
metric registry. Done when behaviour is unchanged and the existing tests pass;
this file adds the proof that the tools now *are* the registry.

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import ast
import os
import pathlib
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai.tools import TOOL_EXECUTORS, compare_periods, get_low_stock, get_profit, get_sales_summary, get_stock
from app.core.security import hash_pin
from app.metrics import compute
from app.models import Business, Item, Staff
from app.services.catalog import ensure_default_variant
from app.services.expenses import record_expense
from app.services.orders import OrderLineSpec, PaymentSpec, create_order, void_order
from app.services.pricing import ensure_pricing_settings
from app.services.stock import open_item_stock

from tests.conftest import seed_books

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")
D = Decimal
TOOLS_FILE = pathlib.Path(__file__).resolve().parents[1] / "app" / "ai" / "tools.py"


def test_the_tools_file_contains_no_arithmetic_of_its_own():
    """Nothing else in the app computes these numbers (roadmap M9-T1). The
    reading tools may match names and shape output; they may not sum, count
    or read the sales view themselves."""
    src = TOOLS_FILE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden_calls = {"sum", "count", "avg", "min", "max"}   # func.sum(...) etc.
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "func" and node.func.attr in forbidden_calls:
                offenders.append(f"func.{node.func.attr} at line {node.lineno}")
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "Sale":
            offenders.append(f"Sale.{node.attr} at line {node.lineno}")
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module == "app.models" for alias in node.names}
    assert "Sale" not in imported and "Expense" not in imported, imported
    assert offenders == [], offenders
    assert "from app.metrics import compute" in src
    # The tool set itself is unchanged: the same eight names.
    assert set(TOOL_EXECUTORS) == {
        "get_stock", "get_sales_summary", "compare_periods", "get_profit", "correct_stock", "get_low_stock",
        "record_expense", "search_history",
    }


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
    """Today: 2 Kopi (20.000, cost 8.000) + a two-line bill (1 Roti 15.000 cost 6.000 + 1 Kopi), one voided Kopi;
    an expense of 12.000; Roti has a reorder threshold of 40 with 29 left."""
    async with session_factory() as s:
        biz = Business(name="Tools Registry", owner_phone=f"62967{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await seed_books(s, bid)
        await ensure_pricing_settings(s, bid)
        owner = Staff(business_id=bid, name="Bu Ratna", role="owner", pin_hash=hash_pin("1234"))
        sari = Staff(business_id=bid, name="Sari", pin_hash=hash_pin("2345"))
        kopi = Item(business_id=bid, name="Kopi Arabica", unit="cup", current_stock=D(30), cost_price=D(8000), sell_price=D(20000), reorder_threshold=D(5))
        roti = Item(business_id=bid, name="Roti Bakar", unit="pcs", current_stock=D(30), cost_price=D(6000), sell_price=D(15000), reorder_threshold=D(40))
        s.add_all([owner, sari, kopi, roti])
        await s.flush()
        for it in (kopi, roti):
            await open_item_stock(s, it, unit_cost=it.cost_price)
            await ensure_default_variant(s, it)
        now = datetime.now(timezone.utc)

        async def sell(lines, paid, when):
            return await create_order(s, business_id=bid, staff_id=sari.id, lines=lines,
                                      payments=[PaymentSpec(method="cash", amount=D(paid))], sold_at=when)

        await sell([OrderLineSpec(item_id=kopi.id, quantity=D(2))], 40000, now - timedelta(minutes=30))
        await sell([OrderLineSpec(item_id=roti.id, quantity=D(1)), OrderLineSpec(item_id=kopi.id, quantity=D(1))], 35000, now - timedelta(minutes=20))
        voided = await sell([OrderLineSpec(item_id=kopi.id, quantity=D(1))], 20000, now - timedelta(minutes=10))
        await void_order(s, business_id=bid, order_id=voided.order.id, staff_id=sari.id, manager_pin="1234")
        await record_expense(s, bid, amount=D(12000), description="es batu", category="operasional", source="manual", occurred_at=now - timedelta(minutes=5))
        await s.commit()
        ids = {"bid": bid, "kopi": kopi.id, "roti": roti.id}
    yield ids
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


async def test_sales_and_profit_tools_return_the_registrys_figures(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        biz = await s.get(Business, c["bid"])
        summary = await get_sales_summary(s, biz, {"period": "today"})
        assert summary["revenue"] == 75000.0                       # 40.000 + 35.000 (+ 20.000 − 20.000)
        assert summary["transactions"] == 2                        # two orders; the two-line bill is one, the void is none
        assert summary["units_sold"] == 4.0                        # 2 + 1 + 1 (+ 1 − 1)
        assert summary["top_items"][0] == {"name": "Kopi Arabica", "quantity": 3.0, "revenue": 60000.0}
        assert summary["period_label"] == "hari ini"
        # The same numbers, straight from the registry.
        assert D(str(summary["revenue"])) == (await compute(s, biz, "revenue", period="today")).value
        assert summary["transactions"] == (await compute(s, biz, "transaction_count", period="today")).value
        profit = await get_profit(s, biz, {"period": "today"})
        assert profit["revenue"] == 75000.0 and profit["cost_of_goods_estimate"] == 30000.0   # 3 × 8.000 + 6.000
        assert profit["cost_of_goods_lines_without_snapshot"] == 0
        assert profit["recorded_expenses"] == 12000.0 and profit["net_after_expenses"] == 63000.0
        assert D(str(profit["cost_of_goods_estimate"])) == (await compute(s, biz, "cogs", period="today")).value
        both = await compare_periods(s, biz, {"period_a": "today", "period_b": "yesterday"})
        assert both["period_a"]["revenue"] == 75000.0 and both["revenue_delta"] == 75000.0 and both["revenue_delta_pct"] is None
        # An unknown period string from the model falls back to today, as before.
        assert (await get_sales_summary(s, biz, {"period": "fortnight"}))["period_label"] == "hari ini"


async def test_stock_tools_return_the_registrys_rows(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        biz = await s.get(Business, c["bid"])
        everything = await get_stock(s, biz, {})
        assert everything["found"] and [(i["name"], i["current_stock"], i["below_reorder_threshold"]) for i in everything["items"]] == [
            ("Kopi Arabica", 27.0, False), ("Roti Bakar", 29.0, True),
        ]
        one = await get_stock(s, biz, {"item_name": "arabica"})
        assert one["found"] and [i["name"] for i in one["items"]] == ["Kopi Arabica"] and one["items"][0]["reorder_threshold"] == 5.0
        missing = await get_stock(s, biz, {"item_name": "durian"})
        assert missing == {"found": False, "query": "durian", "known_items": ["Kopi Arabica", "Roti Bakar"]}
        registry_rows = (await compute(s, biz, "stock_on_hand")).rows
        assert [r["stock"] for r in registry_rows] == [27.0, 29.0]
        low = await get_low_stock(s, biz, {})
        assert not low["all_clear"] and [i["name"] for i in low["at_risk_items"]] == ["Roti Bakar"]   # below threshold
        roti = low["at_risk_items"][0]
        assert roti["below_reorder_threshold"] is True and roti["current_stock"] == 29.0
        days = (await compute(s, biz, "stock_days_remaining", item_id=c["roti"])).value
        assert roti["days_remaining"] == (float(days) if days is not None else None)
