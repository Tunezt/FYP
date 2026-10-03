"""till-18 — the café's links and device tokens do not stop on a date.

The owner, 3 October 2026: "I don't want our system to suddenly stop working a
year later because of this." The till's pairing link, each printer's device
token and the QR menu on the tables used to carry a one-year expiry. They now
carry none. What ends them is a decision, not a calendar: re-pairing ("Tablet
hilang? Putuskan perangkat lama") retires the till link and the printer
tokens. Login *sessions* (owner, cashier) still end after hours, by design —
re-entering a PIN is not the system stopping.

Needs the local Postgres (roadmap §2). No skip marker.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import jwt

from app.core.config import get_settings
from app.core.security import create_menu_token, create_pairing_token, create_token, decode_token
from tests.test_service_journey import (  # noqa: F401 (fixtures)
    _auth, cafe, client, engine, session_factory,
)


def _owner(c):
    return {"Authorization": f"Bearer {create_token(business_id=str(c['bid']), scope='owner')}"}


def test_the_three_long_lived_tokens_carry_no_end_date():
    bid = "11111111-1111-1111-1111-111111111111"
    for token in (create_pairing_token(bid, 1), create_menu_token(bid),
                  create_token(business_id=bid, scope="printer", permanent=True, extra={"printer": "front"})):
        assert "exp" not in decode_token(token)
    # A login session still ends.
    assert "exp" in decode_token(create_token(business_id=bid, scope="owner"))
    assert "exp" in decode_token(create_token(business_id=bid, scope="pos", staff_id=bid))


def test_they_still_decode_ten_years_from_now():
    bid = "11111111-1111-1111-1111-111111111111"
    tokens = [create_pairing_token(bid, 1), create_menu_token(bid),
              create_token(business_id=bid, scope="printer", permanent=True, extra={"printer": "kitchen"})]
    session = create_token(business_id=bid, scope="owner")
    settings = get_settings()
    later = datetime.now(timezone.utc) + timedelta(days=3650)
    with patch("jwt.api_jwt.datetime") as clock:      # PyJWT reads the clock here to judge `exp`
        clock.now.return_value = later
        clock.fromtimestamp = datetime.fromtimestamp
        for token in tokens:
            assert jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])["business_id"] == bid
        try:
            jwt.decode(session, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
            raise AssertionError("a login session must not last ten years")
        except jwt.ExpiredSignatureError:
            pass


async def test_the_owner_can_still_end_them(client, cafe):
    """No date, but not unstoppable: re-pairing retires the till link and the
    printer tokens issued before it."""
    c = cafe
    owner = _owner(c)
    link = (await client.post("/auth/pos-pairing", headers=owner)).json()["pairing_token"]
    issued = (await client.post("/api/printers/front/token", headers=owner, json={})).json()
    assert issued["expires_at"] is None and "exp" not in decode_token(issued["token"])
    printer = {"Authorization": f"Bearer {issued['token']}"}
    assert (await client.get(f"/pos/business/{link}")).status_code == 200
    assert (await client.post("/print/agent/claim", headers=printer, json={"device": "tes-front"})).status_code == 200

    assert (await client.post("/auth/pos-pairing/reset", headers=owner)).status_code == 200
    assert (await client.get(f"/pos/business/{link}")).status_code in (401, 403)
    assert (await client.post("/print/agent/claim", headers=printer, json={"device": "tes-front"})).status_code in (401, 403)

    # The QR menu on the tables is not a device: it keeps working.
    menu = (await client.post("/auth/menu-link", headers=owner)).json()["menu_token"]
    assert "exp" not in decode_token(menu)
    assert (await client.get(f"/menu/{menu}")).status_code == 200
