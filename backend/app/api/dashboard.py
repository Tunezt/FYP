"""Owner dashboard API — every route requires scope="owner" (OwnerCtx), every
query runs in the tenant-pinned session. List views are paginated.

Since M9-T3 every *figure* on the dashboard — overview tiles, the sales trend,
the monthly P&L chart, stock velocity — comes from the metric registry
(app/metrics), the same implementation the WhatsApp assistant reads. The
endpoints here shape the result for a widget; they do not compute it.
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
from sqlalchemy.orm import aliased

from app.ai.periods import business_day, day_bounds
from app.core.deps import OwnerCtx
from app.models import (
    Account, Alert, Approval, Business, Expense, GoodsReceipt, Item, ItemVariant, Modifier, ModifierGroup, PoLine, PostingRule,
    PurchaseOrder, Receipt, RecipeLine, Sale, Staff, Supplier, Uom, UomConversion,
)
from app.schemas.pos import OrderSummaryOut, OrdersPage, ReceiptOut, RefundIn, ReversalIn, ReversalOut
from app.schemas.dashboard import (
    ApprovalRow,
    BackdatedSaleIn,
    PinLockoutRow,
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
    MetricSpecOut,
    MetricValueOut,
    LoyaltySettingsPatch,
    PointsAdjustIn,
    PointsMovementOut,
    PromoCreateIn,
    PromoOut,
    PromoUpdateIn,
    VoucherCreateIn,
    VoucherOut,
    VoucherUpdateIn,
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
    from app.metrics import compute

    today_revenue = await compute(ctx.session, business, "revenue", period="today")
    today_tx = await compute(ctx.session, business, "transaction_count", period="today")
    yesterday_revenue = await compute(ctx.session, business, "revenue", period="yesterday")
    month_revenue = await compute(ctx.session, business, "revenue", period="this_month")
    month_expenses = await compute(ctx.session, business, "expense_total", period="this_month")
    stock = await compute(ctx.session, business, "stock_on_hand")
    unacked = (
        await ctx.session.execute(
            select(func.count(Alert.id)).where(Alert.is_acknowledged.is_(False))
        )
    ).scalar_one()

    return OverviewOut(
        business_name=business.name,
        today_revenue=float(today_revenue.value or 0),
        today_transactions=int(today_tx.value or 0),
        yesterday_revenue=float(yesterday_revenue.value or 0),
        month_revenue=float(month_revenue.value or 0),
        month_expenses=float(month_expenses.value or 0),
        month_net=float(month_revenue.value or 0) - float(month_expenses.value or 0),
        unacknowledged_alerts=int(unacked),
        low_stock_items=sum(1 for r in stock.rows if r["below_reorder_threshold"]),
        onboarding_completed=business.onboarding_completed_at is not None,
    )


@router.get("/sales-trend", response_model=list[TrendPoint])
async def sales_trend(ctx: OwnerCtx, days: int = Query(default=30, ge=1, le=730)):
    """Revenue and order count per business-local day, dense (zero-filled so
    charts do not skip quiet days), each day the registry's own number.

    The ceiling is two years, not one: the dashboard asks for twice the window
    it draws so it can show "vs the period before". At the 365-day tab that is
    730, and the old 365 ceiling rejected the request outright, which is how
    "Tahun Ini" came to draw an empty chart."""
    business = await _business(ctx)
    from app.metrics import local_day_windows, series

    windows = local_day_windows(business, days)
    revenue = await series(ctx.session, business, "revenue", windows)
    orders = await series(ctx.session, business, "transaction_count", windows)
    return [
        TrendPoint(date=key, revenue=float(r.value or 0), transactions=int(o.value or 0))
        for (key, r), (_k, o) in zip(revenue, orders)
    ]


def _day_range_filters(column, business, since: date | None, until: date | None) -> list:
    """[since, until] as *business* days (M15-T4), both ends inclusive, on any
    timestamp column. One day means that whole day, from its start hour to the
    next — the same boundary the day headers and the metric layer use."""
    filters = []
    if since is not None:
        filters.append(column >= day_bounds(since, business.timezone, business.day_start_hour)[0])
    if until is not None:
        filters.append(column < day_bounds(until, business.timezone, business.day_start_hour)[1])
    return filters


@router.get("/sales", response_model=SalesPage)
async def sales_history(
    ctx: OwnerCtx,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    since: date | None = Query(default=None, description="business day, inclusive"),
    until: date | None = Query(default=None, description="business day, inclusive"),
    staff_id: uuid.UUID | None = Query(default=None),
):
    """Filters are the owner's own words: a date range in *business* days
    (M15-T4, so a 00:15 bill filters under the night before) and one cashier.
    `until` is inclusive — "1 Sep to 1 Sep" is that whole day, not nothing."""
    business = await _business(ctx)
    filters = _day_range_filters(Sale.sold_at, business, since, until)
    if staff_id is not None:
        filters.append(Sale.staff_id == staff_id)

    total = (await ctx.session.execute(select(func.count(Sale.id)).where(*filters))).scalar_one()
    rows = (
        await ctx.session.execute(
            select(Sale, Item.name, Staff.name)
            .join(Item, Item.id == Sale.item_id)
            .join(Staff, Staff.id == Sale.staff_id)
            .where(*filters)
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
    """Every item with the registry's stock velocity (one grouped query inside
    `stock_days_remaining`); prices come from the item row."""
    business = await _business(ctx)
    from app.metrics import compute

    reading = await compute(ctx.session, business, "stock_days_remaining")
    by_id = {r["item_id"]: r for r in reading.rows}
    items = (await ctx.session.execute(select(Item).order_by(Item.name))).scalars().all()
    out: list[InventoryItem] = []
    for item in items:
        r = by_id.get(item.id, {})
        daily = r.get("daily_usage") or 0.0
        out.append(
            InventoryItem(
                id=item.id,
                name=item.name,
                unit=item.unit,
                current_stock=item.current_stock,
                cost_price=item.cost_price,
                sell_price=item.sell_price,
                reorder_threshold=item.reorder_threshold,
                avg_daily_usage=round(daily, 3) if daily > 0 else None,
                days_remaining=r.get("days_remaining"),
                below_reorder_threshold=bool(r.get("below_reorder_threshold", item.current_stock <= item.reorder_threshold)),
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


def _local_day_bounds(tz: ZoneInfo, day: date, day_start_hour: int = 0) -> tuple[datetime, datetime]:
    """[start, next day start) of one business day, in UTC. `day_start_hour`
    (M15-T4) moves the boundary off midnight, so a statement covering "3 Sept"
    for a 4am café runs 03/09 04:00 → 04/09 04:00 and includes the bill settled
    at 00:15 that night."""
    start = datetime.combine(day, time(hour=day_start_hour), tzinfo=tz)
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
    until = until or business_day(datetime.now(timezone.utc), business.timezone, business.day_start_hour)
    since = since or until.replace(day=1)
    if until < since:
        raise HTTPException(status_code=422, detail="Tanggal akhir tidak boleh sebelum tanggal awal")
    start_utc, _ = _local_day_bounds(tz, since, business.day_start_hour)
    _, end_utc = _local_day_bounds(tz, until, business.day_start_hour)
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
    as_of = as_of or business_day(datetime.now(timezone.utc), business.timezone, business.day_start_hour)
    _, end_utc = _local_day_bounds(tz, as_of, business.day_start_hour)
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
    since: date | None = Query(default=None, description="business day, inclusive"),
    until: date | None = Query(default=None, description="business day, inclusive"),
    category: str | None = Query(default=None),
):
    """Filtered in SQL for the same reason as /sales: the owner asking "what
    did we spend on bahan baku last week" must not get the answer computed from
    whichever 25 rows this page happens to hold."""
    business = await _business(ctx)
    filters = _day_range_filters(Expense.occurred_at, business, since, until)
    if category:
        filters.append(Expense.category == category)

    total = (await ctx.session.execute(select(func.count(Expense.id)).where(*filters))).scalar_one()
    rows = (
        (
            await ctx.session.execute(
                select(Expense)
                .where(*filters)
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
    """Revenue and recorded expenses per business-local month, each the
    registry's own number; `net` is their difference (the operating view the
    money page has always shown — the accounting P&L is /api/statements)."""
    business = await _business(ctx)
    from app.metrics import local_month_windows, series

    windows = local_month_windows(business, months)
    revenue = await series(ctx.session, business, "revenue", windows)
    spend = await series(ctx.session, business, "expense_total", windows)
    return [
        PnlMonth(month=key, revenue=float(r.value or 0), expenses=float(e.value or 0), net=float(r.value or 0) - float(e.value or 0))
        for (key, r), (_k, e) in zip(revenue, spend)
    ]


@router.get("/alerts", response_model=list[AlertRow])
async def alerts(
    ctx: OwnerCtx,
    limit: int = Query(default=50, ge=1, le=200),
    since: date | None = Query(default=None, description="business day, inclusive"),
    until: date | None = Query(default=None, description="business day, inclusive"),
    severity: str | None = Query(default=None),
):
    business = await _business(ctx)
    filters = _day_range_filters(Alert.created_at, business, since, until)
    if severity:
        filters.append(Alert.severity == severity)
    rows = (
        await ctx.session.execute(
            select(Alert, Item.name)
            .outerjoin(Item, Item.id == Alert.related_item_id)
            .where(*filters)
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


# ── Vouchers (M8-T4) ────────────────────────────────────────────────────────

_VOUCHER_ERRORS = {
    "code": (422, "Kode voucher minimal 3 karakter (huruf, angka, tanda minus)"),
    "kind": (422, "Jenis voucher tidak dikenali"),
    "value": (422, "Nilai voucher tidak valid (persen 0–100, rupiah lebih dari nol)"),
    "max_discount": (422, "Batas potongan harus lebih dari nol"),
    "min_spend": (422, "Minimal belanja tidak boleh negatif"),
    "dates": (422, "Tanggal kedaluwarsa harus setelah tanggal mulai"),
    "max_uses": (422, "Jumlah pemakaian harus lebih dari nol dan tidak kurang dari yang sudah terpakai"),
    "count": (422, "Jumlah kode harus 1–1000 (satu kode tertentu hanya bisa dibuat satu)"),
    "duplicate": (409, "Kode voucher ini sudah ada"),
    "not_found": (404, "Voucher tidak ditemukan"),
}


@router.get("/vouchers", response_model=list[VoucherOut])
async def list_vouchers_endpoint(
    ctx: OwnerCtx, q: str = Query(default="", max_length=40), batch_id: uuid.UUID | None = None,
    include_inactive: bool = Query(default=True), limit: int = Query(default=200, ge=1, le=1000),
):
    from app.services.vouchers import list_vouchers

    rows = await list_vouchers(ctx.session, q_text=q or None, batch_id=batch_id, include_inactive=include_inactive, limit=limit)
    return [VoucherOut.model_validate(v) for v in rows]


@router.post("/vouchers", response_model=list[VoucherOut], status_code=201)
async def add_vouchers(payload: VoucherCreateIn, ctx: OwnerCtx):
    """One code or a batch of generated codes (M8-T4). Returns every code made."""
    from app.services.vouchers import VoucherInvalid, create_vouchers

    try:
        rows = await create_vouchers(ctx.session, ctx.business_id, **payload.model_dump())
    except VoucherInvalid as exc:
        status, detail = _VOUCHER_ERRORS[exc.code]
        raise HTTPException(status_code=status, detail=detail)
    return [VoucherOut.model_validate(v) for v in rows]


@router.patch("/vouchers/{voucher_id}", response_model=VoucherOut)
async def edit_voucher(voucher_id: uuid.UUID, payload: VoucherUpdateIn, ctx: OwnerCtx):
    from app.models import Voucher
    from app.services.vouchers import VoucherInvalid, update_voucher

    row = await ctx.session.get(Voucher, voucher_id)
    if row is None:
        status, detail = _VOUCHER_ERRORS["not_found"]
        raise HTTPException(status_code=status, detail=detail)
    try:
        row = await update_voucher(ctx.session, row, **payload.model_dump(exclude_unset=True))
    except VoucherInvalid as exc:
        status, detail = _VOUCHER_ERRORS[exc.code]
        raise HTTPException(status_code=status, detail=detail)
    return VoucherOut.model_validate(row)


# ── Metric layer (M9-T1) ────────────────────────────────────────────────────
#
# The one place numbers come from. M9-T2 points the assistant's tools here and
# M9-T3 the dashboard's widgets, so both read the same implementation.


@router.get("/metrics", response_model=list[MetricSpecOut])
async def metric_catalogue(ctx: OwnerCtx):
    from app.metrics import list_metrics

    return [MetricSpecOut(**m.describe()) for m in list_metrics()]


@router.get("/metrics/{name}", response_model=MetricValueOut)
async def metric_value(
    name: str, ctx: OwnerCtx, period: str | None = Query(default=None), since: datetime | None = None,
    until: datetime | None = None, item_id: uuid.UUID | None = None, limit: int = Query(default=5, ge=1, le=100),
):
    """One metric over a named period (in the business's timezone) or an
    explicit [since, until). Instant metrics ignore the window."""
    from app.metrics import MetricNotFound, compute
    from app.metrics.registry import MetricArgumentInvalid

    business = await _business(ctx)
    try:
        result = await compute(ctx.session, business, name, period=period, since=since, until=until, item_id=item_id, limit=limit)
    except MetricNotFound:
        raise HTTPException(status_code=404, detail=f"Metrik '{name}' tidak dikenali")
    except MetricArgumentInvalid as exc:
        messages = {
            "period": "Periode tidak dikenali",
            "range": "Rentang waktu tidak valid — isi since dan until, dengan until setelah since",
            "dimension": "Metrik ini tidak bisa difilter per barang",
        }
        raise HTTPException(status_code=422, detail=messages.get(exc.code, "Permintaan metrik tidak valid"))
    return MetricValueOut(**result.as_dict())


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


# ── Orders: find one, read it, reverse it (M15-T11) ─────────────────────────


@router.get("/orders", response_model=OrdersPage)
async def owner_orders(
    ctx: OwnerCtx,
    q: str = Query(default="", max_length=40),
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    """Every sale, newest first, searchable by receipt number — the owner's way
    into a mistake found after the shift closed (M15-T11). The till's own list
    (`GET /pos/orders`) stops at today; this one does not, because that is the
    difference the two screens exist for."""
    from app.schemas.pos import OrderSummaryOut
    from app.services.orders import list_orders

    rows, total = await list_orders(ctx.session, q=q, limit=limit, offset=offset)
    return OrdersPage(total=total, rows=[OrderSummaryOut(**vars(r)) for r in rows])


@router.get("/orders/{order_id}/receipt", response_model=ReceiptOut)
async def owner_receipt(order_id: uuid.UUID, ctx: OwnerCtx):
    """The same receipt the cashier printed, shaped by the same function — the
    owner deciding whether to void must not be looking at a different document."""
    from app.api.pos import receipt_view

    return await receipt_view(ctx.session, ctx.business_id, order_id)


@router.post("/orders/{order_id}/void", response_model=ReversalOut)
async def owner_void_order(order_id: uuid.UUID, payload: ReversalIn, ctx: OwnerCtx):
    """Manager-PIN gated, exactly like the till (M15-T11).

    The owner is already authenticated, so the PIN is not proving who they are.
    It is doing two other things: it stops a dashboard left open on an
    unattended laptop from reversing a sale with one click, and it produces the
    `approvals` row (M15-T7) that names a person rather than a session."""
    from app.api.pos import run_reversal
    from app.services.orders import void_order

    return await run_reversal(
        ctx, order_id, void_order, "/api/orders/{id}/void", channel="dashboard",
        manager_pin=payload.manager_pin, note=payload.note,
    )


@router.post("/orders/{order_id}/refund", response_model=ReversalOut)
async def owner_refund_order(order_id: uuid.UUID, payload: RefundIn, ctx: OwnerCtx):
    """Like void, but `restock=false` keeps stock down when the goods are not
    coming back (eaten, spoiled, thrown away)."""
    from app.api.pos import run_reversal
    from app.services.orders import refund_order

    return await run_reversal(
        ctx, order_id, refund_order, "/api/orders/{id}/refund", channel="dashboard",
        manager_pin=payload.manager_pin, note=payload.note, restock=payload.restock,
    )


# ── PIN brute-force protection (M15-T12) ────────────────────────────────────

_SCOPE_LABEL = {
    "pos_login": "PIN kasir",
    "pos_device": "Perangkat kasir",
    "manager_pin": "PIN manajer",
}


async def _lockout_who(ctx: OwnerCtx, row) -> str:
    """The subject as a person, not a key. `staff:<uuid>` in a list the owner is
    supposed to act on is not information."""
    kind, _, value = row.subject.partition(":")
    if kind in ("staff", "asked_by"):
        try:
            staff = await ctx.session.get(Staff, uuid.UUID(value))
        except ValueError:
            staff = None
        name = staff.name if staff is not None else "staf yang sudah dihapus"
        return name if kind == "staff" else f"diminta oleh {name}"
    return "perangkat kasir ini"


@router.get("/pin-lockouts", response_model=list[PinLockoutRow])
async def list_pin_lockouts(ctx: OwnerCtx):
    """Who is currently being throttled, and how close they are (M15-T12).

    Only subjects still inside the counting window are listed — an expired
    counter is history, and the history lives in `request_logs` where every
    single failure is recorded whether or not it ever reached a threshold."""
    from app.services.pin_guard import active

    now = datetime.now(timezone.utc)
    rows = await active(ctx.session, now=now)
    return [
        PinLockoutRow(
            id=row.id, scope=row.scope, who=await _lockout_who(ctx, row), failures=row.failures,
            first_failed_at=row.first_failed_at, last_failed_at=row.last_failed_at,
            locked_until=row.locked_until,
            locked_now=row.locked_until is not None and row.locked_until > now,
        )
        for row in rows
    ]


@router.delete("/pin-lockouts/{lockout_id}", status_code=204)
async def clear_pin_lockout(lockout_id: uuid.UUID, ctx: OwnerCtx):
    """Let somebody back in now. A cashier locked out of their own till at the
    start of a rush cannot wait fifteen minutes, and the owner is the person who
    can tell "forgot their PIN" from "trying everyone else's"."""
    from app.models import PinAttempt

    row = await ctx.session.get(PinAttempt, lockout_id)
    if row is None or row.business_id != ctx.business_id:
        raise HTTPException(status_code=404, detail="Data percobaan PIN tidak ditemukan")
    await ctx.session.delete(row)
    await ctx.session.flush()
    return None


# ── Backdated sale entry (M15-T10) ──────────────────────────────────────────

_BACKDATED_ERRORS = {
    "future": (422, "Tanggal penjualan tidak boleh di masa depan"),
    "too_old": (422, "Penjualan lebih dari 60 hari lalu tidak bisa dicatat di sini — hubungi pengembang"),
    "before_business": (422, "Tanggal ini sebelum usaha ini terdaftar"),
    "staff": (422, "Kasir tidak dikenali atau sudah tidak aktif"),
}

BACKDATE_MAX_DAYS = 60


@router.post("/backdated-sales", response_model=OrderSummaryOut, status_code=201)
async def record_backdated_sale(payload: BackdatedSaleIn, ctx: OwnerCtx):
    """Enter a sale that happened on paper (M15-T10).

    The offline queue (M14) covers a lost connection. It does not cover a lost
    *device*: one tablet is one point of failure, and when it dies mid-service
    the staff keep selling on paper. Those sales still have to reach the books,
    at the time they actually happened — otherwise the day's takings are wrong,
    the shift reconciliation is wrong, and the stock is wrong by however many
    cups were poured.

    It posts through the ordinary engine. Nothing here is a special case: the
    same pricing, the same atomic stock guard, the same journal entry, the same
    points. The single difference is `entry_source = 'manual_backdated'`, which
    says a human chose the timestamp rather than the clock — visible in the
    audit trail, and available to anything that wants to treat it differently.

    Owner-only, because choosing a sale's timestamp is exactly the power that
    would let someone move takings between days."""
    from app.services.customers import CustomerInvalid
    from app.services.orders import EmptyOrder, OrderLineSpec, PaymentSpec, create_order
    from app.services.sales import InsufficientStock, ItemNotFound

    business = await _business(ctx)
    now = datetime.now(timezone.utc)
    sold_at = payload.sold_at
    if sold_at.tzinfo is None:
        sold_at = sold_at.replace(tzinfo=timezone.utc)
    if sold_at > now:
        raise _backdated_error("future")
    if (now - sold_at).days > BACKDATE_MAX_DAYS:
        raise _backdated_error("too_old")
    if sold_at < business.created_at:
        raise _backdated_error("before_business")

    staff = await ctx.session.get(Staff, payload.staff_id)
    if staff is None or staff.business_id != ctx.business_id or not staff.is_active:
        raise _backdated_error("staff")

    # The note rides on the lines, which is the only free-text the order model
    # has and which is where a reversal already writes its own explanation.
    note = (payload.note or "").strip() or None
    lines = [
        OrderLineSpec(item_id=l.item_id, variant_id=l.variant_id, quantity=l.quantity, notes=note)
        for l in payload.lines
    ]
    # Price it first so the payment matches to the rupiah: a paper sale has no
    # keypad to disagree with, and a mismatch here would be an error the owner
    # cannot act on.
    quote = await _quote_total(ctx, lines)
    try:
        created = await create_order(
            ctx.session, business_id=ctx.business_id, staff_id=staff.id, lines=lines,
            payments=[PaymentSpec(method=payload.payment_method, amount=quote)],
            sold_at=sold_at, customer_id=payload.customer_id, entry_source="manual_backdated",
        )
    except EmptyOrder:
        raise HTTPException(status_code=422, detail="Penjualan harus punya minimal satu barang")
    except InsufficientStock as exc:
        # A sale from paper is not a licence to go below zero: the same atomic
        # guard applies, and the whole entry rolls back rather than half-landing.
        raise HTTPException(
            status_code=409, detail=f"Stok tidak cukup — {exc.item_name} tersisa {exc.available}"
        )
    except ItemNotFound:
        raise HTTPException(status_code=404, detail="Barang tidak ditemukan")
    except CustomerInvalid:
        raise HTTPException(status_code=404, detail="Pelanggan tidak ditemukan atau sudah tidak aktif")

    order = created.order
    from app.services.orders import order_number

    return OrderSummaryOut(
        id=order.id, number=order_number(order.id), sold_at=order.sold_at, status=order.status,
        order_type=order.order_type, total=order.total, line_count=len(created.lines),
        staff_name=staff.name, customer_name=None, table_label=None, entry_source=order.entry_source,
    )


def _backdated_error(code: str) -> HTTPException:
    status, detail = _BACKDATED_ERRORS[code]
    return HTTPException(status_code=status, detail=detail)


async def _quote_total(ctx: OwnerCtx, lines) -> Decimal:
    """What the till would have charged for these lines, priced by the one
    pricing service (M7-T4b) so a paper sale and a rung-up sale agree."""
    from app.services.catalog import default_variant
    from app.services.pricing import LineInput, price_order, pricing_config

    config = await pricing_config(ctx.session, ctx.business_id)
    inputs = []
    for spec in lines:
        item = await ctx.session.get(Item, spec.item_id)
        if item is None or item.business_id != ctx.business_id:
            raise HTTPException(status_code=404, detail="Barang tidak ditemukan")
        variant = (
            await ctx.session.get(ItemVariant, spec.variant_id)
            if spec.variant_id else await default_variant(ctx.session, item.id)
        )
        price = Decimal(variant.sell_price) if variant is not None else Decimal(item.sell_price)
        inputs.append(LineInput(unit_price=price, quantity=Decimal(spec.quantity)))
    return price_order(inputs, config, order_type="takeaway").total


# ── Override audit trail (M15-T7) ───────────────────────────────────────────


@router.get("/approvals", response_model=list[ApprovalRow])
async def list_approvals(
    ctx: OwnerCtx,
    limit: int = Query(default=50, ge=1, le=200),
    action: str | None = Query(default=None),
):
    """Every void, refund and manager-approved discount, newest first: who
    approved it, what role they held at the time, who asked, and what it was
    worth. An override the owner cannot read afterwards is not an audit trail."""
    if action is not None and action not in ("discount", "void", "refund"):
        raise HTTPException(status_code=422, detail="Jenis otorisasi tidak dikenali")
    approver = aliased(Staff)
    requester = aliased(Staff)
    stmt = (
        select(Approval, approver.name, requester.name)
        .join(approver, approver.id == Approval.approved_by)
        .outerjoin(requester, requester.id == Approval.requested_by)
        .order_by(Approval.created_at.desc(), Approval.id)
        .limit(limit)
    )
    if action is not None:
        stmt = stmt.where(Approval.action == action)
    return [
        ApprovalRow(
            id=a.id, order_id=a.order_id, action=a.action, approved_by=a.approved_by,
            approver_name=approver_name, approver_role=a.approver_role,
            requested_by=a.requested_by, requested_by_name=requester_name,
            amount=a.amount, note=a.note, created_at=a.created_at,
        )
        for a, approver_name, requester_name in (await ctx.session.execute(stmt)).all()
    ]


@router.get("/cash-movements")
async def list_cash_movements(ctx: OwnerCtx, limit: int = Query(default=50, ge=1, le=500)):
    """Cash in and out (M7-T2), newest first."""
    from app.schemas.pos import CashMovementOut
    from app.services.cash import cash_movement_view, list_cash_movements as _list

    return [CashMovementOut(**await cash_movement_view(ctx.session, r)) for r in await _list(ctx.session, limit=limit)]
