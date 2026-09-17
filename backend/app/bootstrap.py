"""Set up one real business: `python -m app.bootstrap` (roadmap M15-T3)

`app.seed` builds the demo cafe — eleven products, a month of invented sales,
two fictional regulars — and deletes and recreates it every time it runs. That
must never be pointed at the cafe that is actually trading, so this is the
entrypoint that is, and `app.seed` now refuses when ENVIRONMENT=production.

What this creates is exactly what registering through the dashboard creates,
and nothing more:

    the business · the owner's staff row and PIN · the standard units of
    measure and their conversions · the standard Indonesian SME chart of
    accounts · the posting rules · pricing settings · loyalty settings

"And nothing else" in the roadmap means no demo data. It does not mean skipping
the rules: without posting rules the engine refuses every event, so a business
without them cannot ring up its first sale. What comes out of here is empty and
usable, which is the point.

Usage:

    python -m app.bootstrap --name "Poernama" --owner "Ibu Ratna" --phone 081200011112
    ... --pin 1234              # or leave it off and be prompted, so it stays out of history
    ... --timezone Asia/Jakarta --language id --type cafe

It refuses if the phone already has a business, so running it twice by mistake
cannot produce a second empty cafe.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import re
import sys
import uuid
from dataclasses import dataclass

from sqlalchemy import select

from app.core.db import engine, plain_session, tenant_session
from app.core.security import hash_pin
from app.models import Business, Staff
from app.services.accounts import ensure_standard_chart
from app.services.points import ensure_loyalty_settings
from app.services.posting_rules import ensure_standard_rules
from app.services.pricing import ensure_pricing_settings
from app.services.units import ensure_standard_uoms
from app.whatsapp.client import normalize_phone, to_international_phone

PIN_PATTERN = re.compile(r"^\d{4,8}$")


class BootstrapError(RuntimeError):
    """Operator-facing. Never rendered in the UI, so English is fine here."""


@dataclass
class BootstrapResult:
    business_id: uuid.UUID
    name: str
    owner_name: str
    owner_phone: str
    accounts: int
    posting_rules: int
    uoms: int


async def bootstrap(
    *,
    name: str,
    owner_name: str,
    phone: str,
    pin: str,
    timezone: str = "Asia/Jakarta",
    language: str = "id",
    business_type: str = "cafe",
) -> BootstrapResult:
    """One business, ready to trade and holding no data. Same steps, in the same
    order, as POST /auth/register — that endpoint is the other way in, and the
    two must not drift."""
    if not name.strip():
        raise BootstrapError("the business needs a name")
    if not owner_name.strip():
        raise BootstrapError("the owner needs a name")
    if not PIN_PATTERN.match(pin or ""):
        raise BootstrapError("the owner PIN must be 4 to 8 digits")

    owner_phone = to_international_phone(normalize_phone(phone))
    if len(owner_phone) < 8:
        raise BootstrapError(f"that does not look like a phone number: {phone!r}")

    async with plain_session() as session:
        taken = (
            await session.execute(select(Business.id).where(Business.owner_phone == owner_phone))
        ).scalar_one_or_none()
        if taken is not None:
            raise BootstrapError(
                f"{owner_phone} already has a business ({taken}). Bootstrap creates a new one; "
                "it does not reset an existing one."
            )
        business = Business(
            name=name.strip(),
            business_type=business_type,
            owner_phone=owner_phone,
            language_preference=language,
            timezone=timezone,
        )
        session.add(business)
        await session.flush()
        business_id = business.id

    async with tenant_session(business_id) as session:
        session.add(
            Staff(
                business_id=business_id,
                name=owner_name.strip(),
                role="owner",
                phone=owner_phone,
                pin_hash=hash_pin(pin),
            )
        )
        uoms = await ensure_standard_uoms(session, business_id)       # M4-T3
        accounts = await ensure_standard_chart(session, business_id)  # M6-T1
        rules = await ensure_standard_rules(session, business_id)     # M6-T3
        await ensure_pricing_settings(session, business_id)           # M7-T4
        await ensure_loyalty_settings(session, business_id)           # M8-T2

    return BootstrapResult(
        business_id=business_id,
        name=name.strip(),
        owner_name=owner_name.strip(),
        owner_phone=owner_phone,
        accounts=len(accounts),
        posting_rules=len(rules),
        uoms=len(uoms),
    )


def _ask_for_pin() -> str:
    """Prompted rather than passed, so the owner's PIN does not sit in shell
    history. Fails loudly when there is nobody to ask."""
    if not sys.stdin.isatty():
        raise BootstrapError("no --pin given and nothing to prompt: pass --pin when running unattended")
    first = getpass.getpass("Owner PIN (4-8 digits): ")
    if first != getpass.getpass("Repeat the PIN: "):
        raise BootstrapError("the two PINs do not match")
    return first


async def main() -> int:
    parser = argparse.ArgumentParser(description="Create one empty, usable business.")
    parser.add_argument("--name", required=True, help="the business name, as the owner would write it")
    parser.add_argument("--owner", required=True, help="the owner's name")
    parser.add_argument("--phone", required=True, help="the owner's WhatsApp number (0812... or 62812...)")
    parser.add_argument("--pin", help="owner PIN, 4-8 digits (prompted if omitted)")
    parser.add_argument("--timezone", default="Asia/Jakarta")
    parser.add_argument("--language", default="id", choices=["id", "ms", "en"])
    parser.add_argument("--type", dest="business_type", default="cafe")
    args = parser.parse_args()

    try:
        result = await bootstrap(
            name=args.name,
            owner_name=args.owner,
            phone=args.phone,
            pin=args.pin or _ask_for_pin(),
            timezone=args.timezone,
            language=args.language,
            business_type=args.business_type,
        )
    except BootstrapError as exc:
        print(f"Bootstrap failed: {exc}")
        return 1
    finally:
        await engine.dispose()

    print(f"Created '{result.name}' (business_id={result.business_id})")
    print(f"Owner {result.owner_name} | phone {result.owner_phone} | PIN set")
    print(
        f"{result.accounts} accounts, {result.posting_rules} posting rules, {result.uoms} units of measure."
    )
    print("No products, no sales, no customers. Add the menu from the dashboard or a photo of it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
