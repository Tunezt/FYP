"""till-1 — shop expenses recorded from the dashboard, including from a photo.

Until the café's WhatsApp number is live there was no way at all to record a
grocery or supplier nota. The dashboard now does it two ways:

* **Tambah pengeluaran**: amount, category, date, note, optional photo. An
  ordinary expense through the one writer (M6-T6), so it is on the P&L.
* **Foto nota**: the photo goes through the *same* parser, draft and
  confirmation as the WhatsApp path (M5-T4). Scanning writes nothing; the
  owner checks the lines and confirms, and confirming writes exactly what a
  WhatsApp YA writes: the receipt row, a goods receipt for the matched lines
  (stock in, moving-average cost) and the nota total as a `receipt` expense.

The model call and the storage upload are mocked (the vision path itself is
measured in M1); everything after them runs for real against local Postgres.
"""
import base64
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai.periods import business_day
from app.core.security import create_token, hash_pin
from app.main import app
from app.models import Business, Expense, GoodsReceipt, GoodsReceiptLine, Item, PendingConfirmation, Receipt, Staff, StockMovement
from app.services.catalog import ensure_default_variant
from app.services.ledger import account_balances
from app.services.stock import open_item_stock
from app.services.suppliers import create_supplier
from app.services.units import ensure_standard_uoms

from tests.conftest import seed_books

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")
PHOTO = base64.b64encode(b"\xff\xd8\xff" + b"nota" * 64).decode()


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


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def _make_shop(session_factory, name="Nota Test"):
    async with session_factory() as s:
        biz = Business(name=name, owner_phone=f"62985{uuid.uuid4().hex[:9]}", day_start_hour=4)
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await seed_books(s, bid)
        uoms = await ensure_standard_uoms(s, bid)
        await create_supplier(s, bid, name="Toko Manis")
        owner = Staff(business_id=bid, name="Ibu Diah", role="owner", pin_hash=hash_pin("1234"))
        gula = Item(business_id=bid, name="Gula Aren", unit="kg", current_stock=Decimal(4), cost_price=Decimal(30000), uom_id=uoms["kg"].id)
        susu = Item(business_id=bid, name="Susu UHT", unit="liter", current_stock=Decimal(10), cost_price=Decimal(17000), uom_id=uoms["liter"].id)
        kecap = Item(business_id=bid, name="Kecap Manis", unit="botol", current_stock=Decimal(1), cost_price=Decimal(15000))
        s.add_all([owner, gula, susu, kecap])
        await s.flush()
        for it in (gula, susu, kecap):
            await open_item_stock(s, it, unit_cost=it.cost_price)
            await ensure_default_variant(s, it)
        await s.commit()
        return {
            "bid": bid, "gula": gula.id, "susu": susu.id, "kecap": kecap.id,
            "owner": create_token(business_id=str(bid), scope="owner", staff_id=str(owner.id)),
        }


@pytest.fixture
async def shop(session_factory):
    ids = await _make_shop(session_factory)
    yield ids
    async with session_factory() as s:
        row = await s.get(Business, ids["bid"])
        if row:
            await s.delete(row)
        await s.commit()


INVOICE = {
    "document_type": "receipt", "supplier": "toko manis", "date": "2026-09-30",
    "items": [
        {"name": "Gula Aren", "quantity": 3, "unit": "kg", "unit_written": True, "unit_price": 38000, "unit_price_written": True, "line_total": 114000},
        {"name": "susu uht", "quantity": 2500, "unit": "ml", "unit_written": True, "unit_price": 20, "unit_price_written": True, "line_total": 50000},
        {"name": "Kecap BH", "quantity": 1, "unit": "", "unit_written": False, "unit_price": 17000, "unit_price_written": True, "line_total": 17000},
        {"name": "Plastik", "quantity": 1, "unit": "pak", "unit_written": True, "unit_price": 9000, "unit_price_written": True, "line_total": 9000},
    ],
    "total_amount": 190000, "confidence": "medium", "ambiguities": [],
}


def _upload(path_holder: list):
    async def fake(business_id, data, mime):
        path = f"{business_id}/{uuid.uuid4()}.jpg"
        path_holder.append(path)
        return path
    return fake


async def _gaps(s):
    return (await s.execute(text(
        "select i.name from items i left join stock_movements m on m.item_id = i.id "
        "group by i.id, i.name, i.current_stock having i.current_stock <> coalesce(sum(m.qty_delta), 0)"
    ))).scalars().all()


async def test_a_typed_expense_with_a_photo_lands_on_the_pnl(client, session_factory, shop):
    c = shop
    uploads: list[str] = []
    with patch("app.whatsapp.storage.upload_receipt_image", new=_upload(uploads)):
        resp = await client.post("/api/expenses", headers=_auth(c["owner"]), json={
            "amount": 88000, "category": "operasional", "description": "isi ulang gas",
            "image_base64": PHOTO, "mime_type": "image/jpeg",
        })
    assert resp.status_code == 201, resp.text
    row = resp.json()
    assert Decimal(row["amount"]) == Decimal("88000.00") and row["source"] == "manual" and row["category"] == "operasional"
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        expense = await s.get(Expense, uuid.UUID(row["id"]))
        photo = await s.get(Receipt, expense.receipt_id)
        assert photo.image_url == uploads[0] and photo.total_amount == Decimal("88000.00")
        assert expense.description == "isi ulang gas"
        balances = await account_balances(s)
        assert balances["5500"] == Decimal("88000.00") and balances["1100"] == Decimal("-88000.00")

    listed = (await client.get("/api/expenses?page=1&page_size=10", headers=_auth(c["owner"]))).json()
    assert [r["id"] for r in listed["rows"]] == [row["id"]]


async def test_a_typed_expense_on_a_past_day_lands_on_that_business_day(client, session_factory, shop):
    c = shop
    yesterday = business_day(datetime.now(timezone.utc), "Asia/Jakarta", 4) - timedelta(days=1)
    resp = await client.post("/api/expenses", headers=_auth(c["owner"]), json={
        "amount": 50000, "category": "gaji", "occurred_on": yesterday.isoformat(), "description": None,
    })
    assert resp.status_code == 201, resp.text
    occurred = datetime.fromisoformat(resp.json()["occurred_at"])
    assert business_day(occurred, "Asia/Jakarta", 4) == yesterday
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert (await account_balances(s))["5300"] == Decimal("50000.00")   # gaji has its own account


async def test_the_server_refuses_what_the_form_would_not_send(client, session_factory, shop):
    c = shop
    tomorrow = business_day(datetime.now(timezone.utc), "Asia/Jakarta", 4) + timedelta(days=2)
    future = await client.post("/api/expenses", headers=_auth(c["owner"]), json={
        "amount": 10000, "category": "lainnya", "occurred_on": tomorrow.isoformat(),
    })
    assert future.status_code == 422 and "tidak boleh" in future.json()["detail"]
    zero = await client.post("/api/expenses", headers=_auth(c["owner"]), json={"amount": 0, "category": "lainnya"})
    assert zero.status_code == 422
    no_category = await client.post("/api/expenses", headers=_auth(c["owner"]), json={"amount": 1000, "category": ""})
    assert no_category.status_code == 422
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert (await s.execute(select(func.count(Expense.id)))).scalar_one() == 0


async def test_scanning_reads_the_nota_and_writes_nothing(client, session_factory, shop):
    c = shop
    uploads: list[str] = []
    with (
        patch("app.whatsapp.storage.upload_receipt_image", new=_upload(uploads)),
        patch("app.ai.vision.parse_business_document", new=AsyncMock(return_value=INVOICE)) as parse,
    ):
        resp = await client.post("/api/receipts/scan", headers=_auth(c["owner"]), json={"image_base64": PHOTO, "mime_type": "image/jpeg"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert parse.await_count == 1 and body["image_path"] == uploads[0]
    draft = body["draft"]
    assert [m["item_name"] for m in draft["matched"]] == ["Gula Aren", "Susu UHT"]
    assert [q["name_read"] for q in draft["questions"]] == ["Kecap BH", "Plastik"]
    assert draft["supplier_name"] == "Toko Manis"
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert (await s.get(Item, c["gula"])).current_stock == Decimal("4.000")
        for model in (Receipt, Expense, GoodsReceipt, PendingConfirmation):
            assert (await s.execute(select(func.count(model.id)))).scalar_one() == 0


async def test_confirming_the_checked_lines_writes_what_whatsapp_ya_writes(client, session_factory, shop):
    """The owner fixes the gula quantity (the photo said 3, the bag says 2),
    answers the Kecap question by picking Kecap Manis, and marks the plastic
    bags cost only. Stock moves for exactly those lines; the whole nota is the
    expense; only what did not go into stock reaches the P&L."""
    c = shop
    path = f"{c['bid']}/{uuid.uuid4()}.jpg"
    with patch("app.services.rag.embed_receipt", new=AsyncMock()):
        resp = await client.post("/api/receipts/confirm", headers=_auth(c["owner"]), json={
            "image_path": path, "supplier": "toko manis", "nota_date": "2026-09-30", "total_amount": 190000,
            "lines": [
                {"name": "Gula Aren", "quantity": 2, "unit": "kg", "unit_price": 38000, "line_total": 76000},
                {"name": "susu uht", "quantity": 2500, "unit": "ml", "unit_price": 20, "line_total": 50000},
                {"name": "Kecap BH", "quantity": 1, "unit": "", "unit_price": 17000, "line_total": 17000, "item_id": str(c["kecap"])},
                {"name": "Plastik", "quantity": 1, "unit": "pak", "unit_price": 9000, "line_total": 9000, "skip": True},
            ],
        })
    assert resp.status_code == 201, resp.text
    out = resp.json()
    assert out["goods_receipt_number"] == 1 and out["skipped"] == ["Plastik"]
    assert out["stocked"] == ["2 kg Gula Aren", "2500 ml Susu UHT", "1 botol Kecap Manis"]
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert (await s.get(Item, c["gula"])).current_stock == Decimal("6.000")
        assert (await s.get(Item, c["susu"])).current_stock == Decimal("12.500")
        kecap = await s.get(Item, c["kecap"])
        assert kecap.current_stock == Decimal("2.000") and kecap.cost_price == Decimal("16000.00")   # (15.000 + 17.000) / 2
        gr = (await s.execute(select(GoodsReceipt))).scalar_one()
        assert gr.subtotal == Decimal("143000.00") and gr.received_by is not None
        assert len((await s.execute(select(GoodsReceiptLine))).scalars().all()) == 3
        photo = (await s.execute(select(Receipt))).scalar_one()
        assert photo.image_url == path and photo.parsed_data["source"] == "dashboard"
        expense = (await s.execute(select(Expense))).scalar_one()
        assert expense.source == "receipt" and expense.amount == Decimal("190000.00") and expense.receipt_id == photo.id
        moves = (await s.execute(select(StockMovement).where(StockMovement.source_type == "goods_receipt"))).scalars().all()
        assert sorted(m.reason for m in moves) == ["purchase", "purchase", "purchase"]
        assert await _gaps(s) == []
        balances = await account_balances(s)
        assert balances["1300"] == Decimal("143000.00")             # into stock
        assert balances["5200"] == Decimal("47000.00")              # 190.000 − 143.000 not in stock
        debit = (await s.execute(text("select coalesce(sum(debit),0) - coalesce(sum(credit),0) from journal_lines"))).scalar_one()
        assert debit == 0


async def test_a_nota_is_recorded_once_and_only_from_this_business(client, session_factory, shop):
    c = shop
    path = f"{c['bid']}/{uuid.uuid4()}.jpg"
    body = {"image_path": path, "total_amount": 25000, "lines": [
        {"name": "Gula Aren", "quantity": 1, "unit": "kg", "unit_price": 25000, "line_total": 25000},
    ]}
    with patch("app.services.rag.embed_receipt", new=AsyncMock()):
        first = await client.post("/api/receipts/confirm", headers=_auth(c["owner"]), json=body)
        again = await client.post("/api/receipts/confirm", headers=_auth(c["owner"]), json=body)
        foreign = await client.post("/api/receipts/confirm", headers=_auth(c["owner"]), json={
            **body, "image_path": f"{uuid.uuid4()}/{uuid.uuid4()}.jpg",
        })
        empty = await client.post("/api/receipts/confirm", headers=_auth(c["owner"]), json={
            "image_path": f"{c['bid']}/{uuid.uuid4()}.jpg", "total_amount": 0, "lines": [
                {"name": "Plastik", "quantity": 1, "skip": True},
            ],
        })
    assert first.status_code == 201
    assert again.status_code == 409 and "sudah dicatat" in again.json()["detail"]
    assert foreign.status_code == 422
    assert empty.status_code == 422 and "Belum ada yang bisa dicatat" in empty.json()["detail"]
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert (await s.get(Item, c["gula"])).current_stock == Decimal("5.000")
        assert (await s.execute(select(func.count(GoodsReceipt.id)))).scalar_one() == 1
        assert (await s.execute(select(func.count(Expense.id)))).scalar_one() == 1


async def test_a_full_quota_or_a_photo_that_is_not_a_nota_is_said_plainly(client, session_factory, shop):
    c = shop

    class Quota(Exception):
        code = 429

    with (
        patch("app.whatsapp.storage.upload_receipt_image", new=_upload([])),
        patch("app.ai.vision.parse_business_document", new=AsyncMock(side_effect=Quota("RESOURCE_EXHAUSTED"))),
    ):
        busy = await client.post("/api/receipts/scan", headers=_auth(c["owner"]), json={"image_base64": PHOTO, "mime_type": "image/jpeg"})
    assert busy.status_code == 429 and "5 foto per menit" in busy.json()["detail"]

    with (
        patch("app.whatsapp.storage.upload_receipt_image", new=_upload([])),
        patch("app.ai.vision.parse_business_document", new=AsyncMock(return_value={"document_type": "other", "items": []})),
    ):
        other = await client.post("/api/receipts/scan", headers=_auth(c["owner"]), json={"image_base64": PHOTO, "mime_type": "image/jpeg"})
    assert other.status_code == 422 and "Tambah pengeluaran" in other.json()["detail"]

    garbage = await client.post("/api/receipts/scan", headers=_auth(c["owner"]), json={"image_base64": "bukan-foto!!" * 4, "mime_type": "image/jpeg"})
    assert garbage.status_code == 422


async def test_the_till_cannot_reach_these_routes(client, session_factory, shop):
    c = shop
    pos = create_token(business_id=str(c["bid"]), scope="pos", staff_id=str(uuid.uuid4()))
    for path, body in (
        ("/api/expenses", {"amount": 1000, "category": "lainnya"}),
        ("/api/receipts/scan", {"image_base64": PHOTO, "mime_type": "image/jpeg"}),
        ("/api/receipts/confirm", {"image_path": "x", "total_amount": 1}),
    ):
        resp = await client.post(path, headers=_auth(pos), json=body)
        assert resp.status_code in (401, 403), (path, resp.status_code)
