"""till-2 — the server keeps its own validation, and says it in Indonesian.

Every form now keeps its primary button disabled until it is valid and says
what is missing. That is a courtesy, not a guard: a request that skips the
form (an old tablet, a retry, a script) must still be refused, and the
refusal is shown to a cashier as it comes, so it names the fields in words
they use instead of a list of English pydantic errors.
"""
import os
import uuid

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import create_token, hash_pin
from app.core.validation import validation_message
from app.main import app
from app.models import Business, CashMovement, Staff

from tests.conftest import seed_books

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


@pytest.fixture
async def client():
    from app.core.db import engine as app_engine

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await app_engine.dispose()


async def _set_tenant(session, business_id):
    await session.execute(text("select set_config('app.current_business_id', :bid, true)"), {"bid": str(business_id)})


@pytest.fixture
async def cafe(session_factory):
    async with session_factory() as s:
        biz = Business(name="Validasi Test", owner_phone=f"62986{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await seed_books(s, bid)
        owner = Staff(business_id=bid, name="Ibu Diah", role="owner", pin_hash=hash_pin("1234"))
        sari = Staff(business_id=bid, name="Sari", pin_hash=hash_pin("2345"))
        s.add_all([owner, sari])
        await s.commit()
        ids = {
            "bid": bid,
            "pos": {"Authorization": f"Bearer {create_token(business_id=str(bid), scope='pos', staff_id=str(sari.id))}"},
            "owner": {"Authorization": f"Bearer {create_token(business_id=str(bid), scope='owner', staff_id=str(owner.id))}"},
        }
    yield ids
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


def test_the_message_names_the_fields_in_indonesian():
    errors = [
        {"loc": ("body", "amount"), "msg": "Input should be greater than 0"},
        {"loc": ("body", "reason"), "msg": "String should have at least 1 character"},
        {"loc": ("body", "lines", 0, "quantity"), "msg": "Input should be greater than 0"},
    ]
    message = validation_message(errors)
    assert message == "Isian belum benar: jumlah, alasan — periksa lagi lalu coba simpan"
    many = [{"loc": ("body", f)} for f in ("name", "phone", "pin", "category", "unit")]
    assert validation_message(many).endswith("nama, nomor HP, PIN, jenis dan lainnya — periksa lagi lalu coba simpan")


async def test_a_kas_entry_the_form_would_not_send_is_refused_and_writes_nothing(client, session_factory, cafe):
    c = cafe
    for body, field in (
        ({"kind": "petty_cash", "amount": 0, "reason": "es batu"}, "jumlah"),
        ({"kind": "petty_cash", "amount": 5000, "reason": ""}, "alasan"),
    ):
        resp = await client.post("/pos/cash", headers=c["pos"], json=body)
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert isinstance(detail, str) and field in detail and detail.startswith("Isian belum benar")
    no_supplier = await client.post("/pos/cash", headers=c["pos"], json={"kind": "supplier_payment", "amount": 5000, "reason": "bayar"})
    assert no_supplier.status_code == 422 and "supplier" in no_supplier.json()["detail"].lower()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert (await s.execute(select(func.count(CashMovement.id)))).scalar_one() == 0


async def test_dashboard_forms_get_the_same_plain_refusal(client, cafe):
    c = cafe
    resp = await client.post("/api/customers", headers=c["owner"], json={"name": ""})
    assert resp.status_code == 422 and resp.json()["detail"] == "Isian belum benar: nama — periksa lagi lalu coba simpan"
    assert resp.json()["errors"][0]["loc"] == ["body", "name"]          # the raw detail is still there to debug
