"""The customer's receipt, only when asked (till-5b; the owner's decision 4).

After payment the cashier asks the customer and taps one of:

  paper     the receipt joins the print queue (the front printer), once
  qr        a web receipt: the till shows a QR of /struk/<code> to scan
  whatsapp  route A (till-7): a QR of wa.me/<bot>?text=STRUK <code>; the
            customer sends it and the bot replies with the receipt. Only
            while the bot's number is configured (`WHATSAPP_RECEIPT_NUMBER`)
  none      nothing

Kitchen and Bar slips always print, whatever is chosen (printing.py). The last
choice is kept on the order (`receipt_choice`, `receipt_choice_at`) so paper
use can be reported. Changing one's mind is allowed: a QR receipt can still be
printed afterwards, and printing twice is still one receipt job.

The code. One bot number serves every café, so a code must say whose receipt
it is on its own, and must not be guessable: "001" would hand a stranger
another table's bill. It is the café's own 8-character id prefix, a dash, and
10 random characters from an alphabet without look-alikes (31^10 ≈ 8 × 10^14).
It is made only when a web or WhatsApp receipt is asked for.
"""
from __future__ import annotations

import re
import secrets
import uuid
from datetime import datetime, timezone
from urllib.parse import quote

from sqlalchemy import cast, select, String
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Business, Order

CHOICES = ("paper", "qr", "whatsapp", "none")
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
CODE_RE = re.compile(r"^([0-9a-f]{8})-([abcdefghjkmnpqrstuvwxyz23456789]{10})$")


class ReceiptChoiceInvalid(Exception):
    """`code`: choice · not_paid · whatsapp_off."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def new_receipt_code(business_id: uuid.UUID) -> str:
    return f"{str(business_id)[:8]}-{''.join(secrets.choice(_ALPHABET) for _ in range(10))}"


def normalize_code(text: str | None) -> str | None:
    """A code as a customer might send it back: any case, stray spaces."""
    code = (text or "").strip().lower()
    return code if CODE_RE.match(code) else None


def whatsapp_number() -> str:
    from app.core.config import get_settings

    return "".join(ch for ch in (get_settings().whatsapp_receipt_number or "") if ch.isdigit())


def whatsapp_live() -> bool:
    """The till offers WhatsApp receipts only once the bot's Indonesian number
    is set; until then the option is shown disabled, "Belum aktif"."""
    return len(whatsapp_number()) >= 8


def whatsapp_link(code: str) -> str:
    return f"https://wa.me/{whatsapp_number()}?text={quote(f'STRUK {code}')}"


def web_path(code: str) -> str:
    return f"/struk/{code}"


async def ensure_receipt_code(session: AsyncSession, order: Order) -> str:
    if not order.receipt_code:
        order.receipt_code = new_receipt_code(order.business_id)
        await session.flush()
    return order.receipt_code


async def choose_receipt(session: AsyncSession, order: Order, choice: str, *, staff_id: uuid.UUID | None) -> dict:
    """Record the choice and do what it needs. Returns what the till shows."""
    from app.services.printing import enqueue_receipt

    if choice not in CHOICES:
        raise ReceiptChoiceInvalid("choice")
    if order.status != "completed" or order.entry_source != "live":
        raise ReceiptChoiceInvalid("not_paid")
    if choice == "whatsapp" and not whatsapp_live():
        raise ReceiptChoiceInvalid("whatsapp_off")
    out: dict = {"choice": choice, "receipt_code": None, "web_path": None, "whatsapp_link": None, "print_job_id": None}
    if choice == "paper":
        job = await enqueue_receipt(session, order, staff_id=staff_id)
        out["print_job_id"] = job.id
    elif choice in ("qr", "whatsapp"):
        code = await ensure_receipt_code(session, order)
        out["receipt_code"], out["web_path"] = code, web_path(code)
        if choice == "whatsapp":
            out["whatsapp_link"] = whatsapp_link(code)
    order.receipt_choice = choice
    order.receipt_choice_at = datetime.now(timezone.utc)
    await session.flush()
    return out


async def business_for_code(session: AsyncSession, code: str) -> list[uuid.UUID]:
    """The café(s) whose id starts with the code's prefix. `businesses` has no
    row-level policy (it is the tenant), so this lookup needs no tenant; the
    order itself is then read inside that café's own session."""
    match = CODE_RE.match(code or "")
    if not match:
        return []
    rows = (await session.execute(
        select(Business.id).where(cast(Business.id, String).like(f"{match.group(1)}%"))
    )).scalars().all()
    return list(rows)


async def find_order_by_code(code: str) -> tuple[uuid.UUID, uuid.UUID] | None:
    """(business_id, order_id) for a receipt code, or None. Opens its own
    sessions: the caller (the public page, the bot) has no tenant yet."""
    from app.core.db import plain_session, tenant_session

    code = normalize_code(code)
    if code is None:
        return None
    async with plain_session() as session:
        candidates = await business_for_code(session, code)
    for business_id in candidates:
        async with tenant_session(business_id) as session:
            order_id = (await session.execute(select(Order.id).where(Order.receipt_code == code))).scalar_one_or_none()
            if order_id is not None:
                return business_id, order_id
    return None
