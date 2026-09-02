"""The stock ledger write helper (roadmap M2-T2).

Every change to `items.current_stock`, wherever it originates, writes exactly one
`stock_movements` row through `record_movement` **in the same transaction** — the
caller's session, flushed but never committed here. `items.current_stock` stays
the concurrency guard (the atomic conditional UPDATE in services/sales.py and the
`check (current_stock >= 0)` constraint); the movement row is written alongside
it, never instead of it. SUM(qty_delta) per item must always equal
`items.current_stock` (the M2-T3 invariant).

Reasons (enum `stock_movement_reason`):
  sale / sale_void / refund   selling and its reversals
  purchase                    goods coming in (receipt photo, goods receipt)
  waste                       stock thrown away
  production_in / _out        recipe output / inputs (M4)
  opname                      an absolute count replacing the running figure
                              (stock book photo, Excel import, dashboard edit)
  correction                  an owner-declared fix ("stok kopi sebenarnya 12")

`unit_cost` is the cost per unit at the moment of the movement when the caller
knows it (a purchase price, a sale's cost snapshot). Callers that do not know it
pass None. Never a guess.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Item, StockMovement

MONEY = Decimal("0.01")


def moving_average(on_hand: Decimal, old_cost: Decimal, qty_in: Decimal, unit_cost: Decimal) -> Decimal:
    """Weighted average cost after receiving `qty_in` at `unit_cost` on top of
    `on_hand` units carried at `old_cost`. Stock at or below zero (or an item
    that never had a cost) simply takes the new price. Exact Decimal, rounded
    half-up to rupiah cents."""
    on_hand = max(Decimal(on_hand), Decimal(0))
    if on_hand == 0 or old_cost <= 0:
        return Decimal(unit_cost).quantize(MONEY, rounding=ROUND_HALF_UP)
    total = on_hand * Decimal(old_cost) + Decimal(qty_in) * Decimal(unit_cost)
    return (total / (on_hand + Decimal(qty_in))).quantize(MONEY, rounding=ROUND_HALF_UP)


async def record_movement(
    session: AsyncSession,
    *,
    business_id: uuid.UUID,
    item_id: uuid.UUID,
    qty_delta: Decimal,
    reason: str,
    source_type: str | None = None,
    source_id: uuid.UUID | None = None,
    unit_cost: Decimal | None = None,
    staff_id: uuid.UUID | None = None,
    created_at: datetime | None = None,
) -> StockMovement:
    """Append one ledger row. A zero delta is still recorded when the caller
    asks for it (an opname that confirms the count is real history), so this
    never silently drops a call."""
    movement = StockMovement(
        business_id=business_id,
        item_id=item_id,
        qty_delta=Decimal(qty_delta),
        reason=reason,
        source_type=source_type,
        source_id=source_id,
        unit_cost=None if unit_cost is None else Decimal(unit_cost),
        staff_id=staff_id,
    )
    if created_at is not None:
        movement.created_at = created_at
    session.add(movement)
    await session.flush()
    return movement


async def set_absolute_stock(
    session: AsyncSession,
    item: Item,
    new_qty: Decimal,
    *,
    reason: str,
    source_type: str | None = None,
    source_id: uuid.UUID | None = None,
    unit_cost: Decimal | None = None,
    staff_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> StockMovement:
    """Replace an item's running figure with a counted one and ledger the
    difference. Used by every 'set the stock to X' path (opname, correction)."""
    old = Decimal(item.current_stock)
    new_qty = Decimal(new_qty)
    item.current_stock = new_qty
    if now is not None:
        item.updated_at = now
    return await record_movement(
        session,
        business_id=item.business_id,
        item_id=item.id,
        qty_delta=new_qty - old,
        reason=reason,
        source_type=source_type,
        source_id=source_id,
        unit_cost=unit_cost,
        staff_id=staff_id,
    )


async def add_stock(
    session: AsyncSession,
    item: Item,
    qty: Decimal,
    *,
    reason: str = "purchase",
    source_type: str | None = None,
    source_id: uuid.UUID | None = None,
    unit_cost: Decimal | None = None,
    staff_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> StockMovement:
    """Goods in: increase the running figure and ledger the same positive delta.

    A purchase with a known unit cost also recomputes the item's moving-average
    cost (roadmap M4-T5): the new average weights what was on hand at the old
    average against what came in at the new price. `items.cost_price` is
    therefore "today's cost"; every sale snapshots it onto its line, and
    historical margin never reads it again."""
    qty = Decimal(qty)
    on_hand = Decimal(item.current_stock)
    repriced = reason == "purchase" and unit_cost is not None and qty > 0
    if repriced:
        item.cost_price = moving_average(on_hand, Decimal(item.cost_price), qty, Decimal(unit_cost))
    item.current_stock = on_hand + qty
    if now is not None:
        item.updated_at = now
    if repriced:
        # The default variant is what a sale prices its cost from (M4-T1);
        # keep it in step here so every purchase path gets it for free.
        from app.services.catalog import sync_default_from_item

        await sync_default_from_item(session, item)
    return await record_movement(
        session,
        business_id=item.business_id,
        item_id=item.id,
        qty_delta=qty,
        reason=reason,
        source_type=source_type,
        source_id=source_id,
        unit_cost=unit_cost,
        staff_id=staff_id,
    )


async def open_item_stock(
    session: AsyncSession,
    item: Item,
    *,
    reason: str = "opname",
    source_type: str | None = None,
    source_id: uuid.UUID | None = None,
    unit_cost: Decimal | None = None,
    staff_id: uuid.UUID | None = None,
) -> StockMovement | None:
    """A brand-new item created with an opening quantity: ledger that opening
    balance so the item's history starts at its first known count. Returns None
    when the opening quantity is zero (nothing moved)."""
    opening = Decimal(item.current_stock or 0)
    if opening == 0:
        return None
    return await record_movement(
        session,
        business_id=item.business_id,
        item_id=item.id,
        qty_delta=opening,
        reason=reason,
        source_type=source_type,
        source_id=source_id,
        unit_cost=unit_cost,
        staff_id=staff_id,
    )
