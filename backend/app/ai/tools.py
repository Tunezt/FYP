"""Fixed tool set for the WhatsApp assistant (no free-form text-to-SQL, ever).

Each tool = a Gemini FunctionDeclaration + an async executor
`(session, business, args) -> dict`. The executor result is JSON-safe and goes
straight to the response composer. The registry below is the single source of
truth for what the assistant can do — adding a capability means adding a tool
here, nothing else.

The declared function name doubles as the classified intent recorded in
request_logs.classified_intent (with 'search_history' logged as the RAG path
and 'clarify' as the fallback).

Since M9-T2 the reading tools compute nothing themselves: every figure comes
from the metric registry (app/metrics), the same implementation the dashboard
reads, so the assistant and the dashboard cannot disagree. A static test keeps
this file free of its own sums.
"""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Awaitable, Callable

from google.genai import types
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.periods import PERIODS
from app.metrics import compute
from app.models import Business, Item

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
    stock = await compute(session, business, "stock_on_hand")   # every item, from the registry
    rows = stock.rows
    if name_query:
        wanted = {i.id for i in await _match_items(session, name_query)}
        rows = [r for r in rows if r["item_id"] in wanted]
        if not rows:
            return {
                "found": False,
                "query": name_query,
                "known_items": [r["name"] for r in stock.rows][:25],
            }
    return {
        "found": True,
        "items": [
            {
                "name": r["name"],
                "current_stock": r["stock"],
                "unit": r["unit"],
                "reorder_threshold": r["reorder_threshold"],
                "below_reorder_threshold": r["below_reorder_threshold"],
            }
            for r in rows
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
    """Revenue, order count, units and top items for a named period — each one
    a registry metric (M9-T2). `transactions` counts orders (voids excluded),
    which is what the word means; a one-line order reads exactly as before."""
    period = period if period in PERIODS else "today"
    revenue = await compute(session, business, "revenue", period=period)
    orders = await compute(session, business, "transaction_count", period=period)
    units = await compute(session, business, "item_units_sold", period=period)
    top = await compute(session, business, "top_items_by_revenue", period=period, limit=5)
    return {
        "period": period,
        "period_label": revenue.period_label,
        "revenue": _num(revenue.value),
        "transactions": int(orders.value or 0),
        "units_sold": _num(units.value),
        "top_items": [
            {"name": r["name"], "quantity": r["quantity"], "revenue": r["revenue"]} for r in top.rows
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
    period = period if period in PERIODS else "today"
    # COGS is the registry's: the cost snapshotted on each line at sale time
    # (M4-T5), with pre-order lines falling back to today's cost and counted so
    # the caller knows how much is estimated.
    revenue = await compute(session, business, "revenue", period=period)
    cogs = await compute(session, business, "cogs", period=period)
    expenses = await compute(session, business, "expense_total", period=period)
    revenue_f = _num(revenue.value) or 0.0
    expenses_f = _num(expenses.value) or 0.0
    return {
        "period": period,
        "period_label": revenue.period_label,
        "revenue": revenue_f,
        "cost_of_goods_estimate": _num(cogs.value),
        "cost_of_goods_lines_without_snapshot": int((cogs.rows[0]["lines_without_snapshot"]) if cogs.rows else 0),
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
    # Owner-declared fix over WhatsApp: a 'correction' ledger row in the same
    # transaction (M2-T2); no cost is known for a correction.
    from app.services.stock import set_absolute_stock

    await set_absolute_stock(
        session, item, new_qty, reason="correction", source_type="whatsapp",
        now=datetime.now(timezone.utc),
    )
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
    # The registry's reorder list (one grouped query, the locked velocity
    # formula); this tool only decides what "at risk" means: below the reorder
    # threshold, or three days or less of stock at the current rate.
    reading = await compute(session, business, "stock_days_remaining")
    risky = []
    for r in sorted(reading.rows, key=lambda x: x["name"]):
        low_days = r["days_remaining"] is not None and r["days_remaining"] <= 3
        if r["below_reorder_threshold"] or low_days:
            risky.append(
                {
                    "name": r["name"],
                    "current_stock": r["stock"],
                    "unit": r["unit"],
                    "days_remaining": r["days_remaining"],
                    "below_reorder_threshold": r["below_reorder_threshold"],
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
    """Through the one expense writer (M6-T6): the row and its journal entry
    land in this transaction, so the expense is on the P&L the moment the
    owner's message is handled."""
    from app.services.expenses import ExpenseInvalid, record_expense as write_expense

    amount = Decimal(str(args["amount"]))
    try:
        expense = await write_expense(
            session, business.id, amount=amount, category=args.get("category"),
            description=args.get("description"), source="manual",
        )
    except ExpenseInvalid:
        return {"ok": False, "error": "invalid_amount"}
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


# ── M9-T4: the tool set grows, over the registry ─────────────────────────────
#
# Seven more fixed signatures. Every figure below is a registry metric; the
# tools resolve names the owner typed (a supplier, an item, a customer) into
# ids, call `compute`, and shape the answer. Still no free-form SQL.


async def _match_supplier(session: AsyncSession, name_query: str):
    from app.models import Supplier

    pattern = f"%{name_query.strip()}%"
    rows = (await session.execute(select(Supplier).where(Supplier.name.ilike(pattern), Supplier.is_active.is_(True)).order_by(Supplier.name))).scalars().all()
    return list(rows)


async def _supplier_names(session: AsyncSession) -> list[str]:
    from app.models import Supplier

    return list((await session.execute(select(Supplier.name).where(Supplier.is_active.is_(True)).order_by(Supplier.name))).scalars().all())


def _period(args: dict, default: str) -> str:
    period = args.get("period") or default
    return period if period in PERIODS else default


@tool(
    types.FunctionDeclaration(
        name="get_purchase_history",
        description=(
            "Goods received from suppliers in a period, optionally one supplier: dates, "
            "values, line counts. Use for 'belanja ke supplier X berapa', 'riwayat pembelian', "
            "'kapan terakhir kirim'."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "supplier_name": types.Schema(type=types.Type.STRING, description="Supplier as the owner named it. Omit for all suppliers."),
                "period": _PERIOD_PARAM,
            },
        ),
    )
)
async def get_purchase_history(session: AsyncSession, business: Business, args: dict) -> dict:
    period = _period(args, "last_30_days")
    name_query = (args.get("supplier_name") or "").strip()
    supplier_id = None
    if name_query:
        matches = await _match_supplier(session, name_query)
        if not matches:
            return {"found": False, "query": name_query, "known_suppliers": (await _supplier_names(session))[:25]}
        supplier_id = matches[0].id
    result = await compute(session, business, "purchase_history", period=period, supplier_id=supplier_id, limit=20)
    return {
        "found": True, "period": period, "period_label": result.period_label,
        "supplier": matches[0].name if name_query else None,
        "total_received": _num(result.value),
        "receipts": [
            {"number": r["number"], "received_at": r["received_at"].isoformat(), "supplier": r["supplier"],
             "total": r["total"], "lines": r["lines"]}
            for r in result.rows
        ],
    }


@tool(
    types.FunctionDeclaration(
        name="get_supplier_prices",
        description=(
            "Last purchase price per item per supplier, with the previous price and the change. "
            "Use for 'harga gula di supplier mana paling murah', 'harga beli terakhir', 'naik ga harganya'."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "item_name": types.Schema(type=types.Type.STRING, description="Item to look at. Omit for every item."),
                "supplier_name": types.Schema(type=types.Type.STRING, description="Supplier to look at. Omit for every supplier."),
            },
        ),
    )
)
async def get_supplier_prices(session: AsyncSession, business: Business, args: dict) -> dict:
    item_query = (args.get("item_name") or "").strip()
    supplier_query = (args.get("supplier_name") or "").strip()
    item_id = supplier_id = None
    if item_query:
        items = await _match_items(session, item_query)
        if not items:
            return {"found": False, "query": item_query, "known_items": [i.name for i in await _match_items(session, "")][:25]}
        item_id = items[0].id
    if supplier_query:
        suppliers = await _match_supplier(session, supplier_query)
        if not suppliers:
            return {"found": False, "query": supplier_query, "known_suppliers": (await _supplier_names(session))[:25]}
        supplier_id = suppliers[0].id
    result = await compute(session, business, "supplier_prices", item_id=item_id, supplier_id=supplier_id)
    return {
        "found": True,
        "prices": [
            {"item": r["item"], "unit": r["unit"], "supplier": r["supplier"], "last_price": r["last_price"],
             "last_bought_at": r["last_bought_at"].isoformat(), "previous_price": r["previous_price"], "change_pct": r["change_pct"]}
            for r in result.rows
        ],
    }


@tool(
    types.FunctionDeclaration(
        name="get_recipe_cost",
        description=(
            "Ingredient cost of one unit of a menu item from its recipe, at current component "
            "costs, with the selling price and margin. Use for 'modal es kopi susu berapa', "
            "'HPP per porsi', 'untung per gelas'."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={"item_name": types.Schema(type=types.Type.STRING)},
            required=["item_name"],
        ),
    )
)
async def get_recipe_cost(session: AsyncSession, business: Business, args: dict) -> dict:
    name_query = (args.get("item_name") or "").strip()
    items = await _match_items(session, name_query) if name_query else []
    if not items:
        return {"found": False, "query": name_query, "known_items": [i.name for i in await _match_items(session, "")][:25]}
    item = items[0]
    result = await compute(session, business, "recipe_cost", item_id=item.id)
    return {
        "found": True, "item": item.name, "cost_per_unit": _num(result.value), "note": result.note,
        "components": [
            {"component": r["component"], "quantity": r["quantity"], "unit": r["unit"], "unit_cost": r["unit_cost"], "cost": r["cost"]}
            for r in result.rows
        ],
    }


@tool(
    types.FunctionDeclaration(
        name="get_shift_summary",
        description=(
            "Till shifts closed in a period: who, float, cash taken, expected, counted, variance. "
            "Use for 'kas kemarin kurang ga', 'shift siapa yang selisih', 'tutup kasir'."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={"period": _PERIOD_PARAM},
            required=["period"],
        ),
    )
)
async def get_shift_summary(session: AsyncSession, business: Business, args: dict) -> dict:
    period = _period(args, "today")
    result = await compute(session, business, "shift_summary", period=period, limit=20)
    return {
        "period": period, "period_label": result.period_label, "total_variance": _num(result.value),
        "shifts": [
            {"staff": r["staff"], "closed_at": r["closed_at"].isoformat() if r["closed_at"] else None,
             "opening_float": r["opening_float"], "cash_sales": r["cash_sales"], "cash_in": r["cash_in"], "cash_out": r["cash_out"],
             "expected_cash": r["expected_cash"], "counted_cash": r["counted_cash"], "variance": r["variance"], "notes": r["notes"]}
            for r in result.rows
        ],
    }


@tool(
    types.FunctionDeclaration(
        name="get_customer_summary",
        description=(
            "One customer's visits, spend, last visit and points — by name or phone — or the top "
            "customers of a period when no name is given. Use for 'si Andi udah belanja berapa', "
            "'pelanggan paling sering', 'poin bu Rina'."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "customer": types.Schema(type=types.Type.STRING, description="Name or phone number as the owner typed it. Omit for the top customers."),
                "period": _PERIOD_PARAM,
            },
        ),
    )
)
async def get_customer_summary(session: AsyncSession, business: Business, args: dict) -> dict:
    from app.services.customers import search_customers

    period = _period(args, "this_month")
    query = (args.get("customer") or "").strip()
    customer_id = None
    if query:
        matches = await search_customers(session, query, limit=5)
        if not matches:
            return {"found": False, "query": query}
        customer_id = matches[0].id
    result = await compute(session, business, "customer_summary", period=period, customer_id=customer_id, limit=5)
    return {
        "found": True, "period": period, "period_label": result.period_label,
        "customers": [
            {"name": r["name"], "phone": r["phone"], "visits": r["visits"], "total_spent": _num(r["total_spent"]),
             "last_visit": r["last_visit"].isoformat() if r["last_visit"] else None, "points_balance": r["points_balance"],
             "period_visits": r["period_visits"], "period_spend": r["period_spend"]}
            for r in result.rows
        ],
    }


@tool(
    types.FunctionDeclaration(
        name="get_promo_performance",
        description=(
            "How each promo did in a period: times applied, cost, and the revenue of the orders it "
            "applied to. Use for 'promo BOGO laku ga', 'promo mana yang jalan', 'biaya promo bulan ini'."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={"period": _PERIOD_PARAM},
            required=["period"],
        ),
    )
)
async def get_promo_performance(session: AsyncSession, business: Business, args: dict) -> dict:
    period = _period(args, "this_month")
    result = await compute(session, business, "promo_performance", period=period, limit=20)
    return {
        "period": period, "period_label": result.period_label, "total_cost": _num(result.value),
        "promos": [
            {"name": r["name"], "kind": r["kind"], "is_active": r["is_active"], "applications": r["applications"],
             "orders": r["orders"], "given_away": r["given_away"], "order_revenue": r["order_revenue"]}
            for r in result.rows
        ],
    }


@tool(
    types.FunctionDeclaration(
        name="draft_purchase_order",
        description=(
            "Draft a purchase order to a supplier for the items and quantities the owner states "
            "('pesan ke Toko Jaya: gula 10 kg, kopi 5 kg'). Prices default to the last price paid to "
            "that supplier. It is a DRAFT the owner confirms on the dashboard — nothing is sent."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "supplier_name": types.Schema(type=types.Type.STRING),
                "lines": types.Schema(
                    type=types.Type.ARRAY,
                    items=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "item_name": types.Schema(type=types.Type.STRING),
                            "quantity": types.Schema(type=types.Type.NUMBER, description="In the item's own unit."),
                        },
                        required=["item_name", "quantity"],
                    ),
                ),
            },
            required=["supplier_name", "lines"],
        ),
    )
)
async def draft_purchase_order(session: AsyncSession, business: Business, args: dict) -> dict:
    from app.services.purchasing import PoLineSpec, PurchaseOrderInvalid, create_purchase_order, lines_of

    supplier_query = (args.get("supplier_name") or "").strip()
    suppliers = await _match_supplier(session, supplier_query) if supplier_query else []
    if not suppliers:
        return {"drafted": False, "reason": "supplier_not_found", "query": supplier_query,
                "known_suppliers": (await _supplier_names(session))[:25]}
    supplier = suppliers[0]
    prices = {r["item_id"]: r["last_price"] for r in (await compute(session, business, "supplier_prices", supplier_id=supplier.id)).rows}
    specs, unknown, resolved = [], [], []
    for raw in args.get("lines") or []:
        name = (raw.get("item_name") or "").strip()
        try:
            qty = Decimal(str(raw.get("quantity") or 0))
        except Exception:
            qty = Decimal(0)
        matches = await _match_items(session, name) if name else []
        if not matches or qty <= 0:
            unknown.append(name or "?")
            continue
        item = matches[0]
        unit_cost = Decimal(str(prices.get(item.id, 0) or 0))
        specs.append(PoLineSpec(item_id=item.id, quantity=qty, unit_cost=unit_cost))
        resolved.append({"item": item.name, "quantity": _num(qty), "unit": item.unit, "unit_cost": _num(unit_cost),
                         "price_known": item.id in prices})
    if not specs:
        return {"drafted": False, "reason": "no_valid_lines", "unknown_items": unknown,
                "known_items": [i.name for i in await _match_items(session, "")][:25]}
    try:
        po = await create_purchase_order(session, business.id, supplier_id=supplier.id, lines=specs,
                                         notes="dibuat dari asisten WhatsApp")
    except PurchaseOrderInvalid as exc:
        return {"drafted": False, "reason": exc.code}
    lines = await lines_of(session, po.id)
    return {
        "drafted": True, "po_number": po.number, "status": po.status, "supplier": supplier.name,
        "subtotal": _num(sum((Decimal(l.line_total) for l in lines), Decimal(0))),
        "lines": resolved, "unknown_items": unknown,
        "note": "Draft — konfirmasi dan kirim dari dashboard (Stok › Pesanan pembelian).",
    }
