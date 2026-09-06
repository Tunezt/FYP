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
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.core.db import tenant_session
from app.core.deps import PosCtx
from app.schemas.pos import CashMovementIn, CashMovementOut, PosSupplierOut, ShiftCloseIn, ShiftOpenIn, ShiftOut
from app.schemas.menu import PosTicketOut, TicketCancelIn, TicketSettleIn
from app.schemas.pos import KitchenLineOut, KitchenStateIn, KitchenTicketOut
from app.core.security import create_token, decode_token, verify_pin
from app.models import Business, Item, ItemVariant, Modifier, RequestLog, Staff
from app.schemas.pos import (
    ItemOut,
    OrderIn,
    OrderLineOut,
    OrderOut,
    PosCustomerIn,
    PosCustomerOut,
    PosLoyaltyOut,
    QuoteIn,
    QuoteLineOut,
    QuoteOut,
    QuotePromoOut,
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
    OrderTypeInvalid,
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
from app.services.customers import CustomerInvalid
from app.services.vouchers import VoucherInvalid
from app.services.points import InsufficientPoints, PointsInvalid
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


_ORDER_TYPE_ERRORS = {   # M11-T3
    "address": "Pesanan antar perlu alamat pengantaran",
    "phone": "Pesanan antar perlu nomor HP penerima — isi nomornya atau pilih pelanggan yang punya nomor",
}

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
    # Promos (M8-T3): the same engine the sale runs, at "now" in the business's timezone.
    from datetime import datetime, timezone as _tz

    from app.services.promos import CartLine, apply_promos, load_rules

    rules = await load_rules(ctx.session)
    price_of = {}
    for r in rules:
        for iid in (r.bonus_item_id, r.item_id):
            if iid is not None and iid not in price_of:
                it = await ctx.session.get(Item, iid)
                if it is not None:
                    v = await default_variant(ctx.session, it.id)
                    price_of[iid] = Decimal(v.sell_price) if v is not None else Decimal(it.sell_price)
    business = await ctx.session.get(Business, ctx.business_id)
    promo = apply_promos(
        [CartLine(item_id=l.item_id, unit_price=i.unit_price, quantity=i.quantity, line_discount=i.line_discount) for l, i in zip(payload.lines, inputs)],
        rules, at_utc=datetime.now(_tz.utc), tz=business.timezone if business else "Asia/Jakarta",
        bill_discount=payload.bill_discount, price_of=price_of,
    )
    bonus_inputs = [LineInput(unit_price=b.unit_price, quantity=b.quantity, promo_discount=(b.unit_price * b.quantity).quantize(Decimal("0.01"))) for b in promo.bonus_lines]
    all_inputs = [
        LineInput(unit_price=i.unit_price, quantity=i.quantity, line_discount=i.line_discount, promo_discount=d)
        for i, d in zip(inputs, promo.line_discounts)
    ] + bonus_inputs
    # A voucher code (M8-T4): validated here so the cashier sees why it fails; the use is only taken by the sale.
    from app.services.vouchers import check_voucher, normalize_code, voucher_by_code

    voucher_amount, voucher_error = Decimal(0), None
    if payload.voucher_code:
        try:
            before = price_order(all_inputs, config, bill_discount=payload.bill_discount, promo_bill_discount=promo.bill_discount, order_type=payload.order_type)
            voucher_amount = check_voucher(await voucher_by_code(ctx.session, payload.voucher_code), base=before.net, at=datetime.now(_tz.utc)).amount
        except VoucherInvalid as exc:
            voucher_error = _voucher_message(exc)
        except PricingInvalid as exc:
            raise HTTPException(status_code=422, detail=_PRICING_ERRORS.get(exc.code, "Perhitungan harga tidak valid"))
    try:
        bill = price_order(all_inputs, config, bill_discount=payload.bill_discount, promo_bill_discount=promo.bill_discount, voucher_discount=voucher_amount, order_type=payload.order_type)
    except PricingInvalid as exc:
        raise HTTPException(status_code=422, detail=_PRICING_ERRORS.get(exc.code, "Perhitungan harga tidak valid"))
    item_ids = [l.item_id for l in payload.lines] + [b.item_id for b in promo.bonus_lines]
    bonus_names = [None] * len(payload.lines) + [b.promo_name for b in promo.bonus_lines]
    return QuoteOut(
        subtotal=bill.subtotal, discount_total=bill.discount_total, promo_total=bill.promo_total,
        promos=[QuotePromoOut(promo_id=a.promo_id, name=a.promo_name, amount=a.amount, bonus_quantity=a.bonus_quantity) for a in promo.applications],
        voucher_total=bill.voucher_total, voucher_code=normalize_code(payload.voucher_code) if payload.voucher_code and not voucher_error else None,
        voucher_error=voucher_error,
        service_charge=bill.service_charge, delivery_fee=bill.delivery_fee,
        tax_total=bill.tax_total, tax_inclusive=bill.tax_inclusive, rounding=bill.rounding, total=bill.total,
        discount_requires_pin=config.discount_requires_pin,
        lines=[
            QuoteLineOut(item_id=iid, unit_price=pl.unit_price, quantity=pl.quantity, gross=pl.gross,
                         line_discount=pl.line_discount, line_total=pl.line_total, promo_discount=pl.promo_discount,
                         is_bonus=name is not None, promo_name=name)
            for iid, name, pl in zip(item_ids, bonus_names, bill.lines)
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
            customer_id=payload.customer_id,
            voucher_code=payload.voucher_code,
            table_label=payload.table_label,
            delivery_address=payload.delivery_address,
            guest_name=payload.guest_name,
            guest_phone=payload.guest_phone,
        )
    except OrderTypeInvalid as exc:
        raise HTTPException(status_code=422, detail=_ORDER_TYPE_ERRORS[exc.code])
    except VoucherInvalid as exc:
        raise HTTPException(status_code=409 if exc.code == "used_up" else 422, detail=_voucher_message(exc))
    except CustomerInvalid:
        raise HTTPException(status_code=404, detail="Pelanggan tidak ditemukan atau sudah tidak aktif")
    except InsufficientPoints as exc:
        raise HTTPException(status_code=409, detail=f"Poin tidak cukup — butuh {exc.needed}, tersedia {exc.available}")
    except PointsInvalid as exc:
        raise HTTPException(status_code=422, detail=_POINTS_ERRORS.get(exc.code, "Pembayaran poin tidak valid").format(exc.detail))
    except DiscountNeedsManager:
        raise HTTPException(status_code=403, detail="Diskon perlu PIN manajer — minta pemilik atau manajer memasukkan PIN-nya")
    except ManagerPinRejected:
        raise HTTPException(status_code=403, detail="PIN manajer salah — minta pemilik atau manajer untuk memasukkan PIN-nya")
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
    return await _order_out(ctx.session, created)


async def _order_out(session, created) -> OrderOut:
    order = created.order
    from app.services.points import points_of_order

    earned, redeemed = await points_of_order(session, order.id)
    return OrderOut(
        id=order.id,
        order_type=order.order_type,
        table_label=order.table_label,
        delivery_address=order.delivery_address,
        delivery_fee=order.delivery_fee,
        customer_id=order.customer_id,
        points_earned=earned,
        points_redeemed=redeemed,
        subtotal=order.subtotal,
        discount_total=order.discount_total,
        promo_total=order.promo_total,
        voucher_total=order.voucher_total,
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


# ── The e-menu queue (M11-T1) ────────────────────────────────────────────────
#
# Tickets placed from the QR menu are open rows in this business's own orders
# table. The till lists them, settles one by taking payment (the ordinary sale,
# written on the ticket's row) or cancels one it cannot serve.


@router.get("/tickets", response_model=list[PosTicketOut])
async def pos_tickets(ctx: PosCtx):
    from app.api.menu import ticket_out
    from app.services.tickets import open_tickets

    return [ticket_out(t, PosTicketOut) for t in await open_tickets(ctx.session)]


@router.post("/tickets/{order_id}/settle", response_model=OrderOut)
async def pos_settle_ticket(order_id: uuid.UUID, payload: TicketSettleIn, ctx: PosCtx):
    """Payment for a guest's ticket: exactly what `POST /pos/orders` does, on
    the ticket's own row. All-or-nothing; a second till gets 409."""
    from app.services.orders import TicketNotOpen
    from app.services.tickets import TicketNotFound, get_ticket, settle_ticket

    start = time.perf_counter()
    try:
        ticket = await get_ticket(ctx.session, order_id)
        created = await settle_ticket(
            ctx.session, business_id=ctx.business_id, ticket=ticket, staff_id=ctx.staff_id,
            payments=[PaymentSpec(method=p.method, amount=p.amount, reference=p.reference) for p in payload.payments],
            bill_discount=payload.bill_discount, manager_pin=payload.manager_pin,
            customer_id=payload.customer_id, voucher_code=payload.voucher_code,
        )
    except TicketNotFound:
        raise HTTPException(status_code=404, detail="Pesanan tidak ditemukan")
    except TicketNotOpen as exc:
        raise HTTPException(status_code=409, detail=_TICKET_CLOSED.get(exc.status, "Pesanan ini sudah diproses"))
    except OrderTypeInvalid as exc:
        raise HTTPException(status_code=422, detail=_ORDER_TYPE_ERRORS[exc.code])
    except VoucherInvalid as exc:
        raise HTTPException(status_code=409 if exc.code == "used_up" else 422, detail=_voucher_message(exc))
    except CustomerInvalid:
        raise HTTPException(status_code=404, detail="Pelanggan tidak ditemukan atau sudah tidak aktif")
    except InsufficientPoints as exc:
        raise HTTPException(status_code=409, detail=f"Poin tidak cukup — butuh {exc.needed}, tersedia {exc.available}")
    except PointsInvalid as exc:
        raise HTTPException(status_code=422, detail=_POINTS_ERRORS.get(exc.code, "Pembayaran poin tidak valid").format(exc.detail))
    except DiscountNeedsManager:
        raise HTTPException(status_code=403, detail="Diskon perlu PIN manajer — minta pemilik atau manajer memasukkan PIN-nya")
    except ManagerPinRejected:
        raise HTTPException(status_code=403, detail="PIN manajer salah — minta pemilik atau manajer untuk memasukkan PIN-nya")
    except PricingInvalid as exc:
        raise HTTPException(status_code=422, detail=_PRICING_ERRORS.get(exc.code, "Perhitungan harga tidak valid"))
    except (ItemNotFound, VariantNotFound):
        raise HTTPException(status_code=409, detail="Menu di pesanan ini sudah tidak ada — batalkan pesanan dan buat ulang di kasir")
    except ModifierSelectionInvalid:
        raise HTTPException(status_code=409, detail="Pilihan tambahan di pesanan ini sudah tidak berlaku — batalkan dan buat ulang di kasir")
    except InsufficientStock as exc:
        raise HTTPException(status_code=409, detail=f"Stok tidak cukup — {exc.item_name} tersisa {exc.available}")
    except PaymentMismatch as exc:
        raise HTTPException(status_code=422, detail=f"Pembayaran {_rp(exc.paid)} tidak sama dengan total {_rp(exc.total)}")

    for cl in created.lines:
        await check_low_stock_for_item(ctx.session, ctx.business_id, cl.line.item_id)
    ctx.session.add(RequestLog(
        business_id=ctx.business_id, channel="pos", path="/pos/tickets/settle",
        latency_ms=int((time.perf_counter() - start) * 1000), status="ok",
    ))
    return await _order_out(ctx.session, created)


_TICKET_CLOSED = {
    "completed": "Pesanan ini sudah dibayar di kasir lain",
    "voided": "Pesanan ini sudah dibatalkan",
    "refunded": "Pesanan ini sudah dibayar dan dikembalikan",
}


@router.post("/tickets/{order_id}/cancel", response_model=PosTicketOut)
async def pos_cancel_ticket(order_id: uuid.UUID, payload: TicketCancelIn, ctx: PosCtx):
    from app.api.menu import ticket_out
    from app.services.orders import TicketNotOpen
    from app.services.tickets import TicketNotFound, cancel_ticket, get_ticket

    try:
        ticket = await get_ticket(ctx.session, order_id)
        ticket = await cancel_ticket(ctx.session, ticket=ticket, reason=payload.reason, staff_id=ctx.staff_id)
    except TicketNotFound:
        raise HTTPException(status_code=404, detail="Pesanan tidak ditemukan")
    except TicketNotOpen as exc:
        raise HTTPException(status_code=409, detail=_TICKET_CLOSED.get(exc.status, "Pesanan ini sudah diproses"))
    return ticket_out(ticket, PosTicketOut)


@router.get("/orders/{order_id}/receipt", response_model=ReceiptOut)
async def pos_receipt(order_id: uuid.UUID, ctx: PosCtx):
    """What gets printed (browser print, M4-T2): every line with its size and
    modifiers as sold, payments, totals. Voided orders print with their
    reversing lines so the paper says what happened."""
    try:
        data = await load_receipt(ctx.session, business_id=ctx.business_id, order_id=order_id)
    except OrderNotFound:
        raise HTTPException(status_code=404, detail="Transaksi tidak ditemukan")
    from app.services.points import points_of_order
    from app.services.pricing import pricing_config

    business = await ctx.session.get(Business, ctx.business_id)
    config = await pricing_config(ctx.session, ctx.business_id)
    order = data["order"]
    earned, redeemed = await points_of_order(ctx.session, order.id)
    from app.models import Promo
    from app.services.promos import applications_of_order

    from app.models import Voucher
    from app.services.vouchers import redemptions_of_order

    voucher_code = None
    for red in await redemptions_of_order(ctx.session, order.id):
        if red.amount > 0:
            v = await ctx.session.get(Voucher, red.voucher_id)
            voucher_code = v.code if v else None
    promo_names: list[str] = []
    for app_ in await applications_of_order(ctx.session, order.id):
        pr = await ctx.session.get(Promo, app_.promo_id)
        if pr is not None and pr.name not in promo_names:
            promo_names.append(pr.name)
    return ReceiptOut(
        order_id=order.id,
        number=str(order.id)[-8:].upper(),
        business_name=business.name if business else "",
        staff_name=data["staff_name"],
        customer_name=data.get("customer_name"),
        points_earned=earned,
        points_redeemed=redeemed,
        status=order.status,
        order_type=order.order_type,
        table_label=order.table_label,
        delivery_address=order.delivery_address,
        delivery_fee=order.delivery_fee,
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
        promo_total=order.promo_total,
        promo_names=promo_names,
        voucher_total=order.voucher_total,
        voucher_code=voucher_code,
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
        raise HTTPException(status_code=403, detail="PIN manajer salah — minta pemilik atau manajer untuk memasukkan PIN-nya")
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


# ── Customers at the till (M8-T1) ──────────────────────────────────────────

_CUSTOMER_ERRORS = {
    "name": (422, "Nama pelanggan tidak boleh kosong"),
    "phone": (422, "Nomor HP tidak valid — pakai 8–15 angka"),
    "duplicate_phone": (409, "Nomor HP ini sudah terdaftar atas pelanggan lain"),
    "not_found": (404, "Pelanggan tidak ditemukan atau sudah tidak aktif"),
}
_VOUCHER_ERRORS = {
    "not_found": "Kode voucher tidak dikenali",
    "inactive": "Voucher ini sudah dinonaktifkan",
    "not_started": "Voucher ini belum berlaku",
    "expired": "Voucher ini sudah kedaluwarsa",
    "used_up": "Voucher ini sudah terpakai",
    "min_spend_not_met": "Voucher ini berlaku untuk belanja minimal Rp {}",
}


def _voucher_message(exc) -> str:
    template = _VOUCHER_ERRORS.get(exc.code, "Voucher tidak bisa dipakai")
    try:
        return template.format(f"{Decimal(exc.detail):,.0f}".replace(",", ".")) if exc.detail else template
    except Exception:
        return template


_POINTS_ERRORS = {
    "inactive": "Program poin belum aktif — nyalakan di dashboard",
    "customer": "Bayar pakai poin perlu pelanggan terdaftar",
    "whole": "Nilai poin harus kelipatan nilai satu poin",
    "min": "Minimal tukar {} poin",
    "points": "Jumlah poin harus lebih dari nol",
    "delta": "Perubahan poin tidak boleh nol",
}


async def _pos_customer_out(ctx, view: dict) -> PosCustomerOut:
    from app.services.points import loyalty_config, rupiah_for_points

    cfg = await loyalty_config(ctx.session, ctx.business_id)
    balance = int(view.get("points_balance") or 0)
    return PosCustomerOut(**view, points_value=rupiah_for_points(balance, cfg) if cfg.is_active else Decimal(0))


@router.get("/loyalty", response_model=PosLoyaltyOut)
async def pos_loyalty(ctx: PosCtx):
    """The points programme, so the kiosk knows whether to offer "pakai poin" (M8-T2)."""
    from app.services.points import loyalty_config

    cfg = await loyalty_config(ctx.session, ctx.business_id)
    return PosLoyaltyOut(is_active=cfg.is_active, rupiah_per_point=cfg.rupiah_per_point, point_value=cfg.point_value,
                         min_redeem_points=cfg.min_redeem_points)


@router.get("/customers", response_model=list[PosCustomerOut])
async def pos_search_customers(ctx: PosCtx, q: str = Query(default="", max_length=60)):
    """Find a customer by name or phone to attach to the order (M8-T1)."""
    from app.services.customers import customer_views, search_customers

    rows = await search_customers(ctx.session, q, limit=10)
    return [await _pos_customer_out(ctx, v) for v in await customer_views(ctx.session, rows)]


@router.post("/customers", response_model=PosCustomerOut, status_code=201)
async def pos_add_customer(payload: PosCustomerIn, ctx: PosCtx):
    """Quick add from the kiosk. A phone that already belongs to someone is a
    409 with their name, not a second record."""
    from app.services.customers import CustomerInvalid, create_customer, customer_view

    try:
        row = await create_customer(ctx.session, ctx.business_id, name=payload.name, phone=payload.phone)
    except CustomerInvalid as exc:
        status, detail = _CUSTOMER_ERRORS[exc.code]
        raise HTTPException(status_code=status, detail=detail)
    return await _pos_customer_out(ctx, await customer_view(ctx.session, row))


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


# ── Kitchen display (M11-T2) ─────────────────────────────────────────────────
#
# Every paid order is a kitchen ticket; the board is what is not yet bumped.
# State changes append to kitchen_events and only move forward.


def _kitchen_out(t) -> KitchenTicketOut:
    return KitchenTicketOut(
        order_id=t.order_id, code=t.code, source=t.source, order_type=t.order_type, table_label=t.table_label,
        guest_name=t.guest_name, note=t.note, delivery_address=t.delivery_address,
        sold_at=t.sold_at, state=t.state, state_since=t.state_since,
        lines=[KitchenLineOut(name=l.name, quantity=l.quantity, modifiers=l.modifiers, notes=l.notes) for l in t.lines],
    )


@router.get("/kitchen", response_model=list[KitchenTicketOut])
async def pos_kitchen(ctx: PosCtx):
    from app.services.kitchen import board

    return [_kitchen_out(t) for t in await board(ctx.session)]


@router.post("/kitchen/{order_id}/state", response_model=KitchenTicketOut)
async def pos_kitchen_state(order_id: uuid.UUID, payload: KitchenStateIn, ctx: PosCtx):
    from app.services.kitchen import STATE_LABEL_ID, KitchenInvalid, set_state

    try:
        t = await set_state(ctx.session, business_id=ctx.business_id, order_id=order_id, state=payload.state, staff_id=ctx.staff_id)
    except KitchenInvalid as exc:
        if exc.code == "not_found":
            raise HTTPException(status_code=404, detail="Pesanan tidak ditemukan")
        if exc.code == "not_paid":
            raise HTTPException(status_code=409, detail="Pesanan belum dibayar — dapur hanya menyiapkan pesanan yang sudah dibayar")
        raise HTTPException(
            status_code=409,
            detail=f"Pesanan sudah {STATE_LABEL_ID.get(exc.current or '', exc.current)} — tidak bisa diubah ke {STATE_LABEL_ID.get(exc.wanted or '', exc.wanted)}",
        )
    return _kitchen_out(t)
