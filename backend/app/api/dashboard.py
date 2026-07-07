"""Owner dashboard API — every route requires scope="owner" (OwnerCtx), every
query runs in the tenant-pinned session. Aggregations are single grouped
queries (no per-row loops against the DB); list views are paginated.
"""
import asyncio
import io
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from app.ai.periods import period_range
from app.core.deps import OwnerCtx
from app.models import Alert, Business, Expense, Item, Receipt, Sale, Staff
from app.schemas.dashboard import (
    AlertRow,
    BusinessUpdateIn,
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
)
from app.schemas.auth import BusinessOut
from app.services.velocity import VELOCITY_WINDOW_DAYS
from app.whatsapp.storage import create_signed_url

router = APIRouter(prefix="/api", tags=["dashboard"])


async def _business(ctx) -> Business:
    business = await ctx.session.get(Business, ctx.business_id)
    if business is None:
        raise HTTPException(status_code=404, detail="Usaha tidak ditemukan")
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
async def sales_trend(ctx: OwnerCtx, days: int = Query(default=30, ge=7, le=90)):
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
    ctx.session.add(item)
    await ctx.session.flush()
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
    for field, value in changes.items():
        setattr(item, field, value)
    if changes:
        item.updated_at = datetime.now(timezone.utc)
    await ctx.session.flush()
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


@router.post("/business/complete-onboarding", response_model=BusinessOut)
async def complete_onboarding(ctx: OwnerCtx):
    business = await _business(ctx)
    if business.onboarding_completed_at is None:
        business.onboarding_completed_at = datetime.now(timezone.utc)
    return BusinessOut.model_validate(business)


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
