"""M15-T7 — the manager override, and the audit trail it leaves.

Void and refund need a manager's PIN. Until now the only role that could give
one was `owner`, and in a real café the owner is not always on site: a cashier
who cannot void a mistake starts doing arithmetic in their head instead, which
is how a ledger loses its connection to reality.

The done-criterion is three claims, and each has a test below: a manager can
void without the owner present, the void records who approved it, and a plain
staff PIN still cannot. The fourth thing the roadmap asks for — "cannot see
reports or settings" — is asserted from the other side: no manager PIN, and no
token issued to one, reaches an owner-scoped route.

Needs the local Postgres (roadmap §2). No skip marker: unreachable DB fails.
"""
import os
import uuid
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import create_pairing_token, create_token, hash_pin
from app.main import app
from app.models import Approval, Business, Item, Order, Staff
from app.services.catalog import ensure_default_variant
from app.services.orders import (
    APPROVER_ROLES, DiscountNeedsManager, ManagerPinRejected, OrderLineSpec, PaymentSpec,
    create_order, refund_order, verify_manager_pin, void_order,
)
from app.services.pricing import ensure_pricing_settings
from app.services.stock import open_item_stock

from tests.conftest import seed_books

DB_URL = os.getenv("INTEGRATION_DATABASE_URL")
D = Decimal

PIN = {"owner": "1234", "manager": "4321", "cashier": "2345", "ex_manager": "9876"}


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
    await session.execute(
        text("select set_config('app.current_business_id', :bid, true)"), {"bid": str(business_id)}
    )


@pytest.fixture
async def cafe(session_factory):
    """Bu Ratna (owner, off site), Pak Yudi (manager, on the floor), Sari
    (cashier), and Dewi — a manager who has been demoted back to cashier.
    One item, plenty of stock, and one completed 3-cup sale rung up by Sari."""
    async with session_factory() as s:
        biz = Business(name="Kopi Manajer", owner_phone=f"62962{uuid.uuid4().hex[:9]}")
        s.add(biz)
        await s.commit()
        bid = biz.id
    async with session_factory() as s:
        await _set_tenant(s, bid)
        await seed_books(s, bid)
        await ensure_pricing_settings(s, bid)
        owner = Staff(business_id=bid, name="Bu Ratna", role="owner", pin_hash=hash_pin(PIN["owner"]))
        yudi = Staff(business_id=bid, name="Pak Yudi", role="manager", pin_hash=hash_pin(PIN["manager"]))
        sari = Staff(business_id=bid, name="Sari", role="staff", pin_hash=hash_pin(PIN["cashier"]))
        dewi = Staff(business_id=bid, name="Dewi", role="staff", pin_hash=hash_pin(PIN["ex_manager"]))
        kopi = Item(business_id=bid, name="Kopi", unit="cup", current_stock=D(50),
                    cost_price=D(8000), sell_price=D(20000))
        s.add_all([owner, yudi, sari, dewi, kopi])
        await s.flush()
        await open_item_stock(s, kopi, unit_cost=kopi.cost_price)
        await ensure_default_variant(s, kopi)
        sold = await create_order(
            s, business_id=bid, staff_id=sari.id,
            lines=[OrderLineSpec(item_id=kopi.id, quantity=D(3))],
            payments=[PaymentSpec(method="cash", amount=D(60000))],
        )
        await s.commit()
        c = {"bid": bid, "kopi": kopi.id, "owner": owner.id, "yudi": yudi.id, "sari": sari.id,
             "dewi": dewi.id, "order": sold.order.id,
             "ownertok": create_token(business_id=str(bid), scope="owner"),
             "postok": create_token(business_id=str(bid), scope="pos", staff_id=str(sari.id)),
             "pairing": create_pairing_token(str(bid))}
    yield c
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


async def _sell(s, c, qty=D(2), paid=D(40000), **kw):
    return await create_order(
        s, business_id=c["bid"], staff_id=c["sari"],
        lines=[OrderLineSpec(item_id=c["kopi"], quantity=qty)],
        payments=[PaymentSpec(method="cash", amount=paid)], **kw,
    )


# ── who may approve ──────────────────────────────────────────────────────────


async def test_a_manager_pin_authorises_and_a_plain_staff_pin_does_not(session_factory, cafe):
    async with session_factory() as s:
        await _set_tenant(s, cafe["bid"])
        assert (await verify_manager_pin(s, PIN["manager"], business_id=cafe["bid"])).name == "Pak Yudi"
        assert (await verify_manager_pin(s, PIN["owner"], business_id=cafe["bid"])).name == "Bu Ratna"
        for rejected in (PIN["cashier"], PIN["ex_manager"], "0000"):
            with pytest.raises(ManagerPinRejected):
                await verify_manager_pin(s, rejected, business_id=cafe["bid"])
        assert APPROVER_ROLES == ("owner", "manager")


async def test_a_deactivated_manager_can_no_longer_authorise(session_factory, cafe):
    """Revoking access is one flag, checked on the row at the moment of the
    override — not on a token issued earlier in the shift."""
    async with session_factory() as s:
        await _set_tenant(s, cafe["bid"])
        yudi = await s.get(Staff, cafe["yudi"])
        yudi.is_active = False
        await s.flush()
        with pytest.raises(ManagerPinRejected):
            await verify_manager_pin(s, PIN["manager"], business_id=cafe["bid"])
        await s.rollback()


# ── a manager can void without the owner present ─────────────────────────────


async def test_a_manager_voids_without_the_owner_and_the_void_records_who_approved(session_factory, cafe):
    """The roadmap's done-criterion, all three clauses on one order."""
    async with session_factory() as s:
        await _set_tenant(s, cafe["bid"])
        # A plain cashier's PIN is refused — the guard is not simply "any PIN".
        with pytest.raises(ManagerPinRejected):
            await void_order(s, business_id=cafe["bid"], order_id=cafe["order"],
                             staff_id=cafe["sari"], manager_pin=PIN["cashier"])
        await s.rollback()

    async with session_factory() as s:
        await _set_tenant(s, cafe["bid"])
        rev = await void_order(s, business_id=cafe["bid"], order_id=cafe["order"],
                               staff_id=cafe["sari"], manager_pin=PIN["manager"], note="salah pesan")
        await s.commit()
        assert rev.order.status == "voided"

    async with session_factory() as s:
        await _set_tenant(s, cafe["bid"])
        row = (await s.execute(select(Approval).where(Approval.order_id == cafe["order"]))).scalar_one()
        assert row.action == "void"
        assert row.approved_by == cafe["yudi"] and row.approver_role == "manager"
        assert row.requested_by == cafe["sari"]          # who asked, not only who allowed
        assert row.amount == D("60000.00")               # what it was worth
        assert row.note == "salah pesan"
        # The stock came back and the order is readable in full: the override is
        # recorded alongside the reversal, not instead of it.
        assert (await s.get(Item, cafe["kopi"])).current_stock == D("50.000")
        assert (await s.get(Order, cafe["order"])).status == "voided"


async def test_the_approver_role_is_a_snapshot_not_a_lookup(session_factory, cafe):
    """A cashier promoted next month must not change what last month's trail
    says about who was allowed to approve."""
    async with session_factory() as s:
        await _set_tenant(s, cafe["bid"])
        await void_order(s, business_id=cafe["bid"], order_id=cafe["order"],
                         staff_id=cafe["sari"], manager_pin=PIN["manager"])
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, cafe["bid"])
        yudi = await s.get(Staff, cafe["yudi"])
        yudi.role = "staff"                              # demoted after the fact
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, cafe["bid"])
        row = (await s.execute(select(Approval))).scalars().one()
        assert row.approver_role == "manager" and (await s.get(Staff, cafe["yudi"])).role == "staff"


async def test_a_refund_and_a_discount_are_recorded_too(session_factory, cafe):
    """"Every override stays fully attributed" — not only the void."""
    async with session_factory() as s:
        await _set_tenant(s, cafe["bid"])
        # A discount needs the PIN by default; without one the sale is refused.
        with pytest.raises(DiscountNeedsManager):
            await _sell(s, cafe, bill_discount=D(5000), paid=D(35000))
        await s.rollback()

    async with session_factory() as s:
        await _set_tenant(s, cafe["bid"])
        discounted = await _sell(s, cafe, bill_discount=D(5000), paid=D(35000), manager_pin=PIN["manager"])
        refunded = await _sell(s, cafe)
        await refund_order(s, business_id=cafe["bid"], order_id=refunded.order.id,
                           staff_id=cafe["sari"], manager_pin=PIN["owner"], note="kopi tumpah")
        await s.commit()
        ids = {"discounted": discounted.order.id, "refunded": refunded.order.id}

    async with session_factory() as s:
        await _set_tenant(s, cafe["bid"])
        rows = {r.action: r for r in (await s.execute(select(Approval))).scalars().all()}
        assert set(rows) == {"discount", "refund"}
        assert rows["discount"].order_id == ids["discounted"]
        assert rows["discount"].amount == D("5000.00")           # the discount given, not the bill
        assert rows["discount"].approved_by == cafe["yudi"] and rows["discount"].approver_role == "manager"
        assert rows["refund"].order_id == ids["refunded"]
        assert rows["refund"].amount == D("40000.00")            # the total handed back
        assert rows["refund"].approved_by == cafe["owner"] and rows["refund"].approver_role == "owner"
        assert rows["refund"].note == "kopi tumpah"


async def test_a_sale_with_no_override_leaves_no_row(session_factory, cafe):
    """The trail is overrides only. An ordinary sale is not an authorisation,
    and a trail padded with non-events is one nobody reads."""
    async with session_factory() as s:
        await _set_tenant(s, cafe["bid"])
        await _sell(s, cafe)
        await s.commit()
    async with session_factory() as s:
        await _set_tenant(s, cafe["bid"])
        assert (await s.execute(select(Approval))).scalars().all() == []


# ── the till and the dashboard ───────────────────────────────────────────────


async def test_the_till_accepts_a_manager_pin_and_refuses_a_cashiers(client, cafe):
    auth = {"Authorization": f"Bearer {cafe['postok']}"}
    r = await client.post(f"/pos/orders/{cafe['order']}/void",
                          json={"manager_pin": PIN["cashier"]}, headers=auth)
    assert r.status_code == 403 and "manajer" in r.json()["detail"]
    r = await client.post(f"/pos/orders/{cafe['order']}/void",
                          json={"manager_pin": PIN["manager"], "note": "salah pesan"}, headers=auth)
    assert r.status_code == 200 and r.json()["status"] == "voided"


async def test_the_owner_reads_the_trail_back(client, cafe):
    pos = {"Authorization": f"Bearer {cafe['postok']}"}
    owner = {"Authorization": f"Bearer {cafe['ownertok']}"}
    await client.post(f"/pos/orders/{cafe['order']}/void",
                      json={"manager_pin": PIN["manager"], "note": "salah pesan"}, headers=pos)

    r = await client.get("/api/approvals", headers=owner)
    assert r.status_code == 200
    row = r.json()[0]
    assert row["action"] == "void" and row["approver_name"] == "Pak Yudi"
    assert row["approver_role"] == "manager" and row["requested_by_name"] == "Sari"
    assert row["amount"] == "60000.00" and row["order_id"] == str(cafe["order"])

    assert (await client.get("/api/approvals?action=refund", headers=owner)).json() == []
    assert len((await client.get("/api/approvals?action=void", headers=owner)).json()) == 1
    assert (await client.get("/api/approvals?action=bribe", headers=owner)).status_code == 422


# ── a manager cannot see reports or settings ─────────────────────────────────


async def test_a_manager_gets_till_access_only(client, cafe):
    """The role approves overrides; it does not open the dashboard. A manager
    signs in at the kiosk exactly like a cashier and receives a `pos` token, and
    owner scope is issued only to the phone that owns the business."""
    r = await client.post("/pos/login", json={"pairing_token": cafe["pairing"],
                                              "staff_id": str(cafe["yudi"]), "pin": PIN["manager"]})
    assert r.status_code == 200
    token = r.json()["token"]
    from app.core.security import decode_token

    assert decode_token(token)["scope"] == "pos"

    manager = {"Authorization": f"Bearer {token}"}
    for route in ("/api/overview", "/api/pnl", "/api/business", "/api/approvals",
                  "/api/pricing-settings", "/auth/staff"):
        assert (await client.get(route, headers=manager)).status_code == 403, route


async def test_the_owner_promotes_and_demotes_but_never_hands_out_owner(client, cafe):
    owner = {"Authorization": f"Bearer {cafe['ownertok']}"}
    r = await client.post("/auth/staff", json={"name": "Rian", "pin": "5555", "role": "manager"}, headers=owner)
    assert r.status_code == 200 and r.json()["role"] == "manager"
    rian = r.json()["id"]

    assert (await client.post("/auth/staff", json={"name": "Nia", "pin": "6666"}, headers=owner)).json()["role"] == "staff"
    assert (await client.post("/auth/staff", json={"name": "Bos", "pin": "7777", "role": "owner"},
                              headers=owner)).status_code == 422

    r = await client.patch(f"/auth/staff/{rian}", json={"role": "staff"}, headers=owner)
    assert r.status_code == 200 and r.json()["role"] == "staff"
    assert (await client.patch(f"/auth/staff/{rian}", json={"role": "owner"}, headers=owner)).status_code == 422
    r = await client.patch(f"/auth/staff/{cafe['owner']}", json={"role": "manager"}, headers=owner)
    assert r.status_code == 400 and "pemilik" in r.json()["detail"].lower()
    assert (await client.patch(f"/auth/staff/{uuid.uuid4()}", json={"role": "manager"}, headers=owner)).status_code == 404


async def test_a_promoted_cashier_can_then_approve(session_factory, client, cafe):
    """The whole point of the endpoint: the owner leaves, promotes Dewi from her
    phone, and the till can void again."""
    owner = {"Authorization": f"Bearer {cafe['ownertok']}"}
    pos = {"Authorization": f"Bearer {cafe['postok']}"}
    assert (await client.post(f"/pos/orders/{cafe['order']}/void",
                              json={"manager_pin": PIN["ex_manager"]}, headers=pos)).status_code == 403
    assert (await client.patch(f"/auth/staff/{cafe['dewi']}", json={"role": "manager"}, headers=owner)).status_code == 200
    r = await client.post(f"/pos/orders/{cafe['order']}/void",
                          json={"manager_pin": PIN["ex_manager"]}, headers=pos)
    assert r.status_code == 200

    async with session_factory() as s:
        await _set_tenant(s, cafe["bid"])
        row = (await s.execute(select(Approval))).scalars().one()
        assert row.approved_by == cafe["dewi"] and row.approver_role == "manager"
