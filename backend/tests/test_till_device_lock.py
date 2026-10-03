"""kasir-1 — the till on one tablet only, built and left switched off.

The owner, 4 October 2026: anyone holding the till link and a staff PIN can
open the till on their own phone. The café should be able to say "this tablet
is the till" and have every other device refused. Built now, activated later:
the switch (`businesses.till_device_lock`) is off for every café until the
owner turns it on in Pengaturan.

Needs the local Postgres (roadmap §2). No skip marker.
"""
import hashlib

from sqlalchemy import select

from app.core.security import create_token
from app.models import Business
from tests.test_service_journey import (  # noqa: F401 (fixtures)
    _auth, cafe, client, engine, session_factory,
)

TABLET = {"X-Till-Device": "tablet-toko-" + "a" * 32}
PHONE = {"X-Till-Device": "hp-karyawan-" + "b" * 32}
WRONG = "Kasir ini hanya bisa dibuka di tablet toko"


def _owner(c):
    return {"Authorization": f"Bearer {create_token(business_id=str(c['bid']), scope='owner')}"}


async def _link(client, c) -> str:
    return (await client.post("/auth/pos-pairing", headers=_owner(c))).json()["pairing_token"]


async def _login(client, c, link, headers, pin="2345"):
    return await client.post("/pos/login", headers=headers,
                             json={"pairing_token": link, "staff_id": str(c["sari"]), "pin": pin})


async def _row(session_factory, c) -> Business:
    async with session_factory() as s:
        return (await s.execute(select(Business).where(Business.id == c["bid"]))).scalar_one()


async def test_it_is_off_until_the_owner_turns_it_on(client, session_factory, cafe):
    c = cafe
    link = await _link(client, c)
    shown = (await client.get("/api/business", headers=_owner(c))).json()
    assert shown["till_device_lock"] is False and shown["till_device_bound_at"] is None
    # Any device, with or without a key of its own, opens the till as before.
    for headers in (TABLET, PHONE, {}):
        assert (await client.get(f"/pos/business/{link}", headers=headers)).status_code == 200
        assert (await _login(client, c, link, headers)).status_code == 200
    # ...and logging in binds nothing while the switch is off.
    row = await _row(session_factory, c)
    assert row.till_device_lock is False and row.till_device_hash is None and row.till_device_bound_at is None


async def test_switched_on_the_first_correct_pin_makes_that_device_the_till(client, session_factory, cafe):
    c = cafe
    link = await _link(client, c)
    on = await client.patch("/api/business", headers=_owner(c), json={"till_device_lock": True})
    assert on.status_code == 200 and on.json()["till_device_lock"] is True and on.json()["till_device_bound_at"] is None

    # Nothing is bound yet, so the staff list still shows; a wrong PIN binds nothing.
    assert (await client.get(f"/pos/business/{link}", headers=PHONE)).status_code == 200
    assert (await _login(client, c, link, PHONE, pin="0000")).status_code == 401
    assert (await _row(session_factory, c)).till_device_hash is None

    assert (await _login(client, c, link, TABLET)).status_code == 200
    row = await _row(session_factory, c)
    # Only the hash of the tablet's key is kept, never the key.
    assert row.till_device_hash == hashlib.sha256(TABLET["X-Till-Device"].encode()).hexdigest()
    assert row.till_device_bound_at is not None
    assert TABLET["X-Till-Device"] not in row.till_device_hash
    shown = (await client.get("/api/business", headers=_owner(c))).json()
    assert shown["till_device_bound_at"] is not None and "till_device_hash" not in shown

    # The tablet keeps working, as often as it likes.
    assert (await client.get(f"/pos/business/{link}", headers=TABLET)).status_code == 200
    assert (await _login(client, c, link, TABLET)).status_code == 200

    # Every other device is refused before it sees a name or tries a PIN.
    for headers in (PHONE, {}, {"X-Till-Device": "short"}):
        boot = await client.get(f"/pos/business/{link}", headers=headers)
        assert boot.status_code == 403 and WRONG in boot.json()["detail"], boot.text
        refused = await _login(client, c, link, headers)
        assert refused.status_code == 403 and WRONG in refused.json()["detail"], refused.text
    # The right PIN on the wrong device is refused the same way as a wrong one:
    # the answer says nothing about the PIN, and nobody earned a cooldown.
    assert (await _login(client, c, link, PHONE, pin="0000")).status_code == 403
    assert (await _login(client, c, link, TABLET)).status_code == 200


async def test_a_device_with_no_key_cannot_become_the_till(client, session_factory, cafe):
    c = cafe
    link = await _link(client, c)
    await client.patch("/api/business", headers=_owner(c), json={"till_device_lock": True})
    refused = await _login(client, c, link, {})
    assert refused.status_code == 403 and "/kasir" in refused.json()["detail"]
    assert (await _row(session_factory, c)).till_device_hash is None
    # The tablet, which has a key, can still claim it afterwards.
    assert (await _login(client, c, link, TABLET)).status_code == 200


async def test_a_new_tablet_is_bound_by_re_pairing_or_by_switching_off_and_on(client, session_factory, cafe):
    c = cafe
    owner = _owner(c)
    link = await _link(client, c)
    await client.patch("/api/business", headers=owner, json={"till_device_lock": True})
    assert (await _login(client, c, link, TABLET)).status_code == 200
    assert (await _login(client, c, link, PHONE)).status_code == 403

    # "Tablet hilang? Putuskan perangkat lama": the old link dies with the old
    # tablet, and the next device through the new link becomes the till.
    new_link = (await client.post("/auth/pos-pairing/reset", headers=owner)).json()["pairing_token"]
    row = await _row(session_factory, c)
    assert row.till_device_lock is True and row.till_device_hash is None and row.till_device_bound_at is None
    assert (await _login(client, c, link, TABLET)).status_code == 401          # the old link
    assert (await _login(client, c, new_link, PHONE)).status_code == 200       # the replacement tablet
    assert (await _login(client, c, new_link, TABLET)).status_code == 403      # the lost one, even with the new link

    # Switching it off forgets the tablet; every device is welcome again.
    off = await client.patch("/api/business", headers=owner, json={"till_device_lock": False})
    assert off.json()["till_device_lock"] is False and off.json()["till_device_bound_at"] is None
    assert (await _login(client, c, new_link, TABLET)).status_code == 200
    assert (await _row(session_factory, c)).till_device_hash is None
    # Saving other settings does not disturb a binding.
    await client.patch("/api/business", headers=owner, json={"till_device_lock": True})
    assert (await _login(client, c, new_link, TABLET)).status_code == 200
    bound = (await _row(session_factory, c)).till_device_hash
    await client.patch("/api/business", headers=owner, json={"till_device_lock": True, "name": "Kafe Uji"})
    assert (await _row(session_factory, c)).till_device_hash == bound


async def test_only_the_owner_can_turn_it_on(client, cafe):
    c = cafe
    refused = await client.patch("/api/business", headers=_auth(c["pos"]), json={"till_device_lock": True})
    assert refused.status_code == 403
    assert (await client.patch("/api/business", json={"till_device_lock": True})).status_code == 401
