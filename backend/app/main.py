import asyncio
import logging
import sys
import time

if sys.platform == "win32":
    # asyncpg + SSL on Windows defaults to ProactorEventLoop, which hangs or
    # resets Supabase pooler connections — SelectorEventLoop is required.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app")

settings = get_settings()

from app.core import otp_fallback  # noqa: E402

otp_fallback.announce(settings)

app = FastAPI(title="Warung Pintar API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=(
        [
            settings.frontend_origin,
            "http://localhost:3001",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:3001",
        ]
        if settings.environment == "development"
        else [settings.frontend_origin]
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def latency_header(request: Request, call_next):
    """Wall-clock latency header on every response, plus a persistent
    request_logs row for authenticated dashboard API calls (WhatsApp messages
    and POS sales write their own richer rows in their handlers)."""
    start = time.perf_counter()
    response = await call_next(request)
    latency_ms = int((time.perf_counter() - start) * 1000)
    response.headers["X-Response-Time-Ms"] = str(latency_ms)

    business_id = getattr(request.state, "business_id", None)
    path = request.url.path
    if business_id is not None and path.startswith("/api") and path != "/api/stock-template":
        try:
            from app.core.db import tenant_session
            from app.models import RequestLog

            async with tenant_session(business_id) as session:
                session.add(
                    RequestLog(
                        business_id=business_id,
                        channel="dashboard",
                        path=path,
                        latency_ms=latency_ms,
                        status="ok" if response.status_code < 400 else str(response.status_code),
                    )
                )
        except Exception:
            logger.exception("request_logs write failed (non-fatal)")
    return response


@app.get("/health")
async def health():
    return {"status": "ok", "environment": settings.environment}


@app.get("/health/db")
async def health_db():
    from sqlalchemy import text

    from app.core.db import plain_session
    from app.core.db_errors import DB_UNAVAILABLE

    try:
        async with plain_session() as session:
            await session.execute(text("select 1"))
        return {"status": "ok", "database": "connected"}
    except Exception as exc:
        logger.warning("database health check failed: %s", exc)
        return {"status": "error", "database": "unreachable", "detail": DB_UNAVAILABLE}


def _include_routers() -> None:
    """Routers are registered here as each build phase lands them."""
    from app.api.auth import router as auth_router
    from app.api.pos import router as pos_router
    from app.api.dashboard import router as dashboard_router
    from app.api.menu import router as menu_router
    from app.whatsapp.webhook import router as webhook_router

    app.include_router(auth_router)
    app.include_router(pos_router)
    app.include_router(dashboard_router)
    app.include_router(menu_router)
    app.include_router(webhook_router)


_include_routers()
