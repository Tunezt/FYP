import logging
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app")

settings = get_settings()

app = FastAPI(title="Warung Pintar API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def latency_header(request: Request, call_next):
    """Wall-clock latency on every response. Persistent per-interaction logging
    (request_logs) happens in the handlers that know the business context."""
    start = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Response-Time-Ms"] = str(int((time.perf_counter() - start) * 1000))
    return response


@app.get("/health")
async def health():
    return {"status": "ok", "environment": settings.environment}


def _include_routers() -> None:
    """Routers are registered here as each build phase lands them."""
    from app.api.auth import router as auth_router
    from app.api.pos import router as pos_router
    from app.api.dashboard import router as dashboard_router
    from app.whatsapp.webhook import router as webhook_router

    app.include_router(auth_router)
    app.include_router(pos_router)
    app.include_router(dashboard_router)
    app.include_router(webhook_router)


_include_routers()
