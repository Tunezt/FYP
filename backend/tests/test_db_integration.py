"""Database integration tests — RLS tenant isolation + the concurrent-sale race.

These need a real Postgres with the migration applied, so they run only when
INTEGRATION_DATABASE_URL is set (point it at a disposable database, run
`alembic upgrade head` against it first):

    INTEGRATION_DATABASE_URL=postgresql+asyncpg://... pytest tests/test_db_integration.py

They are skipped otherwise (no Postgres/Docker on the build machine — see
docs/progress.md Phase 0).
"""
import asyncio
import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import hash_pin
from app.models import Business, Item, Sale, Staff, StockMovement
from app.services.sales import InsufficientStock, record_sale

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DB_URL, reason="INTEGRATION_DATABASE_URL not set — no Postgres available"
)


@pytest.fixture
async def engine():
    engine = create_async_engine(DB_URL, connect_args={"statement_cache_size": 0})
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _set_tenant(session: AsyncSession, business_id) -> None:
    await session.execute(
        text("select set_config('app.current_business_id', :bid, true)"),
        {"bid": str(business_id)},
    )


async def _make_business(session_factory, suffix: str):
    async with session_factory() as session:
        business = Business(
            name=f"RLS Test {suffix}",
            owner_phone=f"62999{uuid.uuid4().hex[:9]}",
        )
        session.add(business)
        await session.commit()
        return business


@pytest.fixture
async def two_tenants(session_factory):
    a = await _make_business(session_factory, "A")
    b = await _make_business(session_factory, "B")
    yield a, b
    async with session_factory() as session:
        for biz in (a, b):
            row = await session.get(Business, biz.id)
            if row:
                await session.delete(row)
        await session.commit()


async def test_rls_blocks_cross_tenant_reads(session_factory, two_tenants):
    a, b = two_tenants
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        session.add(Item(business_id=a.id, name="Rahasia A", unit="kg", current_stock=Decimal(5)))
        await session.commit()

    # Tenant B sees nothing of A's items.
    async with session_factory() as session:
        await _set_tenant(session, b.id)
        rows = (await session.execute(select(Item).where(Item.name == "Rahasia A"))).scalars().all()
        assert rows == []

    # Tenant A sees its own row.
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        rows = (await session.execute(select(Item).where(Item.name == "Rahasia A"))).scalars().all()
        assert len(rows) == 1


async def test_rls_blocks_cross_tenant_writes(session_factory, two_tenants):
    a, b = two_tenants
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        # WITH CHECK policy: inserting a row claiming to belong to B while the
        # transaction is pinned to A must fail.
        session.add(Item(business_id=b.id, name="Smuggled", unit="kg"))
        with pytest.raises(Exception):
            await session.commit()


async def test_rls_no_context_means_no_rows(session_factory, two_tenants):
    a, _ = two_tenants
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        session.add(Item(business_id=a.id, name="Ctx Item", unit="pcs", current_stock=Decimal(1)))
        await session.commit()

    async with session_factory() as session:  # no set_config at all
        with pytest.raises(Exception):
            # current_setting('app.current_business_id') errors when unset —
            # queries fail closed, they don't leak.
            (await session.execute(select(Item))).scalars().all()


async def test_rls_isolates_stock_movements(session_factory, two_tenants):
    """M2-T1 / roadmap §2: business A cannot read B's stock_movements rows, and
    cannot write a row claiming to be B's."""
    a, b = two_tenants
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        item = Item(business_id=a.id, name="Ledger Item A", unit="kg", current_stock=Decimal(3))
        session.add(item)
        await session.flush()
        session.add(
            StockMovement(
                business_id=a.id, item_id=item.id, qty_delta=Decimal(3),
                reason="purchase", source_type="test", unit_cost=Decimal("12500.00"),
            )
        )
        await session.commit()
        item_id = item.id

    async with session_factory() as session:  # B sees nothing of A's ledger
        await _set_tenant(session, b.id)
        rows = (await session.execute(select(StockMovement).where(StockMovement.item_id == item_id))).scalars().all()
        assert rows == []

    async with session_factory() as session:  # A sees its own row, with exact numbers
        await _set_tenant(session, a.id)
        row = (await session.execute(select(StockMovement).where(StockMovement.item_id == item_id))).scalar_one()
        assert row.qty_delta == Decimal("3.000")
        assert row.unit_cost == Decimal("12500.00")
        assert row.reason == "purchase"

    async with session_factory() as session:  # A cannot smuggle a row into B's ledger
        await _set_tenant(session, a.id)
        session.add(
            StockMovement(business_id=b.id, item_id=item_id, qty_delta=Decimal(1), reason="correction")
        )
        with pytest.raises(Exception):
            await session.commit()


async def test_concurrent_sale_of_last_unit(session_factory, two_tenants):
    """Two staff sell the last unit at the same moment: exactly one succeeds,
    stock never goes negative (the brief's atomic UPDATE guarantee)."""
    a, _ = two_tenants
    async with session_factory() as session:
        await _set_tenant(session, a.id)
        staff = Staff(business_id=a.id, name="Racer", pin_hash=hash_pin("0000"))
        item = Item(
            business_id=a.id, name="Last Croissant", unit="pcs",
            current_stock=Decimal(1), sell_price=Decimal(28000),
        )
        session.add_all([staff, item])
        await session.commit()
        staff_id, item_id = staff.id, item.id

    async def try_sell():
        async with session_factory() as session:
            await _set_tenant(session, a.id)
            try:
                await record_sale(
                    session,
                    business_id=a.id,
                    staff_id=staff_id,
                    item_id=item_id,
                    quantity=Decimal(1),
                )
                await session.commit()
                return "sold"
            except InsufficientStock:
                await session.rollback()
                return "rejected"

    results = await asyncio.gather(try_sell(), try_sell())
    assert sorted(results) == ["rejected", "sold"]

    async with session_factory() as session:
        await _set_tenant(session, a.id)
        item = await session.get(Item, item_id)
        assert item.current_stock == Decimal(0)
        sales = (await session.execute(select(Sale).where(Sale.item_id == item_id))).scalars().all()
        assert len(sales) == 1
