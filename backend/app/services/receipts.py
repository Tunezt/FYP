"""Receipt/stock-book photo pipeline + the confirmation gate (Section 4).

Gate rule — a correctness requirement, not polish:
  confidence == "high" AND no listed ambiguities AND at least one item
    → commit immediately
  anything else
    → park in pending_confirmations, send the owner a summary, and only
      commit after an affirmative reply. NEVER silently write a low-confidence
      parse.
"""
import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Business, Item, PendingConfirmation, Receipt
from app.services.catalog import ensure_default_variant, sync_default_from_item
from app.services.stock import add_stock, open_item_stock, set_absolute_stock

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


# p2-6: "yes, and add the new items too" for a stock-book photo with items not yet
# in the item list. A plain YA updates the existing items only.
AFFIRMATIVE_ALL = {"ya semua", "ya semuanya", "semua", "semuanya", "ya tambah semua", "tambah semua",
                   "ya tambahkan semua", "yes all", "all"}


def classify_reply_keyword(text: str) -> str:
    """'confirm' | 'confirm_all' | 'deny' | 'other' — pure keyword fast path."""
    word = " ".join(text.lower().replace(",", " ").split()).rstrip("!.…")
    if word in AFFIRMATIVE_ALL:
        return "confirm_all"
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
        lines.append(f"- {qty} {unit} {item.get('name', '?')}{price_part}")
    total = parsed.get("total_amount") or 0
    if lines and (parsed.get("supplier") or total):
        lines.append("")
    if parsed.get("supplier"):
        lines.append(f"Supplier: {parsed['supplier']}")
    if total:
        lines.append(f"*Total: Rp {total:,.0f}*".replace(",", "."))
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


async def find_item(session: AsyncSession, name: str) -> Item | None:
    """The item a name read off a photo refers to (loose: a contained name matches)."""
    return (
        await session.execute(select(Item).where(Item.name.ilike(f"%{name}%")).order_by(Item.name).limit(1))
    ).scalar_one_or_none()


async def new_ledger_items(session: AsyncSession, parsed: dict) -> list[str]:
    """Names on a stock-book photo that are not in the item list yet (p2-6)."""
    if parsed.get("document_type") != "stock_ledger":
        return []
    names = [(e.get("name") or "").strip() for e in parsed.get("items", [])]
    return [n for n in names if n and await find_item(session, n) is None]


async def commit_parse(
    session: AsyncSession, business: Business, parsed: dict, image_path: str, add_new_items: bool = False
) -> dict:
    """Write receipt (+ expense for purchases) and apply stock effects.

    receipt      → purchased quantities are ADDED to matching items' stock
                   (unmatched items are created so the purchase isn't lost)
    stock_ledger → quantities are ABSOLUTE (a stock count), set not added.
                   A name not in the item list is skipped unless the owner
                   asked for new items too (`add_new_items`, "YA SEMUA"; p2-6):
                   a random photo must not fill the catalogue.
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
    # Purchase history (M5-T1): link the photo to a known supplier by name.
    from app.services.suppliers import link_receipt

    await link_receipt(session, receipt)

    stock_effects: list[dict] = []
    skipped: list[str] = []
    capitalised = Decimal(0)  # stock taken in at a written price (M6-T6)
    for entry in parsed.get("items", []):
        name = (entry.get("name") or "").strip()
        if not name:
            continue
        qty = _dec(entry.get("quantity"))
        unit = (entry.get("unit") or "pcs").strip()
        unit_price = _dec(entry.get("unit_price"))

        match = await find_item(session, name)
        if match is None and doc_type == "stock_ledger" and not add_new_items:
            skipped.append(name)
            continue

        # Every branch below writes its stock_movements row in this same
        # transaction (M2-T2). A purchase price is the unit cost when written;
        # a stock count carries no cost — NULL, never a guess.
        known_cost = unit_price if unit_price > 0 else None
        if match is None:
            item = Item(
                business_id=business.id,
                name=name,
                unit=unit,
                current_stock=qty,
                cost_price=unit_price,
            )
            session.add(item)
            await session.flush()
            await open_item_stock(
                session, item,
                reason="opname" if doc_type == "stock_ledger" else "purchase",
                source_type="receipt", source_id=receipt.id, unit_cost=known_cost,
            )
            await ensure_default_variant(session, item)  # M4-T1
            if doc_type != "stock_ledger" and known_cost is not None:
                capitalised += qty * known_cost
            stock_effects.append({"item": name, "action": "created", "stock": float(qty), "unit": unit})
        elif doc_type == "stock_ledger":
            old = match.current_stock
            await set_absolute_stock(
                session, match, qty, reason="opname",
                source_type="receipt", source_id=receipt.id, now=datetime.now(timezone.utc),
            )
            stock_effects.append(
                {"item": match.name, "action": "set", "old": float(old), "stock": float(qty), "unit": match.unit}
            )
        else:
            # add_stock recomputes the moving-average cost when a price was
            # written (M4-T5); the default variant mirrors it (M4-T1).
            await add_stock(
                session, match, qty, reason="purchase",
                source_type="receipt", source_id=receipt.id, unit_cost=known_cost,
                now=datetime.now(timezone.utc),
            )
            if known_cost is not None:
                await sync_default_from_item(session, match)
                capitalised += qty * known_cost
            stock_effects.append(
                {"item": match.name, "action": "added", "added": float(qty), "stock": float(match.current_stock), "unit": match.unit}
            )

    expense_amount = _dec(parsed.get("total_amount"))
    if doc_type == "receipt":
        from app.services.expenses import record_expense
        from app.services.posting import post_event

        # Books (M6-T6): what went into stock at a written price is inventory
        # (GoodsReceived, as a goods receipt posts); only the remainder of the
        # total is expensed, so a purchase is never inventory and expense both.
        capitalised = capitalised.quantize(Decimal("0.01"))
        if capitalised > 0:
            await post_event(
                session, business.id, "GoodsReceived", {"inventory": capitalised},
                source_type="receipt", source_id=receipt.id,
                memo=f"nota {parsed.get('supplier') or 'pembelian'}", posted_at=occurred_at,
            )
        if expense_amount > 0:
            await record_expense(
                session, business.id, amount=expense_amount, category="bahan baku",
                description=f"Nota {parsed.get('supplier') or 'pembelian'}", source="receipt",
                receipt_id=receipt.id, occurred_at=occurred_at,
                ledger_amount=max(expense_amount - capitalised, Decimal(0)),
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
    if skipped:
        facts["not_saved_not_in_item_list"] = skipped
        facts["to_add_them"] = "kirim ulang fotonya lalu balas YA SEMUA, atau Tambah barang di halaman Stok"

    # Embed for RAG (Phase 4). Import here so a Gemini outage can't break the
    # commit itself — embedding failures degrade search, not data.
    try:
        from app.services.rag import embed_receipt

        await embed_receipt(session, receipt)
    except Exception:
        logger.exception("Embedding failed for receipt %s (non-fatal)", receipt.id)

    return facts


# ── duplicate receipt check (fin-n1-dup-receipt) ─────────────────────────────

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]


def image_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def find_duplicate_receipt(session: AsyncSession, parsed: dict) -> Receipt | None:
    """A saved receipt of this business that this photo likely repeats: the same
    image bytes, or the same supplier + date + total (a different photo of the
    same nota). Only receipts: a stock-book count is absolute, so a repeat is harmless."""
    if parsed.get("document_type", "receipt") != "receipt":
        return None
    digest = parsed.get("image_sha256")
    if digest:
        hit = (
            await session.execute(
                select(Receipt).where(Receipt.parsed_data["image_sha256"].astext == digest).limit(1)
            )
        ).scalar_one_or_none()
        if hit is not None:
            return hit
    supplier = " ".join(str(parsed.get("supplier") or "").lower().split())
    day = _parse_date(parsed.get("date"))
    total = _dec(parsed.get("total_amount"))
    if not (supplier and day and total > 0):
        return None
    rows = (
        await session.execute(
            select(Receipt).where(
                Receipt.occurred_at >= day,
                Receipt.occurred_at < day + timedelta(days=1),
                Receipt.total_amount == total,
            )
        )
    ).scalars().all()
    return next((r for r in rows if " ".join((r.supplier or "").lower().split()) == supplier), None)


def duplicate_warning(dup: Receipt) -> str:
    """Appended to the read-back, so the last thing the owner reads before replying."""
    wib = dup.occurred_at.astimezone(timezone(timedelta(hours=7)))
    who = " ".join(w.capitalize() if w.islower() else w for w in (dup.supplier or "pembelian").split())
    when = f"{wib.day} {_MONTHS[wib.month - 1]}"
    amount = f"Rp {dup.total_amount:,.0f}".replace(",", ".") if dup.total_amount else "tanpa total"
    return (
        f"⚠️ *Nota ini mirip nota {who} {when} {amount} yang sudah tersimpan.*\n"
        "Kalau dicatat lagi, pengeluaran dan stok jadi dobel.\n"
        "Balas *YA* kalau ini pembelian baru, atau *BATAL*.\n"
        f"_(This looks like the receipt already saved: {who}, {when}, {amount}. Saving it again "
        "doubles the expense and stock. Reply YA only if it is a new purchase, or BATAL.)_"
    )


async def with_duplicate_note(session: AsyncSession, parsed: dict, prompt: str) -> str:
    dup = await find_duplicate_receipt(session, parsed)
    return prompt if dup is None else f"{prompt}\n\n{duplicate_warning(dup)}"


# ── pending confirmation lifecycle ───────────────────────────────────────────


async def create_pending(
    session: AsyncSession, business: Business, parsed: dict, image_path: str,
    kind: str | None = None, extra: dict | None = None,
) -> PendingConfirmation:
    # One pending at a time per business: a new photo supersedes the old wait.
    await session.execute(
        delete(PendingConfirmation).where(PendingConfirmation.business_id == business.id)
    )
    pending = PendingConfirmation(
        business_id=business.id,
        kind=kind or ("stock_import" if parsed.get("document_type") == "stock_ledger" else "receipt"),
        payload={"parsed": parsed, "image_path": image_path, **(extra or {})},
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
