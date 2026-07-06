"""Receipt/stock-book photo pipeline + the confirmation gate (Section 4).

Gate rule — a correctness requirement, not polish:
  confidence == "high" AND no listed ambiguities AND at least one item
    → commit immediately
  anything else
    → park in pending_confirmations, send the owner a summary, and only
      commit after an affirmative reply. NEVER silently write a low-confidence
      parse.
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Business, Expense, Item, PendingConfirmation, Receipt

logger = logging.getLogger("receipts")

PENDING_TTL_MINUTES = 60

# Fast-path confirmation keywords (id / ms / en). Anything else goes through
# the Gemini revision flow — see app/whatsapp/processor.py.
AFFIRMATIVE = {"ya", "yes", "y", "iya", "yup", "ok", "oke", "okay", "betul", "benar",
               "correct", "sip", "gas", "simpan", "save", "confirm", "ye", "boleh"}
NEGATIVE = {"no", "tidak", "tak", "ga", "gak", "nggak", "salah", "wrong", "batal",
            "cancel", "jangan", "hapus", "bukan"}


def needs_confirmation(parsed: dict) -> bool:
    return (
        parsed.get("confidence") != "high"
        or bool(parsed.get("ambiguities"))
        or not parsed.get("items")
    )


def classify_reply_keyword(text: str) -> str:
    """'confirm' | 'deny' | 'other' — pure keyword fast path."""
    word = text.strip().lower().rstrip("!.…")
    if word in AFFIRMATIVE:
        return "confirm"
    if word in NEGATIVE:
        return "deny"
    return "other"


def summarize_parse(parsed: dict) -> str:
    """The 'I read: …' body of the confirmation message (composer adds tone)."""
    lines = []
    for item in parsed.get("items", []):
        qty = item.get("quantity", "?")
        unit = item.get("unit", "")
        price = item.get("line_total") or 0
        price_part = f" — Rp {price:,.0f}".replace(",", ".") if price else ""
        lines.append(f"• {qty} {unit} {item.get('name', '?')}{price_part}")
    if parsed.get("supplier"):
        lines.append(f"Supplier: {parsed['supplier']}")
    total = parsed.get("total_amount") or 0
    if total:
        lines.append(f"Total: Rp {total:,.0f}".replace(",", "."))
    return "\n".join(lines) if lines else "(tidak ada item terbaca)"


def _dec(value, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


async def commit_parse(
    session: AsyncSession, business: Business, parsed: dict, image_path: str
) -> dict:
    """Write receipt (+ expense for purchases) and apply stock effects.

    receipt      → purchased quantities are ADDED to matching items' stock
                   (unmatched items are created so the purchase isn't lost)
    stock_ledger → quantities are ABSOLUTE (a stock count), set not added
    """
    doc_type = parsed.get("document_type", "receipt")
    occurred_at = _parse_date(parsed.get("date")) or datetime.now(timezone.utc)

    receipt = Receipt(
        business_id=business.id,
        image_url=image_path,
        parsed_data=parsed,
        supplier=(parsed.get("supplier") or None),
        total_amount=_dec(parsed.get("total_amount")) or None,
        occurred_at=occurred_at,
    )
    session.add(receipt)
    await session.flush()

    stock_effects: list[dict] = []
    for entry in parsed.get("items", []):
        name = (entry.get("name") or "").strip()
        if not name:
            continue
        qty = _dec(entry.get("quantity"))
        unit = (entry.get("unit") or "pcs").strip()
        unit_price = _dec(entry.get("unit_price"))

        match = (
            await session.execute(
                select(Item).where(Item.name.ilike(f"%{name}%")).order_by(Item.name).limit(1)
            )
        ).scalar_one_or_none()

        if match is None:
            item = Item(
                business_id=business.id,
                name=name,
                unit=unit,
                current_stock=qty,
                cost_price=unit_price,
            )
            session.add(item)
            stock_effects.append({"item": name, "action": "created", "stock": float(qty), "unit": unit})
        elif doc_type == "stock_ledger":
            old = match.current_stock
            match.current_stock = qty
            match.updated_at = datetime.now(timezone.utc)
            stock_effects.append(
                {"item": match.name, "action": "set", "old": float(old), "stock": float(qty), "unit": match.unit}
            )
        else:
            match.current_stock = match.current_stock + qty
            match.updated_at = datetime.now(timezone.utc)
            if unit_price > 0:
                match.cost_price = unit_price
            stock_effects.append(
                {"item": match.name, "action": "added", "added": float(qty), "stock": float(match.current_stock), "unit": match.unit}
            )

    expense_amount = _dec(parsed.get("total_amount"))
    if doc_type == "receipt" and expense_amount > 0:
        session.add(
            Expense(
                business_id=business.id,
                amount=expense_amount,
                category="bahan baku",
                description=f"Nota {parsed.get('supplier') or 'pembelian'}",
                source="receipt",
                receipt_id=receipt.id,
                occurred_at=occurred_at,
            )
        )

    await session.flush()

    facts = {
        "saved": True,
        "document_type": doc_type,
        "supplier": parsed.get("supplier"),
        "total_amount": float(expense_amount) if expense_amount else None,
        "expense_recorded": doc_type == "receipt" and expense_amount > 0,
        "stock_effects": stock_effects,
        "receipt_id": str(receipt.id),
    }

    # Embed for RAG (Phase 4). Import here so a Gemini outage can't break the
    # commit itself — embedding failures degrade search, not data.
    try:
        from app.services.rag import embed_receipt

        await embed_receipt(session, receipt)
    except Exception:
        logger.exception("Embedding failed for receipt %s (non-fatal)", receipt.id)

    return facts


# ── pending confirmation lifecycle ───────────────────────────────────────────


async def create_pending(
    session: AsyncSession, business: Business, parsed: dict, image_path: str
) -> PendingConfirmation:
    # One pending at a time per business: a new photo supersedes the old wait.
    await session.execute(
        delete(PendingConfirmation).where(PendingConfirmation.business_id == business.id)
    )
    pending = PendingConfirmation(
        business_id=business.id,
        kind="stock_import" if parsed.get("document_type") == "stock_ledger" else "receipt",
        payload={"parsed": parsed, "image_path": image_path},
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=PENDING_TTL_MINUTES),
    )
    session.add(pending)
    await session.flush()
    return pending


async def get_active_pending(
    session: AsyncSession, business_id: uuid.UUID
) -> PendingConfirmation | None:
    pending = (
        await session.execute(
            select(PendingConfirmation)
            .where(PendingConfirmation.business_id == business_id)
            .order_by(PendingConfirmation.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if pending and pending.expires_at < datetime.now(timezone.utc):
        await session.delete(pending)
        await session.flush()
        return None
    return pending


async def discard_pending(session: AsyncSession, pending: PendingConfirmation) -> None:
    await session.delete(pending)
    await session.flush()
