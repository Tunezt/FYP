"""Units of measure and conversion (roadmap M4-T3).

Stock in kg, consume in g: a quantity expressed in one unit is converted to the
item's stock unit before it touches `items.current_stock` or the ledger, and
rounded to the ledger's precision (numeric(12,3): 1 g of a kg-stocked item is
0.001). Conversions are per business (`uom_conversions`); a direct row or its
reverse is used, nothing is guessed.
"""
from __future__ import annotations

import uuid
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Item, Uom, UomConversion
from app.services.sales import ATOMIC_DECREMENT, InsufficientStock
from app.services.stock import record_movement

QTY_PLACES = Decimal("0.001")

# Mirrors migration 0009 for businesses created after it.
STANDARD_UOMS: list[tuple[str, str]] = [
    ("kg", "kilogram"), ("g", "gram"), ("liter", "liter"), ("ml", "mililiter"),
    ("pcs", "pcs"), ("cup", "cup"), ("porsi", "porsi"), ("botol", "botol"), ("bungkus", "bungkus"),
    ("dus", "dus"), ("kaleng", "kaleng"), ("pouch", "pouch"), ("sachet", "sachet"), ("tray", "tray"),
    ("ikat", "ikat"), ("pack", "pack"),
]
STANDARD_CONVERSIONS: list[tuple[str, str, Decimal]] = [
    ("kg", "g", Decimal(1000)), ("g", "kg", Decimal("0.001")),
    ("liter", "ml", Decimal(1000)), ("ml", "liter", Decimal("0.001")),
]
_UNIT_ALIASES = {"l": "liter", "ltr": "liter", "gram": "g", "gr": "g", "kilogram": "kg", "mililiter": "ml"}


class UnitConversionMissing(Exception):
    def __init__(self, from_code: str, to_code: str):
        self.from_code, self.to_code = from_code, to_code
        super().__init__(f"no conversion {from_code} -> {to_code}")


class ItemHasNoUom(Exception):
    pass


class UomInvalid(Exception):
    """`code`: code, duplicate, same, factor."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


async def ensure_standard_uoms(session: AsyncSession, business_id: uuid.UUID) -> dict[str, Uom]:
    """Idempotent: create any standard unit/conversion the business lacks.
    Returns {code: Uom}."""
    existing = {u.code.lower(): u for u in (await session.execute(select(Uom))).scalars()}
    for code, name in STANDARD_UOMS:
        if code not in existing:
            u = Uom(business_id=business_id, code=code, name=name)
            session.add(u)
            existing[code] = u
    await session.flush()
    pairs = {
        (c.from_uom_id, c.to_uom_id)
        for c in (await session.execute(select(UomConversion))).scalars()
    }
    for from_code, to_code, factor in STANDARD_CONVERSIONS:
        f, t = existing[from_code], existing[to_code]
        if (f.id, t.id) not in pairs:
            session.add(UomConversion(business_id=business_id, from_uom_id=f.id, to_uom_id=t.id, factor=factor))
    await session.flush()
    return existing


async def uom_by_code(session: AsyncSession, code: str) -> Uom | None:
    normalised = _UNIT_ALIASES.get(code.strip().lower(), code.strip().lower())
    return (
        await session.execute(select(Uom).where(func.lower(Uom.code) == normalised))
    ).scalar_one_or_none()


async def create_uom(session: AsyncSession, business_id: uuid.UUID, *, code: str, name: str | None = None) -> Uom:
    code = (code or "").strip()
    if not code:
        raise UomInvalid("code")
    if await uom_by_code(session, code) is not None:
        raise UomInvalid("duplicate")
    u = Uom(business_id=business_id, code=code, name=(name or code).strip())
    session.add(u)
    await session.flush()
    return u


async def create_conversion(
    session: AsyncSession, business_id: uuid.UUID, *, from_uom: Uom, to_uom: Uom, factor: Decimal, both_ways: bool = True
) -> UomConversion:
    if from_uom.id == to_uom.id:
        raise UomInvalid("same")
    factor = Decimal(factor)
    if factor <= 0:
        raise UomInvalid("factor")
    existing = (
        await session.execute(
            select(UomConversion).where(UomConversion.from_uom_id == from_uom.id, UomConversion.to_uom_id == to_uom.id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise UomInvalid("duplicate")
    conv = UomConversion(business_id=business_id, from_uom_id=from_uom.id, to_uom_id=to_uom.id, factor=factor)
    session.add(conv)
    if both_ways:
        reverse = (
            await session.execute(
                select(UomConversion).where(UomConversion.from_uom_id == to_uom.id, UomConversion.to_uom_id == from_uom.id)
            )
        ).scalar_one_or_none()
        if reverse is None:
            session.add(UomConversion(business_id=business_id, from_uom_id=to_uom.id, to_uom_id=from_uom.id,
                                      factor=(Decimal(1) / factor)))
    await session.flush()
    return conv


async def convert_quantity(session: AsyncSession, quantity: Decimal, from_uom_id: uuid.UUID, to_uom_id: uuid.UUID) -> Decimal:
    """Exact Decimal conversion, NOT yet rounded to ledger precision."""
    quantity = Decimal(quantity)
    if from_uom_id == to_uom_id:
        return quantity
    direct = (
        await session.execute(
            select(UomConversion.factor).where(UomConversion.from_uom_id == from_uom_id, UomConversion.to_uom_id == to_uom_id)
        )
    ).scalar_one_or_none()
    if direct is not None:
        return quantity * Decimal(direct)
    reverse = (
        await session.execute(
            select(UomConversion.factor).where(UomConversion.from_uom_id == to_uom_id, UomConversion.to_uom_id == from_uom_id)
        )
    ).scalar_one_or_none()
    if reverse is not None:
        return quantity / Decimal(reverse)
    f = await session.get(Uom, from_uom_id)
    t = await session.get(Uom, to_uom_id)
    raise UnitConversionMissing(f.code if f else "?", t.code if t else "?")


def to_ledger_precision(quantity: Decimal) -> Decimal:
    return Decimal(quantity).quantize(QTY_PLACES, rounding=ROUND_HALF_UP)


async def consume_stock(
    session: AsyncSession,
    item: Item,
    quantity: Decimal,
    uom_id: uuid.UUID | None = None,
    *,
    reason: str = "production_out",
    source_type: str | None = None,
    source_id: uuid.UUID | None = None,
    unit_cost: Decimal | None = None,
    staff_id: uuid.UUID | None = None,
) -> Decimal:
    """Take `quantity` (in `uom_id`, or the item's own unit when None) out of
    the item's stock, converting across the unit boundary, guarded by the same
    atomic conditional UPDATE as a sale, with its ledger row. Returns the
    quantity removed in the item's unit."""
    if uom_id is not None and uom_id != item.uom_id:
        if item.uom_id is None:
            raise ItemHasNoUom()
        qty_item_unit = to_ledger_precision(await convert_quantity(session, quantity, uom_id, item.uom_id))
    else:
        qty_item_unit = to_ledger_precision(quantity)
    if qty_item_unit <= 0:
        return Decimal(0)
    remaining = (await session.execute(ATOMIC_DECREMENT, {"qty": qty_item_unit, "item_id": item.id})).scalar_one_or_none()
    if remaining is None:
        raise InsufficientStock(item.name, item.current_stock)
    movement = await record_movement(
        session, business_id=item.business_id, item_id=item.id, qty_delta=-qty_item_unit, reason=reason,
        source_type=source_type, source_id=source_id, unit_cost=unit_cost, staff_id=staff_id,
    )
    item.current_stock = Decimal(remaining)
    if reason == "waste":
        # Thrown away: expense at the item's average cost (StockWasted, M6-T4).
        from app.services.posting import post_event

        value = (qty_item_unit * Decimal(unit_cost if unit_cost is not None else item.cost_price or 0)).quantize(Decimal("0.01"))
        await post_event(
            session, item.business_id, "StockWasted", {"waste": value},
            source_type="stock_movement", source_id=movement.id,
            memo=f"barang rusak {item.name}: {qty_item_unit} {item.unit}", created_by=staff_id,
        )
    return qty_item_unit
