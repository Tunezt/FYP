"""POS kiosk endpoints.

Three access levels, escalating:
1. pairing token (in the kiosk URL) → may list staff names + attempt PIN login
2. PIN login → short-lived scope="pos" JWT for one staff member
3. pos JWT → item list + sale recording. Nothing else — owner analytics,
   staff management, and WhatsApp flows all require scope="owner".
"""
import time
from decimal import Decimal
import uuid

import jwt as pyjwt
from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.core.db import tenant_session
from app.core.deps import PosCtx
from app.schemas.pos import CashMovementIn, CashMovementOut, PosSupplierOut, ShiftCloseIn, ShiftOpenIn, ShiftOut
from app.core.security import create_token, decode_token, verify_pin
from app.models import Business, Item, ItemVariant, Modifier, RequestLog, Staff
from app.schemas.pos import (
    ItemOut,
    OrderIn,
    OrderLineOut,
    OrderOut,
    QuoteIn,
    QuoteLineOut,
    QuoteOut,
    PaymentOut,
    LineModifierOut,
    PosBusinessOut,
    PosModifierGroupOut,
    PosModifierOut,
    PosVariantOut,
    ReceiptLineOut,
    ReceiptOut,
    PosLoginIn,
    PosLoginOut,
    PosStaffOut,
    RefundIn,
    ReversalIn,
    ReversalLineOut,
    ReversalOut,
    SaleIn,
    SaleOut,
)
from app.services.orders import (
    DiscountNeedsManager,
    ManagerPinRejected,
    ModifierSelectionInvalid,
    OrderLineSpec,
    OrderNotFound,
    OrderNotReversible,
    PaymentMismatch,
    PaymentSpec,
    RecipeUnitMismatch,
    Reversal,
    VariantNotFound,
    create_order,
    load_receipt,
    refund_order,
    void_order,
)
from app.services.pricing import PricingInvalid
from app.services.sales import InsufficientStock, ItemNotFound, record_sale
from app.services.velocity import check_low_stock_for_item

router = APIRouter(prefix="/pos", tags=["pos"])


def _pairing_business_id(pairing_token: str) -> uuid.UUID:
    try:
        claims = decode_token(pairing_token)
    except pyjwt.PyJWTError:
        raise HTTPException(
            status_code=401, detail="Tautan kasir tidak berlaku — minta tautan baru ke pemilik ya"
        )
    if claims.get("scope") != "pos-pairing":
        raise HTTPException(status_code=401, detail="Tautan kasir tidak dikenali")
    return uuid.UUID(claims["business_id"])


@router.get("/business/{pairing_token}", response_model=PosBusinessOut)
async def pos_business(pairing_token: str):
    """Kiosk boot: business name + active staff names for the 'who are you' screen."""
    business_id = _pairing_business_id(pairing_token)
    async with tenant_session(business_id) as session:
        business = await session.get(Business, business_id)
        if business is None:
            raise HTTPException(status_code=404, detail="Usaha tidak ditemukan")
        staff = (
            (
                await session.execute(
                    select(Staff).where(Staff.is_active.is_(True)).order_by(Staff.created_at)
                )
            )
            .scalars()
            .all()
        )
        return PosBusinessOut(
            business_name=business.name,
            staff=[PosStaffOut.model_validate(s) for s in staff],
        )


@router.post("/login", response_model=PosLoginOut)
async def pos_login(payload: PosLoginIn):
    business_id = _pairing_business_id(payload.pairing_token)
    async with tenant_session(business_id) as session:
        staff = await session.get(Staff, payload.staff_id)
        if staff is None or not staff.is_active or not verify_pin(payload.pin, staff.pin_hash):
            # One message for both wrong-person and wrong-PIN: no oracle.
            raise HTTPException(status_code=401, detail="PIN salah — coba lagi")
        business = await session.get(Business, business_id)
        token = create_token(
            business_id=str(business_id), scope="pos", staff_id=str(staff.id)
        )
        return PosLoginOut(token=token, staff_name=staff.name, business_name=business.name)


@router.get("/items", response_model=list[ItemOut])
async def pos_items(ctx: PosCtx):
    items = (
        (await ctx.session.execute(select(Item).order_by(Item.name))).scalars().all()
    )
    variants = (
        await ctx.session.execute(
            select(ItemVariant)
            .where(ItemVariant.is_active.is_(True))
            .order_by(ItemVariant.is_default.desc(), ItemVariant.sell_price, ItemVariant.name)
        )
    ).scalars().all()
    by_item: dict[uuid.UUID, list[ItemVariant]] = {}
    for v in variants:
        by_item.setdefault(v.item_id, []).append(v)
    from app.services.catalog import made_to_order_item_ids, modifier_catalog

    groups_by_item = await modifier_catalog(ctx.session)
    made_to_order = await made_to_order_item_ids(ctx.session)
    return [
        ItemOut(
            id=i.id, name=i.name, unit=i.unit, current_stock=i.current_stock, sell_price=i.sell_price,
            reorder_threshold=i.reorder_threshold, made_to_order=i.id in made_to_order,
            variants=[PosVariantOut.model_validate(v) for v in by_item.get(i.id, [])],
            modifier_groups=[
                PosModifierGroupOut(
                    id=g.id, name=g.name, selection=g.selection, is_required=g.is_required,
                    min_select=g.min_select, max_select=g.max_select,
                    modifiers=[PosModifierOut.model_validate(m) for m in mods],
                )
                for g, mods in groups_by_item.get(i.id, [])
            ],
        )
        for i in items
    ]


@router.post("/sales", response_model=SaleOut)
async def pos_record_sale(payload: SaleIn, ctx: PosCtx):
    start = time.perf_counter()
    try:
        recorded = await record_sale(
            ctx.session,
            business_id=ctx.business_id,
            staff_id=ctx.staff_id,
            item_id=payload.item_id,
            quantity=payload.quantity,
            unit_price=payload.unit_price,
        )
    except ItemNotFound:
        raise HTTPException(status_code=404, detail="Barang tidak ditemukan")
    except InsufficientStock as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Stok tidak cukup — {exc.item_name} tersisa {exc.available}",
        )

    # Synchronous velocity check (Section 7): sudden depletion surfaces now,
    # not at the next nightly cron.
    await check_low_stock_for_item(ctx.session, ctx.business_id, payload.item_id)

    ctx.session.add(
        RequestLog(
            business_id=ctx.business_id,
            channel="pos",
            path="/pos/sales",
            latency_ms=int((time.perf_counter() - start) * 1000),
            status="ok",
        )
    )

    line, order = recorded.line, recorded.order
    return SaleOut(
        id=line.id,
        item_id=line.item_id,
        item_name=recorded.item_name,
        quantity=line.quantity,
        unit_price=line.unit_price,
        total_price=line.line_total,
        remaining_stock=recorded.remaining_stock,
        sold_at=order.sold_at,
    )


def _rp(amount) -> str:
    return f"Rp {amount:,.0f}".replace(",", ".")


_PRICING_ERRORS = {
    "quantity": "Jumlah harus lebih dari nol",
    "price": "Harga tidak boleh negatif",
    "line_discount": "Diskon baris tidak boleh lebih besar dari harga barisnya",
    "discount": "Diskon tidak boleh lebih besar dari total belanja",
    "rate": "Pengaturan pajak / service charge tidak valid — periksa di dashboard",
    "mode": "Pengaturan pembulatan tidak valid — periksa di dashboard",
    "unit": "Pengaturan pembulatan tidak valid — periksa di dashboard",
}


@router.post("/quote", response_model=QuoteOut)
async def pos_quote(payload: QuoteIn, ctx: PosCtx):
    """Price the cart without selling it (M7-T4b): the kiosk shows the customer
    exactly what `POST /pos/orders` will charge, from the same pure function,
    so the screen and the ledger cannot disagree by a rupiah. Writes nothing."""
    from app.services.catalog import default_variant
    from app.services.pricing import LineInput, price_order, pricing_config

    config = await pricing_config(ctx.session, ctx.business_id)
    inputs: list[LineInput] = []
    for l in payload.lines:
        item = await ctx.session.get(Item, l.item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Barang tidak ditemukan")
        if l.variant_id is not None:
            variant = await ctx.session.get(ItemVariant, l.variant_id)
            if variant is None or variant.item_id != item.id or not variant.is_active:
                raise HTTPException(status_code=404, detail="Varian barang tidak ditemukan atau sudah tidak aktif")
        else:
            variant = await default_variant(ctx.session, item.id)
        base = Decimal(l.unit_price) if l.unit_price is not None else (
            Decimal(variant.sell_price) if variant is not None else Decimal(item.sell_price)
        )
        extras = Decimal(0)
        for mid in l.modifier_ids:
            m = await ctx.session.get(Modifier, mid)
            if m is not None:
                extras += Decimal(m.price_delta)
        inputs.append(LineInput(unit_price=(base + extras).quantize(Decimal("0.01")), quantity=l.quantity, line_discount=l.line_discount))
    try:
        bill = price_order(inputs, config, bill_discount=payload.bill_discount)
    except PricingInvalid as exc:
        raise HTTPException(status_code=422, detail=_PRICING_ERRORS.get(exc.code, "Perhitungan harga tidak valid"))
    return QuoteOut(
        subtotal=bill.subtotal, discount_total=bill.discount_total, service_charge=bill.service_charge,
        tax_total=bill.tax_total, tax_inclusive=bill.tax_inclusive, rounding=bill.rounding, total=bill.total,
        discount_requires_pin=config.discount_requires_pin,
        lines=[
            QuoteLineOut(item_id=l.item_id, unit_price=pl.unit_price, quantity=pl.quantity, gross=pl.gross,
                         line_discount=pl.line_discount, line_total=pl.line_total)
            for l, pl in zip(payload.lines, bill.lines)
        ],
    )


@router.post("/orders", response_model=OrderOut, status_code=201)
async def pos_create_order(payload: OrderIn, ctx: PosCtx):
    """A multi-line order with one or more payments (M3-T3). All-or-nothing:
    an out-of-stock line or payments that do not add up leave nothing behind."""
    start = time.perf_counter()
    try:
        created = await create_order(
            ctx.session,
            business_id=ctx.business_id,
            staff_id=ctx.staff_id,
            order_type=payload.order_type,
            lines=[
                OrderLineSpec(item_id=l.item_id, variant_id=l.variant_id, modifier_ids=list(l.modifier_ids),
                              quantity=l.quantity, unit_price=l.unit_price, notes=l.notes,
                              line_discount=l.line_discount)
                for l in payload.lines
            ],
            payments=[PaymentSpec(method=p.method, amount=p.amount, reference=p.reference) for p in payload.payments],
            bill_discount=payload.bill_discount,
            manager_pin=payload.manager_pin,
        )
    except DiscountNeedsManager:
        raise HTTPException(status_code=403, detail="Diskon perlu PIN manajer — minta pemilik memasukkan PIN-nya")
    except ManagerPinRejected:
        raise HTTPException(status_code=403, detail="PIN manajer salah — minta pemilik untuk memasukkan PIN-nya")
    except PricingInvalid as exc:
        raise HTTPException(status_code=422, detail=_PRICING_ERRORS.get(exc.code, "Perhitungan harga tidak valid"))
    except ItemNotFound:
        raise HTTPException(status_code=404, detail="Barang tidak ditemukan")
    except VariantNotFound:
        raise HTTPException(status_code=404, detail="Varian barang tidak ditemukan atau sudah tidak aktif")
    except RecipeUnitMismatch as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Bahan '{exc.component_name}' belum punya satuan — atur satuannya di dashboard dulu",
        )
    except ModifierSelectionInvalid as exc:
        messages = {
            "unknown": "Pilihan tambahan tidak dikenali atau sudah tidak aktif",
            "required": f"Pilihan '{exc.group_name}' wajib diisi",
            "single": f"Pilihan '{exc.group_name}' hanya boleh satu",
            "max": f"Pilihan '{exc.group_name}' melebihi batas maksimal",
        }
        raise HTTPException(status_code=422, detail=messages[exc.code])
    except InsufficientStock as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Stok tidak cukup — {exc.item_name} tersisa {exc.available}",
        )
    except PaymentMismatch as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Pembayaran {_rp(exc.paid)} tidak sama dengan total {_rp(exc.total)}",
        )

    for cl in created.lines:
        await check_low_stock_for_item(ctx.session, ctx.business_id, cl.line.item_id)

    ctx.session.add(
        RequestLog(
            business_id=ctx.business_id,
            channel="pos",
            path="/pos/orders",
            latency_ms=int((time.perf_counter() - start) * 1000),
            status="ok",
        )
    )
    order = created.order
    return OrderOut(
        id=order.id,
        order_type=order.order_type,
        subtotal=order.subtotal,
        discount_total=order.discount_total,
        service_charge=order.service_charge,
        tax_total=order.tax_total,
        rounding=order.rounding,
        total=order.total,
        sold_at=order.sold_at,
        lines=[
            OrderLineOut(
                id=cl.line.id, item_id=cl.line.item_id, variant_id=cl.line.variant_id, item_name=cl.item_name,
                quantity=cl.line.quantity,
                unit_price=cl.line.unit_price, line_discount=cl.line.line_discount, line_total=cl.line.line_total,
                remaining_stock=cl.remaining_stock,
                modifiers=[LineModifierOut(name=m.name, price_delta=m.price_delta) for m in cl.modifiers],
            )
            for cl in created.lines
        ],
        payments=[PaymentOut(id=p.id, method=p.method, amount=p.amount, reference=p.reference) for p in created.payments],
    )


@router.get("/orders/{order_id}/receipt", response_model=ReceiptOut)
async def pos_receipt(order_id: uuid.UUID, ctx: PosCtx):
    """What gets printed (browser print, M4-T2): every line with its size and
    modifiers as sold, payments, totals. Voided orders print with their
    reversing lines so the paper says what happened."""
    try:
        data = await load_receipt(ctx.session, business_id=ctx.business_id, order_id=order_id)
    except OrderNotFound:
        raise HTTPException(status_code=404, detail="Transaksi tidak ditemukan")
    from app.services.pricing import pricing_config

    business = await ctx.session.get(Business, ctx.business_id)
    config = await pricing_config(ctx.session, ctx.business_id)
    order = data["order"]
    return ReceiptOut(
        order_id=order.id,
        number=str(order.id)[-8:].upper(),
        business_name=business.name if business else "",
        staff_name=data["staff_name"],
        status=order.status,
        order_type=order.order_type,
        sold_at=order.sold_at,
        lines=[
            ReceiptLineOut(
                name=entry["item_name"], variant=entry["variant_name"], quantity=entry["line"].quantity,
                unit_price=entry["line"].unit_price, line_total=entry["line"].line_total,
                modifiers=[LineModifierOut(name=m.name, price_delta=m.price_delta) for m in entry["modifiers"]],
                notes=entry["line"].notes,
            )
            for entry in data["lines"]
        ],
        subtotal=order.subtotal,
        discount_total=order.discount_total,
        service_charge=order.service_charge,
        tax_total=order.tax_total,
        tax_inclusive=config.tax_inclusive,
        rounding=order.rounding,
        total=order.total,
        payments=[PaymentOut(id=p.id, method=p.method, amount=p.amount, reference=p.reference) for p in data["payments"]],
    )


_STATUS_ID = {"voided": "sudah dibatalkan", "refunded": "sudah dikembalikan", "open": "masih terbuka"}


def _reversal_out(rev: Reversal) -> ReversalOut:
    return ReversalOut(
        order_id=rev.order.id,
        status=rev.order.status,
        reversing_lines=[
            ReversalLineOut(
                id=l.id, item_id=l.item_id, quantity=l.quantity, line_total=l.line_total,
                stock_after=rev.restocked.get(l.item_id),
            )
            for l in rev.reversing_lines
        ],
        reversing_payments=[
            PaymentOut(id=p.id, method=p.method, amount=p.amount, reference=p.reference) for p in rev.reversing_payments
        ],
    )


async def _run_reversal(ctx, order_id: uuid.UUID, fn, path: str, **kwargs) -> ReversalOut:
    start = time.perf_counter()
    try:
        rev = await fn(ctx.session, business_id=ctx.business_id, order_id=order_id, staff_id=ctx.staff_id, **kwargs)
    except ManagerPinRejected:
        raise HTTPException(status_code=403, detail="PIN manajer salah — minta pemilik untuk memasukkan PIN-nya")
    except OrderNotFound:
        raise HTTPException(status_code=404, detail="Transaksi tidak ditemukan")
    except OrderNotReversible as exc:
        raise HTTPException(
            status_code=409, detail=f"Transaksi ini {_STATUS_ID.get(exc.status, exc.status)} — tidak bisa dibalik lagi"
        )
    ctx.session.add(
        RequestLog(
            business_id=ctx.business_id, channel="pos", path=path,
            latency_ms=int((time.perf_counter() - start) * 1000), status="ok",
        )
    )
    return _reversal_out(rev)


@router.post("/orders/{order_id}/void", response_model=ReversalOut)
async def pos_void_order(order_id: uuid.UUID, payload: ReversalIn, ctx: PosCtx):
    """Manager-PIN gated. Writes reversing lines, payments and stock movements;
    the original order stays readable in full (M3-T4)."""
    return await _run_reversal(
        ctx, order_id, void_order, "/pos/orders/{id}/void", manager_pin=payload.manager_pin, note=payload.note,
    )


@router.post("/orders/{order_id}/refund", response_model=ReversalOut)
async def pos_refund_order(order_id: uuid.UUID, payload: RefundIn, ctx: PosCtx):
    """Manager-PIN gated. Like void, but `restock=false` keeps stock down when
    the goods are not coming back."""
    return await _run_reversal(
        ctx, order_id, refund_order, "/pos/orders/{id}/refund",
        manager_pin=payload.manager_pin, note=payload.note, restock=payload.restock,
    )


# ── Shifts (M7-T1) ──────────────────────────────────────────────────────────

_SHIFT_ERRORS = {
    "already_open": (409, "Shift kamu belum ditutup — tutup dulu sebelum buka yang baru"),
    "float": (422, "Modal awal tidak boleh negatif"),
    "not_open": (409, "Belum ada shift yang terbuka"),
    "closed": (409, "Shift ini sudah ditutup"),
    "counted": (422, "Uang yang dihitung tidak boleh negatif"),
}


@router.get("/shift", response_model=ShiftOut | None)
async def pos_current_shift(ctx: PosCtx):
    """The cashier's open shift with its live cash expectation, or null."""
    from app.services.shifts import current_shift, shift_view

    shift = await current_shift(ctx.session, ctx.staff_id)
    return ShiftOut(**await shift_view(ctx.session, shift)) if shift else None


@router.post("/shift/open", response_model=ShiftOut, status_code=201)
async def pos_open_shift(payload: ShiftOpenIn, ctx: PosCtx):
    from app.services.shifts import ShiftInvalid, open_shift, shift_view

    try:
        shift = await open_shift(ctx.session, ctx.business_id, staff_id=ctx.staff_id, opening_float=payload.opening_float)
    except ShiftInvalid as exc:
        status, detail = _SHIFT_ERRORS[exc.code]
        raise HTTPException(status_code=status, detail=detail)
    return ShiftOut(**await shift_view(ctx.session, shift))


@router.post("/shift/close", response_model=ShiftOut)
async def pos_close_shift(payload: ShiftCloseIn, ctx: PosCtx):
    """Close the cashier's open shift: expected, counted and variance are written once."""
    from app.services.shifts import ShiftInvalid, close_shift, current_shift, shift_view

    shift = await current_shift(ctx.session, ctx.staff_id)
    if shift is None:
        status, detail = _SHIFT_ERRORS["not_open"]
        raise HTTPException(status_code=status, detail=detail)
    try:
        shift = await close_shift(ctx.session, shift, counted_cash=payload.counted_cash, closed_by=ctx.staff_id, notes=payload.notes)
    except ShiftInvalid as exc:
        status, detail = _SHIFT_ERRORS[exc.code]
        raise HTTPException(status_code=status, detail=detail)
    return ShiftOut(**await shift_view(ctx.session, shift))


# ── Cash in and out (M7-T2) ─────────────────────────────────────────────────

_CASH_ERRORS = {
    "kind": (422, "Jenis kas tidak dikenali"),
    "via": (422, "Sumber atau tujuan uang tidak dikenali"),
    "amount": (422, "Jumlah tidak boleh nol atau negatif"),
    "reason": (422, "Alasan tidak boleh kosong"),
    "supplier": (404, "Supplier tidak ditemukan"),
}


@router.get("/suppliers", response_model=list[PosSupplierOut])
async def pos_suppliers(ctx: PosCtx):
    """Names to pick from when paying a supplier from the drawer."""
    from app.models import Supplier

    rows = (await ctx.session.execute(select(Supplier).where(Supplier.is_active.is_(True)).order_by(Supplier.name))).scalars().all()
    return [PosSupplierOut.model_validate(r) for r in rows]


@router.post("/cash", response_model=CashMovementOut, status_code=201)
async def pos_record_cash(payload: CashMovementIn, ctx: PosCtx):
    """Cash in, petty cash out, supplier paid, bank drop — posted to the ledger
    and stamped with the cashier's open shift."""
    from app.services.cash import CashInvalid, cash_movement_view, record_cash_movement

    try:
        row = await record_cash_movement(
            ctx.session, ctx.business_id, kind=payload.kind, amount=payload.amount, reason=payload.reason,
            staff_id=ctx.staff_id, via=payload.via, category=payload.category, supplier_id=payload.supplier_id,
        )
    except CashInvalid as exc:
        status, detail = _CASH_ERRORS[exc.code]
        raise HTTPException(status_code=status, detail=detail)
    return CashMovementOut(**await cash_movement_view(ctx.session, row))


@router.get("/cash", response_model=list[CashMovementOut])
async def pos_cash_movements(ctx: PosCtx):
    """This cashier's open shift's movements, newest first (empty without a shift)."""
    from app.services.cash import cash_movement_view, list_cash_movements
    from app.services.shifts import current_shift

    shift = await current_shift(ctx.session, ctx.staff_id)
    if shift is None:
        return []
    return [CashMovementOut(**await cash_movement_view(ctx.session, r)) for r in await list_cash_movements(ctx.session, shift_id=shift.id)]
