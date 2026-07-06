"""Fixed tool set for the WhatsApp assistant (no free-form text-to-SQL, ever).

Each tool = a Gemini FunctionDeclaration + an async executor
`(session, business, args) -> dict`. The executor result is JSON-safe and goes
straight to the response composer. The registry below is the single source of
truth for what the assistant can do — adding a capability means adding a tool
here, nothing else.

The declared function name doubles as the classified intent recorded in
request_logs.classified_intent (with 'search_history' logged as the RAG path
and 'clarify' as the fallback).
"""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Awaitable, Callable

from google.genai import types
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.periods import PERIODS, period_range
from app.models import Business, Expense, Item, Sale
from app.services.velocity import VELOCITY_WINDOW_DAYS

ToolExecutor = Callable[[AsyncSession, Business, dict], Awaitable[dict]]

TOOL_DECLARATIONS: list[types.FunctionDeclaration] = []
TOOL_EXECUTORS: dict[str, ToolExecutor] = {}


def tool(declaration: types.FunctionDeclaration):
    def register(fn: ToolExecutor) -> ToolExecutor:
        TOOL_DECLARATIONS.append(declaration)
        TOOL_EXECUTORS[declaration.name] = fn
        return fn

    return register


def _num(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


async def _match_items(session: AsyncSession, name_query: str) -> list[Item]:
    """Case-insensitive substring match; tolerant of partial names the owner
    types ('arabica' matches 'Kopi Arabica')."""
    pattern = f"%{name_query.strip()}%"
    rows = (
        await session.execute(select(Item).where(Item.name.ilike(pattern)).order_by(Item.name))
    ).scalars()
    return list(rows)


# ── get_stock ────────────────────────────────────────────────────────────────


@tool(
    types.FunctionDeclaration(
        name="get_stock",
        description=(
            "Look up current stock level(s). Use when the owner asks how much of "
            "an item is left / stok / baki stok. Omit item_name to list all items."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "item_name": types.Schema(
                    type=types.Type.STRING,
                    description="Item to look up, as the owner named it (any language). Omit for all items.",
                )
            },
        ),
    )
)
async def get_stock(session: AsyncSession, business: Business, args: dict) -> dict:
    name_query = (args.get("item_name") or "").strip()
    if name_query:
        items = await _match_items(session, name_query)
        if not items:
            all_names = (
                (await session.execute(select(Item.name).order_by(Item.name))).scalars().all()
            )
            return {
                "found": False,
                "query": name_query,
                "known_items": all_names[:25],
            }
    else:
        items = list(
            (await session.execute(select(Item).order_by(Item.name))).scalars().all()
        )

    return {
        "found": True,
        "items": [
            {
                "name": i.name,
                "current_stock": _num(i.current_stock),
                "unit": i.unit,
                "reorder_threshold": _num(i.reorder_threshold),
                "below_reorder_threshold": i.current_stock <= i.reorder_threshold,
            }
            for i in items
        ],
    }


# ── sales / money tools ──────────────────────────────────────────────────────

_PERIOD_PARAM = types.Schema(
    type=types.Type.STRING,
    enum=PERIODS,
    description="Time period, resolved in the business's timezone.",
)


async def _sales_facts(
    session: AsyncSession, business: Business, period: str
) -> dict:
    start, end, label = period_range(period, business.timezone)
    revenue, tx_count, qty_sum = (
        await session.execute(
            select(
                func.coalesce(func.sum(Sale.total_price), 0),
                func.count(Sale.id),
                func.coalesce(func.sum(Sale.quantity), 0),
            ).where(Sale.sold_at >= start, Sale.sold_at < end)
        )
    ).one()
    top = (
        await session.execute(
            select(
                Item.name,
                func.sum(Sale.quantity).label("qty"),
                func.sum(Sale.total_price).label("amount"),
            )
            .join(Item, Item.id == Sale.item_id)
            .where(Sale.sold_at >= start, Sale.sold_at < end)
            .group_by(Item.name)
            .order_by(desc("amount"))
            .limit(5)
        )
    ).all()
    return {
        "period": period,
        "period_label": label,
        "revenue": _num(revenue),
        "transactions": int(tx_count),
        "units_sold": _num(qty_sum),
        "top_items": [
            {"name": name, "quantity": _num(q), "revenue": _num(a)} for name, q, a in top
        ],
    }


@tool(
    types.FunctionDeclaration(
        name="get_sales_summary",
        description=(
            "Sales totals for a period: revenue, transaction count, top items. "
            "Use for 'berapa penjualan hari ini', 'jualan minggu ini', 'sales today'."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={"period": _PERIOD_PARAM},
            required=["period"],
        ),
    )
)
async def get_sales_summary(session: AsyncSession, business: Business, args: dict) -> dict:
    return await _sales_facts(session, business, args.get("period", "today"))


@tool(
    types.FunctionDeclaration(
        name="compare_periods",
        description=(
            "Compare sales between two periods (e.g. this week vs last week, "
            "'lebih rame mana minggu ini sama minggu lalu?')."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={"period_a": _PERIOD_PARAM, "period_b": _PERIOD_PARAM},
            required=["period_a", "period_b"],
        ),
    )
)
async def compare_periods(session: AsyncSession, business: Business, args: dict) -> dict:
    a = await _sales_facts(session, business, args.get("period_a", "this_week"))
    b = await _sales_facts(session, business, args.get("period_b", "last_week"))
    delta = (a["revenue"] or 0) - (b["revenue"] or 0)
    pct = round(delta / b["revenue"] * 100, 1) if b["revenue"] else None
    return {"period_a": a, "period_b": b, "revenue_delta": delta, "revenue_delta_pct": pct}


@tool(
    types.FunctionDeclaration(
        name="get_profit",
        description=(
            "Profit & loss for a period: revenue, cost of goods (from item cost "
            "prices), recorded expenses, net. Use for 'untung ga bulan ini?', "
            "'am I making money', 'rugi atau untung'."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={"period": _PERIOD_PARAM},
            required=["period"],
        ),
    )
)
async def get_profit(session: AsyncSession, business: Business, args: dict) -> dict:
    period = args.get("period", "this_month")
    start, end, label = period_range(period, business.timezone)
    revenue, cogs = (
        await session.execute(
            select(
                func.coalesce(func.sum(Sale.total_price), 0),
                func.coalesce(func.sum(Sale.quantity * Item.cost_price), 0),
            )
            .join(Item, Item.id == Sale.item_id)
            .where(Sale.sold_at >= start, Sale.sold_at < end)
        )
    ).one()
    expenses = (
        await session.execute(
            select(func.coalesce(func.sum(Expense.amount), 0)).where(
                Expense.occurred_at >= start, Expense.occurred_at < end
            )
        )
    ).scalar_one()
    revenue_f = _num(revenue) or 0.0
    expenses_f = _num(expenses) or 0.0
    return {
        "period": period,
        "period_label": label,
        "revenue": revenue_f,
        "cost_of_goods_estimate": _num(cogs),
        "recorded_expenses": expenses_f,
        "net_after_expenses": round(revenue_f - expenses_f, 2),
        "note": "net = revenue minus recorded expenses; COGS shown separately (expenses may already include ingredient purchases)",
    }


# ── stock management tools ───────────────────────────────────────────────────


@tool(
    types.FunctionDeclaration(
        name="correct_stock",
        description=(
            "Set an item's stock to a new absolute value after a physical count "
            "('stok gula ternyata 5 kg', 'update arabica jadi 12'). Only for "
            "corrections the owner states explicitly — never guess a value."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "item_name": types.Schema(type=types.Type.STRING),
                "new_quantity": types.Schema(
                    type=types.Type.NUMBER, description="The corrected absolute stock level."
                ),
            },
            required=["item_name", "new_quantity"],
        ),
    )
)
async def correct_stock(session: AsyncSession, business: Business, args: dict) -> dict:
    new_qty = Decimal(str(args["new_quantity"]))
    if new_qty < 0:
        return {"ok": False, "error": "negative_quantity"}
    matches = await _match_items(session, args.get("item_name", ""))
    if not matches:
        all_names = (
            (await session.execute(select(Item.name).order_by(Item.name))).scalars().all()
        )
        return {"ok": False, "error": "item_not_found", "known_items": all_names[:25]}
    if len(matches) > 1:
        return {
            "ok": False,
            "error": "ambiguous_item",
            "candidates": [m.name for m in matches],
        }
    item = matches[0]
    old = item.current_stock
    item.current_stock = new_qty
    item.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return {
        "ok": True,
        "item": item.name,
        "unit": item.unit,
        "old_stock": _num(old),
        "new_stock": _num(new_qty),
    }


@tool(
    types.FunctionDeclaration(
        name="get_low_stock",
        description=(
            "List items at risk of running out (below reorder threshold or few "
            "days remaining at current sales pace). Use for 'apa yang mau habis', "
            "'what should I restock'."
        ),
        parameters=types.Schema(type=types.Type.OBJECT, properties={}),
    )
)
async def get_low_stock(session: AsyncSession, business: Business, args: dict) -> dict:
    items = (
        (await session.execute(select(Item).order_by(Item.name))).scalars().all()
    )
    # Velocity for all items from ONE grouped query (mirrors /api/items) —
    # a per-item loop here would be an N+1 on the WhatsApp hot path.
    since = datetime.now(timezone.utc) - timedelta(days=VELOCITY_WINDOW_DAYS)
    usage_rows = (
        await session.execute(
            select(Sale.item_id, func.sum(Sale.quantity))
            .where(Sale.sold_at >= since)
            .group_by(Sale.item_id)
        )
    ).all()
    usage = {item_id: Decimal(qty) for item_id, qty in usage_rows}

    risky = []
    for item in items:
        daily = usage.get(item.id, Decimal(0)) / VELOCITY_WINDOW_DAYS
        days_remaining = (
            (item.current_stock / daily).quantize(Decimal("0.1")) if daily > 0 else None
        )
        below_threshold = item.current_stock <= item.reorder_threshold
        low_days = days_remaining is not None and days_remaining <= 3
        if below_threshold or low_days:
            risky.append(
                {
                    "name": item.name,
                    "current_stock": _num(item.current_stock),
                    "unit": item.unit,
                    "days_remaining": _num(days_remaining),
                    "below_reorder_threshold": below_threshold,
                }
            )
    return {"at_risk_items": risky, "all_clear": not risky}


@tool(
    types.FunctionDeclaration(
        name="record_expense",
        description=(
            "Record a business expense the owner states in chat ('tadi beli gas "
            "88 ribu', 'bayar listrik 850rb'). Amount in rupiah as a number."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "amount": types.Schema(type=types.Type.NUMBER, description="Amount in rupiah."),
                "category": types.Schema(
                    type=types.Type.STRING,
                    description="One of: bahan baku, operasional, gaji, sewa, lainnya",
                ),
                "description": types.Schema(type=types.Type.STRING),
            },
            required=["amount", "description"],
        ),
    )
)
async def record_expense(session: AsyncSession, business: Business, args: dict) -> dict:
    amount = Decimal(str(args["amount"]))
    if amount <= 0:
        return {"ok": False, "error": "invalid_amount"}
    expense = Expense(
        business_id=business.id,
        amount=amount,
        category=args.get("category") or "lainnya",
        description=args.get("description"),
        source="manual",
        occurred_at=datetime.now(timezone.utc),
    )
    session.add(expense)
    await session.flush()
    return {
        "ok": True,
        "amount": _num(amount),
        "category": expense.category,
        "description": expense.description,
    }


# ── RAG path (Phase 4) ───────────────────────────────────────────────────────


@tool(
    types.FunctionDeclaration(
        name="search_history",
        description=(
            "Search past purchase receipts / nota history when the answer lives "
            "in unstructured records rather than current numbers: 'pernah beli "
            "gula dari supplier ini?', 'kapan terakhir beli susu', 'have I "
            "bought X before', 'biasanya beli arabica di mana'."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "query": types.Schema(
                    type=types.Type.STRING,
                    description="The owner's question, rephrased as a search query if helpful.",
                )
            },
            required=["query"],
        ),
    )
)
async def search_history(session: AsyncSession, business: Business, args: dict) -> dict:
    from app.services.rag import search_receipts

    documents = await search_receipts(session, business.id, args.get("query", ""))
    if not documents:
        return {
            "matches": [],
            "note": "No matching receipts in history — either it was never bought via a recorded nota, or receipts haven't been photographed yet.",
        }
    return {
        "matches": [
            {
                "content": d.page_content,
                "supplier": d.metadata.get("supplier"),
                "date": d.metadata.get("occurred_at"),
                "total_amount": d.metadata.get("total_amount"),
            }
            for d in documents
        ]
    }
