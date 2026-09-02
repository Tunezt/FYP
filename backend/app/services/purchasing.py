"""Purchase orders (roadmap M5-T2).

  draft ──order──▶ ordered ──receive (M5-T3)──▶ partially_received ──▶ received
    │                 │
    └────cancel───────┘   (only while nothing has been received)

Lines can be added/changed/removed while the PO is a draft (a draft is a
worksheet, not history). Once ordered, lines are frozen; receiving advances
`received_quantity`. A PO never touches stock itself.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Item, PoLine, PurchaseOrder, Supplier, Uom

MONEY = Decimal("0.01")
OPEN_STATUSES = ("ordered", "partially_received")


class PurchaseOrderInvalid(Exception):
    """`code`: supplier, supplier_inactive, item, uom, quantity, not_draft, empty,
    not_open, already_received, line."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass
class PoLineSpec:
    item_id: uuid.UUID
    quantity: Decimal
    unit_cost: Decimal = Decimal(0)
    uom_id: uuid.UUID | None = None


async def _next_number(session: AsyncSession) -> int:
    current = (await session.execute(select(func.max(PurchaseOrder.number)))).scalar_one()
    return int(current or 0) + 1


async def _recompute_subtotal(session: AsyncSession, po: PurchaseOrder) -> None:
    total = (await session.execute(select(func.coalesce(func.sum(PoLine.line_total), 0)).where(PoLine.po_id == po.id))).scalar_one()
    po.subtotal = Decimal(total).quantize(MONEY)
    po.updated_at = datetime.now(timezone.utc)
    await session.flush()


async def _validated_line(session: AsyncSession, spec: PoLineSpec) -> tuple[Item, Decimal, Decimal]:
    item = await session.get(Item, spec.item_id)
    if item is None:
        raise PurchaseOrderInvalid("item")
    quantity = Decimal(spec.quantity)
    if quantity <= 0:
        raise PurchaseOrderInvalid("quantity")
    if spec.uom_id is not None and await session.get(Uom, spec.uom_id) is None:
        raise PurchaseOrderInvalid("uom")
    unit_cost = Decimal(spec.unit_cost or 0)
    if unit_cost < 0:
        raise PurchaseOrderInvalid("quantity")
    return item, quantity, unit_cost


async def create_purchase_order(
    session: AsyncSession, business_id: uuid.UUID, *, supplier_id: uuid.UUID, lines: list[PoLineSpec],
    notes: str | None = None, expected_at: date | None = None, created_by: uuid.UUID | None = None,
) -> PurchaseOrder:
    supplier = await session.get(Supplier, supplier_id)
    if supplier is None:
        raise PurchaseOrderInvalid("supplier")
    if not supplier.is_active:
        raise PurchaseOrderInvalid("supplier_inactive")
    po = PurchaseOrder(
        business_id=business_id, supplier_id=supplier_id, status="draft", number=await _next_number(session),
        notes=notes, expected_at=expected_at, created_by=created_by,
    )
    session.add(po)
    await session.flush()
    for spec in lines:
        await add_line(session, po, spec)
    return po


async def add_line(session: AsyncSession, po: PurchaseOrder, spec: PoLineSpec) -> PoLine:
    if po.status != "draft":
        raise PurchaseOrderInvalid("not_draft")
    item, quantity, unit_cost = await _validated_line(session, spec)
    line = PoLine(
        business_id=po.business_id, po_id=po.id, item_id=item.id, quantity=quantity, uom_id=spec.uom_id,
        unit_cost=unit_cost, line_total=(quantity * unit_cost).quantize(MONEY),
    )
    session.add(line)
    await session.flush()
    await _recompute_subtotal(session, po)
    return line


async def update_line(session: AsyncSession, po: PurchaseOrder, line: PoLine, **changes) -> PoLine:
    if po.status != "draft":
        raise PurchaseOrderInvalid("not_draft")
    if line.po_id != po.id:
        raise PurchaseOrderInvalid("line")
    spec = PoLineSpec(
        item_id=changes.get("item_id") or line.item_id,
        quantity=changes["quantity"] if changes.get("quantity") is not None else line.quantity,
        unit_cost=changes["unit_cost"] if changes.get("unit_cost") is not None else line.unit_cost,
        uom_id=changes["uom_id"] if "uom_id" in changes else line.uom_id,
    )
    item, quantity, unit_cost = await _validated_line(session, spec)
    line.item_id, line.quantity, line.unit_cost, line.uom_id = item.id, quantity, unit_cost, spec.uom_id
    line.line_total = (quantity * unit_cost).quantize(MONEY)
    line.updated_at = datetime.now(timezone.utc)
    await session.flush()
    await _recompute_subtotal(session, po)
    return line


async def remove_line(session: AsyncSession, po: PurchaseOrder, line: PoLine) -> None:
    """A draft is a worksheet, not history: its lines may be removed."""
    if po.status != "draft":
        raise PurchaseOrderInvalid("not_draft")
    if line.po_id != po.id:
        raise PurchaseOrderInvalid("line")
    await session.delete(line)
    await session.flush()
    await _recompute_subtotal(session, po)


async def mark_ordered(session: AsyncSession, po: PurchaseOrder) -> PurchaseOrder:
    if po.status != "draft":
        raise PurchaseOrderInvalid("not_draft")
    n = (await session.execute(select(func.count(PoLine.id)).where(PoLine.po_id == po.id))).scalar_one()
    if n == 0:
        raise PurchaseOrderInvalid("empty")
    po.status = "ordered"
    po.ordered_at = datetime.now(timezone.utc)
    po.updated_at = po.ordered_at
    await session.flush()
    return po


async def cancel(session: AsyncSession, po: PurchaseOrder) -> PurchaseOrder:
    if po.status not in ("draft", "ordered"):
        raise PurchaseOrderInvalid("not_open")
    received = (await session.execute(select(func.coalesce(func.sum(PoLine.received_quantity), 0)).where(PoLine.po_id == po.id))).scalar_one()
    if Decimal(received) > 0:
        raise PurchaseOrderInvalid("already_received")
    po.status = "cancelled"
    po.cancelled_at = datetime.now(timezone.utc)
    po.updated_at = po.cancelled_at
    await session.flush()
    return po


def refresh_status_from_lines(po: PurchaseOrder, lines: list[PoLine]) -> str:
    """Used by receiving (M5-T3): all lines fully received → received; any
    received → partially_received; else unchanged (ordered)."""
    if po.status in ("draft", "cancelled") or not lines:
        return po.status
    if all(Decimal(l.received_quantity) >= Decimal(l.quantity) for l in lines):
        return "received"
    if any(Decimal(l.received_quantity) > 0 for l in lines):
        return "partially_received"
    return "ordered"


async def lines_of(session: AsyncSession, po_id: uuid.UUID) -> list[PoLine]:
    return (await session.execute(select(PoLine).where(PoLine.po_id == po_id).order_by(PoLine.created_at))).scalars().all()
