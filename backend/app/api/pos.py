"""POS kiosk endpoints.

Three access levels, escalating:
1. pairing token (in the kiosk URL) → may list staff names + attempt PIN login
2. PIN login → short-lived scope="pos" JWT for one staff member
3. pos JWT → item list + sale recording. Nothing else — owner analytics,
   staff management, and WhatsApp flows all require scope="owner".
"""
import time
import uuid

import jwt as pyjwt
from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.core.db import tenant_session
from app.core.deps import PosCtx
from app.core.security import create_token, decode_token, verify_pin
from app.models import Business, Item, RequestLog, Staff
from app.schemas.pos import (
    ItemOut,
    OrderIn,
    OrderLineOut,
    OrderOut,
    PaymentOut,
    PosBusinessOut,
    PosLoginIn,
    PosLoginOut,
    PosStaffOut,
    SaleIn,
    SaleOut,
)
from app.services.orders import OrderLineSpec, PaymentMismatch, PaymentSpec, create_order
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
    return items


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
                OrderLineSpec(item_id=l.item_id, quantity=l.quantity, unit_price=l.unit_price, notes=l.notes)
                for l in payload.lines
            ],
            payments=[PaymentSpec(method=p.method, amount=p.amount, reference=p.reference) for p in payload.payments],
        )
    except ItemNotFound:
        raise HTTPException(status_code=404, detail="Barang tidak ditemukan")
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
        total=order.total,
        sold_at=order.sold_at,
        lines=[
            OrderLineOut(
                id=cl.line.id, item_id=cl.line.item_id, item_name=cl.item_name, quantity=cl.line.quantity,
                unit_price=cl.line.unit_price, line_total=cl.line.line_total, remaining_stock=cl.remaining_stock,
            )
            for cl in created.lines
        ],
        payments=[PaymentOut(id=p.id, method=p.method, amount=p.amount, reference=p.reference) for p in created.payments],
    )
