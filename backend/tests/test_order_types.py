"""M11-T3 — order type routing: delivery and dine-in behave differently at the
till. The type decides how the bill is built (service charge only on the
configured types; a flat delivery fee, posted to its own revenue account and
given back on a refund), what the till must collect (an address and a phone
for a delivery), and how the receipt, the kitchen and the registry see it.

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import os
import uuid
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import create_menu_token, create_token, hash_pin
from app.main import app
from app.metrics.registry import compute
from app.models import Account, Business, Item, JournalEntry, JournalLine, Order, PricingSettings, Staff
from app.services.catalog import ensure_default_variant
from app.services.customers import create_customer
from app.services.kitchen import board
from app.services.orders import OrderLineSpec, OrderTypeInvalid, PaymentSpec, create_order, refund_order
from app.services.pricing import ALL_ORDER_TYPES, LineInput, PricingConfig, ensure_pricing_settings, price_order
from app.services.stock import open_item_stock

from tests.conftest import seed_books

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")
D = Decimal


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# ── the pure function ────────────────────────────────────────────────────────


def _cafe(**over) -> PricingConfig:
    base = dict(tax_rate=D(0), tax_inclusive=True, service_charge_rate=D("0.10"), service_before_tax=True,
                rounding_unit=D(0), rounding_mode="nearest", service_applies_to=("dine_in",), delivery_fee=D(8000))
    base.update(over)
    return PricingConfig(**base)


LINES = [LineInput(unit_price=D(20000), quantity=D(2)), LineInput(unit_price=D(15000), quantity=D(1))]   # 55.000


def test_the_service_charge_follows_the_configured_types():
    cafe = _cafe()
    assert price_order(LINES, cafe, order_type="dine_in").service_charge == D("5500.00")
    for kind in ("takeaway", "delivery", "pickup"):
        assert price_order(LINES, cafe, order_type=kind).service_charge == D("0.00"), kind
    # The default config keeps today's behaviour: every type carries it.
    everywhere = PricingConfig(service_charge_rate=D("0.10"))
    assert everywhere.service_applies_to == ALL_ORDER_TYPES
    assert all(price_order(LINES, everywhere, order_type=k).service_charge == D("5500.00") for k in ALL_ORDER_TYPES)


def test_the_delivery_fee_is_on_delivery_only_after_tax_and_inside_the_total():
    cafe = _cafe(tax_rate=D("0.10"), tax_inclusive=False, service_applies_to=("dine_in", "delivery"))
    delivery = price_order(LINES, cafe, order_type="delivery")
    # net 55.000 · service 5.500 · tax 10% of 60.500 = 6.050 · fee 8.000 (not taxed)
    assert (delivery.service_charge, delivery.tax_total, delivery.delivery_fee, delivery.total) == (D("5500.00"), D("6050.00"), D("8000.00"), D("74550.00"))
    assert delivery.net == D("55000.00")                       # the fee is not revenue from goods
    assert delivery.fiscal_components()["delivery_fee"] == D("8000.00")
    dine_in = price_order(LINES, cafe, order_type="dine_in")
    assert dine_in.delivery_fee == D("0.00") and dine_in.total == D("66550.00")
    assert "delivery_fee" in dine_in.fiscal_components() and dine_in.fiscal_components()["delivery_fee"] == D("0.00")
    # Rounding happens after the fee.
    rounded = price_order(LINES, _cafe(rounding_unit=D(1000), delivery_fee=D(8300)), order_type="delivery")
    assert (rounded.delivery_fee, rounded.rounding, rounded.total) == (D("8300.00"), D("-300.00"), D("63000.00"))


# ── the till ─────────────────────────────────────────────────────────────────


@pytest.fixture
async def client():
    from app.core.db import engine as app_engine

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await app_engine.dispose()


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
    await session.execute(text("select set_config('app.current_business_id', :bid, true)"), {"bid": str(business_id)})


@pytest.fixture
async def shop(session_factory):
    """A café with 10% service charge on dine-in only and an 8.000 delivery fee."""
    async with session_factory() as s:
        biz = Business(name="Kafe Antar", owner_phone=f"62966{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await seed_books(s, bid)
        settings = await ensure_pricing_settings(s, bid)
        settings.service_charge_rate = D("0.10")
        settings.service_applies_to = ["dine_in"]
        settings.delivery_fee = D(8000)
        owner = Staff(business_id=bid, name="Bu Ratna", role="owner", pin_hash=hash_pin("1234"))
        sari = Staff(business_id=bid, name="Sari", pin_hash=hash_pin("2345"))
        kopi = Item(business_id=bid, name="Kopi", unit="cup", current_stock=D(50), cost_price=D(8000), sell_price=D(20000), reorder_threshold=D(2))
        roti = Item(business_id=bid, name="Roti", unit="pcs", current_stock=D(50), cost_price=D(6000), sell_price=D(15000), reorder_threshold=D(2))
        s.add_all([owner, sari, kopi, roti])
        await s.flush()
        for it in (kopi, roti):
            await open_item_stock(s, it, unit_cost=it.cost_price)
            await ensure_default_variant(s, it)
        dina = await create_customer(s, bid, name="Dina", phone="0812-1111-2222")
        await s.commit()
        c = {"bid": bid, "owner": owner.id, "sari": sari.id, "kopi": kopi.id, "roti": roti.id, "dina": dina.id,
             "pos": create_token(business_id=str(bid), scope="pos", staff_id=str(sari.id)),
             "ownertok": create_token(business_id=str(bid), scope="owner"), "menu": create_menu_token(str(bid))}
    yield c
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


def _lines(c):
    return [{"item_id": str(c["kopi"]), "quantity": "2"}, {"item_id": str(c["roti"]), "quantity": "1"}]   # 55.000


async def _posted(s, order_id, memo):
    """(debit account code, credit account code, amount) of one component of the sale's entry."""
    rows = (await s.execute(
        select(Account.code, JournalLine.debit, JournalLine.credit)
        .join(Account, Account.id == JournalLine.account_id)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(JournalEntry.source_type == "order", JournalEntry.source_id == order_id, JournalLine.memo == memo)
    )).all()
    return sorted((code, Decimal(d), Decimal(cr)) for code, d, cr in rows)


async def test_the_quote_and_the_sale_agree_for_every_type(client, shop):
    c = shop
    expected = {"dine_in": ("5500.00", "0.00", "60500.00"), "takeaway": ("0.00", "0.00", "55000.00"),
                "delivery": ("0.00", "8000.00", "63000.00"), "pickup": ("0.00", "0.00", "55000.00")}
    for kind, (service, fee, total) in expected.items():
        q = (await client.post("/pos/quote", headers=_auth(c["pos"]), json={"lines": _lines(c), "order_type": kind})).json()
        assert (q["service_charge"], q["delivery_fee"], q["total"]) == (service, fee, total), kind
        body = {"lines": _lines(c), "order_type": kind, "payments": [{"method": "cash", "amount": total}]}
        if kind == "delivery":
            body.update(delivery_address="Jl. Melati 3", guest_phone="0813-0000-9999", guest_name="Bima")
        if kind == "dine_in":
            body["table_label"] = "Meja 5"
        r = await client.post("/pos/orders", headers=_auth(c["pos"]), json=body)
        assert r.status_code == 201, (kind, r.text)
        o = r.json()
        assert (o["service_charge"], o["delivery_fee"], o["total"]) == (service, fee, total)
        receipt = (await client.get(f"/pos/orders/{o['id']}/receipt", headers=_auth(c["pos"]))).json()
        assert receipt["order_type"] == kind and receipt["delivery_fee"] == fee
        if kind == "dine_in":
            assert o["table_label"] == receipt["table_label"] == "Meja 5"
        if kind == "delivery":
            assert o["delivery_address"] == receipt["delivery_address"] == "Jl. Melati 3"


async def test_a_delivery_needs_an_address_and_a_phone(client, session_factory, shop):
    c = shop
    base = {"lines": _lines(c), "order_type": "delivery", "payments": [{"method": "cash", "amount": "63000"}]}
    no_address = await client.post("/pos/orders", headers=_auth(c["pos"]), json={**base, "guest_phone": "0813"})
    assert no_address.status_code == 422 and "alamat" in no_address.json()["detail"]
    no_phone = await client.post("/pos/orders", headers=_auth(c["pos"]), json={**base, "delivery_address": "Jl. Melati 3"})
    assert no_phone.status_code == 422 and "nomor HP" in no_phone.json()["detail"]
    # A customer with a phone number is a receiver.
    with_customer = await client.post("/pos/orders", headers=_auth(c["pos"]), json={**base, "delivery_address": "Jl. Melati 3", "customer_id": str(c["dina"])})
    assert with_customer.status_code == 201, with_customer.text
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert (await s.execute(select(func.count(Order.id)))).scalar_one() == 1     # the two refusals wrote nothing
        with pytest.raises(OrderTypeInvalid) as exc:
            await create_order(s, business_id=c["bid"], staff_id=c["sari"], order_type="delivery",
                               lines=[OrderLineSpec(item_id=c["kopi"], quantity=D(1))], payments=[PaymentSpec(method="cash", amount=D(28000))])
        assert exc.value.code == "address"


async def test_the_fee_is_posted_to_its_own_account_and_given_back_on_refund(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        created = await create_order(
            s, business_id=c["bid"], staff_id=c["sari"], order_type="delivery",
            lines=[OrderLineSpec(item_id=c["kopi"], quantity=D(2)), OrderLineSpec(item_id=c["roti"], quantity=D(1))],
            payments=[PaymentSpec(method="cash", amount=D(63000))], delivery_address="Jl. Melati 3", guest_phone="0813-0000-9999",
        )
        order = created.order
        assert order.delivery_fee == D(8000) and order.total == D(63000) and order.service_charge == D(0)
        # Cash in for the whole 63.000 against sales; the fee reclassified out of sales into 4910.
        assert await _posted(s, order.id, "payment:cash") == [("1100", D(63000), D(0)), ("4100", D(0), D(63000))]
        assert await _posted(s, order.id, "delivery_fee") == [("4100", D(8000), D(0)), ("4910", D(0), D(8000))]
        # Sales net of the fee: 55.000 sits in 4100.
        sales = (await s.execute(
            select(func.coalesce(func.sum(JournalLine.credit - JournalLine.debit), 0)).join(Account, Account.id == JournalLine.account_id)
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .where(JournalEntry.source_id == order.id, Account.code == "4100")
        )).scalar_one()
        assert Decimal(sales) == D(55000)
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        await refund_order(s, business_id=c["bid"], order_id=order.id, staff_id=c["sari"], manager_pin="1234")
        assert await _posted(s, order.id, "delivery_fee_reversal") == [("4100", D(0), D(8000)), ("4910", D(8000), D(0))]
        assert await _posted(s, order.id, "refund:cash") == [("1100", D(0), D(63000)), ("4300", D(63000), D(0))]
        await s.commit()


async def test_the_kitchen_and_the_registry_see_the_type(client, session_factory, shop):
    c = shop
    for kind, extra, total in (("dine_in", {"table_label": "Meja 2"}, "60500"), ("delivery", {"delivery_address": "Jl. Melati 3", "guest_phone": "0813"}, "63000"),
                               ("takeaway", {}, "55000")):
        r = await client.post("/pos/orders", headers=_auth(c["pos"]), json={"lines": _lines(c), "order_type": kind, "payments": [{"method": "cash", "amount": total}], **extra})
        assert r.status_code == 201, r.text
    kitchen = (await client.get("/pos/kitchen", headers=_auth(c["pos"]))).json()
    assert [(k["order_type"], k["table_label"], k["delivery_address"]) for k in kitchen] == [
        ("dine_in", "Meja 2", None), ("delivery", None, "Jl. Melati 3"), ("takeaway", None, None)]
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        biz = await s.get(Business, c["bid"])
        result = await compute(s, biz, "revenue_by_order_type", period="today")
        assert result.value == D(178500)
        assert {r["order_type"]: (r["orders"], r["total"], r["delivery_fees"]) for r in result.rows} == {
            "dine_in": (1, 60500.0, 0.0), "delivery": (1, 63000.0, 8000.0), "takeaway": (1, 55000.0, 0.0)}


async def test_the_guests_estimate_is_routed_too(client, shop):
    c = shop
    lines = [{"item_id": str(c["kopi"]), "quantity": "2"}]
    dine_in = (await client.post(f"/menu/{c['menu']}/orders", json={"lines": lines, "order_type": "dine_in", "table_label": "Meja 1"})).json()
    takeaway = (await client.post(f"/menu/{c['menu']}/orders", json={"lines": lines, "order_type": "takeaway"})).json()
    assert (dine_in["service_charge"], dine_in["total"]) == ("4000.00", "44000.00")
    assert (takeaway["service_charge"], takeaway["total"]) == ("0.00", "40000.00")
    # Settled at the till, the routed figure is what is charged.
    settled = await client.post(f"/pos/tickets/{takeaway['id']}/settle", headers=_auth(c["pos"]), json={"payments": [{"method": "cash", "amount": "40000"}]})
    assert settled.status_code == 200 and settled.json()["service_charge"] == "0.00" and settled.json()["order_type"] == "takeaway"


async def test_the_owner_sets_the_routing(client, session_factory, shop):
    c = shop
    before = (await client.get("/api/pricing-settings", headers=_auth(c["ownertok"]))).json()
    assert before["service_applies_to"] == ["dine_in"] and Decimal(before["delivery_fee"]) == D(8000)
    patched = await client.patch("/api/pricing-settings", headers=_auth(c["ownertok"]),
                                 json={"service_applies_to": ["dine_in", "pickup"], "delivery_fee": "12000"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["service_applies_to"] == ["dine_in", "pickup"] and Decimal(patched.json()["delivery_fee"]) == D(12000)
    bad = await client.patch("/api/pricing-settings", headers=_auth(c["ownertok"]), json={"service_applies_to": ["drive_thru"]})
    assert bad.status_code == 422
    q = (await client.post("/pos/quote", headers=_auth(c["pos"]), json={"lines": _lines(c), "order_type": "pickup"})).json()
    assert q["service_charge"] == "5500.00"
    q = (await client.post("/pos/quote", headers=_auth(c["pos"]), json={"lines": _lines(c), "order_type": "delivery"})).json()
    assert Decimal(q["delivery_fee"]) == D(12000) and Decimal(q["total"]) == D(67000)
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        row = (await s.execute(select(PricingSettings))).scalar_one()
        assert list(row.service_applies_to) == ["dine_in", "pickup"] and row.delivery_fee == D(12000)
