"""FastAPI auth dependencies.

Role separation is enforced here, not in the UI: every protected route depends
on either `owner_ctx` (scope="owner") or `pos_ctx` (scope="pos"), and a token
with the wrong scope is rejected with 403 before any query runs. The session
each context carries is already pinned to the token's business via
`SET LOCAL app.current_business_id`, so RLS backs up the scope check.
"""
import uuid
from dataclasses import dataclass
from typing import Annotated, AsyncIterator

import jwt as pyjwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal, set_tenant
from app.core.security import decode_token


@dataclass
class AuthContext:
    business_id: uuid.UUID
    scope: str
    staff_id: uuid.UUID | None
    session: AsyncSession


def _extract_claims(request: Request, required_scope: str) -> dict:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Belum masuk — silakan masuk dulu ya")
    try:
        claims = decode_token(header.removeprefix("Bearer "))
    except pyjwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Sesi sudah berakhir — silakan masuk lagi ya")
    if claims.get("scope") != required_scope:
        # Explicit rejection, not a silent downgrade: a POS token on an
        # owner-only route (or vice versa) is a 403. The scope name stays in
        # the message (tests assert on it; useful for debugging too).
        audience = (
            "pemilik usaha (akses owner)" if required_scope == "owner" else "perangkat kasir (akses pos)"
        )
        raise HTTPException(status_code=403, detail=f"Fitur ini khusus {audience}")
    return claims


async def _ctx(request: Request, required_scope: str) -> AsyncIterator[AuthContext]:
    claims = _extract_claims(request, required_scope)
    business_id = uuid.UUID(claims["business_id"])
    staff_id = uuid.UUID(claims["staff_id"]) if claims.get("staff_id") else None
    # For the request-logging middleware (never read for authorization).
    request.state.business_id = business_id
    async with SessionLocal() as session:
        await set_tenant(session, business_id)
        try:
            yield AuthContext(
                business_id=business_id, scope=required_scope, staff_id=staff_id, session=session
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def owner_ctx(request: Request) -> AsyncIterator[AuthContext]:
    async for ctx in _ctx(request, "owner"):
        yield ctx


async def pos_ctx(request: Request) -> AsyncIterator[AuthContext]:
    async for ctx in _ctx(request, "pos"):
        yield ctx


OwnerCtx = Annotated[AuthContext, Depends(owner_ctx)]
PosCtx = Annotated[AuthContext, Depends(pos_ctx)]
