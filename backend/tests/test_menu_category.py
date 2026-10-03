"""till-10 — each product is listed under a section of the menu.

The owner's feedback of 2 October 2026 (point 4): the till showed every
product in one alphabetical grid. Now each sellable item carries a section
(`items.menu_category`, migration 0047) that the owner names — Kopi, Non-kopi,
Makanan, Camilan, Dessert, or anything else. The till and the QR menu read it;
unsorted items fall under "Lainnya" on screen (NULL in the data).

Needs the local Postgres (roadmap §2). No skip marker.
"""
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.security import create_token
from app.models import Item
from app.services.catalog import clean_menu_category
from tests.test_service_journey import (  # noqa: F401 (fixtures)
    _auth, _set_tenant, cafe, client, engine, session_factory,
)


def _owner(c):
    return {"Authorization": f"Bearer {create_token(business_id=str(c['bid']), scope='owner')}"}


def test_a_section_name_is_trimmed_and_blank_means_none():
    assert clean_menu_category("  Non-kopi ") == "Non-kopi"
    assert clean_menu_category("Kopi   susu") == "Kopi susu"
    assert clean_menu_category("   ") is None
    assert clean_menu_category("") is None
    assert clean_menu_category(None) is None


async def test_the_owner_sorts_products_into_sections_the_till_and_menu_read_them(client, cafe):
    c = cafe
    owner = _owner(c)
    made = await client.post("/api/items", headers=owner, json={
        "name": "Es Teh Lemon", "unit": "cup", "sell_price": "15000", "menu_category": " Non-kopi ",
    })
    assert made.status_code == 201, made.text
    assert made.json()["menu_category"] == "Non-kopi"
    patched = await client.patch(f"/api/items/{c['americano']}", headers=owner, json={"menu_category": "Kopi"})
    assert patched.status_code == 200 and patched.json()["menu_category"] == "Kopi"

    till = {i["name"]: i["menu_category"] for i in (await client.get("/pos/items", headers=_auth(c["pos"]))).json()}
    assert till["Americano"] == "Kopi" and till["Es Teh Lemon"] == "Non-kopi" and till["Roti"] is None
    menu = {i["name"]: i["menu_category"] for i in (await client.get(f"/menu/{c['menu']}")).json()["items"]}
    assert menu["Americano"] == "Kopi" and menu["Es Teh Lemon"] == "Non-kopi"
    listed = {i["name"]: i["menu_category"] for i in (await client.get("/api/items", headers=owner)).json()}
    assert listed["Americano"] == "Kopi"

    # Leaving other fields alone does not touch the section; "" takes it out.
    kept = await client.patch(f"/api/items/{c['americano']}", headers=owner, json={"sell_price": "19000"})
    assert kept.json()["menu_category"] == "Kopi"
    cleared = await client.patch(f"/api/items/{c['americano']}", headers=owner, json={"menu_category": "  "})
    assert cleared.status_code == 200 and cleared.json()["menu_category"] is None
    # Too long is refused in Indonesian, not silently cut.
    long = await client.patch(f"/api/items/{c['americano']}", headers=owner, json={"menu_category": "x" * 41})
    assert long.status_code == 422 and "kategori menu" in long.json()["detail"]
    # The till cannot change it.
    assert (await client.patch(f"/api/items/{c['roti']}", headers=_auth(c["pos"]), json={"menu_category": "Kopi"})).status_code == 403


async def test_the_database_refuses_a_blank_section(session_factory, cafe):
    async with session_factory() as s:
        await _set_tenant(s, cafe["bid"])
        with pytest.raises(IntegrityError):
            await s.execute(text("update items set menu_category = '   ' where id = :id"), {"id": str(cafe["roti"])})
        await s.rollback()
    async with session_factory() as s:
        await _set_tenant(s, cafe["bid"])
        assert (await s.get(Item, uuid.UUID(str(cafe["roti"])))).menu_category is None
