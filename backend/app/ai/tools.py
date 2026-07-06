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
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

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
