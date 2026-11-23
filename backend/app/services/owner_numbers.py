"""More WhatsApp numbers for the owner's assistant (wa-gw-4).

`businesses.extra_owner_numbers` holds [{name, phone, added_at}]. A number on
it gets every assistant feature the owner's own number gets; login codes and
the nightly alerts still go to `owner_phone` only.
"""
import re
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Business
from app.whatsapp.client import normalize_phone, to_international_phone

MAX_NUMBERS = 10

# Mobile numbers only for the two countries the product serves; any other
# country code passes on length (E.164: at most 15 digits).
_COUNTRY_RULES = {"62": re.compile(r"628\d{7,11}"), "60": re.compile(r"601\d{8,9}")}


class OwnerNumberInvalid(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def clean_owner_number(raw: str | None) -> str:
    """'0812 3456 7890' → '6281234567890', '+60 12-345 6789' / '012-345 6789'
    → '60123456789'. Raises OwnerNumberInvalid('phone') for anything that is
    not a usable mobile number."""
    digits = normalize_phone(raw or "")
    if not digits:
        raise OwnerNumberInvalid("phone")
    digits = to_international_phone(digits)
    rule = _COUNTRY_RULES.get(digits[:2])
    ok = rule.fullmatch(digits) if rule else re.fullmatch(r"[1-9]\d{7,14}", digits)
    if not ok:
        raise OwnerNumberInvalid("phone")
    return digits


def owner_number_filter(phone: str):
    """Businesses whose owner, or one of whose extra numbers, is `phone`."""
    return or_(Business.owner_phone == phone, Business.extra_owner_numbers.contains([{"phone": phone}]))


async def add_owner_number(session: AsyncSession, business: Business, name: str, raw_phone: str) -> None:
    name = " ".join((name or "").split())
    if not name:
        raise OwnerNumberInvalid("name")
    phone = clean_owner_number(raw_phone)
    current = list(business.extra_owner_numbers or [])
    if len(current) >= MAX_NUMBERS:
        raise OwnerNumberInvalid("full")
    # One number belongs to one cafe: the bot must know whose data to answer from.
    taken = (await session.execute(select(Business.id).where(owner_number_filter(phone)).limit(1))).first()
    if taken is not None:
        raise OwnerNumberInvalid("duplicate")
    added_at = datetime.now(timezone.utc).isoformat()
    business.extra_owner_numbers = current + [{"name": name[:60], "phone": phone, "added_at": added_at}]


# wa-gw-5: what a number on several cafes types to pick again.
SWITCH_COMMANDS = {"ganti toko", "switch business", "switch store"}


def chosen_business(businesses: list[Business], phone: str) -> Business | None:
    """The cafe `phone` last picked on WhatsApp, among those it reaches."""
    return next((b for b in businesses if phone in (b.active_owner_numbers or [])), None)


def choose_business(businesses: list[Business], phone: str, chosen: Business | None) -> None:
    """Remembers `chosen` as the cafe `phone` talks to; None forgets the choice."""
    for b in businesses:
        kept = [p for p in (b.active_owner_numbers or []) if p != phone]
        b.active_owner_numbers = kept + [phone] if b is chosen else kept


def remove_owner_number(business: Business, phone: str) -> None:
    current = list(business.extra_owner_numbers or [])
    kept = [n for n in current if n.get("phone") != normalize_phone(phone)]
    if len(kept) == len(current):
        raise OwnerNumberInvalid("not_found")
    business.extra_owner_numbers = kept
