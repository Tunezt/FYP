"""The standard metrics (roadmap M9-T1), one implementation each.

The basis, stated once so every consumer inherits it:

  * Sales figures read the `sales` view — one row per order line, reversing
    lines included — so a void nets to zero and a refund shows as a negative
    line in its own period. `revenue` is the sum of line totals (list price
    net of the cashier's line discount); bill-level discounts, promos,
    vouchers, tax and service charge are their own metrics or the ledger's
    business. This is the basis the assistant and the dashboard have used
    since M3, and M9-T2/T3 must be zero-diff refactors onto it.
  * Cost of goods is the cost snapshotted on each line at sale time (M4-T5);
    a pre-order line without a snapshot falls back to today's cost, and the
    result says how many such lines there were.
  * `transaction_count` counts orders, not lines, and not voids.
  * Instant metrics (stock on hand, days remaining) describe now.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import case, desc, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.metrics.registry import MetricContext, MetricResult, metric
from app.models import (
    Customer, Expense, GoodsReceipt, GoodsReceiptLine, Item, ItemVariant, Order, OrderLine, Promo, PromoApplication, Sale, Shift,
    StockMovement, Supplier,
)

MONEY = Decimal("0.01")
PCT = Decimal("0.1")


def _money(v) -> Decimal:
    return Decimal(v or 0).quantize(MONEY, rounding=ROUND_HALF_UP)


def _in_window(ctx: MetricContext):
    return (Sale.sold_at >= ctx.since, Sale.sold_at < ctx.until)


def _item_filter(ctx: MetricContext, column):
    return [column == ctx.item_id] if ctx.item_id is not None else []


# ── sales ───────────────────────────────────────────────────────────────────


async def _revenue(session: AsyncSession, ctx: MetricContext) -> Decimal:
    return _money((await session.execute(
        select(func.coalesce(func.sum(Sale.total_price), 0)).where(*_in_window(ctx), *_item_filter(ctx, Sale.item_id))
    )).scalar_one())


async def _cogs(session: AsyncSession, ctx: MetricContext) -> tuple[Decimal, int]:
    cogs, unknown = (await session.execute(
        select(
            func.coalesce(func.sum(Sale.quantity * func.coalesce(OrderLine.unit_cost_at_sale, Item.cost_price)), 0),
            func.count(Sale.id).filter(OrderLine.unit_cost_at_sale.is_(None)),
        )
        .join(OrderLine, OrderLine.id == Sale.id)
        .join(Item, Item.id == Sale.item_id)
        .where(*_in_window(ctx), *_item_filter(ctx, Sale.item_id))
    )).one()
    return _money(cogs), int(unknown or 0)


async def _transactions(session: AsyncSession, ctx: MetricContext) -> int:
    return int((await session.execute(
        select(func.count(Order.id)).where(Order.sold_at >= ctx.since, Order.sold_at < ctx.until, Order.status.not_in(("voided", "open")))
    )).scalar_one())


@metric("revenue", description_id="Total penjualan (harga jual bersih diskon baris) dalam periode",
        description_en="Total sales (line prices net of line discounts) in the period", unit="rupiah", dimensions=("item",))
async def revenue(session: AsyncSession, ctx: MetricContext) -> MetricResult:
    return MetricResult(name="revenue", unit="rupiah", value=await _revenue(session, ctx))


@metric("cogs", description_id="Harga pokok penjualan: biaya bahan dari barang yang terjual, dicatat saat jual",
        description_en="Cost of goods sold: the cost snapshotted on each line at sale time", unit="rupiah", dimensions=("item",))
async def cogs(session: AsyncSession, ctx: MetricContext) -> MetricResult:
    value, unknown = await _cogs(session, ctx)
    note = f"{unknown} baris tanpa catatan harga pokok memakai harga pokok hari ini" if unknown else None
    return MetricResult(name="cogs", unit="rupiah", value=value, note=note, rows=[{"lines_without_snapshot": unknown}])


@metric("gross_profit", description_id="Laba kotor: penjualan dikurangi harga pokok penjualan",
        description_en="Gross profit: revenue minus cost of goods sold", unit="rupiah", dimensions=("item",))
async def gross_profit(session: AsyncSession, ctx: MetricContext) -> MetricResult:
    rev = await _revenue(session, ctx)
    cost, _ = await _cogs(session, ctx)
    return MetricResult(name="gross_profit", unit="rupiah", value=_money(rev - cost))


@metric("gross_margin_pct", description_id="Margin kotor: laba kotor sebagai persen dari penjualan",
        description_en="Gross margin: gross profit as a percentage of revenue", unit="pct", dimensions=("item",))
async def gross_margin_pct(session: AsyncSession, ctx: MetricContext) -> MetricResult:
    rev = await _revenue(session, ctx)
    cost, _ = await _cogs(session, ctx)
    value = ((rev - cost) / rev * 100).quantize(PCT, rounding=ROUND_HALF_UP) if rev else None
    return MetricResult(name="gross_margin_pct", unit="pct", value=value)


@metric("transaction_count", description_id="Jumlah transaksi (struk) dalam periode, tidak termasuk yang dibatalkan",
        description_en="Number of orders in the period, voids excluded", unit="count")
async def transaction_count(session: AsyncSession, ctx: MetricContext) -> MetricResult:
    return MetricResult(name="transaction_count", unit="count", value=await _transactions(session, ctx))


@metric("average_ticket", description_id="Rata-rata nilai per transaksi: penjualan dibagi jumlah transaksi",
        description_en="Average order value: revenue divided by the number of orders", unit="rupiah")
async def average_ticket(session: AsyncSession, ctx: MetricContext) -> MetricResult:
    rev = await _revenue(session, ctx)
    n = await _transactions(session, ctx)
    return MetricResult(name="average_ticket", unit="rupiah", value=_money(rev / n) if n else None)


@metric("item_units_sold", description_id="Jumlah unit terjual dalam periode (per barang bila dipilih)",
        description_en="Units sold in the period (for one item when given)", unit="qty", dimensions=("item",))
async def item_units_sold(session: AsyncSession, ctx: MetricContext) -> MetricResult:
    total = (await session.execute(
        select(func.coalesce(func.sum(Sale.quantity), 0)).where(*_in_window(ctx), *_item_filter(ctx, Sale.item_id))
    )).scalar_one()
    rows = []
    if ctx.item_id is None:
        rows = [
            {"item_id": iid, "name": name, "quantity": float(q)}
            for iid, name, q in (await session.execute(
                select(Item.id, Item.name, func.sum(Sale.quantity)).join(Item, Item.id == Sale.item_id)
                .where(*_in_window(ctx)).group_by(Item.id, Item.name).order_by(desc(func.sum(Sale.quantity))).limit(ctx.limit)
            )).all()
        ]
    return MetricResult(name="item_units_sold", unit="qty", value=Decimal(total).quantize(Decimal("0.001")), rows=rows)


@metric("top_items_by_revenue", description_id="Barang dengan penjualan tertinggi dalam periode",
        description_en="Items ranked by revenue in the period", unit="list")
async def top_items_by_revenue(session: AsyncSession, ctx: MetricContext) -> MetricResult:
    rows = (await session.execute(
        select(Item.id, Item.name, func.sum(Sale.quantity).label("qty"), func.sum(Sale.total_price).label("amount"))
        .join(Item, Item.id == Sale.item_id).where(*_in_window(ctx))
        .group_by(Item.id, Item.name).order_by(desc("amount")).limit(ctx.limit)
    )).all()
    return MetricResult(name="top_items_by_revenue", unit="list", rows=[
        {"item_id": iid, "name": name, "quantity": float(q), "revenue": float(_money(a))} for iid, name, q, a in rows
    ])


@metric("top_items_by_margin", description_id="Barang dengan laba kotor tertinggi dalam periode",
        description_en="Items ranked by gross profit in the period", unit="list")
async def top_items_by_margin(session: AsyncSession, ctx: MetricContext) -> MetricResult:
    cost_expr = Sale.quantity * func.coalesce(OrderLine.unit_cost_at_sale, Item.cost_price)
    rows = (await session.execute(
        select(Item.id, Item.name, func.sum(Sale.quantity), func.sum(Sale.total_price), func.sum(cost_expr))
        .join(OrderLine, OrderLine.id == Sale.id).join(Item, Item.id == Sale.item_id).where(*_in_window(ctx))
        .group_by(Item.id, Item.name).order_by(desc(func.sum(Sale.total_price) - func.sum(cost_expr))).limit(ctx.limit)
    )).all()
    return MetricResult(name="top_items_by_margin", unit="list", rows=[
        {"item_id": iid, "name": name, "quantity": float(q), "revenue": float(_money(a)), "cogs": float(_money(c)),
         "gross_profit": float(_money(Decimal(a or 0) - Decimal(c or 0)))}
        for iid, name, q, a, c in rows
    ])


@metric("peak_hour", description_id="Jam (waktu setempat) dengan penjualan tertinggi dalam periode",
        description_en="The hour of day (business-local) with the highest revenue in the period", unit="hour")
async def peak_hour(session: AsyncSession, ctx: MetricContext) -> MetricResult:
    # The business-local hour without naming the zone to Postgres: Indonesia and
    # Malaysia have whole-hour offsets and no DST, and a bundled local Postgres
    # may not ship tzdata, so the offset is taken in Python and added as an interval.
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    offset = ctx.since.astimezone(ZoneInfo(ctx.tz)).utcoffset() or timedelta(0)
    local_hour = func.extract("hour", Sale.sold_at + offset)
    rows = (await session.execute(
        select(local_hour.label("h"), func.sum(Sale.total_price), func.count(Sale.id))
        .where(*_in_window(ctx)).group_by("h").order_by("h")
    )).all()
    by_hour = [{"hour": int(h), "revenue": float(_money(a)), "lines": int(n)} for h, a, n in rows]
    best = max(by_hour, key=lambda r: r["revenue"]) if by_hour else None
    return MetricResult(name="peak_hour", unit="hour", value=best["hour"] if best else None, rows=by_hour)


# ── discounts and promos ────────────────────────────────────────────────────


async def _order_sum(session: AsyncSession, ctx: MetricContext, expr) -> Decimal:
    return _money((await session.execute(
        select(func.coalesce(func.sum(expr), 0)).where(Order.sold_at >= ctx.since, Order.sold_at < ctx.until, Order.status.not_in(("voided", "open")))
    )).scalar_one())


@metric("discount_cost", description_id="Total diskon yang diberikan kasir (per baris dan per struk) dalam periode",
        description_en="Total cashier discounts (line and bill) given in the period", unit="rupiah")
async def discount_cost(session: AsyncSession, ctx: MetricContext) -> MetricResult:
    return MetricResult(name="discount_cost", unit="rupiah", value=await _order_sum(session, ctx, Order.discount_total))


@metric("promo_cost", description_id="Total biaya promo dan voucher dalam periode",
        description_en="Total cost of promos and vouchers in the period", unit="rupiah")
async def promo_cost(session: AsyncSession, ctx: MetricContext) -> MetricResult:
    return MetricResult(name="promo_cost", unit="rupiah", value=await _order_sum(session, ctx, Order.promo_total + Order.voucher_total))


# ── costs, expenses, profit ─────────────────────────────────────────────────


@metric("waste_value", description_id="Nilai barang terbuang/rusak dalam periode, pada harga pokoknya",
        description_en="Value of stock written off as waste in the period, at cost", unit="rupiah", dimensions=("item",))
async def waste_value(session: AsyncSession, ctx: MetricContext) -> MetricResult:
    value = (await session.execute(
        select(func.coalesce(func.sum(-StockMovement.qty_delta * func.coalesce(StockMovement.unit_cost, Item.cost_price)), 0))
        .join(Item, Item.id == StockMovement.item_id)
        .where(StockMovement.reason == "waste", StockMovement.created_at >= ctx.since, StockMovement.created_at < ctx.until,
               *_item_filter(ctx, StockMovement.item_id))
    )).scalar_one()
    return MetricResult(name="waste_value", unit="rupiah", value=_money(value))


@metric("expense_total", description_id="Total pengeluaran tercatat dalam periode",
        description_en="Total recorded expenses in the period", unit="rupiah")
async def expense_total(session: AsyncSession, ctx: MetricContext) -> MetricResult:
    value = (await session.execute(
        select(func.coalesce(func.sum(Expense.amount), 0)).where(Expense.occurred_at >= ctx.since, Expense.occurred_at < ctx.until)
    )).scalar_one()
    return MetricResult(name="expense_total", unit="rupiah", value=_money(value))


@metric("net_profit", description_id="Laba bersih operasional: penjualan − harga pokok − pengeluaran tercatat",
        description_en="Operating net profit: revenue − cost of goods − recorded expenses", unit="rupiah")
async def net_profit(session: AsyncSession, ctx: MetricContext) -> MetricResult:
    rev = await _revenue(session, ctx)
    cost, _ = await _cogs(session, ctx)
    exp = (await expense_total(session, ctx)).value or Decimal(0)
    return MetricResult(name="net_profit", unit="rupiah", value=_money(rev - cost - exp),
                        note="pengeluaran yang sudah jadi persediaan lewat penerimaan barang tidak dihitung dua kali di buku besar; laporan laba rugi akuntansi ada di /api/statements/profit-loss")


@metric("cash_variance", description_id="Total selisih kas shift yang ditutup dalam periode (negatif = kurang)",
        description_en="Total till variance of shifts closed in the period (negative = short)", unit="rupiah")
async def cash_variance(session: AsyncSession, ctx: MetricContext) -> MetricResult:
    value, n = (await session.execute(
        select(func.coalesce(func.sum(Shift.variance), 0), func.count(Shift.id))
        .where(Shift.status == "closed", Shift.closed_at >= ctx.since, Shift.closed_at < ctx.until)
    )).one()
    return MetricResult(name="cash_variance", unit="rupiah", value=_money(value), rows=[{"shifts_closed": int(n)}])


# ── stock (instant) ─────────────────────────────────────────────────────────


def _stock_row(i: Item) -> dict:
    return {
        "item_id": i.id, "name": i.name, "unit": i.unit, "stock": float(i.current_stock),
        "reorder_threshold": float(i.reorder_threshold) if i.reorder_threshold is not None else None,
        "below_reorder_threshold": bool(i.reorder_threshold is not None and i.current_stock <= i.reorder_threshold),
    }


@metric("stock_on_hand", description_id="Stok saat ini (per barang, atau satu barang bila dipilih)",
        description_en="Current stock (per item, or one item when given)", unit="qty", grains=("instant",), dimensions=("item",))
async def stock_on_hand(session: AsyncSession, ctx: MetricContext) -> MetricResult:
    if ctx.item_id is not None:
        item = await session.get(Item, ctx.item_id)
        return MetricResult(name="stock_on_hand", unit="qty", value=Decimal(item.current_stock) if item else None,
                            rows=[_stock_row(item)] if item else [])
    items = (await session.execute(select(Item).order_by(Item.name))).scalars().all()
    return MetricResult(name="stock_on_hand", unit="qty", value=None, rows=[_stock_row(i) for i in items])


@metric("stock_days_remaining", description_id="Perkiraan hari sampai stok habis pada laju penjualan 14 hari terakhir",
        description_en="Estimated days until stock runs out at the trailing 14-day sales rate", unit="days", grains=("instant",), dimensions=("item",))
async def stock_days_remaining(session: AsyncSession, ctx: MetricContext) -> MetricResult:
    """The locked velocity formula (services/velocity): trailing-window sales ÷
    window days, stock ÷ that. One grouped query for every item — this runs on
    the WhatsApp hot path, so no per-item round trips."""
    from datetime import timedelta

    from app.services.velocity import VELOCITY_WINDOW_DAYS

    moment = ctx.now or datetime.now(timezone.utc)
    since = moment - timedelta(days=VELOCITY_WINDOW_DAYS)
    stmt = select(Item).order_by(Item.name)
    if ctx.item_id is not None:
        stmt = stmt.where(Item.id == ctx.item_id)
    items = (await session.execute(stmt)).scalars().all()
    usage = {
        item_id: Decimal(qty)
        for item_id, qty in (await session.execute(
            select(Sale.item_id, func.sum(Sale.quantity)).where(Sale.sold_at >= since).group_by(Sale.item_id)
        )).all()
    }
    rows = []
    for item in items:
        daily = usage.get(item.id, Decimal(0)) / VELOCITY_WINDOW_DAYS
        days = (Decimal(item.current_stock) / daily).quantize(Decimal("0.1")) if daily > 0 else None
        rows.append({**_stock_row(item), "daily_usage": float(daily.quantize(Decimal("0.001"))),
                     "days_remaining": float(days) if days is not None else None})
    if ctx.item_id is not None:
        value = Decimal(str(rows[0]["days_remaining"])) if rows and rows[0]["days_remaining"] is not None else None
        return MetricResult(name="stock_days_remaining", unit="days", value=value, rows=rows)
    rows.sort(key=lambda x: (x["days_remaining"] is None, x["days_remaining"] if x["days_remaining"] is not None else 0))
    return MetricResult(name="stock_days_remaining", unit="days", value=None, rows=rows)   # every item: this is the reorder list


# ── customers ───────────────────────────────────────────────────────────────


@metric("new_customers", description_id="Pelanggan baru yang terdaftar dalam periode",
        description_en="Customers registered in the period", unit="count")
async def new_customers(session: AsyncSession, ctx: MetricContext) -> MetricResult:
    n = (await session.execute(
        select(func.count(Customer.id)).where(Customer.created_at >= ctx.since, Customer.created_at < ctx.until)
    )).scalar_one()
    return MetricResult(name="new_customers", unit="count", value=int(n))


@metric("repeat_rate", description_id="Dari pelanggan yang belanja dalam periode, persen yang sudah pernah belanja sebelumnya",
        description_en="Of customers who bought in the period, the share who had bought before", unit="pct")
async def repeat_rate(session: AsyncSession, ctx: MetricContext) -> MetricResult:
    buyers = (await session.execute(
        select(Order.customer_id).where(Order.customer_id.is_not(None), Order.status.not_in(("voided", "open")),
                                        Order.sold_at >= ctx.since, Order.sold_at < ctx.until).distinct()
    )).scalars().all()
    if not buyers:
        return MetricResult(name="repeat_rate", unit="pct", value=None, rows=[{"buyers": 0, "repeat": 0}])
    repeat = (await session.execute(
        select(func.count(func.distinct(Order.customer_id))).where(
            Order.customer_id.in_(buyers), Order.status.not_in(("voided", "open")), Order.sold_at < ctx.since,
        )
    )).scalar_one()
    value = (Decimal(repeat) / Decimal(len(buyers)) * 100).quantize(PCT, rounding=ROUND_HALF_UP)
    return MetricResult(name="repeat_rate", unit="pct", value=value, rows=[{"buyers": len(buyers), "repeat": int(repeat)}])


# ── purchasing ──────────────────────────────────────────────────────────────


@metric("purchase_history", description_id="Penerimaan barang dalam periode (per pemasok bila dipilih): tanggal, pemasok, nilai, jumlah baris",
        description_en="Goods received in the period (for one supplier when given): date, supplier, value, line count",
        unit="list", dimensions=("supplier",))
async def purchase_history(session: AsyncSession, ctx: MetricContext) -> MetricResult:
    stmt = (
        select(GoodsReceipt, Supplier.name, func.count(GoodsReceiptLine.id))
        .outerjoin(Supplier, Supplier.id == GoodsReceipt.supplier_id)
        .outerjoin(GoodsReceiptLine, GoodsReceiptLine.receipt_id == GoodsReceipt.id)
        .where(GoodsReceipt.received_at >= ctx.since, GoodsReceipt.received_at < ctx.until)
        .group_by(GoodsReceipt.id, Supplier.name)
        .order_by(GoodsReceipt.received_at.desc())
        .limit(max(ctx.limit, 20))
    )
    if ctx.supplier_id is not None:
        stmt = stmt.where(GoodsReceipt.supplier_id == ctx.supplier_id)
    rows = (await session.execute(stmt)).all()
    total = sum((Decimal(g.subtotal) for g, _n, _c in rows), Decimal(0))
    return MetricResult(name="purchase_history", unit="list", value=_money(total), rows=[
        {"receipt_id": g.id, "number": g.number, "received_at": g.received_at, "supplier_id": g.supplier_id,
         "supplier": name, "total": float(_money(g.subtotal)), "lines": int(c)}
        for g, name, c in rows
    ])


@metric("supplier_prices", description_id="Harga beli terakhir per barang per pemasok (per satuan barang), dengan harga sebelumnya",
        description_en="Last purchase price per item per supplier (in the item's unit), with the previous price",
        unit="list", grains=("instant",), dimensions=("item", "supplier"))
async def supplier_prices(session: AsyncSession, ctx: MetricContext) -> MetricResult:
    stmt = (
        select(GoodsReceiptLine.item_id, Item.name, Item.unit, GoodsReceipt.supplier_id, Supplier.name,
               GoodsReceiptLine.unit_cost_item_unit, GoodsReceipt.received_at)
        .join(GoodsReceipt, GoodsReceipt.id == GoodsReceiptLine.receipt_id)
        .join(Item, Item.id == GoodsReceiptLine.item_id)
        .outerjoin(Supplier, Supplier.id == GoodsReceipt.supplier_id)
        .order_by(GoodsReceipt.received_at.desc())
    )
    if ctx.item_id is not None:
        stmt = stmt.where(GoodsReceiptLine.item_id == ctx.item_id)
    if ctx.supplier_id is not None:
        stmt = stmt.where(GoodsReceipt.supplier_id == ctx.supplier_id)
    latest: dict[tuple, dict] = {}
    for item_id, item_name, unit, supplier_id, supplier_name, cost, when in (await session.execute(stmt)).all():
        key = (item_id, supplier_id)
        if key not in latest:
            latest[key] = {"item_id": item_id, "item": item_name, "unit": unit, "supplier_id": supplier_id,
                           "supplier": supplier_name, "last_price": float(_money(cost)), "last_bought_at": when,
                           "previous_price": None, "change_pct": None}
        elif latest[key]["previous_price"] is None:
            prev = float(_money(cost))
            latest[key]["previous_price"] = prev
            latest[key]["change_pct"] = round((latest[key]["last_price"] - prev) / prev * 100, 1) if prev else None
    rows = sorted(latest.values(), key=lambda r: (r["item"], r["supplier"] or ""))
    return MetricResult(name="supplier_prices", unit="list", rows=rows)


@metric("recipe_cost", description_id="Biaya bahan satu porsi menurut resep, pada harga pokok bahan saat ini",
        description_en="Ingredient cost of one unit from its recipe, at the components' current cost",
        unit="rupiah", grains=("instant",), dimensions=("item",))
async def recipe_cost(session: AsyncSession, ctx: MetricContext) -> MetricResult:
    from app.services.catalog import default_variant, recipe_lines_for
    from app.services.units import convert_quantity

    if ctx.item_id is None:
        return MetricResult(name="recipe_cost", unit="rupiah", value=None, note="pilih satu barang")
    item = await session.get(Item, ctx.item_id)
    if item is None:
        return MetricResult(name="recipe_cost", unit="rupiah", value=None)
    variant = await default_variant(session, item.id)
    lines = await recipe_lines_for(session, variant.id, active_only=True) if variant is not None else []
    if not lines:
        cost = Decimal(variant.cost_price) if variant is not None else Decimal(item.cost_price)
        return MetricResult(name="recipe_cost", unit="rupiah", value=_money(cost),
                            rows=[], note="tidak ada resep: memakai harga pokok yang tercatat")
    rows, total = [], Decimal(0)
    for rl in lines:
        comp = await session.get(Item, rl.component_item_id)
        qty = Decimal(rl.quantity)
        if rl.uom_id is not None and comp.uom_id is not None and rl.uom_id != comp.uom_id:
            qty = await convert_quantity(session, qty, rl.uom_id, comp.uom_id)
        line_cost = _money(qty * Decimal(comp.cost_price))
        total += line_cost
        rows.append({"component_id": comp.id, "component": comp.name, "quantity": float(qty), "unit": comp.unit,
                     "unit_cost": float(_money(comp.cost_price)), "cost": float(line_cost)})
    sell = Decimal(variant.sell_price) if variant is not None else Decimal(item.sell_price)
    margin = ((sell - total) / sell * 100).quantize(PCT, rounding=ROUND_HALF_UP) if sell else None
    return MetricResult(name="recipe_cost", unit="rupiah", value=_money(total), rows=rows,
                        note=f"harga jual {sell:.0f}, margin kotor {margin}%" if margin is not None else None)


# ── till, customers, promos ─────────────────────────────────────────────────


@metric("shift_summary", description_id="Shift kasir yang ditutup dalam periode: modal, tunai, seharusnya, dihitung, selisih",
        description_en="Till shifts closed in the period: float, cash taken, expected, counted, variance", unit="list")
async def shift_summary(session: AsyncSession, ctx: MetricContext) -> MetricResult:
    from app.services.shifts import shift_view

    shifts = (await session.execute(
        select(Shift).where(Shift.status == "closed", Shift.closed_at >= ctx.since, Shift.closed_at < ctx.until)
        .order_by(Shift.closed_at.desc()).limit(max(ctx.limit, 20))
    )).scalars().all()
    rows = []
    for sh in shifts:
        v = await shift_view(session, sh)
        rows.append({"shift_id": v["id"], "staff": v["staff_name"], "opened_at": v["opened_at"], "closed_at": v["closed_at"],
                     "opening_float": float(v["opening_float"]), "cash_sales": float(v["cash_sales"]),
                     "cash_refunds": float(v["cash_refunds"]), "cash_in": float(v["cash_in"]), "cash_out": float(v["cash_out"]),
                     "expected_cash": float(v["expected_cash"] or 0), "counted_cash": float(v["counted_cash"] or 0),
                     "variance": float(v["variance"] or 0), "notes": v["notes"]})
    total_variance = sum((Decimal(str(r["variance"])) for r in rows), Decimal(0))
    return MetricResult(name="shift_summary", unit="list", value=_money(total_variance), rows=rows)


@metric("customer_summary", description_id="Ringkasan pelanggan: kunjungan, belanja, kunjungan terakhir, saldo poin (satu pelanggan, atau pelanggan teratas dalam periode)",
        description_en="Customer summary: visits, spend, last visit, points (one customer, or the top customers of the period)",
        unit="list", dimensions=("customer",))
async def customer_summary(session: AsyncSession, ctx: MetricContext) -> MetricResult:
    from app.services.customers import customer_view, customer_views

    if ctx.customer_id is not None:
        cust = await session.get(Customer, ctx.customer_id)
        if cust is None:
            return MetricResult(name="customer_summary", unit="list", rows=[])
        v = await customer_view(session, cust)
        period_spend, period_visits = (await session.execute(
            select(func.coalesce(func.sum(case((Order.status == "completed", Order.total), else_=0)), 0), func.count(Order.id))
            .where(Order.customer_id == cust.id, Order.status.not_in(("voided", "open")), Order.sold_at >= ctx.since, Order.sold_at < ctx.until)
        )).one()
        row = {**v, "period_spend": float(_money(period_spend)), "period_visits": int(period_visits)}
        return MetricResult(name="customer_summary", unit="list", value=_money(v["total_spent"]), rows=[row])
    top = (await session.execute(
        select(Customer, func.coalesce(func.sum(case((Order.status == "completed", Order.total), else_=0)), 0).label("spend"),
               func.count(Order.id).label("visits"))
        .join(Order, Order.customer_id == Customer.id)
        .where(Order.status.not_in(("voided", "open")), Order.sold_at >= ctx.since, Order.sold_at < ctx.until)
        .group_by(Customer.id).order_by(desc("spend")).limit(ctx.limit)
    )).all()
    views = await customer_views(session, [c for c, _s, _v in top])
    rows = [{**v, "period_spend": float(_money(sp)), "period_visits": int(n)} for v, (_c, sp, n) in zip(views, top)]
    return MetricResult(name="customer_summary", unit="list", rows=rows)


@metric("promo_performance", description_id="Kinerja promo dalam periode: berapa kali dipakai, biaya, dan penjualan struk yang memakainya",
        description_en="Promo performance in the period: applications, cost, and revenue of the orders it applied to", unit="list")
async def promo_performance(session: AsyncSession, ctx: MetricContext) -> MetricResult:
    rows = (await session.execute(
        select(Promo.id, Promo.name, Promo.kind, Promo.is_active,
               func.count(PromoApplication.id), func.coalesce(func.sum(PromoApplication.amount), 0),
               func.count(func.distinct(PromoApplication.order_id)))
        .join(PromoApplication, PromoApplication.promo_id == Promo.id)
        .join(Order, Order.id == PromoApplication.order_id)
        .where(Order.status.not_in(("voided", "open")), Order.sold_at >= ctx.since, Order.sold_at < ctx.until)
        .group_by(Promo.id).order_by(desc(func.coalesce(func.sum(PromoApplication.amount), 0)))
        .limit(max(ctx.limit, 20))
    )).all()
    out = []
    for pid, name, kind, active, n, given, n_orders in rows:
        order_revenue = (await session.execute(
            select(func.coalesce(func.sum(Order.total), 0)).where(
                Order.id.in_(select(PromoApplication.order_id).where(PromoApplication.promo_id == pid)),
                Order.status.not_in(("voided", "open")), Order.sold_at >= ctx.since, Order.sold_at < ctx.until,
            )
        )).scalar_one()
        out.append({"promo_id": pid, "name": name, "kind": kind, "is_active": active, "applications": int(n),
                    "orders": int(n_orders), "given_away": float(_money(given)), "order_revenue": float(_money(order_revenue))})
    total = sum((Decimal(str(r["given_away"])) for r in out), Decimal(0))
    return MetricResult(name="promo_performance", unit="list", value=_money(total), rows=out)


STANDARD_METRIC_NAMES = (
    "revenue", "gross_profit", "gross_margin_pct", "cogs", "transaction_count", "average_ticket", "item_units_sold",
    "stock_on_hand", "stock_days_remaining", "waste_value", "expense_total", "net_profit", "cash_variance",
    "discount_cost", "promo_cost", "new_customers", "repeat_rate", "top_items_by_revenue", "top_items_by_margin",
    "peak_hour",
)
