"""Owner dashboard API — every route requires scope="owner" (OwnerCtx), every
query runs in the tenant-pinned session. Aggregations are single grouped
queries (no per-row loops against the DB); list views are paginated.
"""
import asyncio
import io
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from app.ai.periods import period_range
from app.core.deps import OwnerCtx
from app.models import (
    Account, Alert, Business, Expense, GoodsReceipt, Item, ItemVariant, Modifier, ModifierGroup, PoLine, PostingRule,
    PurchaseOrder, Receipt, RecipeLine, Sale, Staff, Supplier, Uom, UomConversion,
)
from app.schemas.dashboard import (
    BalanceSheetOut,
    ProfitAndLossOut,
    StatementLineOut,
    AlertRow,
    BusinessUpdateIn,
    CustomerCreateIn,
    CustomerOut,
    CustomersPage,
    CustomerUpdateIn,
    LoyaltySettingsOut,
    LoyaltySettingsPatch,
    PointsAdjustIn,
    PointsMovementOut,
    PromoCreateIn,
    PromoOut,
    PromoUpdateIn,
    PricingSettingsOut,
    PricingSettingsPatch,
    ExpensesPage,
    InventoryItem,
    ItemCreateIn,
    ItemUpdateIn,
    OverviewOut,
    PnlMonth,
    ReceiptRow,
    ReceiptsPage,
    SaleRow,
    SalesPage,
    TrendPoint,
    VariantCreateIn,
    VariantOut,
    VariantUpdateIn,
    ModifierCreateIn,
    ModifierGroupCreateIn,
    ModifierGroupOut,
    ModifierGroupUpdateIn,
    ModifierOut,
    ModifierUpdateIn,
    UomConversionCreateIn,
    UomConversionOut,
    UomCreateIn,
    UomOut,
    RecipeLineIn,
    RecipeLineOut,
    RecipeLineUpdateIn,
    SupplierCreateIn,
    SupplierHistoryOut,
    SupplierOut,
    SupplierReceiptOut,
    SupplierUpdateIn,
    PoLineIn,
    PoLineOut,
    PoLineUpdateIn,
    PurchaseOrderCreateIn,
    PurchaseOrderOut,
    PurchaseOrderUpdateIn,
    GoodsReceiptCreateIn,
    GoodsReceiptOut,
    GrLineOut,
    AccountCreateIn,
    AccountOut,
    AccountUpdateIn,
    PostingRuleOut,
    PostingRuleUpdateIn,
)
from app.schemas.auth import BusinessOut
from app.services.velocity import VELOCITY_WINDOW_DAYS
from app.whatsapp.storage import create_signed_url

router = APIRouter(prefix="/api", tags=["dashboard"])


async def _business(ctx) -> Business:
    business = await ctx.session.get(Business, ctx.business_id)
    if business is None:
        # The business is resolved from the caller's own auth token, so a miss
        # means the token references a business that no longer exists (e.g. it
        # was re-created with a new id after a dev re-seed). That's an invalid
        # session, not a missing resource — 401 lets the frontend clear the
        # stale token and bounce to login instead of dead-ending on an error.
        raise HTTPException(status_code=401, detail="Sesi sudah berakhir — silakan masuk lagi ya")
    return business


@router.get("/overview", response_model=OverviewOut)
async def overview(ctx: OwnerCtx):
    business = await _business(ctx)
    today_start, today_end, _ = period_range("today", business.timezone)
    y_start, y_end, _ = period_range("yesterday", business.timezone)
    m_start, m_end, _ = period_range("this_month", business.timezone)

    today_revenue, today_tx = (
        await ctx.session.execute(
            select(func.coalesce(func.sum(Sale.total_price), 0), func.count(Sale.id)).where(
                Sale.sold_at >= today_start, Sale.sold_at < today_end
            )
        )
    ).one()
    yesterday_revenue = (
        await ctx.session.execute(
            select(func.coalesce(func.sum(Sale.total_price), 0)).where(
                Sale.sold_at >= y_start, Sale.sold_at < y_end
            )
        )
    ).scalar_one()
    month_revenue = (
        await ctx.session.execute(
            select(func.coalesce(func.sum(Sale.total_price), 0)).where(
                Sale.sold_at >= m_start, Sale.sold_at < m_end
            )
        )
    ).scalar_one()
    month_expenses = (
        await ctx.session.execute(
            select(func.coalesce(func.sum(Expense.amount), 0)).where(
                Expense.occurred_at >= m_start, Expense.occurred_at < m_end
            )
        )
    ).scalar_one()
    unacked = (
        await ctx.session.execute(
            select(func.count(Alert.id)).where(Alert.is_acknowledged.is_(False))
        )
    ).scalar_one()
    low_stock = (
        await ctx.session.execute(
            select(func.count(Item.id)).where(Item.current_stock <= Item.reorder_threshold)
        )
    ).scalar_one()

    return OverviewOut(
        business_name=business.name,
        today_revenue=float(today_revenue),
        today_transactions=int(today_tx),
        yesterday_revenue=float(yesterday_revenue),
        month_revenue=float(month_revenue),
        month_expenses=float(month_expenses),
        month_net=float(month_revenue) - float(month_expenses),
        unacknowledged_alerts=int(unacked),
        low_stock_items=int(low_stock),
        onboarding_completed=business.onboarding_completed_at is not None,
    )


@router.get("/sales-trend", response_model=list[TrendPoint])
async def sales_trend(ctx: OwnerCtx, days: int = Query(default=30, ge=1, le=365)):
    business = await _business(ctx)
    tz = ZoneInfo(business.timezone)
    since_utc, _, _ = period_range("today", business.timezone)
    since_utc -= timedelta(days=days - 1)

    # One grouped query: bucket by business-local calendar day.
    local_day = func.date(func.timezone(business.timezone, Sale.sold_at))
    rows = (
        await ctx.session.execute(
            select(
                local_day.label("day"),
                func.sum(Sale.total_price),
                func.count(Sale.id),
            )
            .where(Sale.sold_at >= since_utc)
            .group_by(local_day)
            .order_by(local_day)
        )
    ).all()
    by_day = {str(day): (float(rev), int(tx)) for day, rev, tx in rows}

    # Dense series (zero-filled) so charts don't skip quiet days.
    start_local = datetime.now(tz).date() - timedelta(days=days - 1)
    series: list[TrendPoint] = []
    for offset in range(days):
        d = start_local + timedelta(days=offset)
        revenue, tx = by_day.get(d.isoformat(), (0.0, 0))
        series.append(TrendPoint(date=d.isoformat(), revenue=revenue, transactions=tx))
    return series


@router.get("/sales", response_model=SalesPage)
async def sales_history(
    ctx: OwnerCtx,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
):
    total = (await ctx.session.execute(select(func.count(Sale.id)))).scalar_one()
    rows = (
        await ctx.session.execute(
            select(Sale, Item.name, Staff.name)
            .join(Item, Item.id == Sale.item_id)
            .join(Staff, Staff.id == Sale.staff_id)
            .order_by(Sale.sold_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return SalesPage(
        total=int(total),
        page=page,
        page_size=page_size,
        rows=[
            SaleRow(
                id=sale.id,
                item_name=item_name,
                staff_name=staff_name,
                quantity=sale.quantity,
                unit_price=sale.unit_price,
                total_price=sale.total_price,
                sold_at=sale.sold_at,
            )
            for sale, item_name, staff_name in rows
        ],
    )


@router.get("/items", response_model=list[InventoryItem])
async def inventory(ctx: OwnerCtx):
    items = (await ctx.session.execute(select(Item).order_by(Item.name))).scalars().all()

    # Velocity for ALL items in one grouped query (no N+1).
    since = datetime.now(timezone.utc) - timedelta(days=VELOCITY_WINDOW_DAYS)
    usage_rows = (
        await ctx.session.execute(
            select(Sale.item_id, func.sum(Sale.quantity))
            .where(Sale.sold_at >= since)
            .group_by(Sale.item_id)
        )
    ).all()
    usage = {item_id: Decimal(qty) for item_id, qty in usage_rows}

    out: list[InventoryItem] = []
    for item in items:
        daily = usage.get(item.id, Decimal(0)) / VELOCITY_WINDOW_DAYS
        days_remaining = float(item.current_stock / daily) if daily > 0 else None
        out.append(
            InventoryItem(
                id=item.id,
                name=item.name,
                unit=item.unit,
                current_stock=item.current_stock,
                cost_price=item.cost_price,
                sell_price=item.sell_price,
                reorder_threshold=item.reorder_threshold,
                avg_daily_usage=round(float(daily), 3) if daily > 0 else None,
                days_remaining=round(days_remaining, 1) if days_remaining is not None else None,
                below_reorder_threshold=item.current_stock <= item.reorder_threshold,
            )
        )
    return out


@router.post("/items", response_model=InventoryItem, status_code=201)
async def create_item(payload: ItemCreateIn, ctx: OwnerCtx):
    item = Item(business_id=ctx.business_id, **payload.model_dump())
    if item.uom_id is None:  # M4-T3: link the free-text unit to a real one when it matches
        from app.services.units import uom_by_code

        known = await uom_by_code(ctx.session, item.unit)
        item.uom_id = known.id if known else None
    ctx.session.add(item)
    await ctx.session.flush()
    # Opening balance goes into the ledger in the same transaction (M2-T2).
    from app.services.catalog import ensure_default_variant
    from app.services.stock import open_item_stock

    await open_item_stock(
        ctx.session, item, reason="opname", source_type="dashboard",
        unit_cost=item.cost_price if item.cost_price and item.cost_price > 0 else None,
        staff_id=ctx.staff_id,
    )
    await ensure_default_variant(ctx.session, item)  # M4-T1
    return InventoryItem(
        id=item.id,
        name=item.name,
        unit=item.unit,
        current_stock=item.current_stock,
        cost_price=item.cost_price,
        sell_price=item.sell_price,
        reorder_threshold=item.reorder_threshold,
        avg_daily_usage=None,
        days_remaining=None,
        below_reorder_threshold=item.current_stock <= item.reorder_threshold,
    )


@router.patch("/items/{item_id}", response_model=InventoryItem)
async def update_item(item_id: uuid.UUID, payload: ItemUpdateIn, ctx: OwnerCtx):
    item = await ctx.session.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Barang tidak ditemukan")
    changes = payload.model_dump(exclude_none=True)
    new_stock = changes.pop("current_stock", None)
    for field, value in changes.items():
        setattr(item, field, value)
    if new_stock is not None:
        # An edited stock figure is a correction: ledgered in this transaction (M2-T2).
        from app.services.stock import set_absolute_stock

        await set_absolute_stock(
            ctx.session, item, new_stock, reason="correction", source_type="dashboard",
            staff_id=ctx.staff_id,
        )
    if changes or new_stock is not None:
        item.updated_at = datetime.now(timezone.utc)
    await ctx.session.flush()
    if "sell_price" in changes or "cost_price" in changes:
        from app.services.catalog import sync_default_from_item

        await sync_default_from_item(ctx.session, item)  # M4-T1: default variant mirrors the item
    return InventoryItem(
        id=item.id,
        name=item.name,
        unit=item.unit,
        current_stock=item.current_stock,
        cost_price=item.cost_price,
        sell_price=item.sell_price,
        reorder_threshold=item.reorder_threshold,
        avg_daily_usage=None,
        days_remaining=None,
        below_reorder_threshold=item.current_stock <= item.reorder_threshold,
    )


_VARIANT_ERRORS = {
    "name": (422, "Nama varian tidak boleh kosong"),
    "duplicate": (409, "Nama varian sudah dipakai untuk barang ini"),
    "default_inactive": (409, "Varian utama tidak bisa dinonaktifkan — jadikan varian lain sebagai utama dulu"),
}


@router.get("/items/{item_id}/variants", response_model=list[VariantOut])
async def list_variants(item_id: uuid.UUID, ctx: OwnerCtx):
    if await ctx.session.get(Item, item_id) is None:
        raise HTTPException(status_code=404, detail="Barang tidak ditemukan")
    rows = (
        await ctx.session.execute(
            select(ItemVariant).where(ItemVariant.item_id == item_id)
            .order_by(ItemVariant.is_default.desc(), ItemVariant.sell_price, ItemVariant.name)
        )
    ).scalars().all()
    return [VariantOut.model_validate(v) for v in rows]


@router.post("/items/{item_id}/variants", response_model=VariantOut, status_code=201)
async def add_variant(item_id: uuid.UUID, payload: VariantCreateIn, ctx: OwnerCtx):
    from app.services.catalog import VariantInvalid, create_variant

    item = await ctx.session.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Barang tidak ditemukan")
    try:
        variant = await create_variant(ctx.session, item, **payload.model_dump())
    except VariantInvalid as exc:
        status, detail = _VARIANT_ERRORS[exc.code]
        raise HTTPException(status_code=status, detail=detail)
    return VariantOut.model_validate(variant)


@router.patch("/variants/{variant_id}", response_model=VariantOut)
async def edit_variant(variant_id: uuid.UUID, payload: VariantUpdateIn, ctx: OwnerCtx):
    from app.services.catalog import VariantInvalid, update_variant

    variant = await ctx.session.get(ItemVariant, variant_id)
    if variant is None:
        raise HTTPException(status_code=404, detail="Varian barang tidak ditemukan")
    item = await ctx.session.get(Item, variant.item_id)
    try:
        variant = await update_variant(ctx.session, variant, item, **payload.model_dump(exclude_none=True))
    except VariantInvalid as exc:
        status, detail = _VARIANT_ERRORS[exc.code]
        raise HTTPException(status_code=status, detail=detail)
    return VariantOut.model_validate(variant)


_UOM_ERRORS = {
    "code": (422, "Kode satuan tidak boleh kosong"),
    "duplicate": (409, "Satuan atau konversi ini sudah ada"),
    "same": (422, "Satuan asal dan tujuan harus berbeda"),
    "factor": (422, "Faktor konversi harus lebih dari nol"),
}


@router.get("/uoms", response_model=list[UomOut])
async def list_uoms(ctx: OwnerCtx):
    rows = (await ctx.session.execute(select(Uom).order_by(Uom.code))).scalars().all()
    return [UomOut.model_validate(u) for u in rows]


@router.post("/uoms", response_model=UomOut, status_code=201)
async def add_uom(payload: UomCreateIn, ctx: OwnerCtx):
    from app.services.units import UomInvalid, create_uom

    try:
        uom = await create_uom(ctx.session, ctx.business_id, code=payload.code, name=payload.name)
    except UomInvalid as exc:
        status, detail = _UOM_ERRORS[exc.code]
        raise HTTPException(status_code=status, detail=detail)
    return UomOut.model_validate(uom)


@router.get("/uom-conversions", response_model=list[UomConversionOut])
async def list_uom_conversions(ctx: OwnerCtx):
    rows = (await ctx.session.execute(select(UomConversion).order_by(UomConversion.created_at))).scalars().all()
    return [UomConversionOut.model_validate(c) for c in rows]


@router.post("/uom-conversions", response_model=UomConversionOut, status_code=201)
async def add_uom_conversion(payload: UomConversionCreateIn, ctx: OwnerCtx):
    from app.services.units import UomInvalid, create_conversion

    from_uom = await ctx.session.get(Uom, payload.from_uom_id)
    to_uom = await ctx.session.get(Uom, payload.to_uom_id)
    if from_uom is None or to_uom is None:
        raise HTTPException(status_code=404, detail="Satuan tidak ditemukan")
    try:
        conv = await create_conversion(
            ctx.session, ctx.business_id, from_uom=from_uom, to_uom=to_uom, factor=payload.factor, both_ways=payload.both_ways
        )
    except UomInvalid as exc:
        status, detail = _UOM_ERRORS[exc.code]
        raise HTTPException(status_code=status, detail=detail)
    return UomConversionOut.model_validate(conv)


_SUPPLIER_ERRORS = {
    "name": (422, "Nama supplier tidak boleh kosong"),
    "duplicate": (409, "Supplier dengan nama ini sudah ada"),
}


@router.get("/suppliers", response_model=list[SupplierOut])
async def list_suppliers(ctx: OwnerCtx, include_inactive: bool = Query(default=False)):
    stmt = select(Supplier).order_by(Supplier.name)
    if not include_inactive:
        stmt = stmt.where(Supplier.is_active.is_(True))
    return [SupplierOut.model_validate(s) for s in (await ctx.session.execute(stmt)).scalars()]


@router.post("/suppliers", response_model=SupplierOut, status_code=201)
async def add_supplier(payload: SupplierCreateIn, ctx: OwnerCtx):
    from app.services.suppliers import SupplierInvalid, create_supplier

    try:
        supplier = await create_supplier(ctx.session, ctx.business_id, **payload.model_dump())
    except SupplierInvalid as exc:
        status, detail = _SUPPLIER_ERRORS[exc.code]
        raise HTTPException(status_code=status, detail=detail)
    return SupplierOut.model_validate(supplier)


@router.patch("/suppliers/{supplier_id}", response_model=SupplierOut)
async def edit_supplier(supplier_id: uuid.UUID, payload: SupplierUpdateIn, ctx: OwnerCtx):
    from app.services.suppliers import SupplierInvalid, update_supplier

    supplier = await ctx.session.get(Supplier, supplier_id)
    if supplier is None:
        raise HTTPException(status_code=404, detail="Supplier tidak ditemukan")
    try:
        supplier = await update_supplier(ctx.session, supplier, **payload.model_dump(exclude_none=True))
    except SupplierInvalid as exc:
        status, detail = _SUPPLIER_ERRORS[exc.code]
        raise HTTPException(status_code=status, detail=detail)
    return SupplierOut.model_validate(supplier)


@router.get("/suppliers/{supplier_id}/history", response_model=SupplierHistoryOut)
async def supplier_history(supplier_id: uuid.UUID, ctx: OwnerCtx):
    from app.services.suppliers import purchase_history

    supplier = await ctx.session.get(Supplier, supplier_id)
    if supplier is None:
        raise HTTPException(status_code=404, detail="Supplier tidak ditemukan")
    h = await purchase_history(ctx.session, supplier)
    return SupplierHistoryOut(
        supplier=SupplierOut.model_validate(supplier), purchase_count=h["purchase_count"],
        total_spent=h["total_spent"], last_purchase_at=h["last_purchase_at"],
        receipts=[SupplierReceiptOut(**r) for r in h["receipts"]],
    )


_PO_ERRORS = {
    "supplier": (404, "Supplier tidak ditemukan"),
    "supplier_inactive": (409, "Supplier ini sudah dinonaktifkan — aktifkan dulu atau pilih supplier lain"),
    "item": (404, "Barang tidak ditemukan"),
    "uom": (404, "Satuan tidak ditemukan"),
    "quantity": (422, "Jumlah dan harga pesanan harus angka yang masuk akal (jumlah lebih dari nol)"),
    "not_draft": (409, "Pesanan ini sudah dikirim ke supplier — barisnya tidak bisa diubah lagi"),
    "empty": (422, "Pesanan belum punya barang — tambahkan dulu sebelum dikirim"),
    "not_open": (409, "Pesanan ini sudah selesai atau sudah dibatalkan"),
    "already_received": (409, "Sebagian barang sudah diterima — pesanan tidak bisa dibatalkan lagi"),
    "line": (404, "Baris pesanan tidak ditemukan"),
}


async def _po_out(session, po: PurchaseOrder) -> PurchaseOrderOut:
    from app.services.purchasing import lines_of

    supplier = await session.get(Supplier, po.supplier_id)
    out_lines = []
    for l in await lines_of(session, po.id):
        item = await session.get(Item, l.item_id)
        uom = await session.get(Uom, l.uom_id) if l.uom_id else None
        out_lines.append(PoLineOut(
            id=l.id, item_id=l.item_id, item_name=item.name if item else "?", quantity=l.quantity,
            uom_id=l.uom_id, uom_code=uom.code if uom else None, unit_cost=l.unit_cost,
            line_total=l.line_total, received_quantity=l.received_quantity,
        ))
    return PurchaseOrderOut(
        id=po.id, number=po.number, supplier_id=po.supplier_id, supplier_name=supplier.name if supplier else "?",
        status=po.status, notes=po.notes, expected_at=po.expected_at, ordered_at=po.ordered_at,
        cancelled_at=po.cancelled_at, subtotal=po.subtotal, created_at=po.created_at, lines=out_lines,
    )


async def _po(session, po_id: uuid.UUID) -> PurchaseOrder:
    po = await session.get(PurchaseOrder, po_id)
    if po is None:
        raise HTTPException(status_code=404, detail="Pesanan pembelian tidak ditemukan")
    return po


def _po_error(exc) -> HTTPException:
    status, detail = _PO_ERRORS[exc.code]
    return HTTPException(status_code=status, detail=detail)


@router.get("/purchase-orders", response_model=list[PurchaseOrderOut])
async def list_purchase_orders(ctx: OwnerCtx, status: str | None = Query(default=None)):
    stmt = select(PurchaseOrder).order_by(PurchaseOrder.number.desc())
    if status:
        stmt = stmt.where(PurchaseOrder.status == status)
    return [await _po_out(ctx.session, po) for po in (await ctx.session.execute(stmt)).scalars()]


@router.post("/purchase-orders", response_model=PurchaseOrderOut, status_code=201)
async def create_po(payload: PurchaseOrderCreateIn, ctx: OwnerCtx):
    from app.services.purchasing import PoLineSpec, PurchaseOrderInvalid, create_purchase_order

    try:
        po = await create_purchase_order(
            ctx.session, ctx.business_id, supplier_id=payload.supplier_id,
            lines=[PoLineSpec(item_id=l.item_id, quantity=l.quantity, unit_cost=l.unit_cost, uom_id=l.uom_id) for l in payload.lines],
            notes=payload.notes, expected_at=payload.expected_at, created_by=ctx.staff_id,
        )
    except PurchaseOrderInvalid as exc:
        raise _po_error(exc)
    return await _po_out(ctx.session, po)


@router.get("/purchase-orders/{po_id}", response_model=PurchaseOrderOut)
async def get_po(po_id: uuid.UUID, ctx: OwnerCtx):
    return await _po_out(ctx.session, await _po(ctx.session, po_id))


@router.patch("/purchase-orders/{po_id}", response_model=PurchaseOrderOut)
async def edit_po(po_id: uuid.UUID, payload: PurchaseOrderUpdateIn, ctx: OwnerCtx):
    po = await _po(ctx.session, po_id)
    changes = payload.model_dump(exclude_none=True)
    for field, value in changes.items():
        setattr(po, field, value)
    if changes:
        po.updated_at = datetime.now(timezone.utc)
        await ctx.session.flush()
    return await _po_out(ctx.session, po)


@router.post("/purchase-orders/{po_id}/lines", response_model=PurchaseOrderOut, status_code=201)
async def add_po_line(po_id: uuid.UUID, payload: PoLineIn, ctx: OwnerCtx):
    from app.services.purchasing import PoLineSpec, PurchaseOrderInvalid, add_line

    po = await _po(ctx.session, po_id)
    try:
        await add_line(ctx.session, po, PoLineSpec(item_id=payload.item_id, quantity=payload.quantity, unit_cost=payload.unit_cost, uom_id=payload.uom_id))
    except PurchaseOrderInvalid as exc:
        raise _po_error(exc)
    return await _po_out(ctx.session, po)


@router.patch("/purchase-orders/{po_id}/lines/{line_id}", response_model=PurchaseOrderOut)
async def edit_po_line(po_id: uuid.UUID, line_id: uuid.UUID, payload: PoLineUpdateIn, ctx: OwnerCtx):
    from app.services.purchasing import PurchaseOrderInvalid, update_line

    po = await _po(ctx.session, po_id)
    line = await ctx.session.get(PoLine, line_id)
    if line is None:
        raise HTTPException(status_code=404, detail="Baris pesanan tidak ditemukan")
    try:
        await update_line(ctx.session, po, line, **payload.model_dump(exclude_unset=True))
    except PurchaseOrderInvalid as exc:
        raise _po_error(exc)
    return await _po_out(ctx.session, po)


@router.delete("/purchase-orders/{po_id}/lines/{line_id}", response_model=PurchaseOrderOut)
async def delete_po_line(po_id: uuid.UUID, line_id: uuid.UUID, ctx: OwnerCtx):
    """Only while the PO is a draft (a worksheet, not history)."""
    from app.services.purchasing import PurchaseOrderInvalid, remove_line

    po = await _po(ctx.session, po_id)
    line = await ctx.session.get(PoLine, line_id)
    if line is None:
        raise HTTPException(status_code=404, detail="Baris pesanan tidak ditemukan")
    try:
        await remove_line(ctx.session, po, line)
    except PurchaseOrderInvalid as exc:
        raise _po_error(exc)
    return await _po_out(ctx.session, po)


@router.post("/purchase-orders/{po_id}/order", response_model=PurchaseOrderOut)
async def order_po(po_id: uuid.UUID, ctx: OwnerCtx):
    from app.services.purchasing import PurchaseOrderInvalid, mark_ordered

    po = await _po(ctx.session, po_id)
    try:
        await mark_ordered(ctx.session, po)
    except PurchaseOrderInvalid as exc:
        raise _po_error(exc)
    return await _po_out(ctx.session, po)


@router.post("/purchase-orders/{po_id}/cancel", response_model=PurchaseOrderOut)
async def cancel_po(po_id: uuid.UUID, ctx: OwnerCtx):
    from app.services.purchasing import PurchaseOrderInvalid, cancel

    po = await _po(ctx.session, po_id)
    try:
        await cancel(ctx.session, po)
    except PurchaseOrderInvalid as exc:
        raise _po_error(exc)
    return await _po_out(ctx.session, po)


_GR_ERRORS = {
    "supplier": (404, "Supplier tidak ditemukan"),
    "po": (404, "Pesanan pembelian tidak ditemukan"),
    "po_closed": (409, "Pesanan ini belum dikirim, sudah selesai, atau sudah dibatalkan — tidak bisa menerima barang untuknya"),
    "po_line": (404, "Baris pesanan tidak ditemukan pada pesanan ini"),
    "po_line_mismatch": (422, "Barang yang diterima tidak cocok dengan baris pesanan yang dipilih"),
    "item": (404, "Barang tidak ditemukan"),
    "uom": (404, "Satuan tidak ditemukan"),
    "quantity": (422, "Jumlah dan harga penerimaan harus angka yang masuk akal (jumlah lebih dari nol)"),
    "over_receipt": (409, "Jumlah diterima melebihi pesanan — centang 'izinkan kelebihan' kalau memang benar"),
    "empty": (422, "Penerimaan belum punya barang — tambahkan dulu"),
    "no_uom": (422, "Barang ini belum punya satuan — atur satuannya di dashboard dulu sebelum menerima dalam satuan lain"),
    "conversion": (422, "Konversi satuan tidak ditemukan — tambahkan konversinya di Pengaturan dulu"),
}


async def _gr_out(session, receipt: GoodsReceipt) -> GoodsReceiptOut:
    from app.services.receiving import receipt_lines

    supplier = await session.get(Supplier, receipt.supplier_id) if receipt.supplier_id else None
    po = await session.get(PurchaseOrder, receipt.po_id) if receipt.po_id else None
    out_lines = []
    for l in await receipt_lines(session, receipt.id):
        item = await session.get(Item, l.item_id)
        uom = await session.get(Uom, l.uom_id) if l.uom_id else None
        out_lines.append(GrLineOut(
            id=l.id, item_id=l.item_id, item_name=item.name if item else "?", po_line_id=l.po_line_id,
            quantity=l.quantity, uom_code=uom.code if uom else None, quantity_item_unit=l.quantity_item_unit,
            item_unit=item.unit if item else "", unit_cost=l.unit_cost, unit_cost_item_unit=l.unit_cost_item_unit,
            line_total=l.line_total, stock_after=item.current_stock if item else Decimal(0),
            avg_cost_after=item.cost_price if item else Decimal(0),
        ))
    return GoodsReceiptOut(
        id=receipt.id, number=receipt.number, supplier_id=receipt.supplier_id,
        supplier_name=supplier.name if supplier else None, po_id=receipt.po_id,
        po_number=po.number if po else None, po_status=po.status if po else None,
        received_at=receipt.received_at, notes=receipt.notes, subtotal=receipt.subtotal, lines=out_lines,
    )


@router.get("/goods-receipts", response_model=list[GoodsReceiptOut])
async def list_goods_receipts(ctx: OwnerCtx, limit: int = Query(default=50, ge=1, le=200)):
    rows = (await ctx.session.execute(select(GoodsReceipt).order_by(GoodsReceipt.number.desc()).limit(limit))).scalars().all()
    return [await _gr_out(ctx.session, r) for r in rows]


@router.get("/goods-receipts/{receipt_id}", response_model=GoodsReceiptOut)
async def get_goods_receipt(receipt_id: uuid.UUID, ctx: OwnerCtx):
    receipt = await ctx.session.get(GoodsReceipt, receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Penerimaan barang tidak ditemukan")
    return await _gr_out(ctx.session, receipt)


@router.post("/goods-receipts", response_model=GoodsReceiptOut, status_code=201)
async def create_goods_receipt(payload: GoodsReceiptCreateIn, ctx: OwnerCtx):
    """Receive goods (M5-T3): stock in, `purchase` ledger rows, moving-average
    cost, PO lines advanced. Partial is fine; over-receipt needs the explicit flag."""
    from app.services.receiving import GrLineSpec, ReceivingInvalid, receive_goods

    try:
        received = await receive_goods(
            ctx.session, ctx.business_id, supplier_id=payload.supplier_id, po_id=payload.po_id,
            lines=[GrLineSpec(item_id=l.item_id, quantity=l.quantity, unit_cost=l.unit_cost, uom_id=l.uom_id, po_line_id=l.po_line_id)
                   for l in payload.lines],
            received_by=ctx.staff_id, notes=payload.notes, allow_over_receipt=payload.allow_over_receipt,
        )
    except ReceivingInvalid as exc:
        status, detail = _GR_ERRORS[exc.code]
        if exc.code == "over_receipt" and exc.detail:
            detail = f"{detail} ({exc.detail})"
        raise HTTPException(status_code=status, detail=detail)
    return await _gr_out(ctx.session, received.receipt)


_ACCOUNT_ERRORS = {
    "code": (422, "Kode akun harus angka (maksimal 8 digit)"),
    "name": (422, "Nama akun tidak boleh kosong"),
    "duplicate": (409, "Kode akun ini sudah dipakai"),
    "type": (422, "Jenis akun tidak dikenali"),
    "system": (409, "Akun bawaan tidak bisa dinonaktifkan — ganti namanya saja kalau perlu"),
}


@router.get("/accounts", response_model=list[AccountOut])
async def list_accounts(ctx: OwnerCtx, include_inactive: bool = Query(default=False)):
    from app.services.accounts import chart

    return [AccountOut.model_validate(a) for a in await chart(ctx.session, include_inactive=include_inactive)]


@router.post("/accounts", response_model=AccountOut, status_code=201)
async def add_account(payload: AccountCreateIn, ctx: OwnerCtx):
    from app.services.accounts import AccountInvalid, create_account

    try:
        account = await create_account(ctx.session, ctx.business_id, **payload.model_dump())
    except AccountInvalid as exc:
        status, detail = _ACCOUNT_ERRORS[exc.code]
        raise HTTPException(status_code=status, detail=detail)
    return AccountOut.model_validate(account)


@router.patch("/accounts/{account_id}", response_model=AccountOut)
async def edit_account(account_id: uuid.UUID, payload: AccountUpdateIn, ctx: OwnerCtx):
    from app.services.accounts import AccountInvalid, update_account

    account = await ctx.session.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Akun tidak ditemukan")
    try:
        account = await update_account(ctx.session, account, **payload.model_dump(exclude_none=True))
    except AccountInvalid as exc:
        status, detail = _ACCOUNT_ERRORS[exc.code]
        raise HTTPException(status_code=status, detail=detail)
    return AccountOut.model_validate(account)


_RULE_ERRORS = {
    "account": (404, "Kode akun tidak ditemukan atau sudah dinonaktifkan"),
    "same": (422, "Akun debit dan kredit tidak boleh sama"),
    "reversal": (422, "Aturan pembalikan tidak memakai akun — yang dibalik adalah jurnal aslinya"),
}


@router.get("/posting-rules", response_model=list[PostingRuleOut])
async def list_posting_rules(ctx: OwnerCtx, event_type: str | None = Query(default=None)):
    stmt = select(PostingRule).order_by(PostingRule.event_type, PostingRule.component)
    if event_type:
        stmt = stmt.where(PostingRule.event_type == event_type)
    return [PostingRuleOut.model_validate(r) for r in (await ctx.session.execute(stmt)).scalars()]


@router.patch("/posting-rules/{rule_id}", response_model=PostingRuleOut)
async def edit_posting_rule(rule_id: uuid.UUID, payload: PostingRuleUpdateIn, ctx: OwnerCtx):
    from app.services.posting_rules import PostingRuleInvalid, update_rule

    rule = await ctx.session.get(PostingRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Aturan jurnal tidak ditemukan")
    try:
        rule = await update_rule(ctx.session, rule, **payload.model_dump(exclude_none=True))
    except PostingRuleInvalid as exc:
        status, detail = _RULE_ERRORS[exc.code]
        raise HTTPException(status_code=status, detail=detail)
    return PostingRuleOut.model_validate(rule)


# ── Statements (M6-T5) ──────────────────────────────────────────────────────


def _local_day_bounds(tz: ZoneInfo, day: date) -> tuple[datetime, datetime]:
    """[start, next day start) of a business-local calendar day, in UTC."""
    start = datetime.combine(day, time.min, tzinfo=tz)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def _statement_lines(lines) -> list[StatementLineOut]:
    return [StatementLineOut(code=l.code, name=l.name, amount=l.amount) for l in lines]


@router.get("/statements/profit-loss", response_model=ProfitAndLossOut)
async def statement_profit_loss(
    ctx: OwnerCtx, since: date | None = Query(default=None), until: date | None = Query(default=None),
):
    """Laba rugi over inclusive business-local dates; default: this month to date."""
    from app.services.statements import profit_and_loss

    business = await _business(ctx)
    tz = ZoneInfo(business.timezone)
    until = until or datetime.now(tz).date()
    since = since or until.replace(day=1)
    if until < since:
        raise HTTPException(status_code=422, detail="Tanggal akhir tidak boleh sebelum tanggal awal")
    start_utc, _ = _local_day_bounds(tz, since)
    _, end_utc = _local_day_bounds(tz, until)
    report = await profit_and_loss(ctx.session, since=start_utc, until=end_utc)
    return ProfitAndLossOut(
        since=since, until=until,
        revenue=_statement_lines(report.revenue), revenue_total=report.revenue_total,
        cogs=_statement_lines(report.cogs), cogs_total=report.cogs_total, gross_profit=report.gross_profit,
        expenses=_statement_lines(report.expenses), expenses_total=report.expenses_total, net_profit=report.net_profit,
    )


@router.get("/statements/balance-sheet", response_model=BalanceSheetOut)
async def statement_balance_sheet(ctx: OwnerCtx, as_of: date | None = Query(default=None)):
    """Neraca at the end of a business-local day; default today."""
    from app.services.statements import balance_sheet

    business = await _business(ctx)
    tz = ZoneInfo(business.timezone)
    as_of = as_of or datetime.now(tz).date()
    _, end_utc = _local_day_bounds(tz, as_of)
    sheet = await balance_sheet(ctx.session, until=end_utc)
    return BalanceSheetOut(
        as_of=as_of,
        assets=_statement_lines(sheet.assets), assets_total=sheet.assets_total,
        liabilities=_statement_lines(sheet.liabilities), liabilities_total=sheet.liabilities_total,
        equity=_statement_lines(sheet.equity), equity_total=sheet.equity_total,
        current_earnings=sheet.current_earnings, liabilities_and_equity_total=sheet.liabilities_and_equity_total,
        balances=sheet.balances,
    )


_RECIPE_ERRORS = {
    "self": (422, "Bahan tidak boleh barang itu sendiri"),
    "quantity": (422, "Jumlah bahan harus lebih dari nol"),
}


async def _recipe_out(session, line: RecipeLine) -> RecipeLineOut:
    component = await session.get(Item, line.component_item_id)
    uom = await session.get(Uom, line.uom_id) if line.uom_id else None
    return RecipeLineOut(
        id=line.id, variant_id=line.variant_id, component_item_id=line.component_item_id,
        component_name=component.name if component else "?", quantity=line.quantity,
        uom_id=line.uom_id, uom_code=uom.code if uom else None, is_active=line.is_active,
    )


@router.get("/variants/{variant_id}/recipe", response_model=list[RecipeLineOut])
async def get_recipe(variant_id: uuid.UUID, ctx: OwnerCtx):
    from app.services.catalog import recipe_lines_for

    if await ctx.session.get(ItemVariant, variant_id) is None:
        raise HTTPException(status_code=404, detail="Varian barang tidak ditemukan")
    return [await _recipe_out(ctx.session, l) for l in await recipe_lines_for(ctx.session, variant_id)]


@router.post("/variants/{variant_id}/recipe", response_model=RecipeLineOut, status_code=201)
async def put_recipe_line(variant_id: uuid.UUID, payload: RecipeLineIn, ctx: OwnerCtx):
    """Upsert one component of the variant's recipe (M4-T4)."""
    from app.services.catalog import RecipeInvalid, set_recipe_line

    variant = await ctx.session.get(ItemVariant, variant_id)
    if variant is None:
        raise HTTPException(status_code=404, detail="Varian barang tidak ditemukan")
    component = await ctx.session.get(Item, payload.component_item_id)
    if component is None:
        raise HTTPException(status_code=404, detail="Bahan tidak ditemukan")
    if payload.uom_id is not None and await ctx.session.get(Uom, payload.uom_id) is None:
        raise HTTPException(status_code=404, detail="Satuan tidak ditemukan")
    try:
        line = await set_recipe_line(ctx.session, variant, component, quantity=payload.quantity, uom_id=payload.uom_id)
    except RecipeInvalid as exc:
        status, detail = _RECIPE_ERRORS[exc.code]
        raise HTTPException(status_code=status, detail=detail)
    return await _recipe_out(ctx.session, line)


@router.patch("/recipe-lines/{line_id}", response_model=RecipeLineOut)
async def edit_recipe_line(line_id: uuid.UUID, payload: RecipeLineUpdateIn, ctx: OwnerCtx):
    line = await ctx.session.get(RecipeLine, line_id)
    if line is None:
        raise HTTPException(status_code=404, detail="Bahan resep tidak ditemukan")
    changes = payload.model_dump(exclude_none=True)
    if "quantity" in changes:
        line.quantity = changes["quantity"]
    if "uom_id" in changes:
        if await ctx.session.get(Uom, changes["uom_id"]) is None:
            raise HTTPException(status_code=404, detail="Satuan tidak ditemukan")
        line.uom_id = changes["uom_id"]
    if "is_active" in changes:
        line.is_active = changes["is_active"]
    line.updated_at = datetime.now(timezone.utc)
    await ctx.session.flush()
    return await _recipe_out(ctx.session, line)


_MODIFIER_ERRORS = {
    "name": (422, "Nama pilihan tidak boleh kosong"),
    "duplicate": (409, "Nama pilihan sudah dipakai"),
    "bounds": (422, "Minimal pilihan tidak boleh lebih besar dari maksimal"),
}


def _group_out(group: ModifierGroup, mods: list[Modifier]) -> ModifierGroupOut:
    return ModifierGroupOut(
        id=group.id, item_id=group.item_id, name=group.name, selection=group.selection,
        is_required=group.is_required, min_select=group.min_select, max_select=group.max_select,
        sort_order=group.sort_order, is_active=group.is_active,
        modifiers=[ModifierOut.model_validate(m) for m in mods],
    )


@router.get("/items/{item_id}/modifier-groups", response_model=list[ModifierGroupOut])
async def list_modifier_groups(item_id: uuid.UUID, ctx: OwnerCtx):
    if await ctx.session.get(Item, item_id) is None:
        raise HTTPException(status_code=404, detail="Barang tidak ditemukan")
    groups = (
        await ctx.session.execute(
            select(ModifierGroup).where(ModifierGroup.item_id == item_id).order_by(ModifierGroup.sort_order, ModifierGroup.name)
        )
    ).scalars().all()
    mods = (
        await ctx.session.execute(
            select(Modifier).where(Modifier.group_id.in_([g.id for g in groups])).order_by(Modifier.sort_order, Modifier.name)
        )
    ).scalars().all() if groups else []
    by_group: dict[uuid.UUID, list[Modifier]] = {}
    for m in mods:
        by_group.setdefault(m.group_id, []).append(m)
    return [_group_out(g, by_group.get(g.id, [])) for g in groups]


@router.post("/items/{item_id}/modifier-groups", response_model=ModifierGroupOut, status_code=201)
async def add_modifier_group(item_id: uuid.UUID, payload: ModifierGroupCreateIn, ctx: OwnerCtx):
    from app.services.catalog import ModifierInvalid, create_modifier_group

    item = await ctx.session.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Barang tidak ditemukan")
    try:
        group = await create_modifier_group(ctx.session, item, **payload.model_dump())
    except ModifierInvalid as exc:
        status, detail = _MODIFIER_ERRORS[exc.code]
        raise HTTPException(status_code=status, detail=detail)
    return _group_out(group, [])


@router.patch("/modifier-groups/{group_id}", response_model=ModifierGroupOut)
async def edit_modifier_group(group_id: uuid.UUID, payload: ModifierGroupUpdateIn, ctx: OwnerCtx):
    from app.services.catalog import ModifierInvalid, update_modifier_group

    group = await ctx.session.get(ModifierGroup, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Kelompok pilihan tidak ditemukan")
    try:
        group = await update_modifier_group(ctx.session, group, **payload.model_dump(exclude_none=True))
    except ModifierInvalid as exc:
        status, detail = _MODIFIER_ERRORS[exc.code]
        raise HTTPException(status_code=status, detail=detail)
    mods = (await ctx.session.execute(select(Modifier).where(Modifier.group_id == group.id).order_by(Modifier.sort_order, Modifier.name))).scalars().all()
    return _group_out(group, mods)


@router.post("/modifier-groups/{group_id}/modifiers", response_model=ModifierOut, status_code=201)
async def add_modifier(group_id: uuid.UUID, payload: ModifierCreateIn, ctx: OwnerCtx):
    from app.services.catalog import ModifierInvalid, create_modifier

    group = await ctx.session.get(ModifierGroup, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Kelompok pilihan tidak ditemukan")
    try:
        modifier = await create_modifier(ctx.session, group, **payload.model_dump())
    except ModifierInvalid as exc:
        status, detail = _MODIFIER_ERRORS[exc.code]
        raise HTTPException(status_code=status, detail=detail)
    return ModifierOut.model_validate(modifier)


@router.patch("/modifiers/{modifier_id}", response_model=ModifierOut)
async def edit_modifier(modifier_id: uuid.UUID, payload: ModifierUpdateIn, ctx: OwnerCtx):
    from app.services.catalog import ModifierInvalid, update_modifier

    modifier = await ctx.session.get(Modifier, modifier_id)
    if modifier is None:
        raise HTTPException(status_code=404, detail="Pilihan tidak ditemukan")
    try:
        modifier = await update_modifier(ctx.session, modifier, **payload.model_dump(exclude_none=True))
    except ModifierInvalid as exc:
        status, detail = _MODIFIER_ERRORS[exc.code]
        raise HTTPException(status_code=status, detail=detail)
    return ModifierOut.model_validate(modifier)


@router.get("/expenses", response_model=ExpensesPage)
async def expenses(
    ctx: OwnerCtx,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
):
    total = (await ctx.session.execute(select(func.count(Expense.id)))).scalar_one()
    rows = (
        (
            await ctx.session.execute(
                select(Expense)
                .order_by(Expense.occurred_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return ExpensesPage(total=int(total), page=page, page_size=page_size, rows=rows)


@router.get("/pnl", response_model=list[PnlMonth])
async def pnl(ctx: OwnerCtx, months: int = Query(default=6, ge=1, le=12)):
    business = await _business(ctx)
    month_start, _, _ = period_range("this_month", business.timezone)
    tz = ZoneInfo(business.timezone)

    # First fencepost: (months-1) months before this month's start, local.
    first_local = month_start.astimezone(tz)
    for _ in range(months - 1):
        first_local = (first_local - timedelta(days=1)).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
    since = first_local.astimezone(timezone.utc)

    sale_month = func.to_char(func.timezone(business.timezone, Sale.sold_at), "YYYY-MM")
    expense_month = func.to_char(func.timezone(business.timezone, Expense.occurred_at), "YYYY-MM")

    # Two grouped queries on one session (a single AsyncSession is one DB
    # connection — queries on it are inherently sequential).
    revenue_rows = await ctx.session.execute(
        select(sale_month.label("m"), func.sum(Sale.total_price))
        .where(Sale.sold_at >= since)
        .group_by("m")
    )
    expense_rows = await ctx.session.execute(
        select(expense_month.label("m"), func.sum(Expense.amount))
        .where(Expense.occurred_at >= since)
        .group_by("m")
    )
    revenue_by_month = {m: float(v) for m, v in revenue_rows.all()}
    expenses_by_month = {m: float(v) for m, v in expense_rows.all()}

    series: list[PnlMonth] = []
    cursor = first_local
    for _ in range(months):
        key = cursor.strftime("%Y-%m")
        revenue = revenue_by_month.get(key, 0.0)
        spend = expenses_by_month.get(key, 0.0)
        series.append(PnlMonth(month=key, revenue=revenue, expenses=spend, net=revenue - spend))
        cursor = (cursor + timedelta(days=32)).replace(day=1)
    return series


@router.get("/alerts", response_model=list[AlertRow])
async def alerts(ctx: OwnerCtx, limit: int = Query(default=50, ge=1, le=200)):
    rows = (
        await ctx.session.execute(
            select(Alert, Item.name)
            .outerjoin(Item, Item.id == Alert.related_item_id)
            .order_by(Alert.created_at.desc())
            .limit(limit)
        )
    ).all()
    return [
        AlertRow(
            id=alert.id,
            type=alert.type,
            metric=alert.metric,
            severity=alert.severity,
            message=alert.message,
            related_item_name=item_name,
            is_acknowledged=alert.is_acknowledged,
            created_at=alert.created_at,
        )
        for alert, item_name in rows
    ]


@router.post("/alerts/{alert_id}/ack", response_model=AlertRow)
async def acknowledge_alert(alert_id: uuid.UUID, ctx: OwnerCtx):
    alert = await ctx.session.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Peringatan tidak ditemukan")
    alert.is_acknowledged = True
    item_name = None
    if alert.related_item_id:
        item = await ctx.session.get(Item, alert.related_item_id)
        item_name = item.name if item else None
    return AlertRow(
        id=alert.id,
        type=alert.type,
        metric=alert.metric,
        severity=alert.severity,
        message=alert.message,
        related_item_name=item_name,
        is_acknowledged=True,
        created_at=alert.created_at,
    )


@router.get("/receipts", response_model=ReceiptsPage)
async def receipts(
    ctx: OwnerCtx,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=12, ge=1, le=50),
):
    total = (await ctx.session.execute(select(func.count(Receipt.id)))).scalar_one()
    rows = (
        (
            await ctx.session.execute(
                select(Receipt)
                .order_by(Receipt.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    signed = await asyncio.gather(*(create_signed_url(r.image_url) for r in rows))
    return ReceiptsPage(
        total=int(total),
        page=page,
        page_size=page_size,
        rows=[
            ReceiptRow(
                id=r.id,
                supplier=r.supplier,
                total_amount=r.total_amount,
                occurred_at=r.occurred_at,
                created_at=r.created_at,
                image_signed_url=url,
                item_count=len((r.parsed_data or {}).get("items", [])),
            )
            for r, url in zip(rows, signed)
        ],
    )


@router.get("/business", response_model=BusinessOut)
async def get_business(ctx: OwnerCtx):
    return BusinessOut.model_validate(await _business(ctx))


@router.patch("/business", response_model=BusinessOut)
async def update_business(payload: BusinessUpdateIn, ctx: OwnerCtx):
    business = await _business(ctx)
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(business, field, value)
    await ctx.session.flush()
    return BusinessOut.model_validate(business)


# ── Customers (M8-T1) ───────────────────────────────────────────────────────

_CUSTOMER_ERRORS = {
    "name": (422, "Nama pelanggan tidak boleh kosong"),
    "phone": (422, "Nomor HP tidak valid — pakai 8–15 angka"),
    "duplicate_phone": (409, "Nomor HP ini sudah terdaftar atas pelanggan lain"),
    "not_found": (404, "Pelanggan tidak ditemukan"),
}


@router.get("/customers", response_model=CustomersPage)
async def list_customers(
    ctx: OwnerCtx, q: str = Query(default="", max_length=60), include_inactive: bool = Query(default=False),
    page: int = Query(default=1, ge=1), page_size: int = Query(default=30, ge=1, le=200),
):
    """Customers with their derived visits / spend / last visit (M8-T1)."""
    from app.models import Customer
    from app.services.customers import customer_views, search_customers

    matched = await search_customers(ctx.session, q, limit=10000, include_inactive=include_inactive)
    total = len(matched)
    start = (page - 1) * page_size
    rows = await customer_views(ctx.session, matched[start:start + page_size])
    return CustomersPage(total=total, page=page, page_size=page_size, rows=[CustomerOut(**r) for r in rows])


@router.post("/customers", response_model=CustomerOut, status_code=201)
async def add_customer(payload: CustomerCreateIn, ctx: OwnerCtx):
    from app.services.customers import CustomerInvalid, create_customer, customer_view

    try:
        row = await create_customer(ctx.session, ctx.business_id, **payload.model_dump())
    except CustomerInvalid as exc:
        status, detail = _CUSTOMER_ERRORS[exc.code]
        raise HTTPException(status_code=status, detail=detail)
    return CustomerOut(**await customer_view(ctx.session, row))


@router.patch("/customers/{customer_id}", response_model=CustomerOut)
async def edit_customer(customer_id: uuid.UUID, payload: CustomerUpdateIn, ctx: OwnerCtx):
    from app.models import Customer
    from app.services.customers import CustomerInvalid, customer_view, update_customer

    row = await ctx.session.get(Customer, customer_id)
    if row is None:
        status, detail = _CUSTOMER_ERRORS["not_found"]
        raise HTTPException(status_code=status, detail=detail)
    try:
        row = await update_customer(ctx.session, row, **payload.model_dump(exclude_unset=True))
    except CustomerInvalid as exc:
        status, detail = _CUSTOMER_ERRORS[exc.code]
        raise HTTPException(status_code=status, detail=detail)
    return CustomerOut(**await customer_view(ctx.session, row))


# ── Points (M8-T2) ──────────────────────────────────────────────────────────


@router.get("/loyalty-settings", response_model=LoyaltySettingsOut)
async def get_loyalty_settings(ctx: OwnerCtx):
    from app.services.points import ensure_loyalty_settings

    return LoyaltySettingsOut.model_validate(await ensure_loyalty_settings(ctx.session, ctx.business_id))


@router.patch("/loyalty-settings", response_model=LoyaltySettingsOut)
async def update_loyalty_settings(payload: LoyaltySettingsPatch, ctx: OwnerCtx):
    """Takes effect on the next sale. Balances already earned keep their points;
    a changed point value changes what they buy, which is what a warung means
    when it changes the programme."""
    from app.services.points import ensure_loyalty_settings

    row = await ensure_loyalty_settings(ctx.session, ctx.business_id)
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(row, field, value)
    row.updated_at = datetime.now(timezone.utc)
    await ctx.session.flush()
    return LoyaltySettingsOut.model_validate(row)


@router.get("/customers/{customer_id}/points", response_model=list[PointsMovementOut])
async def customer_points(customer_id: uuid.UUID, ctx: OwnerCtx, limit: int = Query(default=50, ge=1, le=500)):
    from app.models import Customer
    from app.services.points import list_points

    if await ctx.session.get(Customer, customer_id) is None:
        raise HTTPException(status_code=404, detail="Pelanggan tidak ditemukan")
    return [PointsMovementOut.model_validate(m) for m in await list_points(ctx.session, customer_id, limit=limit)]


@router.post("/customers/{customer_id}/points/adjust", response_model=CustomerOut)
async def adjust_customer_points(customer_id: uuid.UUID, payload: PointsAdjustIn, ctx: OwnerCtx):
    """A manual correction (M8-T2): a new ledger row, never an edit."""
    from app.models import Customer
    from app.services.customers import customer_view
    from app.services.points import InsufficientPoints, PointsInvalid, adjust_points

    row = await ctx.session.get(Customer, customer_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Pelanggan tidak ditemukan")
    try:
        await adjust_points(ctx.session, ctx.business_id, row, payload.points_delta, staff_id=ctx.staff_id, notes=payload.notes)
    except PointsInvalid:
        raise HTTPException(status_code=422, detail="Perubahan poin tidak boleh nol")
    except InsufficientPoints as exc:
        raise HTTPException(status_code=409, detail=f"Poin tidak cukup — tersedia {exc.available}")
    return CustomerOut(**await customer_view(ctx.session, row))


# ── Promos (M8-T3) ──────────────────────────────────────────────────────────

_PROMO_ERRORS = {
    "name": (422, "Nama promo tidak boleh kosong"),
    "kind": (422, "Jenis promo tidak dikenali"),
    "value": (422, "Nilai promo tidak valid (persen 0–100, rupiah lebih dari nol)"),
    "item": (422, "Barang promo tidak ditemukan"),
    "bonus_item": (422, "Barang bonus tidak ditemukan"),
    "bonus_quantity": (422, "Jumlah bonus harus lebih dari nol"),
    "max_per_order": (422, "Batas per struk harus lebih dari nol"),
    "condition": (422, "Syarat promo tidak lengkap atau tidak valid"),
    "not_found": (404, "Promo tidak ditemukan"),
}


async def _promo_out(session, promo) -> PromoOut:
    from app.models import PromoApplication
    from app.services.promos import promo_view

    n, given = (
        await session.execute(
            select(func.count(PromoApplication.id), func.coalesce(func.sum(PromoApplication.amount), 0))
            .where(PromoApplication.promo_id == promo.id)
        )
    ).one()
    return PromoOut(**await promo_view(session, promo), applications=int(n), given_away=Decimal(given))


@router.get("/promos", response_model=list[PromoOut])
async def list_promos(ctx: OwnerCtx, include_inactive: bool = Query(default=True)):
    from app.models import Promo

    stmt = select(Promo).order_by(Promo.is_active.desc(), Promo.name)
    if not include_inactive:
        stmt = stmt.where(Promo.is_active.is_(True))
    return [await _promo_out(ctx.session, p) for p in (await ctx.session.execute(stmt)).scalars()]


@router.post("/promos", response_model=PromoOut, status_code=201)
async def add_promo(payload: PromoCreateIn, ctx: OwnerCtx):
    from app.services.promos import PromoInvalid, create_promo

    data = payload.model_dump()
    conditions = data.pop("conditions")
    try:
        promo = await create_promo(ctx.session, ctx.business_id, conditions=conditions, **data)
    except PromoInvalid as exc:
        status, detail = _PROMO_ERRORS[exc.code]
        raise HTTPException(status_code=status, detail=detail)
    return await _promo_out(ctx.session, promo)


@router.patch("/promos/{promo_id}", response_model=PromoOut)
async def edit_promo(promo_id: uuid.UUID, payload: PromoUpdateIn, ctx: OwnerCtx):
    from app.models import Promo
    from app.services.promos import PromoInvalid, update_promo

    promo = await ctx.session.get(Promo, promo_id)
    if promo is None:
        status, detail = _PROMO_ERRORS["not_found"]
        raise HTTPException(status_code=status, detail=detail)
    data = payload.model_dump(exclude_unset=True)
    conditions = data.pop("conditions", None)
    try:
        promo = await update_promo(ctx.session, promo, conditions=conditions, **data)
    except PromoInvalid as exc:
        status, detail = _PROMO_ERRORS[exc.code]
        raise HTTPException(status_code=status, detail=detail)
    return await _promo_out(ctx.session, promo)


@router.get("/pricing-settings", response_model=PricingSettingsOut)
async def get_pricing_settings(ctx: OwnerCtx):
    """How this business builds a bill (M7-T4b): tax, service charge, rupiah
    rounding, and whether a discount needs the manager PIN."""
    from app.services.pricing import ensure_pricing_settings

    return PricingSettingsOut.model_validate(await ensure_pricing_settings(ctx.session, ctx.business_id))


@router.patch("/pricing-settings", response_model=PricingSettingsOut)
async def update_pricing_settings(payload: PricingSettingsPatch, ctx: OwnerCtx):
    """Takes effect on the next sale; nothing already sold is repriced, and a
    refund undoes what its sale posted, not what today's settings would."""
    from app.services.pricing import ensure_pricing_settings

    row = await ensure_pricing_settings(ctx.session, ctx.business_id)
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(row, field, value)
    row.updated_at = datetime.now(timezone.utc)
    await ctx.session.flush()
    return PricingSettingsOut.model_validate(row)


@router.post("/business/complete-onboarding", response_model=BusinessOut)
async def complete_onboarding(ctx: OwnerCtx):
    business = await _business(ctx)
    if business.onboarding_completed_at is None:
        business.onboarding_completed_at = datetime.now(timezone.utc)
    return BusinessOut.model_validate(business)


CATALOG_IMPORT_MAX_BYTES = 5 * 1024 * 1024


@router.post("/catalog-import")
async def catalog_import(request: Request, ctx: OwnerCtx):
    """Bulk catalogue import (M4-T6): one .xlsx with Barang / Varian / Pilihan /
    Satuan / Konversi / Resep sheets, sent as the raw request body. Validated in
    full first; any bad row means nothing is written and every bad row is named."""
    from app.services.catalog_import import CatalogImportInvalid, import_catalog
    from app.services.stock_import import StockTemplateError

    data = await request.body()
    if not data:
        raise HTTPException(status_code=422, detail="File belum dipilih — kirim berkas Excel katalog")
    if len(data) > CATALOG_IMPORT_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Berkas terlalu besar — maksimal 5 MB, coba pisahkan menjadi beberapa berkas")
    business = await _business(ctx)
    try:
        result = await import_catalog(ctx.session, business, data)
    except StockTemplateError as exc:
        reason = str(exc).split(":", 1)[0]
        messages = {
            "unreadable_file": "Berkas tidak bisa dibaca — pastikan formatnya Excel (.xlsx)",
            "missing_sheets": "Tidak ada sheet yang dikenali — pakai template katalog (Barang, Varian, Pilihan, Satuan, Konversi, Resep)",
            "missing_columns": "Kolom wajib tidak ditemukan — pakai template katalog agar judul kolomnya cocok",
            "no_rows": "Tidak ada baris barang yang bisa dibaca di berkas ini",
        }
        raise HTTPException(status_code=422, detail=messages.get(reason, "Berkas tidak bisa dibaca — pakai template katalog"))
    except CatalogImportInvalid as exc:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Impor dibatalkan — {len(exc.errors)} baris bermasalah, tidak ada yang disimpan. "
                + " | ".join(exc.errors)
            ),
        )
    return result


@router.get("/catalog-template")
async def catalog_template(ctx: OwnerCtx):
    """Downloadable multi-sheet starter for the catalogue import (M4-T6)."""
    import pandas as pd

    sheets = {
        "Barang": pd.DataFrame({
            "Nama Barang": ["Es Kopi Susu", "Biji Arabica", "Susu UHT"],
            "Jumlah": [0, 8, 24], "Satuan": ["cup", "kg", "liter"],
            "Harga Modal": [8000, 145000, 17000], "Harga Jual": [22000, 0, 0], "Batas Minimum": [0, 3, 10],
        }),
        "Varian": pd.DataFrame({
            "Barang": ["Es Kopi Susu"], "Varian": ["Large"], "Harga Jual": [27000], "Harga Modal": [10000],
            "SKU": [""], "Utama": ["tidak"],
        }),
        "Pilihan": pd.DataFrame({
            "Barang": ["Es Kopi Susu"] * 4, "Kelompok": ["Gula", "Gula", "Tambahan", "Tambahan"],
            "Jenis": ["single", "single", "multi", "multi"], "Wajib": ["ya", "ya", "tidak", "tidak"],
            "Pilihan": ["Normal", "Sedikit gula", "Extra shot", "Susu oat"],
            "Tambahan Harga": [0, 0, 5000, 6000], "Default": ["ya", "", "", ""],
        }),
        "Satuan": pd.DataFrame({"Kode": ["karung"], "Nama": ["karung 25 kg"]}),
        "Konversi": pd.DataFrame({"Dari": ["karung"], "Ke": ["kg"], "Faktor": [25]}),
        "Resep": pd.DataFrame({
            "Barang": ["Es Kopi Susu", "Es Kopi Susu", "Es Kopi Susu", "Es Kopi Susu"],
            "Varian": ["Standar", "Standar", "Large", "Large"],
            "Bahan": ["Biji Arabica", "Susu UHT", "Biji Arabica", "Susu UHT"],
            "Jumlah": [18, 120, 24, 180], "Satuan": ["g", "ml", "g", "ml"],
        }),
    }
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as xw:
        for name, df in sheets.items():
            df.to_excel(xw, index=False, sheet_name=name)
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="template-katalog.xlsx"'},
    )


@router.get("/stock-template")
async def stock_template(ctx: OwnerCtx):
    """Downloadable Excel starter for the WhatsApp stock import."""
    import pandas as pd

    df = pd.DataFrame(
        {
            "Nama Barang": ["Biji Arabica", "Gula Aren", "Susu UHT"],
            "Jumlah": [8, 5, 24],
            "Satuan": ["kg", "kg", "liter"],
            "Harga Modal": [145000, 38000, 17000],
            "Harga Jual": [0, 0, 0],
            "Batas Minimum": [3, 2, 10],
        }
    )
    buffer = io.BytesIO()
    df.to_excel(buffer, index=False, sheet_name="Stok")
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="template-stok.xlsx"'},
    )


# ── Shifts (M7-T1) ──────────────────────────────────────────────────────────


@router.get("/shifts")
async def list_shifts(ctx: OwnerCtx, limit: int = Query(default=30, ge=1, le=200)):
    """Newest first; open shifts show their live expected cash."""
    from app.schemas.pos import ShiftOut
    from app.services.shifts import list_shifts as _list, shift_view

    return [ShiftOut(**await shift_view(ctx.session, sh)) for sh in await _list(ctx.session, limit=limit)]


@router.get("/cash-movements")
async def list_cash_movements(ctx: OwnerCtx, limit: int = Query(default=50, ge=1, le=500)):
    """Cash in and out (M7-T2), newest first."""
    from app.schemas.pos import CashMovementOut
    from app.services.cash import cash_movement_view, list_cash_movements as _list

    return [CashMovementOut(**await cash_movement_view(ctx.session, r)) for r in await _list(ctx.session, limit=limit)]
