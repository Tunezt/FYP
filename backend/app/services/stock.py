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
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Item, StockMovement


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
    """Goods in: increase the running figure and ledger the same positive delta."""
    qty = Decimal(qty)
    item.current_stock = Decimal(item.current_stock) + qty
    if now is not None:
        item.updated_at = now
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
