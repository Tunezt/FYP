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
from app.ai.periods import period_range
from app.services import pin_guard
from app.core.deps import PosCtx
from app.schemas.pos import CashMovementIn, CashMovementOut, PosSupplierOut, ShiftCloseIn, ShiftOpenIn, ShiftOut
from app.schemas.menu import ActiveOrderOut, DraftIn, OpenOrderUpdateIn, PosTicketOut, RepriceIn, TicketCancelIn, TicketSettleIn
from app.schemas.pos import KitchenBoardOut, KitchenLineIn, KitchenLineOut, KitchenStateIn, KitchenTicketOut
from app.core.security import create_token, decode_token, verify_pin
from app.models import Business, Item, ItemVariant, Modifier, RequestLog, Staff
from app.schemas.pos import (
    ItemOut,
    OrderIn,
    OrderLineOut,
    OrderOut,
    OrderSummaryOut,
    OrdersPage,
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
    ChoiceMissing,
    ParentOrderInvalid,
    DiscountNeedsManager,
    require_explicit_choices,
    ManagerPinThrottled,
    list_orders,
    order_number,
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
from app.services.service_numbers import service_label
from app.services.velocity import check_low_stock_for_item

router = APIRouter(prefix="/pos", tags=["pos"])


STALE_PAIRING = "Perangkat ini sudah tidak dipasangkan — minta tautan kasir baru ke pemilik ya"


def _pairing_claims(pairing_token: str) -> tuple[uuid.UUID, int]:
    """(business_id, generation). The generation is checked against the business
    row by `_live_business` — a signed token is not enough on its own once the
    owner has re-paired (M15-T8)."""
    try:
        claims = decode_token(pairing_token)
    except pyjwt.PyJWTError:
        raise HTTPException(
            status_code=401, detail="Tautan kasir tidak berlaku — minta tautan baru ke pemilik ya"
        )
    if claims.get("scope") != "pos-pairing":
        raise HTTPException(status_code=401, detail="Tautan kasir tidak dikenali")
    return uuid.UUID(claims["business_id"]), int(claims.get("gen", 1))


async def _live_business(session, business_id: uuid.UUID, generation: int) -> Business:
    business = await session.get(Business, business_id)
    if business is None:
        raise HTTPException(status_code=404, detail="Usaha tidak ditemukan")
    if int(business.pairing_generation) != generation:
        raise HTTPException(status_code=401, detail=STALE_PAIRING)
    return business


@router.get("/business/{pairing_token}", response_model=PosBusinessOut)
async def pos_business(pairing_token: str):
    """Kiosk boot: business name + active staff names for the 'who are you' screen."""
    business_id, generation = _pairing_claims(pairing_token)
    async with tenant_session(business_id) as session:
        business = await _live_business(session, business_id, generation)
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


def _cooldown_message(cooldown) -> str:
    """M15-T12. Says how long to wait, because "salah" for the fourth time with
    no explanation is how a cashier concludes the tablet is broken."""
    seconds = cooldown.seconds
    if seconds < 60:
        wait = f"{seconds} detik"
    else:
        wait = f"{(seconds + 59) // 60} menit"
    return f"Terlalu banyak PIN salah — tunggu {wait} lalu coba lagi"


@router.post("/login", response_model=PosLoginOut)
async def pos_login(payload: PosLoginIn):
    """PIN entry is throttled per staff member and per device (M15-T12): an
    escalating cooldown, never a lock, because a till that stops trading mid-rush
    gets switched off and then nothing is protected."""
    business_id, generation = _pairing_claims(payload.pairing_token)
    async with tenant_session(business_id) as session:
        business = await _live_business(session, business_id, generation)
        who = pin_guard.staff_subject(payload.staff_id)
        device = pin_guard.device_subject(business.pairing_generation)
        for scope, subject in (("pos_login", who), ("pos_device", device)):
            cooling = await pin_guard.check(session, business_id, scope, subject, path="/pos/login")
            if cooling is not None:
                raise HTTPException(status_code=429, detail=_cooldown_message(cooling))

        staff = await session.get(Staff, payload.staff_id)
        if staff is None or not staff.is_active or not verify_pin(payload.pin, staff.pin_hash):
            # One message for both wrong-person and wrong-PIN: no oracle. The
            # count is still kept against the id that was tried, so guessing
            # against a staff member who does not exist is throttled too.
            earned = None
            for scope, subject in (("pos_login", who), ("pos_device", device)):
                hit = await pin_guard.record_failure(business_id, scope, subject, path="/pos/login")
                earned = earned or hit
            if earned is not None:
                raise HTTPException(status_code=429, detail=_cooldown_message(earned))
            raise HTTPException(status_code=401, detail="PIN salah — coba lagi")

        # Right first time or right in the end: either way it is forgiven.
        await pin_guard.clear(session, business_id, "pos_login", who)
        await pin_guard.clear(session, business_id, "pos_device", device)
        token = create_token(
            business_id=str(business_id), scope="pos", staff_id=str(staff.id),
            generation=business.pairing_generation,
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
            reorder_threshold=i.reorder_threshold, made_to_order=i.id in made_to_order, prep_station=i.prep_station,
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


_PARENT_ERRORS = {   # svc-3
    "not_found": "Pesanan utama tidak ditemukan",
    "unpaid": "Pesanan utama belum dibayar — tambahkan itemnya langsung ke pesanan itu",
    "reversed": "Pesanan utama sudah dibatalkan atau dikembalikan — buat pesanan baru",
}


def choice_missing_message(exc: ChoiceMissing) -> str:
    """svc-1: the one wording for a size nobody chose, at the till and on the menu."""
    return f"Ukuran {exc.item_name} belum dipilih — pilih ukurannya dulu"


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
    specs = [
        OrderLineSpec(item_id=l.item_id, variant_id=l.variant_id, modifier_ids=list(l.modifier_ids),
                      quantity=l.quantity, unit_price=l.unit_price, notes=l.notes,
                      line_discount=l.line_discount)
        for l in payload.lines
    ]
    from app.services.tickets import find_by_client_ref, replay_created

    replayed = await find_by_client_ref(ctx.session, ctx.business_id, payload.client_ref)
    if replayed is not None:
        if replayed.status == "open":
            raise HTTPException(status_code=409, detail="Kode transaksi ini dipakai pesanan yang belum dibayar — muat ulang kasir")
        return await _order_out(ctx.session, await replay_created(ctx.session, replayed))
    try:
        await require_explicit_choices(ctx.session, specs)
        created = await create_order(
            ctx.session,
            business_id=ctx.business_id,
            staff_id=ctx.staff_id,
            order_type=payload.order_type,
            lines=specs,
            payments=[PaymentSpec(method=p.method, amount=p.amount, reference=p.reference) for p in payload.payments],
            bill_discount=payload.bill_discount,
            manager_pin=payload.manager_pin,
            customer_id=payload.customer_id,
            voucher_code=payload.voucher_code,
            table_label=payload.table_label,
            delivery_address=payload.delivery_address,
            guest_name=payload.guest_name,
            guest_phone=payload.guest_phone,
            parent_order_id=payload.parent_order_id,
            external_ref=payload.external_ref,
        )
    except ParentOrderInvalid as exc:
        raise HTTPException(status_code=409, detail=_PARENT_ERRORS[exc.code])
    except ChoiceMissing as exc:
        raise HTTPException(status_code=422, detail=choice_missing_message(exc))
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
    except ManagerPinThrottled as exc:
        raise HTTPException(status_code=429, detail=_cooldown_message(exc.cooldown))
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

    if payload.client_ref:
        created.order.client_ref = payload.client_ref
        await ctx.session.flush()
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
        parent_order_id=order.parent_order_id,
        parent_number=order_number(order.parent_order_id) if order.parent_order_id else None,
        order_no=service_label(order), batch_no=order.batch_no or 0,
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
    from app.services.tickets import OrderChanged, PricesChanged, TicketNotFound, find_by_client_ref, get_open_order, settle_ticket

    start = time.perf_counter()
    try:
        # The lock on the reference comes before the row is read, so a second
        # copy of the same tap waits for the first and then replays it (svc-2).
        await find_by_client_ref(ctx.session, ctx.business_id, payload.client_ref)
        ticket = await get_open_order(ctx.session, order_id)
        created = await settle_ticket(
            ctx.session, business_id=ctx.business_id, ticket=ticket, staff_id=ctx.staff_id,
            payments=[PaymentSpec(method=p.method, amount=p.amount, reference=p.reference) for p in payload.payments],
            bill_discount=payload.bill_discount, manager_pin=payload.manager_pin,
            customer_id=payload.customer_id, voucher_code=payload.voucher_code,
            expected_rev=payload.rev, client_ref=payload.client_ref,
        )
    except TicketNotFound:
        raise HTTPException(status_code=404, detail="Pesanan tidak ditemukan")
    except TicketNotOpen as exc:
        raise HTTPException(status_code=409, detail=_TICKET_CLOSED.get(exc.status, "Pesanan ini sudah diproses"))
    except OrderChanged as exc:
        raise HTTPException(status_code=409, detail=order_changed_message(exc))
    except PricesChanged as exc:
        raise HTTPException(status_code=409, detail=prices_changed_message(exc))
    except ParentOrderInvalid as exc:
        raise HTTPException(status_code=409, detail=_PARENT_ERRORS[exc.code])
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
    except ManagerPinThrottled as exc:
        raise HTTPException(status_code=429, detail=_cooldown_message(exc.cooldown))
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
    from app.services.tickets import TicketNotFound, cancel_ticket, get_open_order

    try:
        ticket = await get_open_order(ctx.session, order_id)
        ticket = await cancel_ticket(ctx.session, ticket=ticket, reason=payload.reason, staff_id=ctx.staff_id)
    except TicketNotFound:
        raise HTTPException(status_code=404, detail="Pesanan tidak ditemukan")
    except TicketNotOpen as exc:
        raise HTTPException(status_code=409, detail=_TICKET_CLOSED.get(exc.status, "Pesanan ini sudah diproses"))
    return ticket_out(ticket, PosTicketOut)


def prices_changed_message(exc) -> str:
    """svc-5: name what moved, so the cashier can say it to the customer."""
    parts = []
    for ch in exc.changes[:3]:
        if ch.now is None:
            parts.append(f"{ch.name} sudah tidak tersedia")
        else:
            parts.append(f"{ch.name} {_rp(ch.was)} → {_rp(ch.now)}")
    more = f" dan {len(exc.changes) - 3} lainnya" if len(exc.changes) > 3 else ""
    return f"Harga berubah sejak dipesan: {'; '.join(parts)}{more} — perbarui pesanan dan konfirmasi ke pelanggan dulu"


def order_changed_message(exc) -> str:
    """svc-2: somebody else got to this order first. Say what happened, so the
    cashier reloads instead of retrying the same stale action."""
    if exc.status in _TICKET_CLOSED:
        return _TICKET_CLOSED[exc.status]
    return "Pesanan ini baru saja diubah di perangkat lain — muat ulang dulu, lalu periksa isinya"


# ── Open orders and the active-orders workspace (svc-2) ─────────────────────


async def _price_errors(fn):
    """The cart-pricing failures, in the till's own words."""
    from app.services.tickets import QueueFull, TicketUnavailable

    try:
        return await fn()
    except QueueFull:
        raise HTTPException(status_code=429, detail="Terlalu banyak pesanan tersimpan yang belum dibayar — selesaikan atau batalkan dulu")
    except TicketUnavailable as exc:
        raise HTTPException(status_code=409, detail=f"Stok {exc.item_name} sedang habis — hapus dari pesanan atau ganti menu")
    except ChoiceMissing as exc:
        raise HTTPException(status_code=422, detail=choice_missing_message(exc))
    except ItemNotFound:
        raise HTTPException(status_code=404, detail="Barang tidak ditemukan")
    except VariantNotFound:
        raise HTTPException(status_code=404, detail="Ukuran barang tidak ditemukan atau sudah tidak aktif")
    except ModifierSelectionInvalid as exc:
        messages = {
            "unknown": "Pilihan tambahan tidak dikenali atau sudah tidak aktif",
            "required": f"Pilihan '{exc.group_name}' wajib diisi",
            "single": f"Pilihan '{exc.group_name}' hanya boleh satu",
            "max": f"Pilihan '{exc.group_name}' melebihi batas maksimal",
        }
        raise HTTPException(status_code=422, detail=messages[exc.code])


def _cart_specs(lines) -> list[OrderLineSpec]:
    return [
        OrderLineSpec(item_id=l.item_id, variant_id=l.variant_id, modifier_ids=list(l.modifier_ids), quantity=l.quantity, notes=l.notes)
        for l in lines
    ]


async def _active_views(session, orders, tickets_by_id=None):
    """Unpaid carts and paid-not-handed-over tickets, in one shape."""
    from app.schemas.menu import ActiveLineOut, ActiveOrderOut
    from app.services.kitchen import order_code

    staff_ids = {o.staff_id for o in orders if o.staff_id}
    names = {}
    if staff_ids:
        names = dict((await session.execute(select(Staff.id, Staff.name).where(Staff.id.in_(staff_ids)))).all())
    from app.services.service_numbers import service_day

    today = await service_day(session, orders[0].business_id) if orders else None
    from app.schemas.menu import PrintSummaryOut
    from app.services.printing import display_status, jobs_for_orders

    jobs_by_order = await jobs_for_orders(session, [o.id for o in orders])
    parents = {}
    parent_ids = {getattr(o, "parent_order_id", None) for o in orders} - {None}
    if parent_ids:
        from app.models import Order as _Order

        for pid in parent_ids:
            parents[pid] = await session.get(_Order, pid)
    out = []
    for o in orders:
        cart = o.cart or {}
        ticket = (tickets_by_id or {}).get(o.id)
        if o.status == "open":
            lines = [
                ActiveLineOut(
                    name=l["item_name"], size=l.get("variant_name"), quantity=l["quantity"],
                    modifiers=list(l.get("modifier_names", [])), notes=l.get("notes"), line_total=l.get("line_total"),
                    item_id=uuid.UUID(l["item_id"]), variant_id=uuid.UUID(l["variant_id"]) if l.get("variant_id") else None,
                    modifier_ids=[uuid.UUID(m) for m in l.get("modifier_ids", [])],
                )
                for l in cart.get("lines", [])
            ]
        elif ticket is not None:
            lines = [
                ActiveLineOut(name=l.item_name or l.name, size=l.size, quantity=l.quantity, modifiers=l.modifiers, notes=l.notes,
                              done=getattr(l, "done", False))
                for l in ticket.lines
            ]
        else:
            lines = []
        payment = {"open": "unpaid", "completed": "paid", "voided": "cancelled" if o.cart is not None and "cancelled" in cart else "reversed",
                   "refunded": "reversed"}.get(o.status, "paid")
        parent = parents.get(getattr(o, "parent_order_id", None))
        changes = []
        if o.status == "open":
            from app.schemas.menu import PriceChangeOut
            from app.services.tickets import price_changes

            changes = [PriceChangeOut(name=ch.name, was=ch.was, now=ch.now) for ch in await price_changes(session, o)]
        summaries: dict[str, PrintSummaryOut] = {}
        for j in jobs_by_order.get(o.id, []):
            if j.copy == "reprint":
                if j.kind in summaries:
                    summaries[j.kind].reprints += 1
                    summaries[j.kind].job_id, summaries[j.kind].status = j.id, display_status(j)
                continue
            summaries[j.kind] = PrintSummaryOut(job_id=j.id, kind=j.kind, printer=j.printer, status=display_status(j))
        out.append(ActiveOrderOut(
            print_jobs=list(summaries.values()),
            order_no=service_label(o), service_date=o.service_date, batch_no=o.batch_no or 0,
            external_ref=o.external_ref, previous_day=bool(today and o.service_date and o.service_date < today),
            price_changes=changes,
            id=o.id, code=order_code(o), number=order_number(o.id), source=o.source, status=o.status, payment=payment,
            prep=ticket.state if ticket is not None else None,
            order_type=o.order_type, table_label=o.table_label, guest_name=o.guest_name, note=cart.get("note"),
            placed_at=o.created_at, paid_at=o.sold_at if o.status != "open" else None,
            prep_since=ticket.state_since if ticket is not None else None,
            total=o.total, is_estimate=o.status == "open", rev=int(cart.get("rev", 0)),
            staff_name=names.get(o.staff_id), lines=lines,
            parent_id=parent.id if parent is not None else None,
            parent_code=order_code(parent) if parent is not None else None,
        ))
    return out


@router.get("/active-orders", response_model=list[ActiveOrderOut])
async def pos_active_orders(ctx: PosCtx):
    """Everything the counter still owes someone (svc-2): unpaid carts from the
    till and the QR menu, and paid orders the kitchen has not handed over.
    Oldest first — the customer who has waited longest is at the top."""
    from app.models import Order as _Order
    from app.schemas.menu import ActiveOrderOut  # noqa: F401 (response shape)
    from app.services.kitchen import board
    from app.services.tickets import open_orders

    unpaid = await open_orders(ctx.session)
    tickets = await board(ctx.session)
    paid = [await ctx.session.get(_Order, t.order_id) for t in tickets]
    views = await _active_views(ctx.session, unpaid + paid, {t.order_id: t for t in tickets})
    return sorted(views, key=lambda v: (v.placed_at, str(v.id)))


@router.get("/open-orders/{order_id}", response_model=ActiveOrderOut)
async def pos_open_order(order_id: uuid.UUID, ctx: PosCtx):
    """One unpaid (or just-settled) held cart, fresh, with its revision."""
    from app.services.tickets import TicketNotFound, get_open_order

    try:
        order = await get_open_order(ctx.session, order_id)
    except TicketNotFound:
        raise HTTPException(status_code=404, detail="Pesanan tidak ditemukan")
    return (await _active_views(ctx.session, [order]))[0]


@router.post("/drafts", response_model=ActiveOrderOut, status_code=201)
async def pos_hold_draft(payload: DraftIn, ctx: PosCtx):
    """Park an unpaid order on the server (svc-2). Replaying `client_ref`
    returns the draft already held."""
    from app.services.tickets import hold_draft

    try:
        order = await _price_errors(lambda: hold_draft(
            ctx.session, business_id=ctx.business_id, staff_id=ctx.staff_id, lines=_cart_specs(payload.lines),
            order_type=payload.order_type, table_label=payload.table_label, guest_name=payload.guest_name,
            note=payload.note, client_ref=payload.client_ref, parent_order_id=payload.parent_order_id,
            external_ref=payload.external_ref,
        ))
    except ParentOrderInvalid as exc:
        raise HTTPException(status_code=409, detail=_PARENT_ERRORS[exc.code])
    return (await _active_views(ctx.session, [order]))[0]


@router.put("/open-orders/{order_id}", response_model=ActiveOrderOut)
async def pos_update_open_order(order_id: uuid.UUID, payload: OpenOrderUpdateIn, ctx: PosCtx):
    """Change an unpaid order before payment — a held draft or a guest's QR
    order. Refused with 409 if it was paid, cancelled or edited elsewhere."""
    from app.services.orders import TicketNotOpen
    from app.services.tickets import OrderChanged, TicketNotFound, get_open_order, update_open_order

    try:
        order = await get_open_order(ctx.session, order_id)
    except TicketNotFound:
        raise HTTPException(status_code=404, detail="Pesanan tidak ditemukan")
    try:
        order = await _price_errors(lambda: update_open_order(
            ctx.session, business_id=ctx.business_id, order=order, expected_rev=payload.rev, staff_id=ctx.staff_id,
            lines=_cart_specs(payload.lines), order_type=payload.order_type, table_label=payload.table_label,
            guest_name=payload.guest_name, note=payload.note, external_ref=payload.external_ref,
        ))
    except TicketNotOpen as exc:
        raise HTTPException(status_code=409, detail=_TICKET_CLOSED.get(exc.status, "Pesanan ini sudah diproses"))
    except OrderChanged as exc:
        raise HTTPException(status_code=409, detail=order_changed_message(exc))
    return (await _active_views(ctx.session, [order]))[0]


@router.post("/open-orders/{order_id}/reprice", response_model=ActiveOrderOut)
async def pos_reprice_open_order(order_id: uuid.UUID, payload: RepriceIn, ctx: PosCtx):
    """Bring an unpaid order to today's prices without changing what is in it
    (svc-5). A line whose product, size or option is gone cannot be re-priced;
    the cashier edits it out instead."""
    from app.services.orders import TicketNotOpen
    from app.services.tickets import OrderChanged, TicketNotFound, get_open_order, ticket_specs, update_open_order

    try:
        order = await get_open_order(ctx.session, order_id)
    except TicketNotFound:
        raise HTTPException(status_code=404, detail="Pesanan tidak ditemukan")
    specs = [OrderLineSpec(item_id=s.item_id, variant_id=s.variant_id, modifier_ids=s.modifier_ids, quantity=s.quantity, notes=s.notes)
             for s in ticket_specs(order)] if order.status == "open" else []
    try:
        order = await _price_errors(lambda: update_open_order(
            ctx.session, business_id=ctx.business_id, order=order, expected_rev=payload.rev, staff_id=ctx.staff_id, lines=specs,
        ))
    except TicketNotOpen as exc:
        raise HTTPException(status_code=409, detail=_TICKET_CLOSED.get(exc.status, "Pesanan ini sudah diproses"))
    except OrderChanged as exc:
        raise HTTPException(status_code=409, detail=order_changed_message(exc))
    return (await _active_views(ctx.session, [order]))[0]


async def receipt_view(session, business_id: uuid.UUID, order_id: uuid.UUID) -> ReceiptOut:
    """What gets printed (browser print, M4-T2): every line with its size and
    modifiers as sold, payments, totals. Voided orders print with their
    reversing lines so the paper says what happened.

    Shared by the till and the owner's dashboard (M15-T11): the owner deciding
    whether to void a sale must be looking at exactly what the cashier printed,
    so there is one shaping function and not two that can drift."""
    try:
        data = await load_receipt(session, business_id=business_id, order_id=order_id)
    except OrderNotFound:
        raise HTTPException(status_code=404, detail="Transaksi tidak ditemukan")
    from app.services.points import points_of_order
    from app.services.pricing import pricing_config

    business = await session.get(Business, business_id)
    config = await pricing_config(session, business_id)
    order = data["order"]
    earned, redeemed = await points_of_order(session, order.id)
    from app.models import Promo
    from app.services.promos import applications_of_order

    from app.models import Voucher
    from app.services.vouchers import redemptions_of_order

    voucher_code = None
    for red in await redemptions_of_order(session, order.id):
        if red.amount > 0:
            v = await session.get(Voucher, red.voucher_id)
            voucher_code = v.code if v else None
    promo_names: list[str] = []
    for app_ in await applications_of_order(session, order.id):
        pr = await session.get(Promo, app_.promo_id)
        if pr is not None and pr.name not in promo_names:
            promo_names.append(pr.name)
    return ReceiptOut(
        order_id=order.id,
        number=order_number(order.id),
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
        parent_number=order_number(order.parent_order_id) if order.parent_order_id else None,
        order_no=service_label(order), service_date=order.service_date, batch_no=order.batch_no or 0,
        external_ref=order.external_ref,
    )


@router.get("/orders/{order_id}/receipt", response_model=ReceiptOut)
async def pos_receipt(order_id: uuid.UUID, ctx: PosCtx):
    return await receipt_view(ctx.session, ctx.business_id, order_id)


@router.get("/orders", response_model=OrdersPage)
async def pos_recent_orders(
    ctx: PosCtx,
    q: str = Query(default="", max_length=40),
    limit: int = Query(default=40, ge=1, le=100),
):
    """Today's sales, newest first — the list a cashier opens to find the one
    they rang up wrong (M15-T11). "Today" is the business day (M15-T4), so a
    23:50 sale is still findable at 00:15 without scrolling into yesterday.

    Anything older is the owner's job on the dashboard, because a mistake found
    after the shift closed is a different conversation from one found while the
    customer is still standing there."""
    business = await ctx.session.get(Business, ctx.business_id)
    since, until, _ = period_range("today", business.timezone, day_start_hour=business.day_start_hour)
    rows, total = await list_orders(ctx.session, since=since, until=until, q=q, limit=limit)
    return OrdersPage(total=total, rows=[OrderSummaryOut(**vars(r)) for r in rows])


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


async def run_reversal(ctx, order_id: uuid.UUID, fn, path: str, *, channel: str = "pos", **kwargs) -> ReversalOut:
    """One reversal path for both surfaces (M15-T11). The till and the owner's
    dashboard differ only in `channel` and in which staff id is doing the
    asking; the guard, the error text and the audit row are identical, because
    a void done from the office must not be a different kind of void."""
    start = time.perf_counter()
    try:
        rev = await fn(ctx.session, business_id=ctx.business_id, order_id=order_id, staff_id=ctx.staff_id, **kwargs)
    except ManagerPinThrottled as exc:
        raise HTTPException(status_code=429, detail=_cooldown_message(exc.cooldown))
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
            business_id=ctx.business_id, channel=channel, path=path,
            latency_ms=int((time.perf_counter() - start) * 1000), status="ok",
        )
    )
    return _reversal_out(rev)


@router.post("/orders/{order_id}/void", response_model=ReversalOut)
async def pos_void_order(order_id: uuid.UUID, payload: ReversalIn, ctx: PosCtx):
    """Manager-PIN gated. Writes reversing lines, payments and stock movements;
    the original order stays readable in full (M3-T4)."""
    return await run_reversal(
        ctx, order_id, void_order, "/pos/orders/{id}/void", manager_pin=payload.manager_pin, note=payload.note,
    )


@router.post("/orders/{order_id}/refund", response_model=ReversalOut)
async def pos_refund_order(order_id: uuid.UUID, payload: RefundIn, ctx: PosCtx):
    """Manager-PIN gated. Like void, but `restock=false` keeps stock down when
    the goods are not coming back."""
    return await run_reversal(
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
        sold_at=t.sold_at, state=t.state, state_since=t.state_since, parent_code=t.parent_code,
        order_no=t.order_no, batch_no=t.batch_no,
        status=t.status, reversal_reason=t.reversal_reason, reversed_by=t.reversed_by, reversed_at=t.reversed_at,
        state_by=t.state_by,
        lines=[
            KitchenLineOut(name=l.name, quantity=l.quantity, modifiers=l.modifiers, notes=l.notes, line_id=l.line_id,
                           item_name=l.item_name, size=l.size, done=l.done)
            for l in t.lines
        ],
    )


@router.get("/kitchen", response_model=list[KitchenTicketOut])
async def pos_kitchen(ctx: PosCtx):
    from app.services.kitchen import board

    return [_kitchen_out(t) for t in await board(ctx.session)]


@router.get("/kitchen/board", response_model=KitchenBoardOut)
async def pos_kitchen_board(ctx: PosCtx):
    """The kitchen screen in one read (svc-4): open tickets oldest first, paid
    orders reversed before handover, and what left the pass recently."""
    from datetime import datetime, timezone

    from app.services.kitchen import board, cancellations, history

    now = datetime.now(timezone.utc)
    return KitchenBoardOut(
        server_time=now,
        tickets=[_kitchen_out(t) for t in await board(ctx.session, now=now)],
        cancellations=[_kitchen_out(t) for t in await cancellations(ctx.session, now=now)],
        history=[_kitchen_out(t) for t in await history(ctx.session, now=now)],
    )


def _kitchen_error(exc) -> HTTPException:
    from app.services.kitchen import STATE_LABEL_ID

    label = lambda s: STATE_LABEL_ID.get(s or "", s)  # noqa: E731
    if exc.code == "not_found":
        return HTTPException(status_code=404, detail="Pesanan tidak ditemukan")
    if exc.code == "line_not_found":
        return HTTPException(status_code=404, detail="Item pesanan tidak ditemukan")
    if exc.code == "not_paid":
        return HTTPException(status_code=409, detail="Pesanan belum dibayar — dapur hanya menyiapkan pesanan yang sudah dibayar")
    if exc.code == "conflict":
        return HTTPException(status_code=409, detail=f"Pesanan ini sudah {label(exc.current)} dari perangkat lain — layar sudah diperbarui")
    if exc.code == "lines_pending":
        return HTTPException(status_code=409, detail=f"Masih ada {exc.pending} item belum selesai — tandai semua item dulu")
    if exc.code == "closed":
        return HTTPException(status_code=409, detail=f"Pesanan sudah {label(exc.current)} — item tidak bisa diubah lagi")
    return HTTPException(
        status_code=409,
        detail=f"Pesanan sudah {label(exc.current)} — tidak bisa diubah ke {label(exc.wanted)}",
    )


@router.post("/kitchen/{order_id}/state", response_model=KitchenTicketOut)
async def pos_kitchen_state(order_id: uuid.UUID, payload: KitchenStateIn, ctx: PosCtx):
    from app.services.kitchen import KitchenInvalid, set_state

    try:
        t = await set_state(ctx.session, business_id=ctx.business_id, order_id=order_id, state=payload.state,
                            staff_id=ctx.staff_id, expected=payload.expected)
    except KitchenInvalid as exc:
        raise _kitchen_error(exc)
    return _kitchen_out(t)


@router.post("/kitchen/{order_id}/lines/{line_id}", response_model=KitchenTicketOut)
async def pos_kitchen_line(order_id: uuid.UUID, line_id: uuid.UUID, payload: KitchenLineIn, ctx: PosCtx):
    """Tick or untick one line of a paid ticket (svc-4)."""
    from app.services.kitchen import KitchenInvalid, set_line_done

    try:
        t = await set_line_done(ctx.session, business_id=ctx.business_id, order_id=order_id, line_id=line_id,
                                done=payload.done, staff_id=ctx.staff_id)
    except KitchenInvalid as exc:
        raise _kitchen_error(exc)
    return _kitchen_out(t)
