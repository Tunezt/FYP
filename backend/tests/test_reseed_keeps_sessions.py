"""p2-reseed — re-seeding the demo asks nothing of the owner afterwards.

Before, every `python -m app.seed` made a new business id, so the dashboard session died
(log in again) and the receipt-question step needed the owner to send the staged photo
from WhatsApp and reply YA. Now the business, the owner and the staff keep their ids, so
the owner's dashboard token, the till's pairing link and a cashier's till session all
survive a re-seed; and the seed itself saves the staged Toko Supply receipt through the
YA path (stored photo, receipt, goods receipt, expense, vector) exactly once per seed.

The storage upload and the embedding call are mocked (both are measured elsewhere);
everything else runs against local Postgres. No skip marker.
"""
import hashlib
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import func, select, text

from app.main import app
from app.models import Business, Expense, GoodsReceipt, GoodsReceiptLine, Receipt, Staff
from app.seed import STAGED_PHOTO, seed
from tests.test_service_journey import engine, session_factory  # noqa: F401 (fixtures)

PHONE, NAME = "628115550077", "Poernama Reseed"


@pytest.fixture
async def client():
    from app.core.db import engine as app_engine

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await app_engine.dispose()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def _seed(upload=None):
    upload = upload or AsyncMock(side_effect=lambda bid, data, mime: f"{bid}/staged.jpg")
    with patch("app.whatsapp.storage._dry_run", new=lambda: False), \
            patch("app.whatsapp.storage.upload_receipt_image", new=upload), \
            patch("app.services.rag.embed_text", new=AsyncMock(return_value=[0.01] * 768)):
        await seed(confirm=True, phone=PHONE, business_name=NAME, owner_name="Pemilik", stage_receipt=True)
    assert upload.await_count == 1


async def test_a_reseed_keeps_every_session_and_saves_the_staged_receipt_once(session_factory, client):
    await _seed()
    async with session_factory() as s:
        biz = (await s.execute(select(Business).where(Business.name == NAME))).scalar_one()
        bid = biz.id
        # The WhatsApp cafe pick and an extra owner number live on the business row.
        biz.active_owner_numbers = [PHONE]
        biz.extra_owner_numbers = [{"name": "Adik", "phone": "628115550078", "added_at": "2026-11-20"}]
        await s.commit()

    try:
        login = await client.post("/auth/login-pin", json={"phone": PHONE, "pin": "1234"})
        assert login.status_code == 200, login.text
        owner_token = login.json()["token"]
        pairing = (await client.post("/auth/pos-pairing", headers=_auth(owner_token))).json()["pairing_token"]
        sari_id = (await client.get(f"/pos/business/{pairing}")).json()
        sari_id = next(st["id"] for st in sari_id["staff"] if st["name"] == "Sari")
        till = await client.post("/pos/login", json={"pairing_token": pairing, "staff_id": sari_id, "pin": "2345"})
        assert till.status_code == 200, till.text
        till_token = till.json()["token"]

        await _seed()   # the evening-before re-seed

        # A token only names a business id, so "still works" means it still reaches the refilled cafe.
        business = await client.get("/api/business", headers=_auth(owner_token))
        assert business.status_code == 200 and business.json()["name"] == NAME
        assert (await client.get("/api/overview", headers=_auth(owner_token))).json()["business_name"] == NAME
        assert (await client.get(f"/pos/business/{pairing}")).json()["business_name"] == NAME
        till_items = await client.get("/pos/items", headers=_auth(till_token))
        assert till_items.status_code == 200 and len(till_items.json()) > 0

        async with session_factory() as s:
            biz = (await s.execute(select(Business).where(Business.name == NAME))).scalar_one()
            assert biz.id == bid
            assert biz.active_owner_numbers == [PHONE] and biz.extra_owner_numbers[0]["phone"] == "628115550078"
            await s.execute(text("select set_config('app.current_business_id', :b, false)"), {"b": str(bid)})
            assert str((await s.execute(select(Staff.id).where(Staff.name == "Sari"))).scalar_one()) == sari_id

            # The staged receipt, once: what the owner's photo + YA wrote.
            staged = (await s.execute(select(Receipt).where(Receipt.total_amount == Decimal(684000)))).scalars().all()
            assert len(staged) == 1
            receipt = staged[0]
            assert receipt.supplier == "Toko Supply" and receipt.image_url == f"{bid}/staged.jpg"
            assert receipt.supplier_id is not None and receipt.embedding is not None
            assert receipt.parsed_data["image_sha256"] == hashlib.sha256(STAGED_PHOTO.read_bytes()).hexdigest()
            expenses = (await s.execute(select(Expense).where(Expense.receipt_id == receipt.id))).scalars().all()
            assert [(e.amount, e.source) for e in expenses] == [(Decimal(684000), "receipt")]
            gr = (await s.execute(select(GoodsReceipt).where(GoodsReceipt.notes.contains(receipt.image_url)))).scalar_one()
            n_lines = (await s.execute(select(func.count()).select_from(GoodsReceiptLine)
                                       .where(GoodsReceiptLine.receipt_id == gr.id))).scalar_one()
            assert n_lines == 3   # gula aren, susu UHT, biji arabica: every line matched an item
            await s.rollback()
    finally:
        async with session_factory() as s:
            row = await s.get(Business, bid)
            if row:
                await s.delete(row)
            await s.commit()


async def test_a_failed_upload_still_saves_the_receipt_with_the_bundled_photo(session_factory, capsys):
    from pathlib import Path

    await _seed(upload=AsyncMock(side_effect=RuntimeError("bucket down")))
    assert "WARNING: the staged receipt's photo upload failed (RuntimeError: bucket down)" in capsys.readouterr().out
    async with session_factory() as s:
        bid = (await s.execute(select(Business.id).where(Business.name == NAME))).scalar_one()
        try:
            await s.execute(text("select set_config('app.current_business_id', :b, false)"), {"b": str(bid)})
            receipt = (await s.execute(select(Receipt).where(Receipt.total_amount == Decimal(684000)))).scalar_one()
            assert receipt.image_url == f"demo/{STAGED_PHOTO.name}"   # the dashboard still shows the photo
            shipped = Path(__file__).resolve().parents[2] / "frontend" / "public" / "demo-receipts" / STAGED_PHOTO.name
            assert shipped.read_bytes() == STAGED_PHOTO.read_bytes()
            await s.rollback()
        finally:
            await s.delete(await s.get(Business, bid))
            await s.commit()
