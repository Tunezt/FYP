"""Customers (roadmap M8-T1): who bought, attached to the order.

Phone is the natural key and the WhatsApp identity, so it is normalised to the
same digits-only international form as `businesses.owner_phone` before it is
stored or searched: a customer typed as "0812-3456-7890" and one typed as
"+62 812 3456 7890" are the same person. One customer per phone per business
(partial unique index); a customer with no phone is allowed — a walk-in the
cashier only knows by name.

Nothing about purchases is stored here. Visits, spend and last visit are
derived from `orders` when a customer is read (`customer_summary`), the same
way suppliers derive their history from receipts.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Customer, Order
from app.whatsapp.client import normalize_phone, to_international_phone


class CustomerInvalid(Exception):
    """`code`: name, phone, duplicate_phone, not_found."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def clean_phone(phone: str | None) -> str | None:
    """'' / None → None; otherwise digits only, international (0812… → 62812…).
    Anything that is not 8–15 digits after that is refused."""
    digits = normalize_phone(phone or "")
    if not digits:
        return None
    digits = to_international_phone(digits)
    if not (8 <= len(digits) <= 15):
        raise CustomerInvalid("phone")
    return digits


async def customer_by_phone(session: AsyncSession, phone: str | None) -> Customer | None:
    """A lookup, so an unusable number finds nobody rather than raising."""
    try:
        digits = clean_phone(phone)
    except CustomerInvalid:
        return None
    if digits is None:
        return None
    return (await session.execute(select(Customer).where(Customer.phone == digits))).scalar_one_or_none()


async def create_customer(
    session: AsyncSession, business_id: uuid.UUID, *, name: str, phone: str | None = None,
    address: str | None = None, birthday: date | None = None, notes: str | None = None,
) -> Customer:
    name = (name or "").strip()
    if not name:
        raise CustomerInvalid("name")
    digits = clean_phone(phone)
    if digits is not None and await customer_by_phone(session, digits) is not None:
        raise CustomerInvalid("duplicate_phone")
    row = Customer(
        business_id=business_id, name=name, phone=digits, address=(address or "").strip() or None,
        birthday=birthday, notes=(notes or "").strip() or None,
    )
    session.add(row)
    await session.flush()
    return row


async def update_customer(session: AsyncSession, customer: Customer, **changes) -> Customer:
    if changes.get("name") is not None:
        name = changes["name"].strip()
        if not name:
            raise CustomerInvalid("name")
        customer.name = name
    if "phone" in changes and changes["phone"] is not None:
        digits = clean_phone(changes["phone"])
        if digits is not None:
            other = await customer_by_phone(session, digits)
            if other is not None and other.id != customer.id:
                raise CustomerInvalid("duplicate_phone")
        customer.phone = digits
    for field in ("address", "notes"):
        if field in changes and changes[field] is not None:
            setattr(customer, field, changes[field].strip() or None)
    if "birthday" in changes and changes["birthday"] is not None:
        customer.birthday = changes["birthday"]
    if changes.get("is_active") is not None:
        customer.is_active = bool(changes["is_active"])
    customer.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return customer


async def require_customer(session: AsyncSession, customer_id: uuid.UUID | None) -> Customer | None:
    """The customer an order is being attached to: must exist for this business
    (RLS hides everyone else's) and be active."""
    if customer_id is None:
        return None
    row = await session.get(Customer, customer_id)
    if row is None or not row.is_active:
        raise CustomerInvalid("not_found")
    return row


async def search_customers(session: AsyncSession, q: str | None, *, limit: int = 10, include_inactive: bool = False) -> list[Customer]:
    """By name (substring, case-insensitive) or phone (digits prefix/substring)."""
    stmt = select(Customer)
    if not include_inactive:
        stmt = stmt.where(Customer.is_active.is_(True))
    q = (q or "").strip()
    if q:
        digits = normalize_phone(q)
        conds = [func.lower(Customer.name).contains(q.lower())]
        if digits:
            # "0812…" should find "62812…": try both the raw digits and the international form.
            conds.append(Customer.phone.contains(digits))
            try:
                conds.append(Customer.phone.contains(to_international_phone(digits)))
            except Exception:  # pragma: no cover — to_international_phone never raises today
                pass
        stmt = stmt.where(or_(*conds))
    return (await session.execute(stmt.order_by(Customer.name, Customer.id).limit(limit))).scalars().all()


@dataclass(frozen=True)
class CustomerSummary:
    visits: int              # completed or refunded orders (a void never happened)
    total_spent: Decimal     # Σ total of completed orders — a refunded order is not spend
    last_visit: datetime | None


async def customer_summaries(session: AsyncSession, customer_ids: list[uuid.UUID]) -> dict[uuid.UUID, CustomerSummary]:
    if not customer_ids:
        return {}
    rows = (
        await session.execute(
            select(
                Order.customer_id,
                func.count(Order.id).filter(Order.status != "voided"),
                func.coalesce(func.sum(case((Order.status == "completed", Order.total), else_=0)), 0),
                func.max(Order.sold_at).filter(Order.status != "voided"),
            )
            .where(Order.customer_id.in_(customer_ids))
            .group_by(Order.customer_id)
        )
    ).all()
    out = {cid: CustomerSummary(int(n), Decimal(spent).quantize(Decimal("0.01")), last) for cid, n, spent, last in rows}
    for cid in customer_ids:
        out.setdefault(cid, CustomerSummary(0, Decimal("0.00"), None))
    return out


async def customer_view(session: AsyncSession, customer: Customer) -> dict:
    summary = (await customer_summaries(session, [customer.id]))[customer.id]
    return {
        "id": customer.id, "name": customer.name, "phone": customer.phone, "address": customer.address,
        "birthday": customer.birthday, "notes": customer.notes, "is_active": customer.is_active,
        "visits": summary.visits, "total_spent": summary.total_spent, "last_visit": summary.last_visit,
        "created_at": customer.created_at,
    }


async def customer_views(session: AsyncSession, customers: list[Customer]) -> list[dict]:
    summaries = await customer_summaries(session, [c.id for c in customers])
    return [
        {
            "id": c.id, "name": c.name, "phone": c.phone, "address": c.address, "birthday": c.birthday,
            "notes": c.notes, "is_active": c.is_active, "visits": summaries[c.id].visits,
            "total_spent": summaries[c.id].total_spent, "last_visit": summaries[c.id].last_visit,
            "created_at": c.created_at,
        }
        for c in customers
    ]
