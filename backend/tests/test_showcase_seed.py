"""demo-showcase-seed — the demo café shows every feature, on whatever day it is seeded.

The nightly rules (margin drop, stock-out, void rate, supplier price, takings) depend on
weekday patterns and thresholds, so a seed that fired all five today could fire four next
Wednesday. This seeds the demo as if it were three different days and checks the whole
showcase each time: all five alert types, both read and unread, and the data behind every
page. Needs the local Postgres (roadmap §2). No skip marker.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, text

from app.models import (
    Alert, Approval, Business, Customer, GoodsReceipt, Order, PointsMovement, PrintDevice, PromoApplication,
    PurchaseOrder, Receipt, Shift, Staff, Supplier, Voucher,
)
from app.seed import EMBEDDINGS_FILE, nota_parsed, seed
from app.seed_data import NOTAS
from tests.test_service_journey import engine, session_factory  # noqa: F401 (fixtures)

PHONE, NAME = "628115550001", "Poernama Uji"
ALERT_TYPES = {"anomaly", "margin_drop", "stockout_risk", "supplier_price", "void_rate"}


async def _scoped(session_factory, bid):
    s = session_factory()
    session = await s.__aenter__()
    await session.execute(text("select set_config('app.current_business_id', :b, false)"), {"b": str(bid)})
    return s, session


@pytest.mark.parametrize("days_back", [0, 2, 5])
async def test_the_showcase_has_every_alert_and_every_page_filled(session_factory, days_back):
    today = datetime.now(timezone.utc) - timedelta(days=days_back)
    fired = await seed(confirm=True, phone=PHONE, business_name=NAME, owner_name="Pemilik", today=today)
    async with session_factory() as s:
        bid = (await s.execute(select(Business.id).where(Business.name == NAME))).scalar_one()
        await s.execute(text("select set_config('app.current_business_id', :b, false)"), {"b": str(bid)})

        async def count(model, *where):
            return int((await s.execute(select(func.count()).select_from(model).where(*where))).scalar_one())

        try:
            assert set(fired) == ALERT_TYPES, f"{days_back} days back: {fired}"
            assert await count(Alert, Alert.is_acknowledged.is_(True)) >= 3 and await count(Alert, Alert.is_acknowledged.is_(False)) >= 4
            assert await count(Supplier) == 5 and await count(Receipt) == len(NOTAS)
            statuses = set((await s.execute(select(PurchaseOrder.status))).scalars().all())
            assert statuses == {"draft", "ordered", "partially_received", "received", "cancelled"}
            assert await count(GoodsReceipt) >= 10
            assert await count(Customer) >= 8 and await count(PointsMovement, PointsMovement.reason == "redeem") >= 1
            assert await count(PromoApplication) >= 5 and await count(Voucher, Voucher.uses > 0) >= 3
            variances = (await s.execute(select(Shift.variance).where(Shift.variance != 0))).scalars().all()
            assert any(v < 0 for v in variances) and any(v > 0 for v in variances)
            assert await count(Order, Order.status == "voided") >= 10 and await count(Order, Order.status == "refunded") == 1
            assert await count(Approval) >= 11
            assert await count(Order, Order.status == "open") == 2   # two guests waiting from the table QR
            assert await count(PrintDevice) == 0   # a printer's state is the live bridge's word, never seeded
            assert await count(Staff, Staff.is_active.is_(False)) == 1 and await count(Staff, Staff.role == "manager") == 1
            assert await count(Receipt, Receipt.embedding.is_not(None)) == len(NOTAS), "run scripts/demo_receipt_embeddings.py"
        finally:
            await s.rollback()
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


def test_the_stored_receipt_vectors_match_the_receipt_text():
    import hashlib, json

    from app.models import Receipt as R
    from app.services.rag import receipt_to_document_text

    stored = json.loads(EMBEDDINGS_FILE.read_text(encoding="utf-8"))
    for nota in NOTAS:
        parsed, when, total = nota_parsed(nota)
        text_ = receipt_to_document_text(R(parsed_data=parsed, supplier=nota[1], total_amount=total, occurred_at=when))
        assert hashlib.sha256(text_.encode()).hexdigest() in stored, f"{nota[0]}: text changed, rerun scripts/demo_receipt_embeddings.py"
        assert len(stored[hashlib.sha256(text_.encode()).hexdigest()]) == 768


async def test_a_demo_receipt_photo_is_a_static_file_not_a_bucket_object():
    from app.whatsapp.storage import create_signed_url

    assert await create_signed_url("demo/nota-pak-udin.jpg") == "/demo-receipts/nota-pak-udin.jpg"
    for nota in NOTAS:
        from pathlib import Path

        assert (Path(__file__).resolve().parents[2] / "frontend" / "public" / "demo-receipts" / f"{nota[0]}.jpg").exists()
