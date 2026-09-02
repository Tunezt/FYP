"""Excel stock-template import (onboarding Scenario 1) — deterministic parsing
with openpyxl/pandas, deliberately a separate code path from the vision
pipeline. Quantities are ABSOLUTE (it's an opening-stock sheet): matching items
are set, unknown items are created.

Header matching is forgiving (Indonesian/Malay/English synonyms, any casing)
because owners will edit the template or export from whatever they already use.
"""
import io
import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Business, Item
from app.services.catalog import ensure_default_variant, sync_default_from_item
from app.services.stock import open_item_stock, set_absolute_stock

logger = logging.getLogger("stock_import")

HEADER_SYNONYMS = {
    "name": {"nama", "nama barang", "nama item", "item", "barang", "produk", "name", "product"},
    "quantity": {"jumlah", "stok", "stock", "qty", "quantity", "banyak", "jml", "kuantiti"},
    "unit": {"satuan", "unit", "uom"},
    "cost_price": {"harga modal", "modal", "cost", "harga beli", "cost price", "kos"},
    "sell_price": {"harga jual", "jual", "price", "sell price", "harga", "sale price"},
    "reorder_threshold": {"batas", "minimum", "min", "reorder", "batas minimum", "min stok"},
}


class StockTemplateError(Exception):
    """Raised when the file can't be understood at all (wrong file, no headers)."""


def _map_headers(columns: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for col in columns:
        normalized = str(col).strip().lower()
        for field, synonyms in HEADER_SYNONYMS.items():
            if normalized in synonyms and field not in mapping:
                mapping[field] = col
    return mapping


def _dec(value, default: str = "0") -> Decimal:
    if pd.isna(value):
        return Decimal(default)
    try:
        return Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError):
        return Decimal(default)


def parse_stock_template(data: bytes) -> tuple[list[dict], list[str]]:
    """Returns (rows, warnings). Pure — no DB. Raises StockTemplateError when
    the sheet has no recognizable name+quantity columns."""
    try:
        df = pd.read_excel(io.BytesIO(data))
    except Exception as exc:
        raise StockTemplateError(f"unreadable_file: {exc}") from exc

    mapping = _map_headers(list(df.columns))
    if "name" not in mapping or "quantity" not in mapping:
        raise StockTemplateError("missing_columns: need at least a name and quantity column")

    rows: list[dict] = []
    warnings: list[str] = []
    for idx, record in df.iterrows():
        name = str(record[mapping["name"]]).strip() if not pd.isna(record[mapping["name"]]) else ""
        if not name or name.lower() == "nan":
            continue
        qty = _dec(record[mapping["quantity"]])
        if qty < 0:
            warnings.append(f"row {idx + 2}: negative quantity for '{name}' skipped")
            continue
        rows.append(
            {
                "name": name,
                "quantity": qty,
                "unit": (
                    str(record[mapping["unit"]]).strip()
                    if "unit" in mapping and not pd.isna(record[mapping["unit"]])
                    else "pcs"
                ),
                "cost_price": _dec(record[mapping["cost_price"]]) if "cost_price" in mapping else Decimal(0),
                "sell_price": _dec(record[mapping["sell_price"]]) if "sell_price" in mapping else Decimal(0),
                "reorder_threshold": (
                    _dec(record[mapping["reorder_threshold"]])
                    if "reorder_threshold" in mapping
                    else Decimal(0)
                ),
            }
        )
    if not rows:
        raise StockTemplateError("no_rows: no usable item rows found")
    return rows, warnings


async def apply_stock_template(
    session: AsyncSession, business: Business, rows: list[dict]
) -> dict:
    created, updated = [], []
    for row in rows:
        existing = (
            await session.execute(
                select(Item).where(Item.name.ilike(row["name"])).limit(1)
            )
        ).scalar_one_or_none()
        # An Excel template is a stock count: opname rows in the same
        # transaction (M2-T2). Cost is the template's cost price when given.
        known_cost = row["cost_price"] if row["cost_price"] > 0 else None
        if existing is None:
            item = Item(
                business_id=business.id,
                name=row["name"],
                unit=row["unit"],
                current_stock=row["quantity"],
                cost_price=row["cost_price"],
                sell_price=row["sell_price"],
                reorder_threshold=row["reorder_threshold"],
            )
            session.add(item)
            await session.flush()
            await open_item_stock(
                session, item, reason="opname", source_type="stock_import", unit_cost=known_cost
            )
            await ensure_default_variant(session, item)  # M4-T1
            created.append(row["name"])
        else:
            await set_absolute_stock(
                session, existing, row["quantity"], reason="opname",
                source_type="stock_import", unit_cost=known_cost, now=datetime.now(timezone.utc),
            )
            if row["cost_price"] > 0:
                existing.cost_price = row["cost_price"]
            if row["sell_price"] > 0:
                existing.sell_price = row["sell_price"]
            if row["reorder_threshold"] > 0:
                existing.reorder_threshold = row["reorder_threshold"]
            if row["cost_price"] > 0 or row["sell_price"] > 0:
                await sync_default_from_item(session, existing)  # M4-T1
            updated.append(existing.name)
    await session.flush()
    return {
        "saved": True,
        "items_created": created,
        "items_updated": updated,
        "total_rows": len(rows),
    }
