"""The trend endpoint must serve the range the dashboard actually asks for.

The overview fetches twice the window it draws, so it can say "vs the period
before". On the "Tahun Ini" tab that is 730 days, and the endpoint's old
365-day ceiling rejected it outright — the tab drew an empty chart while the
data sat in the database. The ceiling still exists; it is now two years.

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import os
import uuid

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import create_token
from app.main import app
from app.models import Business

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")


@pytest.fixture
async def engine():
    if not DB_URL:
        pytest.fail("INTEGRATION_DATABASE_URL unset — local Postgres is not configured (roadmap §2)")
    engine = create_async_engine(DB_URL, connect_args={"statement_cache_size": 0})
    yield engine
    await engine.dispose()


@pytest.fixture
async def client():
    from app.core.db import engine as app_engine

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await app_engine.dispose()


@pytest.fixture
async def shop(engine):
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        biz = Business(name="Trend Range", owner_phone=f"62966{uuid.uuid4().hex[:9]}", day_start_hour=4)
        s.add(biz)
        await s.commit()
        bid = biz.id
    yield {"id": bid, "auth": {"Authorization": f"Bearer {create_token(business_id=str(bid), scope='owner')}"}}
    async with factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


@pytest.mark.parametrize("days", [1, 30, 90, 365, 730])
async def test_the_dashboard_windows_are_all_served(client, shop, days):
    r = await client.get(f"/api/sales-trend?days={days}", headers=shop["auth"])
    assert r.status_code == 200, r.text
    points = r.json()
    assert len(points) == days
    # Dense and oldest-first, so a chart can plot it without filling gaps.
    assert points == sorted(points, key=lambda p: p["date"])
    assert all(p["revenue"] == 0.0 and p["transactions"] == 0 for p in points)


@pytest.mark.parametrize("days", [0, -1, 731, 4000])
async def test_a_range_outside_the_ceiling_is_refused_rather_than_silently_trimmed(client, shop, days):
    r = await client.get(f"/api/sales-trend?days={days}", headers=shop["auth"])
    assert r.status_code == 422
