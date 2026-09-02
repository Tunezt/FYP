"""Suppliers (roadmap M5-T1): contact details plus a derived purchase history.

History is never stored on the supplier: it is read from the receipts (and, from
M5-T3, goods receipts) that point at it. A receipt photo is linked to a supplier
when the text the model read matches an existing supplier's name exactly
(case-insensitive); nothing is auto-created from a photo, because a misread name
would become a junk supplier that the owner then has to clean up.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Receipt, Supplier


class SupplierInvalid(Exception):
    """`code`: name, duplicate."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


async def supplier_by_name(session: AsyncSession, name: str, active_only: bool = False) -> Supplier | None:
    name = (name or "").strip()
    if not name:
        return None
    stmt = select(Supplier).where(func.lower(Supplier.name) == name.lower())
    if active_only:
        stmt = stmt.where(Supplier.is_active.is_(True))
    return (await session.execute(stmt)).scalar_one_or_none()


async def create_supplier(
    session: AsyncSession, business_id: uuid.UUID, *, name: str, phone: str | None = None,
    address: str | None = None, notes: str | None = None,
) -> Supplier:
    name = (name or "").strip()
    if not name:
        raise SupplierInvalid("name")
    if await supplier_by_name(session, name) is not None:
        raise SupplierInvalid("duplicate")
    supplier = Supplier(business_id=business_id, name=name, phone=(phone or None), address=(address or None), notes=(notes or None))
    session.add(supplier)
    await session.flush()
    await link_receipts_by_name(session, supplier)
    return supplier


async def update_supplier(session: AsyncSession, supplier: Supplier, **changes) -> Supplier:
    if changes.get("name") is not None:
        new_name = changes["name"].strip()
        if not new_name:
            raise SupplierInvalid("name")
        other = await supplier_by_name(session, new_name)
        if other is not None and other.id != supplier.id:
            raise SupplierInvalid("duplicate")
        supplier.name = new_name
    for field in ("phone", "address", "notes", "is_active"):
        if field in changes and changes[field] is not None:
            setattr(supplier, field, changes[field] or None if field != "is_active" else changes[field])
    supplier.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return supplier


async def link_receipts_by_name(session: AsyncSession, supplier: Supplier) -> int:
    """Attach unlinked receipts whose photo text names this supplier."""
    rows = (
        await session.execute(
            select(Receipt).where(Receipt.supplier_id.is_(None), func.lower(func.trim(Receipt.supplier)) == supplier.name.lower())
        )
    ).scalars().all()
    for r in rows:
        r.supplier_id = supplier.id
    await session.flush()
    return len(rows)


async def link_receipt(session: AsyncSession, receipt: Receipt) -> Supplier | None:
    """Called when a receipt photo is committed: link it if the name is known."""
    supplier = await supplier_by_name(session, receipt.supplier or "", active_only=True)
    if supplier is not None:
        receipt.supplier_id = supplier.id
        await session.flush()
    return supplier


async def purchase_history(session: AsyncSession, supplier: Supplier, limit: int = 50) -> dict:
    """Everything bought from the supplier, newest first, with totals: receipt
    photos (M5-T1) and goods receipts (M5-T3), merged."""
    from app.models import GoodsReceipt, GoodsReceiptLine

    photos = (
        await session.execute(
            select(Receipt).where(Receipt.supplier_id == supplier.id)
            .order_by(func.coalesce(Receipt.occurred_at, Receipt.created_at).desc()).limit(limit)
        )
    ).scalars().all()
    goods = (
        await session.execute(
            select(GoodsReceipt).where(GoodsReceipt.supplier_id == supplier.id)
            .order_by(GoodsReceipt.received_at.desc()).limit(limit)
        )
    ).scalars().all()
    goods_counts: dict = {}
    if goods:
        for rid, n in (await session.execute(
            select(GoodsReceiptLine.receipt_id, func.count(GoodsReceiptLine.id))
            .where(GoodsReceiptLine.receipt_id.in_([g.id for g in goods])).group_by(GoodsReceiptLine.receipt_id)
        )).all():
            goods_counts[rid] = int(n)

    entries = [
        {"id": r.id, "kind": "photo", "occurred_at": r.occurred_at or r.created_at,
         "total_amount": r.total_amount, "item_count": len((r.parsed_data or {}).get("items", []))}
        for r in photos
    ] + [
        {"id": g.id, "kind": "goods_receipt", "occurred_at": g.received_at,
         "total_amount": g.subtotal, "item_count": goods_counts.get(g.id, 0)}
        for g in goods
    ]
    entries.sort(key=lambda e: e["occurred_at"], reverse=True)

    p_count, p_total, p_last = (
        await session.execute(
            select(func.count(Receipt.id), func.coalesce(func.sum(Receipt.total_amount), 0),
                   func.max(func.coalesce(Receipt.occurred_at, Receipt.created_at)))
            .where(Receipt.supplier_id == supplier.id)
        )
    ).one()
    g_count, g_total, g_last = (
        await session.execute(
            select(func.count(GoodsReceipt.id), func.coalesce(func.sum(GoodsReceipt.subtotal), 0), func.max(GoodsReceipt.received_at))
            .where(GoodsReceipt.supplier_id == supplier.id)
        )
    ).one()
    last_dates = [d for d in (p_last, g_last) if d is not None]
    return {
        "supplier": supplier,
        "purchase_count": int(p_count or 0) + int(g_count or 0),
        "total_spent": Decimal(p_total or 0) + Decimal(g_total or 0),
        "last_purchase_at": max(last_dates) if last_dates else None,
        "receipts": entries[:limit],
    }
