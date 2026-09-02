"""M5-T1 — suppliers with contact details and a derived purchase history.

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.dashboard import add_supplier, edit_supplier, list_suppliers, supplier_history
from app.models import Business, Receipt, Supplier
from app.schemas.dashboard import SupplierCreateIn, SupplierUpdateIn
from app.services.receipts import commit_parse
from app.services.suppliers import SupplierInvalid, create_supplier, purchase_history, update_supplier

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")


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
        biz = Business(name="Supplier Test", owner_phone=f"62987{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:  # receipt photos keep books now (M6-T6)
        await _set_tenant(s, bid)
        from tests.conftest import seed_books
        await seed_books(s, bid)
        await s.commit()
    yield bid
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


def _parsed(supplier: str, total: int, items: int = 1) -> dict:
    return {
        "document_type": "receipt", "supplier": supplier, "date": "2026-09-01",
        "items": [{"name": f"Bahan {i}", "quantity": 1, "unit": "kg", "unit_price": total // items, "line_total": total // items}
                  for i in range(items)],
        "total_amount": total, "confidence": "high", "ambiguities": [],
    }


async def test_create_update_and_rules(session_factory, shop):
    async with session_factory() as s:
        await _set_tenant(s, shop)
        toko = await create_supplier(s, shop, name="Toko Manis", phone="0812", address="Pasar Anyar")
        assert toko.is_active and toko.phone == "0812"
        with pytest.raises(SupplierInvalid) as exc:
            await create_supplier(s, shop, name="toko manis")
        assert exc.value.code == "duplicate"
        with pytest.raises(SupplierInvalid) as exc:
            await create_supplier(s, shop, name="  ")
        assert exc.value.code == "name"
        await update_supplier(s, toko, notes="bayar tiap Jumat", is_active=False)
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, shop)
        row = (await s.execute(select(Supplier))).scalar_one()
        assert row.notes == "bayar tiap Jumat" and row.is_active is False


async def test_receipt_photos_link_to_known_supplier_and_build_history(session_factory, shop):
    async with session_factory() as s:  # a photo committed BEFORE the supplier exists stays unlinked …
        await _set_tenant(s, shop)
        biz = await s.get(Business, shop)
        with patch("app.services.rag.embed_receipt", new=AsyncMock()):
            await commit_parse(s, biz, _parsed("Grosir Barokah", 250000, items=2), "r/1.jpg")
        await s.commit()
    async with session_factory() as s:  # … until the supplier is created, which links it by name
        await _set_tenant(s, shop)
        barokah = await create_supplier(s, shop, name="Grosir Barokah")
        await s.commit()
        barokah_id = barokah.id
    async with session_factory() as s:  # photos committed afterwards link on commit
        await _set_tenant(s, shop)
        biz = await s.get(Business, shop)
        with patch("app.services.rag.embed_receipt", new=AsyncMock()):
            await commit_parse(s, biz, _parsed("grosir barokah", 120000), "r/2.jpg")   # case-insensitive
            await commit_parse(s, biz, _parsed("Toko Lain", 99000), "r/3.jpg")         # unknown: stays unlinked, not created
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, shop)
        assert len((await s.execute(select(Supplier))).scalars().all()) == 1  # nothing auto-created from photos
        h = await purchase_history(s, await s.get(Supplier, barokah_id))
        assert h["purchase_count"] == 2 and h["total_spent"] == Decimal("370000.00")
        assert [r["total_amount"] for r in h["receipts"]] == [Decimal("120000.00"), Decimal("250000.00")] or \
               sorted(r["total_amount"] for r in h["receipts"]) == [Decimal("120000.00"), Decimal("250000.00")]
        unlinked = (await s.execute(select(Receipt).where(Receipt.supplier == "Toko Lain"))).scalar_one()
        assert unlinked.supplier_id is None


async def test_owner_endpoints(session_factory, shop):
    async with session_factory() as s:
        await _set_tenant(s, shop)
        ctx = SimpleNamespace(session=s, business_id=shop, staff_id=None)
        created = await add_supplier(SupplierCreateIn(name="Pak Udin Sayur", phone="0813"), ctx)
        with pytest.raises(HTTPException) as exc:
            await add_supplier(SupplierCreateIn(name="pak udin sayur"), ctx)
        assert exc.value.status_code == 409 and "sudah ada" in exc.value.detail
        await edit_supplier(created.id, SupplierUpdateIn(is_active=False), ctx)
        assert [x.name for x in await list_suppliers(ctx, include_inactive=False)] == []
        assert [x.name for x in await list_suppliers(ctx, include_inactive=True)] == ["Pak Udin Sayur"]
        h = await supplier_history(created.id, ctx)
        assert h.purchase_count == 0 and h.total_spent == Decimal(0) and h.receipts == []
        with pytest.raises(HTTPException) as exc:
            await supplier_history(uuid.uuid4(), ctx)
        assert exc.value.status_code == 404
        await s.commit()
