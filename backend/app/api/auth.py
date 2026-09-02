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

from app.core.config import get_settings
from app.core.db import plain_session, tenant_session
from app.core.db_errors import raise_if_db_unreachable
from app.core.deps import OwnerCtx
from app.core.security import (
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
    if settings.environment == "development":
        # Dev convenience only — real deployments deliver via WhatsApp.
        logger.info("[DEV] OTP for %s is %s", payload.phone, code)

    return {"sent": True, "registered": registered, "ttl_minutes": OTP_TTL_MINUTES}


DEV_BYPASS_CODE = "000000"


@router.post("/verify-otp", response_model=OtpVerifyOut)
async def verify_otp(payload: OtpVerifyIn):
    now = datetime.now(timezone.utc)
    settings = get_settings()
    is_dev_bypass = settings.environment == "development" and payload.code == DEV_BYPASS_CODE

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
        from app.services.units import ensure_standard_uoms

        await ensure_standard_uoms(session, business_id)
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
    staff = Staff(
        business_id=ctx.business_id,
        name=payload.name,
        role="staff",
        phone=payload.phone,
        pin_hash=hash_pin(payload.pin),
    )
    ctx.session.add(staff)
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


@router.post("/pos-pairing", response_model=PairingOut)
async def create_pos_pairing(ctx: OwnerCtx):
    token = create_pairing_token(str(ctx.business_id))
    return PairingOut(pairing_token=token, pos_path=f"/pos/{token}")
