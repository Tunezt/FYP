"""till-5b — the customer's receipt only when asked; a receipt to scan.

Decision 4 (1 Oct 2026): after payment the cashier asks and taps *Kertas*,
*QR*, *WhatsApp* or *Tidak perlu*. Kitchen and Bar slips always print. The
choice is kept on the order. *QR* shows a web receipt at /struk/<code>; the
code is unguessable and says whose receipt it is, because one bot number will
serve every café (till-7). A café on `receipt_mode = 'always'` prints at
payment, as every older print test expects.
"""
import os
import re
import uuid
from decimal import Decimal
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import create_token, hash_pin
from app.main import app
from app.models import Business, Customer, Item, Order, PrintJob, Staff
from app.services.catalog import ensure_default_variant
from app.services.receipt_delivery import CODE_RE, normalize_code
from app.services.stock import open_item_stock

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


async def _make(session_factory, mode="ask", name="Struk Pilih"):
    async with session_factory() as s:
        biz = Business(name=name, owner_phone=f"62989{uuid.uuid4().hex[:9]}", receipt_mode=mode)
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await seed_books(s, bid)
        sari = Staff(business_id=bid, name="Sari", pin_hash=hash_pin("2345"))
        kopi = Item(business_id=bid, name="Kopi", unit="cup", current_stock=Decimal(20), sell_price=Decimal(20000), prep_station="bar")
        roti = Item(business_id=bid, name="Roti", unit="pcs", current_stock=Decimal(20), sell_price=Decimal(15000), prep_station="kitchen")
        s.add_all([sari, kopi, roti])
        await s.flush()
        for it in (kopi, roti):
            await open_item_stock(s, it, unit_cost=Decimal(0))
            await ensure_default_variant(s, it)
        andi = Customer(business_id=bid, name="Andi Wijaya", phone="6281234567890")
        s.add(andi)
        await s.commit()
        return {"bid": bid, "kopi": kopi.id, "roti": roti.id, "andi": andi.id,
                "pos": {"Authorization": f"Bearer {create_token(business_id=str(bid), scope='pos', staff_id=str(sari.id))}"}}


async def _drop(session_factory, bid):
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


@pytest.fixture
async def cafe(session_factory):
    c = await _make(session_factory)
    yield c
    await _drop(session_factory, c["bid"])


async def _sell(client, c, **extra):
    resp = await client.post("/pos/orders", headers=c["pos"], json={
        "order_type": "takeaway",
        "lines": [{"item_id": str(c["kopi"]), "quantity": 1}, {"item_id": str(c["roti"]), "quantity": 1}],
        "payments": [{"method": "cash", "amount": 35000}], **extra,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _kinds(session_factory, c, order_id):
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        return sorted(j.kind for j in (await s.execute(select(PrintJob).where(PrintJob.order_id == uuid.UUID(order_id)))).scalars())


async def _choose(client, c, order_id, choice):
    return await client.post(f"/pos/orders/{order_id}/receipt-choice", headers=c["pos"], json={"choice": choice})


async def test_payment_prints_the_slips_and_waits_to_be_asked_for_the_receipt(client, session_factory, cafe):
    c = cafe
    config = (await client.get("/pos/config", headers=c["pos"])).json()
    assert config["receipt_mode"] == "ask" and config["whatsapp_receipts"] is False
    order_id = await _sell(client, c)
    assert await _kinds(session_factory, c, order_id) == ["bar_ticket", "kitchen_ticket"]      # slips always print

    paper = await _choose(client, c, order_id, "paper")
    assert paper.status_code == 200 and paper.json()["print_job_id"]
    again = await _choose(client, c, order_id, "paper")                                        # a second tap
    assert again.json()["print_job_id"] == paper.json()["print_job_id"]
    assert await _kinds(session_factory, c, order_id) == ["bar_ticket", "kitchen_ticket", "receipt"]
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        order = await s.get(Order, uuid.UUID(order_id))
        assert order.receipt_choice == "paper" and order.receipt_choice_at is not None


async def test_no_receipt_is_recorded_and_prints_nothing(client, session_factory, cafe):
    c = cafe
    order_id = await _sell(client, c)
    resp = await _choose(client, c, order_id, "none")
    assert resp.status_code == 200 and resp.json()["receipt_code"] is None
    assert await _kinds(session_factory, c, order_id) == ["bar_ticket", "kitchen_ticket"]
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert (await s.get(Order, uuid.UUID(order_id))).receipt_choice == "none"


async def test_a_qr_receipt_opens_for_whoever_scans_it_and_shows_no_customer(client, session_factory, cafe):
    c = cafe
    order_id = await _sell(client, c, customer_id=str(c["andi"]))
    resp = await _choose(client, c, order_id, "qr")
    out = resp.json()
    code = out["receipt_code"]
    assert CODE_RE.match(code) and code.startswith(str(c["bid"])[:8]) and out["web_path"] == f"/struk/{code}"
    assert await _kinds(session_factory, c, order_id) == ["bar_ticket", "kitchen_ticket"]      # no paper
    again = (await _choose(client, c, order_id, "qr")).json()
    assert again["receipt_code"] == code                                                       # one code per sale

    public = await client.get(f"/public/struk/{code.upper()}")                                  # no token at all
    assert public.status_code == 200
    body = public.json()
    assert body["business_name"] == "Struk Pilih" and body["total"] == "35000.00"
    assert {l["name"] for l in body["lines"]} == {"Kopi", "Roti"}
    assert body["customer_name"] is None and "Andi" not in public.text and "628123" not in public.text

    # Paper afterwards is still possible: the customer changed their mind.
    assert (await _choose(client, c, order_id, "paper")).status_code == 200
    assert await _kinds(session_factory, c, order_id) == ["bar_ticket", "kitchen_ticket", "receipt"]


async def test_a_code_never_opens_another_cafes_receipt(client, session_factory, cafe):
    c = cafe
    other = await _make(session_factory, name="Kafe Lain")
    try:
        mine = (await _choose(client, c, await _sell(client, c), "qr")).json()["receipt_code"]
        theirs_order = await _sell(client, other)
        # Same random part, the other café's prefix: not a receipt.
        forged = f"{str(other['bid'])[:8]}-{mine.split('-')[1]}"
        assert (await client.get(f"/public/struk/{forged}")).status_code == 404
        assert (await client.get("/public/struk/001")).status_code == 404
        assert (await client.get(f"/public/struk/{str(c['bid'])[:8]}-aaaaaaaaaa")).status_code == 404
        # And the other café's sale that nobody asked a QR for has no code at all.
        async with session_factory() as s:
            await _set_tenant(s, other["bid"])
            assert (await s.get(Order, uuid.UUID(theirs_order))).receipt_code is None
    finally:
        await _drop(session_factory, other["bid"])


async def test_whatsapp_is_offered_only_once_the_bot_number_is_live(client, session_factory, cafe):
    c = cafe
    order_id = await _sell(client, c)
    off = await _choose(client, c, order_id, "whatsapp")
    assert off.status_code == 409 and "belum aktif" in off.json()["detail"]
    from app.core.config import get_settings

    with patch.object(get_settings(), "whatsapp_receipt_number", "+62 811-0000-1234"):
        assert (await client.get("/pos/config", headers=c["pos"])).json()["whatsapp_receipts"] is True
        on = (await _choose(client, c, order_id, "whatsapp")).json()
    assert on["whatsapp_link"] == f"https://wa.me/6281100001234?text=STRUK%20{on['receipt_code']}"


async def test_only_a_paid_sale_has_a_receipt(client, session_factory, cafe):
    c = cafe
    draft = await client.post("/pos/drafts", headers=c["pos"], json={
        "lines": [{"item_id": str(c["kopi"]), "quantity": 1}], "order_type": "takeaway", "client_ref": uuid.uuid4().hex,
    })
    assert draft.status_code == 201, draft.text
    refused = await _choose(client, c, draft.json()["id"], "paper")
    assert refused.status_code == 409
    assert (await _choose(client, c, str(uuid.uuid4()), "paper")).status_code == 404
    assert (await _choose(client, c, await _sell(client, c), "fax")).status_code == 422


async def test_a_cafe_that_always_prints_still_does(client, session_factory):
    c = await _make(session_factory, mode="always", name="Selalu Cetak")
    try:
        order_id = await _sell(client, c)
        assert await _kinds(session_factory, c, order_id) == ["bar_ticket", "kitchen_ticket", "receipt"]
    finally:
        await _drop(session_factory, c["bid"])


def test_codes_are_read_back_however_they_are_typed():
    assert normalize_code("  ABCD1234-K7Q2MX4D9P ") == "abcd1234-k7q2mx4d9p"
    assert normalize_code("abcd1234-k7q2mx4d9l") is None            # 'l' is not in the alphabet
    assert normalize_code("001") is None and normalize_code(None) is None
