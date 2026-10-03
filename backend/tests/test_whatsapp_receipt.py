"""till-7 — a receipt on the customer's WhatsApp, route A (decisions 4 and 5).

The customer scans a QR (`wa.me/<bot>?text=STRUK <code>`) and sends the
message. The bot recognises `STRUK <code>` from any number, finds the paid
order in whichever café the code belongs to, and replies with the receipt as
text — free, inside the window the customer opened. Anything else from an
unknown number still hears "belum terdaftar". The code cannot be walked:
lookups are rate-limited per sender; and the log is written only under the
café that owns the receipt, without the customer's number.

Built and tested before the café's Indonesian bot SIM exists: the WhatsApp
client is mocked, as in the other webhook tests.
"""
import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import create_token, hash_pin
from app.main import app
from app.models import Business, Item, PrintJob, RequestLog, Staff
from app.services.business_profile import apply_placeholders, apply_tax_placeholder
from app.services.catalog import ensure_default_variant
from app.services.pricing import ensure_pricing_settings
from app.services.stock import open_item_stock
from app.whatsapp import receipts as wa_receipts
from app.whatsapp.processor import _process_message
from app.whatsapp.receipts import receipt_code_in

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bridge"))
import print_bridge as pb  # noqa: E402

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")
CUSTOMER = "6281399990000"


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


@pytest.fixture(autouse=True)
def fresh_rate_limit():
    wa_receipts._recent.clear()
    yield
    wa_receipts._recent.clear()


async def _set_tenant(session, business_id):
    await session.execute(text("select set_config('app.current_business_id', :bid, true)"), {"bid": str(business_id)})


@pytest.fixture
async def cafe(session_factory):
    async with session_factory() as s:
        biz = Business(name="Poernama", owner_phone=f"62990{uuid.uuid4().hex[:9]}", receipt_mode="ask")
        apply_placeholders(biz)
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        from tests.conftest import seed_books

        await seed_books(s, bid)
        apply_tax_placeholder(await ensure_pricing_settings(s, bid))
        sari = Staff(business_id=bid, name="Sari", pin_hash=hash_pin("2345"))
        kopi = Item(business_id=bid, name="Kopi Susu", unit="cup", current_stock=Decimal(20), sell_price=Decimal(22000), prep_station="bar")
        s.add_all([sari, kopi])
        await s.flush()
        await open_item_stock(s, kopi, unit_cost=Decimal(0))
        await ensure_default_variant(s, kopi)
        await s.commit()
        ids = {"bid": bid, "kopi": kopi.id,
               "pos": {"Authorization": f"Bearer {create_token(business_id=str(bid), scope='pos', staff_id=str(sari.id))}"}}
    yield ids
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


async def _paid_with_code(client, c):
    sale = await client.post("/pos/orders", headers=c["pos"], json={
        "order_type": "takeaway", "lines": [{"item_id": str(c["kopi"]), "quantity": 1}],
        "payments": [{"method": "cash", "amount": 24200, "tendered": 50000}],   # 22.000 + PB1 10% (till-11)
    })
    assert sale.status_code == 201, sale.text
    choice = await client.post(f"/pos/orders/{sale.json()['id']}/receipt-choice", headers=c["pos"], json={"choice": "qr"})
    return sale.json()["id"], choice.json()["receipt_code"]


def _message(body, sender=CUSTOMER):
    return {"id": f"wamid.test-{uuid.uuid4().hex}", "from": sender, "type": "text", "text": {"body": body}}


def test_the_message_is_recognised_however_it_is_typed():
    assert receipt_code_in("STRUK 8b6a1a66-g8wb8u8nuw") == "8b6a1a66-g8wb8u8nuw"
    assert receipt_code_in("  struk   8B6A1A66-G8WB8U8NUW ") == "8B6A1A66-G8WB8U8NUW"
    assert receipt_code_in("struk kopi saya mana") is None
    assert receipt_code_in("halo") is None


async def test_a_customer_sends_struk_and_gets_the_receipt(client, session_factory, cafe):
    c = cafe
    _order, code = await _paid_with_code(client, c)
    with patch("app.whatsapp.processor.send_text", new=AsyncMock()) as send_text:
        await _process_message(_message(f"STRUK {code.upper()}"))
    send_text.assert_awaited_once()
    to, body = send_text.await_args.args
    assert to == CUSTOMER
    assert body.startswith("*POERNAMA*") and "Jl. Lorem Ipsum No. 1, Kota Dolor" in body
    assert "1× Kopi Susu — Rp 22.000" in body and "*Total: Rp 24.200*" in body
    assert "PB1 10%: Rp 2.200" in body
    assert "Tunai Rp 24.200 · diterima Rp 50.000 · kembali Rp 25.800" in body
    assert f"/struk/{code}" in body and "belum terdaftar" not in body
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        logs = (await s.execute(select(RequestLog).where(RequestLog.classified_intent == "receipt_lookup"))).scalars().all()
        assert len(logs) == 1 and logs[0].raw_query == f"STRUK {code}" and CUSTOMER not in (logs[0].raw_query or "")


async def test_a_wrong_code_finds_nothing_and_anything_else_is_as_before(client, session_factory, cafe):
    c = cafe
    _order, code = await _paid_with_code(client, c)
    other_prefix = "00000000-" + code.split("-")[1]
    with patch("app.whatsapp.processor.send_text", new=AsyncMock()) as send_text:
        await _process_message(_message(f"STRUK {other_prefix}"))
        await _process_message(_message("STRUK 001"))
        await _process_message(_message("halo, mau pesan"))
    replies = [call.args[1] for call in send_text.await_args_list]
    assert "tidak ditemukan" in replies[0] and "tidak ditemukan" in replies[1]
    assert "belum terdaftar" in replies[2]
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert (await s.execute(select(RequestLog).where(RequestLog.path == "receipt"))).scalars().all() == []


async def test_one_number_cannot_walk_the_codes(client, session_factory, cafe):
    c = cafe
    _order, code = await _paid_with_code(client, c)
    with patch("app.whatsapp.processor.send_text", new=AsyncMock()) as send_text:
        for i in range(wa_receipts.RATE_LIMIT):
            await _process_message(_message(f"STRUK {str(c['bid'])[:8]}-aaaaaaaa{i}a"))
        await _process_message(_message(f"STRUK {code}"))                     # even a real one, now
        await _process_message(_message(f"STRUK {code}", sender="6281300000001"))   # another phone is fine
    replies = [call.args[1] for call in send_text.await_args_list]
    assert all("tidak ditemukan" in r for r in replies[:wa_receipts.RATE_LIMIT])
    assert "Terlalu banyak" in replies[wa_receipts.RATE_LIMIT]
    assert replies[-1].startswith("*POERNAMA*")


async def test_once_the_bot_is_live_the_paper_receipt_carries_the_whatsapp_qr(client, session_factory, cafe):
    from app.core.config import get_settings

    c = cafe
    with patch.object(get_settings(), "whatsapp_receipt_number", "6281100001234"):
        sale = await client.post("/pos/orders", headers=c["pos"], json={
            "order_type": "takeaway", "lines": [{"item_id": str(c["kopi"]), "quantity": 1}],
            "payments": [{"method": "qris", "amount": 24200}],
        })
        choice = (await client.post(f"/pos/orders/{sale.json()['id']}/receipt-choice", headers=c["pos"], json={"choice": "paper"})).json()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        job = await s.get(PrintJob, uuid.UUID(choice["print_job_id"]))
        from app.models import Order

        code = (await s.get(Order, uuid.UUID(sale.json()["id"]))).receipt_code
    qr = [b for b in job.document["blocks"] if b["t"] == "qr"]
    assert code and qr == [{"t": "qr", "data": f"https://wa.me/6281100001234?text=STRUK%20{code}"}]
    # The bridge turns it into an ESC/POS QR (model 2) carrying exactly that link.
    data = pb.render(job.document, pb.Profile(columns=48))
    assert b"\x1d(k\x04\x001A2\x00" in data and qr[0]["data"].encode() in data and b"\x1d(k\x03\x001Q0" in data

    # Without the bot's number, no QR and no code is made for a printed receipt.
    sale2 = await client.post("/pos/orders", headers=c["pos"], json={
        "order_type": "takeaway", "lines": [{"item_id": str(c["kopi"]), "quantity": 1}],
        "payments": [{"method": "qris", "amount": 24200}],
    })
    job2 = (await client.post(f"/pos/orders/{sale2.json()['id']}/receipt-choice", headers=c["pos"], json={"choice": "paper"})).json()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        doc = (await s.get(PrintJob, uuid.UUID(job2["print_job_id"]))).document
    assert not [b for b in doc["blocks"] if b["t"] == "qr"]
