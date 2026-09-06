"""M9-T3 — the thesis test.

The assistant and the dashboard become physically incapable of disagreeing:
the WhatsApp answer to "berapa penjualan hari ini" and the dashboard's
today-revenue tile come from the same registry call, so for the same period
they are the identical figure. This file asserts that end to end — the router
dispatching a tool, the dashboard endpoint shaping a widget, and the metric
endpoint in between — on a day with a multi-line bill and a void, where two
independent implementations would have been most likely to drift.

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import ast
import os
import pathlib
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai.router import handle_text
from app.api.dashboard import inventory, metric_value, overview, pnl, sales_trend
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
DASHBOARD_FILE = pathlib.Path(__file__).resolve().parents[1] / "app" / "api" / "dashboard.py"


def test_the_dashboards_number_endpoints_do_no_arithmetic_of_their_own():
    """overview, sales_trend, pnl and inventory may shape a widget; they may
    not sum, count or read the sales view. Alerts are a count of rows, not a
    business figure, and stay."""
    tree = ast.parse(DASHBOARD_FILE.read_text(encoding="utf-8"))
    guarded = {"overview", "sales_trend", "pnl", "inventory"}
    seen = set()
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name in guarded:
            seen.add(node.name)
            for sub in ast.walk(node):
                if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name) and sub.value.id in {"Sale", "Expense"}:
                    offenders.append(f"{node.name}: {sub.value.id}.{sub.attr} line {sub.lineno}")
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and isinstance(sub.func.value, ast.Name) \
                        and sub.func.value.id == "func" and sub.func.attr in {"sum", "avg"}:
                    offenders.append(f"{node.name}: func.{sub.func.attr} line {sub.lineno}")
    assert seen == guarded, seen
    assert offenders == [], offenders


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
    """Today: 2 Kopi (20.000, cost 8.000); a two-line bill 1 Roti (15.000) + 1 Kopi; one voided Kopi; expense 12.000.
    Yesterday: 1 Roti. Roti's threshold is 40 with 28 left."""
    async with session_factory() as s:
        biz = Business(name="Thesis", owner_phone=f"62966{uuid.uuid4().hex[:9]}")
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

        await sell([OrderLineSpec(item_id=roti.id, quantity=D(1))], 15000, now - timedelta(days=1))
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


def _forced_call(name: str, args: dict):
    return AsyncMock(return_value=SimpleNamespace(name=name, args=args))


async def test_whatsapp_answer_and_dashboard_widget_are_the_identical_figure(session_factory, shop):
    """THE THESIS. 'berapa penjualan hari ini' → the router runs get_sales_summary;
    the dashboard renders /api/overview; the metric endpoint sits between. All
    three say the same rupiah, the same order count, for the same period."""
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        biz = await s.get(Business, c["bid"])
        composer = AsyncMock(return_value="Penjualan hari ini Rp 75.000 dari 2 transaksi.")
        with (
            patch("app.ai.router.force_tool_call", new=_forced_call("get_sales_summary", {"period": "today"})),
            patch("app.ai.router.compose_reply", new=composer),
        ):
            routed = await handle_text(s, biz, "berapa penjualan hari ini?")
        assert routed.intent == "get_sales_summary"
        # What the assistant was given to narrate — the tool's facts.
        _biz, _text, intent, facts = composer.await_args.args
        assert intent == "get_sales_summary"

        ctx = SimpleNamespace(session=s, business_id=c["bid"], staff_id=None)
        widget = await overview(ctx)
        metric = await metric_value("revenue", ctx, period="today", since=None, until=None, item_id=None, limit=5)

        # The identical figure, three ways.
        assert facts["revenue"] == widget.today_revenue == metric.value == 75000.0
        assert facts["transactions"] == widget.today_transactions == 2
        assert D(str(facts["revenue"])) == (await compute(s, biz, "revenue", period="today")).value

        # And the day bar on the sales chart is that same number.
        trend = await sales_trend(ctx, days=7)
        assert trend[-1].revenue == 75000.0 and trend[-1].transactions == 2
        assert trend[-2].revenue == 15000.0 and trend[-2].transactions == 1   # yesterday
        assert sum(pt.revenue for pt in trend) == 90000.0


async def test_profit_tool_and_money_page_agree_and_stock_matches_the_registry(session_factory, shop):
    from app.ai.tools import get_low_stock, get_profit, get_stock

    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        biz = await s.get(Business, c["bid"])
        ctx = SimpleNamespace(session=s, business_id=c["bid"], staff_id=None)
        profit = await get_profit(s, biz, {"period": "this_month"})
        widget = await overview(ctx)
        months = await pnl(ctx, months=1)
        # Revenue this month: the assistant, the overview tile and the P&L chart's current month.
        assert profit["revenue"] == widget.month_revenue == months[-1].revenue
        assert profit["recorded_expenses"] == widget.month_expenses == months[-1].expenses == 12000.0
        assert profit["net_after_expenses"] == widget.month_net == months[-1].net
        # Stock: the inventory page and the assistant's stock tools read the same rows.
        page = await inventory(ctx)
        by_name = {i.name: i for i in page}
        stock = await get_stock(s, biz, {})
        for row in stock["items"]:
            assert float(by_name[row["name"]].current_stock) == row["current_stock"]
            assert by_name[row["name"]].below_reorder_threshold == row["below_reorder_threshold"]
        low = await get_low_stock(s, biz, {})
        assert [i["name"] for i in low["at_risk_items"]] == [i.name for i in page if i.below_reorder_threshold] == ["Roti Bakar"]
        assert widget.low_stock_items == 1
        assert by_name["Roti Bakar"].days_remaining == low["at_risk_items"][0]["days_remaining"]
        registry = {r["name"]: r for r in (await compute(s, biz, "stock_days_remaining")).rows}
        assert by_name["Kopi Arabica"].days_remaining == registry["Kopi Arabica"]["days_remaining"]
        assert by_name["Kopi Arabica"].avg_daily_usage == registry["Kopi Arabica"]["daily_usage"]
