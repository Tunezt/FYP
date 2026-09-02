"""M0-T6 — request_logs under RLS: what happens to unauthenticated traffic?

`request_logs.business_id` is nullable, but the table carries `tenant_isolation`
with a WITH CHECK clause, so a NULL business_id can never pass the policy through
the app's `app_role` connection. These tests establish which of the three
possibilities holds (roadmap M0-T6): **it never happens.** Every write path
resolves a business first and skips the row otherwise:

  * dashboard middleware (`app/main.py`)      — only when request.state.business_id
    was set by an authenticated dependency; a 401 never sets it
  * WhatsApp (`app/whatsapp/processor.py`)     — returns before logging for an
    unregistered sender
  * POS (`app/api/pos.py`)                      — writes inside the authenticated
    tenant session

So unauthenticated traffic is instrumented only via the Python logger, never in
the table — and the nullable column is unreachable from the app. The last test
proves the database itself would reject such a row, which is why "never
attempted" is the only safe state.

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.core.db import engine as app_engine, tenant_session
from app.main import app
from app.models import RequestLog
from app.whatsapp.processor import _process_message

settings = get_settings()

# Rows with a NULL business_id are invisible to app_role (the USING clause
# evaluates to NULL), so the only honest way to assert "none were written" is to
# count them with the elevated role alembic uses. Read-only, test-only.
SUPERUSER_URL = settings.migration_database_url


async def _count_null_business_rows() -> int:
    if not SUPERUSER_URL:
        pytest.fail("MIGRATION_DATABASE_URL is unset — cannot verify request_logs contents")
    engine = create_async_engine(SUPERUSER_URL, connect_args={"statement_cache_size": 0})
    try:
        async with engine.connect() as conn:
            return (
                await conn.execute(
                    text("select count(*) from request_logs where business_id is null")
                )
            ).scalar_one()
    finally:
        await engine.dispose()


@pytest.fixture
async def dispose_app_engine():
    """The app's shared engine pools connections bound to the loop that made
    them; dispose after each test so the next pytest-asyncio loop starts clean."""
    yield
    await app_engine.dispose()


async def test_unauthenticated_api_request_writes_no_log(dispose_app_engine):
    """A dashboard call with no token is rejected (401) and the middleware
    writes nothing — no NULL-business row, no error."""
    before = await _count_null_business_rows()

    client = TestClient(app)
    response = client.get("/api/overview")
    assert response.status_code == 401
    assert "X-Response-Time-Ms" in response.headers  # middleware did run

    response = client.get("/api/overview", headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401

    assert await _count_null_business_rows() == before


async def test_unregistered_whatsapp_sender_writes_no_log(dispose_app_engine):
    """An inbound message from a number no business owns gets the free-form
    "not registered" reply and no request_logs row."""
    before = await _count_null_business_rows()
    unknown_number = "62" + uuid.uuid4().int.__str__()[:11]

    with patch("app.whatsapp.processor.send_text", new=AsyncMock()) as send_text:
        await _process_message(
            {
                "id": f"wamid.test-{uuid.uuid4().hex}",
                "from": unknown_number,
                "type": "text",
                "text": {"body": "halo, stok kopi berapa?"},
            }
        )

    send_text.assert_awaited_once()
    assert send_text.await_args.args[0] == unknown_number
    assert "belum terdaftar" in send_text.await_args.args[1]
    assert await _count_null_business_rows() == before


async def test_null_business_request_log_is_rejected_by_rls(dispose_app_engine):
    """If any path ever tried to log without a business, the database refuses:
    WITH CHECK `business_id = current_setting(...)::uuid` is NULL for a NULL
    business_id, and a NULL check is a failed check. This is why the nullable
    column is unreachable rather than a leak."""
    before = await _count_null_business_rows()

    with pytest.raises(Exception) as excinfo:
        async with tenant_session(uuid.uuid4()) as session:
            session.add(
                RequestLog(business_id=None, channel="test", path="/x", latency_ms=1, status="ok")
            )
            await session.flush()
    assert "row-level security" in str(excinfo.value).lower()

    assert await _count_null_business_rows() == before
