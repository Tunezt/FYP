"""Goods receipts (roadmap M5-T3): the event that moves purchased stock in.

For every received line, in the caller's transaction:
  * the received quantity is converted into the item's own unit (M4-T3) and
    rounded to numeric(12,3)
  * the unit cost is converted the same way (cost per received unit → cost per
    item unit, exact Decimal, rounded to cents)
  * `add_stock(reason="purchase")` increments `items.current_stock`, writes the
    `purchase` stock movement, and recomputes the moving-average cost (M4-T5)
  * a line received against a PO line advances `po_lines.received_quantity`
    (in the PO line's unit) and the PO status follows: partially_received
    while anything is short, received once every line is complete

Partial receipt is normal. Over-receipt (more than the PO line ordered) is a
mistake often enough that it is refused unless the caller says
`allow_over_receipt=True` — explicit, never silent.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GoodsReceipt, GoodsReceiptLine, Item, PoLine, PurchaseOrder, Supplier, Uom
from app.services.purchasing import lines_of, refresh_status_from_lines
from app.services.stock import add_stock
from app.services.units import ItemHasNoUom, UnitConversionMissing, convert_quantity, to_ledger_precision

MONEY = Decimal("0.01")


class ReceivingInvalid(Exception):
    """`code`: supplier, po, po_closed, po_line, po_line_mismatch, item, uom, quantity,
    over_receipt, empty, no_uom, conversion."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}:{detail}")


@dataclass
class GrLineSpec:
    item_id: uuid.UUID
    quantity: Decimal
    unit_cost: Decimal = Decimal(0)      # per received unit
    uom_id: uuid.UUID | None = None       # None = the item's own unit
    po_line_id: uuid.UUID | None = None


@dataclass
class Received:
    receipt: GoodsReceipt
    lines: list[GoodsReceiptLine]
    po: PurchaseOrder | None


async def _next_number(session: AsyncSession) -> int:
    current = (await session.execute(select(func.max(GoodsReceipt.number)))).scalar_one()
    return int(current or 0) + 1


async def _in_item_unit(session: AsyncSession, item: Item, quantity: Decimal, uom_id: uuid.UUID | None) -> Decimal:
    if uom_id is None or uom_id == item.uom_id:
        return Decimal(quantity)
    if item.uom_id is None:
        raise ItemHasNoUom()
    return await convert_quantity(session, Decimal(quantity), uom_id, item.uom_id)


async def receive_goods(
    session: AsyncSession,
    business_id: uuid.UUID,
    *,
    lines: list[GrLineSpec],
    supplier_id: uuid.UUID | None = None,
    po_id: uuid.UUID | None = None,
    received_by: uuid.UUID | None = None,
    notes: str | None = None,
    allow_over_receipt: bool = False,
    received_at: datetime | None = None,
) -> Received:
    if not lines:
        raise ReceivingInvalid("empty")
    po: PurchaseOrder | None = None
    if po_id is not None:
        po = await session.get(PurchaseOrder, po_id)
        if po is None:
            raise ReceivingInvalid("po")
        if po.status not in ("ordered", "partially_received"):
            raise ReceivingInvalid("po_closed", po.status)
        supplier_id = supplier_id or po.supplier_id
    if supplier_id is not None and await session.get(Supplier, supplier_id) is None:
        raise ReceivingInvalid("supplier")

    receipt = GoodsReceipt(
        business_id=business_id, supplier_id=supplier_id, po_id=po_id, number=await _next_number(session),
        received_by=received_by, notes=notes, received_at=received_at or datetime.now(timezone.utc),
    )
    session.add(receipt)
    await session.flush()

    written: list[GoodsReceiptLine] = []
    subtotal = Decimal(0)
    po_lines_touched: dict[uuid.UUID, PoLine] = {}
    for spec in lines:
        item = await session.get(Item, spec.item_id)
        if item is None:
            raise ReceivingInvalid("item")
        quantity = Decimal(spec.quantity)
        if quantity <= 0:
            raise ReceivingInvalid("quantity", item.name)
        if spec.uom_id is not None and await session.get(Uom, spec.uom_id) is None:
            raise ReceivingInvalid("uom")
        unit_cost = Decimal(spec.unit_cost or 0)
        if unit_cost < 0:
            raise ReceivingInvalid("quantity", item.name)

        try:
            qty_item_unit = to_ledger_precision(await _in_item_unit(session, item, quantity, spec.uom_id))
        except ItemHasNoUom:
            raise ReceivingInvalid("no_uom", item.name)
        except UnitConversionMissing as exc:
            raise ReceivingInvalid("conversion", f"{exc.from_code}→{exc.to_code}")
        if qty_item_unit <= 0:
            raise ReceivingInvalid("quantity", item.name)
        # Cost per item unit: the whole line's money spread over what went into the ledger.
        line_total = (quantity * unit_cost).quantize(MONEY)
        cost_item_unit = (line_total / qty_item_unit).quantize(MONEY, rounding=ROUND_HALF_UP) if unit_cost > 0 else Decimal(0)

        po_line: PoLine | None = None
        if spec.po_line_id is not None:
            if po is None:
                raise ReceivingInvalid("po_line")
            po_line = await session.get(PoLine, spec.po_line_id)
            if po_line is None or po_line.po_id != po.id:
                raise ReceivingInvalid("po_line")
            if po_line.item_id != item.id:
                raise ReceivingInvalid("po_line_mismatch", item.name)
            # Advance the PO line in ITS unit.
            try:
                if po_line.uom_id == spec.uom_id:
                    qty_po_unit = quantity
                elif po_line.uom_id is None:
                    qty_po_unit = qty_item_unit
                elif spec.uom_id is None:
                    qty_po_unit = await convert_quantity(session, qty_item_unit, item.uom_id, po_line.uom_id)
                else:
                    qty_po_unit = await convert_quantity(session, quantity, spec.uom_id, po_line.uom_id)
            except UnitConversionMissing as exc:
                raise ReceivingInvalid("conversion", f"{exc.from_code}→{exc.to_code}")
            new_received = (Decimal(po_line.received_quantity) + qty_po_unit).quantize(Decimal("0.001"))
            if new_received > Decimal(po_line.quantity) and not allow_over_receipt:
                raise ReceivingInvalid(
                    "over_receipt",
                    f"{item.name}: dipesan {Decimal(po_line.quantity):g}, sudah diterima {Decimal(po_line.received_quantity):g}, kini {quantity:g}",
                )
            po_line.received_quantity = new_received
            po_line.updated_at = datetime.now(timezone.utc)
            po_lines_touched[po_line.id] = po_line

        gr_line = GoodsReceiptLine(
            business_id=business_id, receipt_id=receipt.id, po_line_id=po_line.id if po_line else None,
            item_id=item.id, quantity=quantity, uom_id=spec.uom_id, quantity_item_unit=qty_item_unit,
            unit_cost=unit_cost, unit_cost_item_unit=cost_item_unit, line_total=line_total,
        )
        session.add(gr_line)
        await session.flush()
        # Stock in, ledger row, moving-average cost — all here (M2-T2, M4-T5).
        await add_stock(
            session, item, qty_item_unit, reason="purchase", source_type="goods_receipt", source_id=gr_line.id,
            unit_cost=cost_item_unit if unit_cost > 0 else None, staff_id=received_by,
            now=datetime.now(timezone.utc),
        )
        written.append(gr_line)
        subtotal += line_total

    receipt.subtotal = subtotal.quantize(MONEY)
    if po is not None:
        po.status = refresh_status_from_lines(po, await lines_of(session, po.id))
        po.updated_at = datetime.now(timezone.utc)
    await session.flush()

    # The books (M6-T4): inventory up, supplier payable up — same transaction.
    from app.services.posting import post_event

    await post_event(
        session, business_id, "GoodsReceived", {"inventory": receipt.subtotal},
        source_type="goods_receipt", source_id=receipt.id,
        memo=f"penerimaan barang #{receipt.number}", posted_at=receipt.received_at, created_by=received_by,
    )
    return Received(receipt=receipt, lines=written, po=po)


async def receipt_lines(session: AsyncSession, receipt_id: uuid.UUID) -> list[GoodsReceiptLine]:
    return (
        await session.execute(
            select(GoodsReceiptLine).where(GoodsReceiptLine.receipt_id == receipt_id).order_by(GoodsReceiptLine.created_at)
        )
    ).scalars().all()
