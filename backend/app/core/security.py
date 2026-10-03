"""JWT issuing/verification and PIN hashing.

Two token scopes exist and they are not interchangeable:
- scope="owner":  full dashboard + owner API access for one business
- scope="pos":    POS kiosk only — sale recording and the item list; carries the
                  staff_id, and is rejected by every owner-only dependency

PIN hashing uses PBKDF2-HMAC-SHA256 (stdlib, no C dependencies) with a per-hash
random salt and constant-time comparison. 4-digit PINs are low-entropy by
nature; the kiosk pairing token being required first is the real gate — the PIN
identifies *which staff member* on an already-paired device.
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import get_settings

_PBKDF2_ITERATIONS = 100_000


def hash_pin(pin: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_pin(pin: str, stored: str) -> bool:
    try:
        _, iterations, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac(
            "sha256", pin.encode(), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def create_token(
    *,
    business_id: str,
    scope: str,
    staff_id: str | None = None,
    ttl_minutes: int | None = None,
    generation: int = 1,
    extra: dict | None = None,
    permanent: bool = False,
) -> str:
    """`generation` (M15-T8) is the business's `pairing_generation` at the moment
    of issue. Checked on every `pos` request, so re-pairing a lost tablet ends
    the sessions it was holding rather than only stopping new logins. Default 1
    matches a business that has never re-paired."""
    settings = get_settings()
    if ttl_minutes is None:
        ttl_minutes = (
            settings.owner_token_ttl_minutes if scope == "owner" else settings.pos_token_ttl_minutes
        )
    now = datetime.now(timezone.utc)
    payload = {
        "sub": staff_id or business_id,
        "business_id": business_id,
        "scope": scope,
        "gen": int(generation),
        "iat": now,
    }
    # till-18: a device's token (a printer's) has no date on which it stops;
    # it ends when the owner disconnects the café's devices (`generation`).
    if not permanent:
        payload["exp"] = now + timedelta(minutes=ttl_minutes)
    if staff_id:
        payload["staff_id"] = staff_id
    for key, value in (extra or {}).items():   # prt-4: e.g. which printer a device token is for
        if key not in payload:
            payload[key] = value
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    """Raises jwt.PyJWTError on anything invalid/expired."""
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


def generate_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_otp(code: str) -> str:
    # OTPs are short-lived; a salted hash guards against DB leakage, not brute
    # force (the attempts counter does that).
    return hashlib.sha256(f"otp:{code}".encode()).hexdigest()


def create_registration_token(phone: str) -> str:
    """Short-lived proof that a phone number passed OTP verification but has no
    business yet — exchanged for an owner token by /auth/register."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"phone": phone, "scope": "register", "iat": now, "exp": now + timedelta(minutes=30)},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def create_menu_token(business_id: str) -> str:
    """The token baked into the QR code on the table (M11-T1). Grants only:
    read the menu, place a ticket, watch that ticket — for one business.

    till-18 (owner, 3 Oct 2026): it does not expire. A sticker printed and
    stuck on every table must not stop working a year later because of a
    date nobody remembers. It carries no secret a guest should not have, and
    a ticket still has to be accepted and paid at the till."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"business_id": business_id, "scope": "menu", "iat": now},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def create_pairing_token(business_id: str, generation: int = 1) -> str:
    """The token baked into the POS kiosk URL. Grants only the ability to list
    staff names and attempt PIN logins for one business — never data access.

    till-18 (owner, 3 Oct 2026): it does not expire; the café's till must not
    stop one morning because a year went by. It dies only when the owner says
    so (M15-T8): the token carries the business's `pairing_generation`, and
    re-pairing ("Tablet hilang?") raises that counter, which retires every
    link and session issued before it. No revocation table — one integer
    compared against the business row that the caller already reads."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "business_id": business_id,
            "scope": "pos-pairing",
            "gen": int(generation),
            "iat": now,
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
