"""M6-T1 — chart of accounts: seeded Indonesian SME chart, merchant-extendable.

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import os
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.dashboard import add_account, edit_account, list_accounts
from app.models import Account, Business
from app.schemas.dashboard import AccountCreateIn, AccountUpdateIn
from app.services.accounts import (
    ACCOUNT_TYPES,
    DEBIT_NORMAL,
    STANDARD_CHART,
    AccountInvalid,
    account_by_code,
    create_account,
    ensure_standard_chart,
    update_account,
)

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")


def test_standard_chart_is_well_formed():
    codes = [c for c, _, _ in STANDARD_CHART]
    assert len(codes) == len(set(codes)) == 27   # 26 + 4250 Diskon promo (M8-T3)
    assert all(t in ACCOUNT_TYPES for _, _, t in STANDARD_CHART)
    by_type = {t: [c for c, _, tt in STANDARD_CHART if tt == t] for t in ACCOUNT_TYPES}
    assert all(c.startswith("1") for c in by_type["asset"]) and all(c.startswith("2") for c in by_type["liability"])
    assert all(c.startswith("3") for c in by_type["equity"]) and all(c.startswith("4") for c in by_type["revenue"])
    assert all(c.startswith("5") for c in by_type["expense"])
    # Every account the event catalogue (roadmap appendix A) needs exists.
    for code in ("1100", "1120", "1200", "1300", "2100", "2200", "2300", "4100", "4200", "5100", "5700", "5800", "5600"):
        assert code in codes
    assert DEBIT_NORMAL == {"asset", "expense"}


@pytest.fixture
async def engine():
    if not DB_URL:
        pytest.fail("INTEGRATION_DATABASE_URL unset — local Postgres is not configured (roadmap §2)")
    engine = create_async_engine(DB_URL, connect_args={"statement_cache_size": 0})
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _set_tenant(session, business_id):
    await session.execute(
        text("select set_config('app.current_business_id', :bid, true)"), {"bid": str(business_id)}
    )


@pytest.fixture
async def shop(session_factory):
    async with session_factory() as s:
        biz = Business(name="Accounts Test", owner_phone=f"62982{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    yield bid
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


async def test_chart_is_seeded_idempotently_and_extendable(session_factory, shop):
    async with session_factory() as s:
        await _set_tenant(s, shop)
        chart = await ensure_standard_chart(s, shop)
        assert len(chart) == 27 and all(a.is_system for a in chart.values())
        await ensure_standard_chart(s, shop)
        assert (await s.execute(select(func.count(Account.id)))).scalar_one() == 27
        kas = await account_by_code(s, "1100")
        assert kas.name == "Kas" and kas.type == "asset"
        custom = await create_account(s, shop, code="5910", name="Langganan internet", type="expense")
        assert custom.is_system is False
        for kwargs, code in (
            ({"code": "5910", "name": "Dup", "type": "expense"}, "duplicate"),
            ({"code": "abc", "name": "X", "type": "expense"}, "code"),
            ({"code": "5920", "name": " ", "type": "expense"}, "name"),
            ({"code": "5920", "name": "X", "type": "hutang"}, "type"),
        ):
            with pytest.raises(AccountInvalid) as exc:
                await create_account(s, shop, **kwargs)
            assert exc.value.code == code
        with pytest.raises(AccountInvalid) as exc:
            await update_account(s, kas, is_active=False)
        assert exc.value.code == "system"
        await update_account(s, kas, name="Kas kecil")
        await update_account(s, custom, is_active=False)
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, shop)
        assert (await account_by_code(s, "1100")).name == "Kas kecil"
        assert (await account_by_code(s, "5910")).is_active is False


async def test_owner_endpoints(session_factory, shop):
    async with session_factory() as s:
        await _set_tenant(s, shop)
        await ensure_standard_chart(s, shop)
        ctx = SimpleNamespace(session=s, business_id=shop, staff_id=None)
        rows = await list_accounts(ctx, include_inactive=False)
        assert [r.code for r in rows][:3] == ["1100", "1110", "1120"] and len(rows) == 27
        created = await add_account(AccountCreateIn(code="1150", name="Dompet digital", type="asset"), ctx)
        assert created.is_system is False
        with pytest.raises(HTTPException) as exc:
            await add_account(AccountCreateIn(code="1150", name="Lagi", type="asset"), ctx)
        assert exc.value.status_code == 409
        with pytest.raises(HTTPException) as exc:
            await edit_account(rows[0].id, AccountUpdateIn(is_active=False), ctx)
        assert exc.value.status_code == 409 and "bawaan" in exc.value.detail
        await edit_account(created.id, AccountUpdateIn(is_active=False), ctx)
        assert len(await list_accounts(ctx, include_inactive=False)) == 27
        assert len(await list_accounts(ctx, include_inactive=True)) == 28   # 27 standard + the one created above
        await s.commit()
