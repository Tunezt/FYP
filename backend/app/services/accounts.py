"""Chart of accounts (roadmap M6-T1).

One small Indonesian SME chart, the same for every business, seeded at
registration and by migration 0014 for businesses that already exist. Codes are
what posting rules (M6-T3) and statements (M6-T5) reference; names are what the
owner sees and may rename. Merchants can add accounts; seeded ones are
`is_system` and cannot be deactivated.

Type decides the normal balance (M6-T5): assets and expenses are debit-normal,
liabilities, equity and revenue are credit-normal.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account

ACCOUNT_TYPES = ("asset", "liability", "equity", "revenue", "expense")
DEBIT_NORMAL = {"asset", "expense"}

# (code, name, type) — the codes below are referenced by name elsewhere; keep them.
STANDARD_CHART: list[tuple[str, str, str]] = [
    ("1100", "Kas", "asset"),
    ("1110", "Bank", "asset"),
    ("1120", "Piutang QRIS / e-wallet", "asset"),
    ("1200", "Piutang usaha", "asset"),
    ("1300", "Persediaan", "asset"),
    ("1400", "Peralatan", "asset"),
    ("2100", "Utang usaha (supplier)", "liability"),
    ("2200", "Utang pajak", "liability"),
    ("2300", "Liabilitas poin pelanggan", "liability"),
    ("2400", "Pendapatan diterima di muka", "liability"),
    ("3100", "Modal pemilik", "equity"),
    ("3200", "Prive (penarikan pemilik)", "equity"),
    ("3900", "Laba ditahan", "equity"),
    ("4100", "Penjualan", "revenue"),
    ("4200", "Diskon penjualan", "revenue"),
    ("4250", "Diskon promo", "revenue"),   # M8-T3, backfilled by migration 0023
    ("4300", "Retur penjualan", "revenue"),
    ("4900", "Pendapatan lain-lain", "revenue"),
    ("4910", "Pendapatan ongkos kirim", "revenue"),   # M11-T3, backfilled by migration 0028
    ("5100", "Harga pokok penjualan (HPP)", "expense"),
    ("5200", "Bahan baku", "expense"),
    ("5300", "Gaji & upah", "expense"),
    ("5400", "Sewa tempat", "expense"),
    ("5500", "Listrik, air & gas", "expense"),
    ("5600", "Pemasaran & promo", "expense"),
    ("5700", "Penyusutan & barang rusak", "expense"),
    ("5800", "Selisih kas", "expense"),
    ("5900", "Beban lain-lain", "expense"),
]

# Where an existing expense category lands (M6-T6 uses this); default 5900.
EXPENSE_CATEGORY_ACCOUNT = {
    "bahan baku": "5200", "operasional": "5500", "gaji": "5300", "sewa": "5400", "listrik": "5500",
    "pemasaran": "5600", "promo": "5600", "lainnya": "5900",
}


class AccountInvalid(Exception):
    """`code`: code, name, duplicate, type, system."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


async def ensure_standard_chart(session: AsyncSession, business_id: uuid.UUID) -> dict[str, Account]:
    """Idempotent: create any standard account the business lacks. Returns {code: Account}."""
    existing = {a.code: a for a in (await session.execute(select(Account))).scalars()}
    for code, name, type_ in STANDARD_CHART:
        if code not in existing:
            a = Account(business_id=business_id, code=code, name=name, type=type_, is_system=True)
            session.add(a)
            existing[code] = a
    await session.flush()
    return existing


async def account_by_code(session: AsyncSession, code: str) -> Account | None:
    return (await session.execute(select(Account).where(Account.code == code.strip()))).scalar_one_or_none()


async def create_account(
    session: AsyncSession, business_id: uuid.UUID, *, code: str, name: str, type: str,
) -> Account:
    code, name = (code or "").strip(), (name or "").strip()
    if not code or not code.isdigit() or len(code) > 8:
        raise AccountInvalid("code")
    if not name:
        raise AccountInvalid("name")
    if type not in ACCOUNT_TYPES:
        raise AccountInvalid("type")
    if await account_by_code(session, code) is not None:
        raise AccountInvalid("duplicate")
    a = Account(business_id=business_id, code=code, name=name, type=type, is_system=False)
    session.add(a)
    await session.flush()
    return a


async def update_account(session: AsyncSession, account: Account, **changes) -> Account:
    if changes.get("name") is not None:
        name = changes["name"].strip()
        if not name:
            raise AccountInvalid("name")
        account.name = name
    if changes.get("is_active") is not None:
        if changes["is_active"] is False and account.is_system:
            raise AccountInvalid("system")
        account.is_active = changes["is_active"]
    account.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return account


async def chart(session: AsyncSession, include_inactive: bool = False) -> list[Account]:
    stmt = select(Account).order_by(Account.code)
    if not include_inactive:
        stmt = stmt.where(Account.is_active.is_(True))
    return (await session.execute(stmt)).scalars().all()
