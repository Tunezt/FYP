"""prt-2 onwards — where each item is prepared, and what that routes.

Bar (at the front, beside the cashier), Dapur (the back kitchen), or nothing to
prepare. Set explicitly per item by the owner; snapshotted on every sold line.
Needs the local Postgres (roadmap §2). No skip marker.
"""
import uuid
from decimal import Decimal

from sqlalchemy import select

from app.core.security import create_token
from app.models import Item, OrderLine
from tests.test_service_journey import (  # noqa: F401 (fixtures)
    _auth, _cash, _line, _make_cafe, _ref, _set_tenant, cafe, client, engine, session_factory,
)

D = Decimal


def _owner(c):
    return {"Authorization": f"Bearer {create_token(business_id=str(c['bid']), scope='owner')}"}


async def test_the_station_is_set_by_the_owner_never_guessed(client, session_factory, cafe):
    c = cafe
    owner = _owner(c)
    # A new item has no station until someone says where it is made, whatever its name.
    made = await client.post("/api/items", headers=owner, json={"name": "Nasi Goreng Kampung", "unit": "porsi", "sell_price": "30000"})
    assert made.status_code == 201 and made.json()["prep_station"] is None
    items = {i["name"]: i for i in (await client.get("/pos/items", headers=_auth(c["pos"]))).json()}
    assert items["Americano"]["prep_station"] is None and items["Nasi Goreng Kampung"]["prep_station"] is None
    assert (await client.patch(f"/api/items/{c['americano']}", headers=owner, json={"prep_station": "bar"})).json()["prep_station"] == "bar"
    assert (await client.patch(f"/api/items/{c['roti']}", headers=owner, json={"prep_station": "kitchen"})).json()["prep_station"] == "kitchen"
    assert (await client.patch(f"/api/items/{c['roti']}", headers=owner, json={"prep_station": "oven"})).status_code == 422
    listed = {i["name"]: i for i in (await client.get("/api/items", headers=owner)).json()}
    assert listed["Americano"]["prep_station"] == "bar" and listed["Roti"]["prep_station"] == "kitchen"
    # A plain till token cannot change it.
    assert (await client.patch(f"/api/items/{c['roti']}", headers=_auth(c["pos"]), json={"prep_station": "bar"})).status_code == 403


async def test_a_sold_line_remembers_where_it_was_made(client, session_factory, cafe):
    c = cafe
    owner = _owner(c)
    await client.patch(f"/api/items/{c['americano']}", headers=owner, json={"prep_station": "bar"})
    await client.patch(f"/api/items/{c['roti']}", headers=owner, json={"prep_station": "kitchen"})
    sale = (await client.post("/pos/orders", headers=_auth(c["pos"]), json={
        "lines": [_line(c, variant="standar", mods=["panas"]), _line(c, item="roti")], "payments": _cash(33000)})).json()
    placed = (await client.post(f"/menu/{c['menu']}/orders", json={"lines": [_line(c, item="roti")], "order_type": "takeaway"})).json()
    # The owner moves the roti to the front display tomorrow; yesterday's line does not move.
    await client.patch(f"/api/items/{c['roti']}", headers=owner, json={"prep_station": "bar"})
    async with session_factory() as s:
        await _set_tenant(s, c["bid"])
        stations = dict((await s.execute(
            select(OrderLine.item_id, OrderLine.prep_station).where(OrderLine.order_id == uuid.UUID(sale["id"]))
        )).all())
        assert stations == {c["americano"]: "bar", c["roti"]: "kitchen"}
        from app.models import Order

        ticket = await s.get(Order, uuid.UUID(placed["id"]))
        assert ticket.cart["lines"][0]["prep_station"] == "kitchen"
        assert (await s.get(Item, c["roti"])).prep_station == "bar"
