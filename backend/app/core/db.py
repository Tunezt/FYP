"""Async database engine/session plus the RLS tenant-context helper.

Every request that touches business data runs inside a transaction that first
executes `SET LOCAL app.current_business_id = '<uuid>'` — the value comes from
the verified JWT, never from a client-supplied header. Row-Level Security
policies on every business-scoped table key off that setting, so even a buggy
query cannot cross tenants.

Background jobs (cron) connect with the same credentials but iterate businesses
explicitly and set the tenant context per business — never a cross-tenant query
by accident.
"""
import ssl
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import text

from app.core.config import get_settings

settings = get_settings()

_connect_args: dict = {"statement_cache_size": 0, "timeout": 5}
if "localhost" not in settings.database_url and "127.0.0.1" not in settings.database_url:
    _connect_args["ssl"] = ssl.create_default_context()

# statement_cache_size=0 keeps asyncpg compatible with Supabase's PgBouncer
# transaction-mode pooler (prepared statements don't survive pooled connections).
engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=5,
    connect_args=_connect_args,
)

SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def set_tenant(session: AsyncSession, business_id: uuid.UUID | str) -> None:
    """Pin the RLS tenant for the current transaction.

    SET LOCAL only lasts until the transaction ends, so this must be called at
    the start of each transaction (SessionLocal begins one implicitly on first
    statement).
    """
    # set_config with is_local=true == SET LOCAL, but parameterizable.
    await session.execute(
        text("select set_config('app.current_business_id', :bid, true)"),
        {"bid": str(business_id)},
    )


@asynccontextmanager
async def tenant_session(business_id: uuid.UUID | str) -> AsyncIterator[AsyncSession]:
    """Session pre-scoped to one business. Commits on clean exit."""
    async with SessionLocal() as session:
        await set_tenant(session, business_id)
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def plain_session() -> AsyncIterator[AsyncSession]:
    """Session with no tenant context — for tenant-resolution lookups only
    (e.g. matching an inbound WhatsApp sender to businesses.owner_phone).
    `businesses` itself is not business_id-scoped."""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
