"""Owner auth (dashboard-first onboarding, per the capstone proposal Chapter 3).

Flow: request-otp → WhatsApp Authentication-template OTP → verify-otp →
either an owner JWT (existing business) or a registration token → register
(business profile + owner PIN) → owner JWT. Staff management and POS pairing
are owner-only.
"""
import logging
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.core import otp_fallback
from app.core.config import get_settings
from app.core.db import plain_session, tenant_session
from app.core.db_errors import raise_if_db_unreachable
from app.schemas.menu import MenuLinkOut
from app.core.deps import OwnerCtx
from app.core.security import (
    create_menu_token,
    create_pairing_token,
    create_registration_token,
    create_token,
    decode_token,
    generate_otp,
    hash_otp,
    hash_pin,
)
from app.models import Business, LoginOtp, Staff
from app.schemas.auth import (
    BusinessOut,
    OtpVerifyIn,
    OtpVerifyOut,
    PairingOut,
    PhoneIn,
    RegisterIn,
    RegisterOut,
    StaffCreateIn,
    StaffOut,
    StaffPinResetIn,
    StaffUpdateIn,
)
from app.whatsapp.client import send_otp_template

logger = logging.getLogger("auth")

router = APIRouter(prefix="/auth", tags=["auth"])

OTP_TTL_MINUTES = 10
OTP_MAX_ATTEMPTS = 5


@router.post("/request-otp")
async def request_otp(payload: PhoneIn):
    settings = get_settings()
    code = generate_otp()
    now = datetime.now(timezone.utc)

    try:
        async with plain_session() as session:
            existing = await session.get(LoginOtp, payload.phone)
            if existing:
                await session.delete(existing)
                await session.flush()
            session.add(
                LoginOtp(
                    phone=payload.phone,
                    code_hash=hash_otp(code),
                    attempts=0,
                    expires_at=now + timedelta(minutes=OTP_TTL_MINUTES),
                )
            )
            registered = (
                await session.execute(select(Business.id).where(Business.owner_phone == payload.phone))
            ).scalar_one_or_none() is not None
    except Exception as exc:
        raise_if_db_unreachable(exc)

    await send_otp_template(payload.phone, code)
    otp_fallback.log_code(payload.phone, code)
    if settings.environment == "development":
        # Dev convenience only — real deployments deliver via WhatsApp.
        logger.info("[DEV] OTP for %s is %s", payload.phone, code)

    return {"sent": True, "registered": registered, "ttl_minutes": OTP_TTL_MINUTES}


DEV_BYPASS_CODE = "000000"


def is_dev_bypass_code(code: str, settings) -> bool:
    """`000000` logs in without a real code — development only, never production,
    whatever else is switched on (the OTP log fallback included)."""
    return settings.environment == "development" and code == DEV_BYPASS_CODE


@router.post("/verify-otp", response_model=OtpVerifyOut)
async def verify_otp(payload: OtpVerifyIn):
    now = datetime.now(timezone.utc)
    settings = get_settings()
    is_dev_bypass = is_dev_bypass_code(payload.code, settings)

    try:
        async with plain_session() as session:
            if not is_dev_bypass:
                record = await session.get(LoginOtp, payload.phone)
                if record is None or record.expires_at < now:
                    raise HTTPException(
                        status_code=400, detail="Kodenya sudah kedaluwarsa — minta kode baru ya"
                    )
                if record.attempts >= OTP_MAX_ATTEMPTS:
                    await session.delete(record)
                    raise HTTPException(
                        status_code=429, detail="Terlalu banyak percobaan — minta kode baru ya"
                    )
                if record.code_hash != hash_otp(payload.code):
                    record.attempts += 1
                    raise HTTPException(status_code=400, detail="Kodenya salah — cek lagi ya")

                # Success — single use.
                await session.delete(record)
            business = (
                await session.execute(select(Business).where(Business.owner_phone == payload.phone))
            ).scalar_one_or_none()
    except HTTPException:
        raise
    except Exception as exc:
        raise_if_db_unreachable(exc)

    if business is None:
        return OtpVerifyOut(
            registered=False, registration_token=create_registration_token(payload.phone)
        )
    return OtpVerifyOut(
        registered=True,
        token=create_token(business_id=str(business.id), scope="owner"),
        business=BusinessOut.model_validate(business),
    )


@router.post("/register", response_model=RegisterOut)
async def register(payload: RegisterIn):
    try:
        claims = decode_token(payload.registration_token)
    except pyjwt.PyJWTError:
        raise HTTPException(
            status_code=401, detail="Sesi pendaftaran sudah berakhir — verifikasi nomormu lagi ya"
        )
    if claims.get("scope") != "register":
        raise HTTPException(
            status_code=401, detail="Sesi pendaftaran tidak dikenali — mulai lagi dari halaman masuk"
        )
    phone = claims["phone"]

    async with plain_session() as session:
        exists = (
            await session.execute(select(Business.id).where(Business.owner_phone == phone))
        ).scalar_one_or_none()
        if exists:
            raise HTTPException(
                status_code=409, detail="Nomor ini sudah punya usaha terdaftar — silakan masuk saja"
            )
        business = Business(
            name=payload.business_name,
            business_type=payload.business_type,
            owner_phone=phone,
            language_preference=payload.language_preference,
            timezone=payload.timezone,
        )
        session.add(business)
        await session.flush()
        business_id = business.id

    # Owner's own staff row (role='owner') so they can use the POS too and so
    # sales.staff_id always resolves. Written under tenant context (RLS).
    async with tenant_session(business_id) as session:
        session.add(
            Staff(
                business_id=business_id,
                name=payload.owner_name,
                role="owner",
                phone=phone,
                pin_hash=hash_pin(payload.owner_pin),
            )
        )
        # Standard units of measure and conversions (M4-T3) — every business
        # starts with kg/g, liter/ml and the usual warung units.
        from app.services.accounts import ensure_standard_chart
        from app.services.units import ensure_standard_uoms

        from app.services.posting_rules import ensure_standard_rules
        from app.services.points import ensure_loyalty_settings
        from app.services.pricing import ensure_pricing_settings

        await ensure_standard_uoms(session, business_id)
        await ensure_standard_chart(session, business_id)  # M6-T1
        await ensure_standard_rules(session, business_id)  # M6-T3
        await ensure_pricing_settings(session, business_id)  # M7-T4
        await ensure_loyalty_settings(session, business_id)  # M8-T2
        business = await session.get(Business, business_id)
        business_out = BusinessOut.model_validate(business)

    return RegisterOut(
        token=create_token(business_id=str(business_id), scope="owner"), business=business_out
    )


# ── Owner-only staff management + POS pairing ────────────────────────────────


@router.get("/staff", response_model=list[StaffOut])
async def list_staff(ctx: OwnerCtx):
    rows = (
        (await ctx.session.execute(select(Staff).order_by(Staff.created_at))).scalars().all()
    )
    return rows


@router.post("/staff", response_model=StaffOut)
async def create_staff(payload: StaffCreateIn, ctx: OwnerCtx):
    """`role` is `staff` or `manager` (M15-T7). A manager can approve a void, a
    refund or a discount at the till and nothing else — reports and settings
    need owner scope, which is issued only to the phone that owns the business,
    never from a staff row."""
    staff = Staff(
        business_id=ctx.business_id,
        name=payload.name,
        role=payload.role,
        phone=payload.phone,
        pin_hash=hash_pin(payload.pin),
    )
    ctx.session.add(staff)
    await ctx.session.flush()
    return staff


@router.patch("/staff/{staff_id}", response_model=StaffOut)
async def update_staff_role(staff_id: str, payload: StaffUpdateIn, ctx: OwnerCtx):
    """Promote a cashier to manager when the owner cannot always be on site, or
    take it back. The owner's own row is not demotable: it is the login."""
    staff = await ctx.session.get(Staff, staff_id)
    if staff is None:
        raise HTTPException(status_code=404, detail="Staf tidak ditemukan")
    if staff.role == "owner":
        raise HTTPException(status_code=400, detail="Peran pemilik tidak bisa diubah")
    staff.role = payload.role
    await ctx.session.flush()
    return staff


@router.post("/staff/{staff_id}/deactivate", response_model=StaffOut)
async def deactivate_staff(staff_id: str, ctx: OwnerCtx):
    staff = await ctx.session.get(Staff, staff_id)
    if staff is None:
        raise HTTPException(status_code=404, detail="Staf tidak ditemukan")
    if staff.role == "owner":
        raise HTTPException(status_code=400, detail="Akun pemilik tidak bisa dinonaktifkan")
    staff.is_active = False
    return staff


@router.post("/staff/{staff_id}/pin", response_model=StaffOut)
async def reset_staff_pin(staff_id: str, payload: StaffPinResetIn, ctx: OwnerCtx):
    """A PIN forgotten mid-service (M15-T8). The owner sets a new one from the
    dashboard and the cashier is back on the till on the next screen — no
    support request, no waiting. The old PIN stops working at once; anything
    already rung up under it is untouched, because a PIN is not an identity."""
    staff = await ctx.session.get(Staff, staff_id)
    if staff is None:
        raise HTTPException(status_code=404, detail="Staf tidak ditemukan")
    staff.pin_hash = hash_pin(payload.pin)
    await ctx.session.flush()
    return staff


async def _current_business(ctx: OwnerCtx) -> Business:
    business = await ctx.session.get(Business, ctx.business_id)
    if business is None:
        raise HTTPException(status_code=404, detail="Usaha tidak ditemukan")
    return business


@router.post("/pos-pairing", response_model=PairingOut)
async def create_pos_pairing(ctx: OwnerCtx):
    """The link for the tablet at the counter. Showing it again is safe: it is
    minted under the current generation and cuts nobody off (M15-T8)."""
    business = await _current_business(ctx)
    token = create_pairing_token(str(ctx.business_id), business.pairing_generation)
    return PairingOut(pairing_token=token, pos_path=f"/pos/{token}")


@router.post("/pos-pairing/reset", response_model=PairingOut)
async def reset_pos_pairing(ctx: OwnerCtx):
    """The tablet is gone — stolen, sold, or simply not coming back (M15-T8).

    Raising the generation retires every kiosk link and every till session
    issued before now, including the one on a device that is still working. That
    bluntness is the point: you re-pair because a device is out of your hands,
    and anything short of "everything before now is void" is not a cut-off."""
    business = await _current_business(ctx)
    business.pairing_generation = int(business.pairing_generation) + 1
    await ctx.session.flush()
    token = create_pairing_token(str(ctx.business_id), business.pairing_generation)
    return PairingOut(pairing_token=token, pos_path=f"/pos/{token}")


@router.post("/menu-link", response_model=MenuLinkOut)
async def create_menu_link(ctx: OwnerCtx):
    """The QR e-menu link (M11-T1): print it as a QR code on every table."""
    token = create_menu_token(str(ctx.business_id))
    return MenuLinkOut(menu_token=token, menu_path=f"/menu/{token}")
