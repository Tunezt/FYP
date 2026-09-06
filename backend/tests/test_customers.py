"""M8-T1 — customers: name, phone, address, birthday; attached to the order.

Phone is the natural key and the WhatsApp identity, so it is normalised the
way the owner's number is and is unique per business. History (visits, spend,
last visit) is derived from orders, never stored.

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import os
import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import hash_pin
from app.models import Business, Customer, Item, Order, Staff
from app.services.catalog import ensure_default_variant
from app.services.customers import (
    CustomerInvalid, clean_phone, create_customer, customer_by_phone, customer_summaries, require_customer,
    search_customers, update_customer,
)
from app.services.orders import OrderLineSpec, PaymentSpec, create_order, refund_order, void_order
from app.services.stock import open_item_stock

from tests.conftest import seed_books

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")
D = Decimal


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
        biz = Business(name="Customer Test", owner_phone=f"62972{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await seed_books(s, bid)
        owner = Staff(business_id=bid, name="Bu Ratna", role="owner", pin_hash=hash_pin("1234"))
        sari = Staff(business_id=bid, name="Sari", pin_hash=hash_pin("2345"))
        kopi = Item(business_id=bid, name="Kopi", unit="cup", current_stock=D(50), cost_price=D(8000), sell_price=D(20000))
        s.add_all([owner, sari, kopi])
        await s.flush()
        await open_item_stock(s, kopi, unit_cost=kopi.cost_price)
        await ensure_default_variant(s, kopi)
        await s.commit()
        ids = {"bid": bid, "owner": owner.id, "sari": sari.id, "kopi": kopi.id}
    yield ids
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


async def _sell(s, c, qty=1, **kw):
    return await create_order(
        s, business_id=c["bid"], staff_id=c["sari"],
        lines=[OrderLineSpec(item_id=c["kopi"], quantity=D(qty))],
        payments=[PaymentSpec(method="cash", amount=D(20000) * qty)], **kw,
    )


@pytest.mark.parametrize(
    "typed,stored",
    [
        ("0812-3456-7890", "6281234567890"),
        ("+62 812 3456 7890", "6281234567890"),
        ("6281234567890", "6281234567890"),
        ("012-345 6789", "60123456789"),      # Malaysian local
        ("", None),
        (None, None),
        ("   ", None),
    ],
)
def test_phone_is_normalised_like_the_owners(typed, stored):
    assert clean_phone(typed) == stored


def test_an_impossible_phone_is_refused():
    for bad in ("081", "1234567890123456"):
        with pytest.raises(CustomerInvalid) as exc:
            clean_phone(bad)
        assert exc.value.code == "phone"


async def test_create_find_update_and_the_phone_is_the_key(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        andi = await create_customer(s, c["bid"], name="  Andi ", phone="0812-3456-7890", address="Jl. Melati 3",
                                     birthday=date(1990, 5, 17), notes="suka kopi susu")
        assert (andi.name, andi.phone, andi.address, andi.birthday) == ("Andi", "6281234567890", "Jl. Melati 3", date(1990, 5, 17))
        walkin = await create_customer(s, c["bid"], name="Ibu tanpa nomor")
        assert walkin.phone is None
        walkin2 = await create_customer(s, c["bid"], name="Bapak tanpa nomor")   # two phone-less customers are fine
        assert walkin2.id != walkin.id
        # Same person typed differently → duplicate.
        with pytest.raises(CustomerInvalid) as exc:
            await create_customer(s, c["bid"], name="Andi lagi", phone="+62 812 3456 7890")
        assert exc.value.code == "duplicate_phone"
        with pytest.raises(CustomerInvalid) as exc:
            await create_customer(s, c["bid"], name="   ")
        assert exc.value.code == "name"
        await s.commit()
        ids = {"andi": andi.id, "walkin": walkin.id}

    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert (await customer_by_phone(s, "081234567890")).id == ids["andi"]
        assert (await customer_by_phone(s, "0899")) is None
        found = await search_customers(s, "0812")
        assert [x.id for x in found] == [ids["andi"]]
        found = await search_customers(s, "tanpa")
        assert sorted(x.name for x in found) == ["Bapak tanpa nomor", "Ibu tanpa nomor"]
        assert len(await search_customers(s, "")) == 3
        andi = await s.get(Customer, ids["andi"])
        await update_customer(s, andi, name="Andi Wijaya", phone="0813 9999 0000", notes="")
        assert (andi.name, andi.phone, andi.notes) == ("Andi Wijaya", "6281399990000", None)
        walkin = await s.get(Customer, ids["walkin"])
        with pytest.raises(CustomerInvalid) as exc:
            await update_customer(s, walkin, phone="0813 9999 0000")   # now Andi's
        assert exc.value.code == "duplicate_phone"
        await update_customer(s, walkin, phone="")                     # clearing is allowed
        assert walkin.phone is None
        await update_customer(s, andi, is_active=False)
        assert [x.id for x in await search_customers(s, "Andi")] == []
        assert [x.id for x in await search_customers(s, "Andi", include_inactive=True)] == [ids["andi"]]
        await s.commit()

    # The database enforces the key too, not just the service.
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        s.add(Customer(business_id=c["bid"], name="Sneaky", phone="6281399990000"))
        with pytest.raises(Exception):
            await s.commit()


async def test_orders_attach_a_customer_and_history_is_derived(session_factory, shop):
    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        andi = await create_customer(s, c["bid"], name="Andi", phone="0812-1111-2222")
        gone = await create_customer(s, c["bid"], name="Gone", phone="0812-3333-4444")
        await update_customer(s, gone, is_active=False)
        await s.commit()
        ids = {"andi": andi.id, "gone": gone.id}

    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        first = await _sell(s, c, 2, customer_id=ids["andi"])       # 40.000
        second = await _sell(s, c, 1, customer_id=ids["andi"])      # 20.000 — will be refunded
        third = await _sell(s, c, 1, customer_id=ids["andi"])       # 20.000 — will be voided
        anon = await _sell(s, c, 1)                                 # no customer
        assert first.order.customer_id == ids["andi"] and anon.order.customer_id is None
        await refund_order(s, business_id=c["bid"], order_id=second.order.id, staff_id=c["sari"], manager_pin="1234")
        await void_order(s, business_id=c["bid"], order_id=third.order.id, staff_id=c["sari"], manager_pin="1234")
        await s.commit()

    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        summary = (await customer_summaries(s, [ids["andi"], ids["gone"]]))
        andi = summary[ids["andi"]]
        assert andi.visits == 2                                      # the void never happened; the refund did
        assert andi.total_spent == D("40000.00")                     # a refunded order is not spend
        assert andi.last_visit is not None
        assert summary[ids["gone"]].visits == 0 and summary[ids["gone"]].total_spent == D("0.00")
        # Attaching an inactive or unknown customer is refused before anything is written.
        n_before = (await s.execute(select(Order))).scalars().all()
        with pytest.raises(CustomerInvalid) as exc:
            await _sell(s, c, 1, customer_id=ids["gone"])
        assert exc.value.code == "not_found"
        await s.rollback()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        with pytest.raises(CustomerInvalid):
            await _sell(s, c, 1, customer_id=uuid.uuid4())
        await s.rollback()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        assert len((await s.execute(select(Order))).scalars().all()) == len(n_before)
        # 50 − (2+1+1+1) sold, +1 back from the refund, +1 back from the void.
        assert (await s.get(Item, c["kopi"])).current_stock == D("47.000")


async def test_the_foreign_key_holds_and_the_receipt_names_the_customer(session_factory, shop):
    from app.api.pos import pos_receipt

    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        andi = await create_customer(s, c["bid"], name="Andi", phone="081211112222")
        sale = await _sell(s, c, 1, customer_id=andi.id)
        await s.commit()
        ids = {"andi": andi.id, "order": sale.order.id}
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        # A dangling customer id cannot be written, even bypassing the service.
        s.add(Order(business_id=c["bid"], staff_id=c["sari"], customer_id=uuid.uuid4(), subtotal=D(0), total=D(0)))
        with pytest.raises(Exception):
            await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        ctx = SimpleNamespace(session=s, business_id=c["bid"], staff_id=c["sari"])
        receipt = await pos_receipt(ids["order"], ctx)
        assert receipt.customer_name == "Andi"


async def test_pos_and_owner_endpoints(session_factory, shop):
    from app.api.dashboard import add_customer, edit_customer, list_customers
    from app.api.pos import pos_add_customer, pos_create_order, pos_search_customers
    from app.schemas.dashboard import CustomerCreateIn, CustomerUpdateIn
    from app.schemas.pos import OrderIn, OrderLineIn, PaymentIn, PosCustomerIn

    c = shop
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        pos = SimpleNamespace(session=s, business_id=c["bid"], staff_id=c["sari"])
        owner = SimpleNamespace(session=s, business_id=c["bid"], staff_id=None)
        added = await pos_add_customer(PosCustomerIn(name="Rina", phone="0813 2222 3333"), pos)
        assert added.phone == "6281322223333" and added.visits == 0
        with pytest.raises(HTTPException) as exc:
            await pos_add_customer(PosCustomerIn(name="Rina lagi", phone="+62 813 2222 3333"), pos)
        assert exc.value.status_code == 409 and "sudah terdaftar" in exc.value.detail
        with pytest.raises(HTTPException) as exc:
            await pos_add_customer(PosCustomerIn(name="X", phone="123"), pos)
        assert exc.value.status_code == 422
        found = await pos_search_customers(pos, q="2222")
        assert [x.id for x in found] == [added.id]
        out = await pos_create_order(
            OrderIn(lines=[OrderLineIn(item_id=c["kopi"], quantity=D(1))], payments=[PaymentIn(method="cash", amount=D(20000))],
                    customer_id=added.id),
            pos,
        )
        assert out.customer_id == added.id
        with pytest.raises(HTTPException) as exc:
            await pos_create_order(
                OrderIn(lines=[OrderLineIn(item_id=c["kopi"], quantity=D(1))], payments=[PaymentIn(method="cash", amount=D(20000))],
                        customer_id=uuid.uuid4()),
                pos,
            )
        assert exc.value.status_code == 404
        # Owner side: list with derived history, create, edit.
        page = await list_customers(owner, q="", include_inactive=False, page=1, page_size=30)
        assert page.total == 1 and page.rows[0].visits == 1 and page.rows[0].total_spent == D("20000.00")
        created = await add_customer(CustomerCreateIn(name="Pak Budi", phone="0812 0000 1111", birthday=date(1980, 1, 2)), owner)
        assert created.birthday == date(1980, 1, 2) and created.phone == "6281200001111"
        edited = await edit_customer(created.id, CustomerUpdateIn(address="Jl. Kenanga 9", is_active=False), owner)
        assert edited.address == "Jl. Kenanga 9" and edited.is_active is False
        assert (await list_customers(owner, q="", include_inactive=False, page=1, page_size=30)).total == 1
        assert (await list_customers(owner, q="budi", include_inactive=True, page=1, page_size=30)).total == 1
        with pytest.raises(HTTPException) as exc:
            await edit_customer(uuid.uuid4(), CustomerUpdateIn(name="?"), owner)
        assert exc.value.status_code == 404
        await s.commit()
