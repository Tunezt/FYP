"""till-5a — a receipt that reads as a receipt, on paper and on screen.

Seen in the first run: a header that was only "POERNAMA"; no address or way to
reach the café; "Struk #22A3D491", an internal id, where a customer looks for
something meaningful; no cash given or change; and "Matcha Latte · Standar" on
the receipt but "Matcha Latte" in *Riwayat transaksi*.

Now: the café's details come from its settings (placeholders, marked, until the
owner types the real ones); the tax is named and rated ("PB1 10%", added on top
of the menu price since till-11; "(termasuk)" when a café keeps it inside);
a cash payment shows what was handed over and the change; the internal
reference is a small "Ref" at the foot; and a size is written by one rule
everywhere — whenever the product has sizes, "Standar" included, never for a
product with one. The paper is checked through the bridge's real ESC/POS
renderer at 80 mm / 48 columns.
"""
import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import create_token, hash_pin
from app.main import app
from app.models import Business, Item, Payment, PrintJob, Staff
from app.services.business_profile import RECEIPT_PLACEHOLDERS, apply_placeholders, apply_tax_placeholder, tax_line_label
from app.services.catalog import create_modifier, create_modifier_group, create_variant, ensure_default_variant
from app.services.pricing import ensure_pricing_settings
from app.services.stock import open_item_stock

from tests.conftest import seed_books

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bridge"))
import print_bridge as pb  # noqa: E402

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")
LONG_NAME = "Matcha Latte Dingin Ekstra Besar Brown Sugar Boba"
LONG_NOTE = "es dipisah, gula setengah, sedotan kertas, tolong dibungkus rapat"


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
        biz = Business(name="Poernama", owner_phone=f"62988{uuid.uuid4().hex[:9]}")
        apply_placeholders(biz)
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await seed_books(s, bid)
        apply_tax_placeholder(await ensure_pricing_settings(s, bid))
        owner = Staff(business_id=bid, name="Ibu Diah", role="owner", pin_hash=hash_pin("1234"))
        sari = Staff(business_id=bid, name="Sari", pin_hash=hash_pin("2345"))
        matcha = Item(business_id=bid, name=LONG_NAME, unit="cup", current_stock=Decimal(50), sell_price=Decimal(28000), prep_station="bar")
        roti = Item(business_id=bid, name="Roti", unit="pcs", current_stock=Decimal(50), sell_price=Decimal(15000), prep_station="kitchen")
        s.add_all([owner, sari, matcha, roti])
        await s.flush()
        for it in (matcha, roti):
            await open_item_stock(s, it, unit_cost=Decimal(0))
        standar = await ensure_default_variant(s, matcha)
        await create_variant(s, matcha, name="Large", sell_price=Decimal(33000))
        await ensure_default_variant(s, roti)
        group = await create_modifier_group(s, matcha, name="Susu")
        oat = await create_modifier(s, group, name="Oat milk", price_delta=Decimal(5000))
        await s.commit()
        ids = {
            "bid": bid, "matcha": matcha.id, "standar": standar.id, "oat": oat.id, "roti": roti.id,
            "pos": {"Authorization": f"Bearer {create_token(business_id=str(bid), scope='pos', staff_id=str(sari.id))}"},
            "owner": {"Authorization": f"Bearer {create_token(business_id=str(bid), scope='owner', staff_id=str(owner.id))}"},
        }
    yield ids
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


async def _sell(client, c, payments):
    resp = await client.post("/pos/orders", headers=c["pos"], json={
        "order_type": "takeaway",
        "lines": [
            {"item_id": str(c["matcha"]), "variant_id": str(c["standar"]), "modifier_ids": [str(c["oat"])], "quantity": 1, "notes": LONG_NOTE},
            {"item_id": str(c["roti"]), "quantity": 1},
        ],
        "payments": payments,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def _paper_lines(document: dict, columns: int = 48) -> list[str]:
    """Every line the bridge would send to an 80 mm printer, as text. The
    bridge's Writer refuses (asserts) any line wider than the paper."""
    lines: list[str] = []
    w = pb.Writer(columns)
    original = w.row

    def row(text_, **kw):
        lines.append(pb.to_printer_text(text_))
        original(text_, **kw)

    w.row = row
    for block in document["blocks"]:
        pb.render_block(w, block, pb.Profile(columns=columns))
    return lines


async def _receipt_doc(session_factory, c, order_id):
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        job = (await s.execute(select(PrintJob).where(PrintJob.order_id == uuid.UUID(order_id), PrintJob.kind == "receipt"))).scalar_one()
        return job.document


def test_the_tax_line_names_the_tax():
    assert tax_line_label("PBJT", Decimal("0.10"), True) == "PBJT 10% (termasuk)"
    assert tax_line_label("PBJT", Decimal("0.10"), False) == "PBJT 10%"
    assert tax_line_label("Pajak", Decimal("0.115"), True) == "Pajak 11.5% (termasuk)"


async def test_the_paper_receipt_has_the_cafe_the_tax_and_the_change(client, session_factory, cafe):
    c = cafe
    # till-11: 48.000 on the menu + PB1 10% = 52.800 to pay.
    sale = await _sell(client, c, [{"method": "cash", "amount": 52800, "tendered": 100000}])
    assert sale["payments"][0]["tendered"] == "100000.00" and sale["total"] == "52800.00"
    doc = await _receipt_doc(session_factory, c, sale["id"])
    paper = _paper_lines(doc)
    joined = "\n".join(paper)
    assert paper[0].strip() == "POERNAMA"
    assert RECEIPT_PLACEHOLDERS["address"] in joined
    assert "0812-0000-0000 - IG @poernama.cafe" in joined                       # "·" is "-" on paper (ASCII only)
    assert "PESANAN" in joined                                                     # the big number stays
    assert any(l.startswith("Kasir") and l.endswith("Sari") for l in paper)   # till-12: label and value
    assert any(l.startswith("Subtotal") and l.endswith("Rp 48.000") for l in paper)
    assert any(l.startswith("PB1 10%") and l.endswith("Rp 4.800") for l in paper)   # on top: 48.000 x 10%
    assert "termasuk" not in joined
    assert any("TOTAL" in l and l.endswith("Rp 52.800") for l in paper)
    assert any(l.startswith("Tunai") and l.endswith("Rp 52.800") for l in paper)
    assert any(l.startswith("Diterima") and l.endswith("Rp 100.000") for l in paper)
    assert any(l.startswith("Kembali") and l.endswith("Rp 47.200") for l in paper)
    assert "Struk #" not in joined and any(l.strip().startswith("Ref ") for l in paper)
    # The longest name, its size, a priced modifier and a long note fit 48 columns.
    assert max(len(l) for l in paper) <= 48
    assert any("Matcha Latte Dingin" in l for l in paper) and "(Standar)" in joined
    assert "+ Oat milk +Rp 5.000" in joined and "sedotan" in joined


async def test_the_size_is_written_by_one_rule_everywhere(client, session_factory, cafe):
    """The matcha has two sizes, so "Standar" is written; the roti has one, so
    nothing is. The same on the screen receipt, the owner's receipt and the
    paper, and on the kitchen/bar slips."""
    c = cafe
    sale = await _sell(client, c, [{"method": "qris", "amount": 52800}])
    till = (await client.get(f"/pos/orders/{sale['id']}/receipt", headers=c["pos"])).json()
    owner = (await client.get(f"/api/orders/{sale['id']}/receipt", headers=c["owner"])).json()
    for view in (till, owner):
        sizes = {l["name"]: l["size"] for l in view["lines"]}
        assert sizes == {LONG_NAME: "Standar", "Roti": None}
        assert view["tax_label"] == "PB1" and view["tax_rate"] == "0.1000"
        assert view["business_address"] == RECEIPT_PLACEHOLDERS["address"]
    doc = await _receipt_doc(session_factory, c, sale["id"])
    items = {b["name"]: b.get("size") for b in doc["blocks"] if b["t"] == "item_priced"}
    assert items == {LONG_NAME: "Standar", "Roti": None}
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        slips = (await s.execute(select(PrintJob).where(PrintJob.order_id == uuid.UUID(sale["id"]), PrintJob.kind.in_(["bar_ticket", "kitchen_ticket"])))).scalars().all()
        slip_sizes = {b["name"]: b.get("size") for j in slips for b in j.document["blocks"] if b["t"] == "item"}
    assert slip_sizes == {LONG_NAME: "Standar", "Roti": None}


async def test_cash_handed_over_must_cover_the_cash_payment(client, session_factory, cafe):
    c = cafe
    short = await client.post("/pos/orders", headers=c["pos"], json={
        "order_type": "takeaway", "lines": [{"item_id": str(c["roti"]), "quantity": 1}],
        "payments": [{"method": "cash", "amount": 15000, "tendered": 10000}],
    })
    assert short.status_code == 422 and "uang diterima" in short.json()["detail"]
    qris = await client.post("/pos/orders", headers=c["pos"], json={
        "order_type": "takeaway", "lines": [{"item_id": str(c["roti"]), "quantity": 1}],
        "payments": [{"method": "qris", "amount": 15000, "tendered": 20000}],
    })
    assert qris.status_code == 422
    exact = await _sell(client, c, [{"method": "cash", "amount": 52800}])        # nothing typed: exact money
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        payment = (await s.execute(select(Payment).where(Payment.order_id == uuid.UUID(exact["id"])))).scalar_one()
        assert payment.tendered is None
    doc = await _receipt_doc(session_factory, c, exact["id"])
    assert "Kembali" not in "\n".join(_paper_lines(doc))


async def test_the_owner_replaces_the_placeholders(client, cafe):
    c = cafe
    before = (await client.get("/api/business", headers=c["owner"])).json()
    assert sorted(before["placeholders"]) == ["address", "contact_phone", "instagram"]
    after = await client.patch("/api/business", headers=c["owner"], json={
        "address": "Jl. Merdeka 12, Bandung", "contact_phone": "0811-2233-4455",
    })
    assert after.status_code == 200 and after.json()["placeholders"] == ["instagram"]
    label = await client.patch("/api/pricing-settings", headers=c["owner"], json={"tax_label": "Pajak Resto"})
    assert label.status_code == 200 and label.json()["tax_label"] == "Pajak Resto"
    quote = await client.post("/pos/quote", headers=c["pos"], json={"lines": [{"item_id": str(c["roti"]), "quantity": 1}]})
    q = quote.json()
    assert q["tax_label"] == "Pajak Resto" and Decimal(q["tax_total"]) == Decimal("1500.00")    # on top: 15.000 x 10%
    assert q["tax_inclusive"] is False and Decimal(q["total"]) == Decimal("16500.00")
    # A café whose prices already include it keeps that: one setting.
    inside = await client.patch("/api/pricing-settings", headers=c["owner"], json={"tax_inclusive": True})
    assert inside.status_code == 200
    q = (await client.post("/pos/quote", headers=c["pos"], json={"lines": [{"item_id": str(c["roti"]), "quantity": 1}]})).json()
    assert Decimal(q["tax_total"]) == Decimal("1363.64") and Decimal(q["total"]) == Decimal("15000.00")


async def test_an_ojol_order_paid_in_the_app_is_owed_by_the_app_not_in_the_drawer(client, session_factory, cafe):
    """till-9: at the till "pickup" is Ojol — a GoFood/GrabFood driver collects
    it and the customer already paid the app. The payment is "other" with the
    reference "ojol": the receipt says "Dibayar aplikasi", the ledger books a
    receivable (1200), and no cash reaches the drawer (1110)."""
    from app.models import Account, JournalEntry, JournalLine

    c = cafe
    resp = await client.post("/pos/orders", headers=c["pos"], json={
        "order_type": "pickup",
        "external_ref": "GF-123",
        "lines": [{"item_id": str(c["roti"]), "quantity": 2}],
        "payments": [{"method": "other", "amount": 33000, "reference": "ojol"}],   # 2 x 15.000 + PB1 10%
    })
    assert resp.status_code == 201, resp.text
    sale = resp.json()
    paper = _paper_lines(await _receipt_doc(session_factory, c, sale["id"]))
    assert any(l.startswith("Dibayar aplikasi") and l.endswith("Rp 33.000") for l in paper)
    assert "Ojol" in "\n".join(paper) and "Ambil sendiri" not in "\n".join(paper)
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        rows = (await s.execute(
            select(Account.code, JournalLine.debit)
            .join(Account, Account.id == JournalLine.account_id)
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .where(JournalEntry.source_type == "order", JournalEntry.source_id == uuid.UUID(sale["id"]), JournalLine.debit > 0)
        )).all()
    debits = {code: Decimal(d) for code, d in rows}
    assert debits.get("1200") == Decimal(33000)
    assert "1110" not in debits
