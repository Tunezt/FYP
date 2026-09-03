"""Cash in and out (roadmap M7-T2): money through the till that is not a sale.

Four kinds, each posting to the ledger in the caller's transaction:

  cash_in           owner tops up the drawer (`via` owner → Dr Kas / Cr Modal)
                    or brings cash from the bank (`via` bank → Dr Kas / Cr Bank)
  petty_cash        small purchase from the drawer — through the expense writer
                    (M6-T6), so it is an expense row on the P&L, Cr Kas
  supplier_payment  settles the payable a goods receipt created
                    (`via` cash → Dr Utang usaha / Cr Kas; transfer → Cr Bank)
  bank_drop         cash taken to the bank (Dr Bank / Cr Kas)

A movement that went through the drawer is stamped with the acting cashier's
open shift (M7-T1) — a supplier paid by transfer did not, so it is not. The
close (M7-T3) expects float + cash payments + cash in − cash out.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CashMovement, Staff, Supplier
from app.services.expenses import ExpenseInvalid, record_expense
from app.services.posting import post_event
from app.services.shifts import open_shift_id

MONEY = Decimal("0.01")

# kind -> (event type, allowed `via`, default `via`)
KINDS: dict[str, tuple[str | None, tuple[str, ...], str]] = {
    "cash_in": ("CashIn", ("owner", "bank"), "owner"),
    "petty_cash": (None, ("cash",), "cash"),            # posts through record_expense
    "supplier_payment": ("SupplierPaid", ("cash", "transfer"), "cash"),
    "bank_drop": ("BankDrop", ("cash",), "cash"),
}
OUTFLOWS = ("petty_cash", "supplier_payment", "bank_drop")


class CashInvalid(Exception):
    """`code`: kind, via, amount, reason, supplier."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def through_the_drawer(kind: str, via: str) -> bool:
    return not (kind == "supplier_payment" and via == "transfer")


async def record_cash_movement(
    session: AsyncSession,
    business_id: uuid.UUID,
    *,
    kind: str,
    amount: Decimal,
    reason: str,
    staff_id: uuid.UUID | None = None,
    via: str | None = None,
    category: str | None = None,
    supplier_id: uuid.UUID | None = None,
    occurred_at: datetime | None = None,
) -> CashMovement:
    if kind not in KINDS:
        raise CashInvalid("kind")
    event_type, allowed, default_via = KINDS[kind]
    via = via or default_via
    if via not in allowed:
        raise CashInvalid("via")
    amount = Decimal(amount).quantize(MONEY)
    if amount <= 0:
        raise CashInvalid("amount")
    reason = (reason or "").strip()
    if not reason:
        raise CashInvalid("reason")
    if kind == "supplier_payment":
        if supplier_id is None or await session.get(Supplier, supplier_id) is None:
            raise CashInvalid("supplier")
    else:
        supplier_id = None
    occurred_at = occurred_at or datetime.now(timezone.utc)
    shift_id = await open_shift_id(session, staff_id) if through_the_drawer(kind, via) else None

    row = CashMovement(
        business_id=business_id, shift_id=shift_id, staff_id=staff_id, kind=kind, via=via, amount=amount,
        reason=reason, category=category if kind == "petty_cash" else None, supplier_id=supplier_id,
        occurred_at=occurred_at,
    )
    if kind == "petty_cash":
        try:
            expense = await record_expense(
                session, business_id, amount=amount, description=reason, category=category,
                source="manual", occurred_at=occurred_at, created_by=staff_id,
            )
        except ExpenseInvalid:
            raise CashInvalid("amount")
        row.expense_id = expense.id
        session.add(row)
        await session.flush()
        return row

    session.add(row)
    await session.flush()
    await post_event(
        session, business_id, event_type, {via: amount},
        source_type="cash_movement", source_id=row.id, memo=reason, posted_at=occurred_at, created_by=staff_id,
    )
    return row


async def list_cash_movements(
    session: AsyncSession, *, shift_id: uuid.UUID | None = None, limit: int = 50,
) -> list[CashMovement]:
    stmt = select(CashMovement).order_by(CashMovement.occurred_at.desc(), CashMovement.id).limit(limit)
    if shift_id is not None:
        stmt = stmt.where(CashMovement.shift_id == shift_id)
    return (await session.execute(stmt)).scalars().all()


async def cash_movement_view(session: AsyncSession, row: CashMovement) -> dict:
    staff = await session.get(Staff, row.staff_id) if row.staff_id else None
    supplier = await session.get(Supplier, row.supplier_id) if row.supplier_id else None
    return {
        "id": row.id, "shift_id": row.shift_id, "staff_id": row.staff_id,
        "staff_name": staff.name if staff else None, "kind": row.kind, "via": row.via,
        "direction": "out" if row.kind in OUTFLOWS else "in", "amount": row.amount, "reason": row.reason,
        "category": row.category, "supplier_id": row.supplier_id,
        "supplier_name": supplier.name if supplier else None, "expense_id": row.expense_id,
        "occurred_at": row.occurred_at,
    }
