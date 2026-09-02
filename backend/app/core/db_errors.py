import asyncio

from fastapi import HTTPException
from sqlalchemy.exc import DBAPIError, OperationalError

DB_UNAVAILABLE = (
    "Database tidak dapat dihubungi. Pastikan Postgres lokal berjalan "
    "(`python scripts/local-pg.py status` atau `docker compose ps`) dan DATABASE_URL di "
    "backend/.env benar. Kalau memakai Supabase: pastikan proyeknya aktif dan jaringanmu "
    "mengizinkan koneksi ke port 5432 (coba hotspot ponsel kalau Wi‑Fi kampus memblokir)."
)


def raise_if_db_unreachable(exc: BaseException) -> None:
    """Map pool/connect timeouts into a 503 the login form can surface."""
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, OSError, OperationalError, DBAPIError)):
        raise HTTPException(status_code=503, detail=DB_UNAVAILABLE) from exc
    raise exc
