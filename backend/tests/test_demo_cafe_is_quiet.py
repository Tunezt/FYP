"""demo-1 — the demo café never messages anyone.

The demo café (`app.seed`) is registered to an invented number, 0812-000-1111.
Invented numbers belong to real people. Once a demo café exists next to real
ones (the owner asked for one on the live system, 4 October 2026), the nightly
job would send that stranger a WhatsApp alert every night. It must not.

Needs the local Postgres (roadmap §2). No skip marker.
"""
import uuid

from sqlalchemy import select, text

from app.core.config import DEMO_OWNER_PHONE
from app.jobs import delivery
from app.models import Alert, Business
from app.seed import OWNER_PHONE
from tests.test_service_journey import engine, session_factory  # noqa: F401 (fixtures)


async def _cafe_with_an_alert(session_factory, phone: str) -> uuid.UUID:
    async with session_factory() as s:
        business = Business(name="Kafe Uji", business_type="cafe", owner_phone=phone)
        s.add(business)
        await s.flush()
        await s.execute(text("select set_config('app.current_business_id', :b, false)"), {"b": str(business.id)})
        s.add(Alert(business_id=business.id, type="low_stock", severity="medium", message="Susu UHT tinggal 2 liter"))
        await s.commit()
        return business.id


async def _deliver(session_factory, bid, monkeypatch) -> tuple[int, list, bool]:
    sent: list[tuple] = []

    async def fake_send_template(to, template, params, language=None):
        sent.append((to, template, params))

    monkeypatch.setattr(delivery, "send_template", fake_send_template)
    async with session_factory() as s:
        await s.execute(text("select set_config('app.current_business_id', :b, false)"), {"b": str(bid)})
        business = await s.get(Business, bid)
        count = await delivery.deliver_unsent(s, business)
        await s.commit()
        still_unsent = not (await s.execute(select(Alert.is_sent))).scalar_one()
    return count, sent, still_unsent


async def _remove(session_factory, bid) -> None:
    async with session_factory() as s:
        row = await s.get(Business, bid)
        if row:
            await s.delete(row)
        await s.commit()


def test_the_seed_and_the_guard_mean_the_same_number():
    assert OWNER_PHONE == DEMO_OWNER_PHONE


async def test_the_demo_cafes_alerts_are_not_sent_to_its_invented_number(session_factory, monkeypatch):
    async with session_factory() as s:      # the local demo café, if seeded, holds this number
        taken = (await s.execute(select(Business).where(Business.owner_phone == DEMO_OWNER_PHONE))).scalar_one_or_none()
    if taken is not None:
        bid, made = taken.id, False
        async with session_factory() as s:
            await s.execute(text("select set_config('app.current_business_id', :b, false)"), {"b": str(bid)})
            before = (await s.execute(select(Alert.id).where(Alert.is_sent.is_(False)))).scalars().all()
        sent: list = []

        async def fake_send_template(to, template, params, language=None):
            sent.append(to)

        monkeypatch.setattr(delivery, "send_template", fake_send_template)
        async with session_factory() as s:
            await s.execute(text("select set_config('app.current_business_id', :b, false)"), {"b": str(bid)})
            count = await delivery.deliver_unsent(s, await s.get(Business, bid))
            await s.commit()
            after = (await s.execute(select(Alert.id).where(Alert.is_sent.is_(False)))).scalars().all()
        assert count == 0 and sent == [] and sorted(after) == sorted(before)
        return
    bid = await _cafe_with_an_alert(session_factory, DEMO_OWNER_PHONE)
    try:
        count, sent, still_unsent = await _deliver(session_factory, bid, monkeypatch)
        # Nothing went out, and the alert is not marked as if it had.
        assert count == 0 and sent == [] and still_unsent
    finally:
        await _remove(session_factory, bid)


async def test_a_real_cafe_is_still_told(session_factory, monkeypatch):
    phone = "62811" + str(uuid.uuid4().int)[:8]
    bid = await _cafe_with_an_alert(session_factory, phone)
    try:
        count, sent, still_unsent = await _deliver(session_factory, bid, monkeypatch)
        assert count == 1 and len(sent) == 1 and sent[0][0] == phone and not still_unsent
    finally:
        await _remove(session_factory, bid)
