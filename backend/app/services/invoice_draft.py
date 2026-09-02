"""Supplier invoice photo → draft goods receipt (roadmap M5-T4).

Sits on the existing vision path: the photo is parsed (app/ai/vision.py), then
this module turns the parse into a DRAFT — supplier matched by name, every line
matched to an existing item — that is parked in `pending_confirmations` and
described to the owner. Nothing touches stock until the owner replies YA; then
the matched lines become one goods receipt (M5-T3: stock in, `purchase` ledger
rows, moving-average cost, supplier history).

Rules that are the whole point:
  * Never write stock from a photo without confirmation — a draft is always
    parked, whatever the model's confidence.
  * Unmatched lines are surfaced as QUESTIONS, never guesses: they are listed
    with any near-miss candidates and are skipped on confirmation. A correction
    reply goes through the revision flow and the draft is rebuilt.
  * A unit the photo names that cannot be converted into the item's unit is also
    a question, not a silent "close enough".
"""
from __future__ import annotations

import difflib
import uuid
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Business, Item
from app.services import units
from app.services.suppliers import supplier_by_name

MATCH_THRESHOLD = 0.72
AMBIGUITY_GAP = 0.08


def _dec(value) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(0)


def _norm(s) -> str:
    return " ".join(str(s or "").lower().replace(".", " ").split())


def _score(read: str, name: str) -> float:
    r, n = _norm(read), _norm(name)
    if not r or not n:
        return 0.0
    if r == n:
        return 1.0
    if r in n or n in r:
        return 0.9
    return difflib.SequenceMatcher(None, r, n).ratio()


async def match_item(session: AsyncSession, read_name: str, items: list[Item] | None = None) -> tuple[Item | None, list[str]]:
    """Best matching item, or None plus the names of near misses (candidates)."""
    if items is None:
        items = (await session.execute(select(Item).order_by(Item.name))).scalars().all()
    scored = sorted(((_score(read_name, i.name), i) for i in items), key=lambda t: t[0], reverse=True)
    if not scored or scored[0][0] < MATCH_THRESHOLD:
        return None, [i.name for s, i in scored[:3] if s >= 0.5]
    best_score, best = scored[0]
    if best_score < 1.0 and len(scored) > 1 and scored[1][0] >= MATCH_THRESHOLD and best_score - scored[1][0] < AMBIGUITY_GAP:
        return None, [i.name for s, i in scored[:3] if s >= MATCH_THRESHOLD]  # ambiguous: ask
    return best, []


async def _resolve_unit(session: AsyncSession, item: Item, unit_text: str) -> tuple[uuid.UUID | None, str | None]:
    """(uom_id to receive in, problem). uom_id None = the item's own unit."""
    text = (unit_text or "").strip()
    if not text or _norm(text) == _norm(item.unit):
        return None, None
    uom = await units.uom_by_code(session, text)
    if uom is None:
        return None, f"satuan '{text}' tidak dikenal untuk {item.name} (stok dalam {item.unit})"
    if item.uom_id is None or uom.id == item.uom_id:
        if item.uom_id is None and _norm(text) != _norm(item.unit):
            return None, f"satuan '{text}' beda dengan satuan stok {item.name} ({item.unit}) dan barangnya belum punya satuan resmi"
        return None, None
    try:
        await units.convert_quantity(session, Decimal(1), uom.id, item.uom_id)
    except units.UnitConversionMissing:
        return None, f"tidak ada konversi {uom.code} → {item.unit} untuk {item.name}"
    return uom.id, None


async def build_draft(session: AsyncSession, business: Business, parsed: dict) -> dict:
    """Match a parsed invoice to the catalogue. Pure read; writes nothing."""
    supplier_text = (parsed.get("supplier") or "").strip()
    supplier = await supplier_by_name(session, supplier_text, active_only=True) if supplier_text else None
    items = (await session.execute(select(Item).order_by(Item.name))).scalars().all()

    matched: list[dict] = []
    questions: list[dict] = []
    for entry in parsed.get("items", []) or []:
        read_name = str(entry.get("name") or "").strip()
        quantity = _dec(entry.get("quantity"))
        unit_text = str(entry.get("unit") or "").strip()
        unit_price = _dec(entry.get("unit_price"))
        line_total = _dec(entry.get("line_total"))
        if unit_price <= 0 and line_total > 0 and quantity > 0:
            unit_price = (line_total / quantity).quantize(Decimal("0.01"))
        base = {"name_read": read_name, "quantity": str(quantity), "unit_read": unit_text,
                "unit_price": str(unit_price), "line_total": str(line_total)}
        if not read_name or quantity <= 0:
            questions.append({**base, "reason": "nama atau jumlah tidak terbaca", "candidates": []})
            continue
        item, candidates = await match_item(session, read_name, items)
        if item is None:
            reason = "mirip dengan: " + ", ".join(candidates) if candidates else "belum ada di daftar barang"
            questions.append({**base, "reason": reason, "candidates": candidates})
            continue
        uom_id, problem = await _resolve_unit(session, item, unit_text)
        if problem:
            questions.append({**base, "reason": problem, "candidates": [item.name], "item_id": str(item.id)})
            continue
        matched.append({**base, "item_id": str(item.id), "item_name": item.name, "item_unit": item.unit,
                        "uom_id": str(uom_id) if uom_id else None})

    return {
        "kind": "goods_receipt_draft",
        "supplier_text": supplier_text,
        "supplier_id": str(supplier.id) if supplier else None,
        "supplier_name": supplier.name if supplier else None,
        "date": parsed.get("date") or "",
        "total_amount": str(_dec(parsed.get("total_amount"))),
        "matched": matched,
        "questions": questions,
        "confidence": parsed.get("confidence"),
        "ambiguities": list(parsed.get("ambiguities") or []),
    }


def _rp(value) -> str:
    return f"Rp {Decimal(str(value)):,.0f}".replace(",", ".")


def draft_summary(draft: dict) -> str:
    """What the owner sees before replying YA. Every question is explicit."""
    lines: list[str] = []
    for m in draft["matched"]:
        unit = m["unit_read"] or m["item_unit"]
        arrow = "" if _norm(m["name_read"]) == _norm(m["item_name"]) else f" → {m['item_name']}"
        money = f" — {_rp(m['line_total'])}" if Decimal(m["line_total"]) > 0 else ""
        lines.append(f"✅ {Decimal(m['quantity']):g} {unit} {m['name_read']}{arrow}{money}")
    for q in draft["questions"]:
        unit = q["unit_read"]
        lines.append(f"❓ {Decimal(q['quantity']):g} {unit} {q['name_read']} — {q['reason']}. Akan dilewati kecuali kamu koreksi.")
    if draft["supplier_name"]:
        lines.append(f"Supplier: {draft['supplier_name']}")
    elif draft["supplier_text"]:
        lines.append(f"Supplier: {draft['supplier_text']} (belum ada di daftar supplier — penerimaan tetap dicatat tanpa supplier)")
    if Decimal(draft["total_amount"]) > 0:
        lines.append(f"Total: {_rp(draft['total_amount'])}")
    for a in draft.get("ambiguities") or []:
        lines.append(f"⚠️ {a}")
    n = len(draft["matched"])
    tail = (
        f"Balas *YA* untuk mencatat penerimaan {n} baris barang (stok bertambah), atau tulis koreksinya."
        if n else "Belum ada baris yang bisa dicatat — koreksi nama barangnya, atau balas *TIDAK* untuk batal."
    )
    return "\n".join(lines) + "\n\n" + tail


async def confirm_draft(
    session: AsyncSession, business: Business, draft: dict, image_path: str, parsed: dict,
    received_by: uuid.UUID | None = None,
) -> dict:
    """YA: one goods receipt for the matched lines (stock, ledger, cost,
    supplier history), the photo kept as a receipt row, the purchase recorded as
    an expense as before. Questions are skipped, and said so."""
    from datetime import datetime, timezone

    from app.models import Expense, Receipt
    from app.services.receipts import _parse_date
    from app.services.receiving import GrLineSpec, receive_goods

    occurred_at = _parse_date(parsed.get("date")) or datetime.now(timezone.utc)
    supplier_id = uuid.UUID(draft["supplier_id"]) if draft.get("supplier_id") else None
    receipt = Receipt(
        business_id=business.id, image_url=image_path, parsed_data={**parsed, "draft": draft},
        supplier=(draft.get("supplier_text") or None), supplier_id=supplier_id,
        total_amount=_dec(draft.get("total_amount")) or None, occurred_at=occurred_at,
    )
    session.add(receipt)
    await session.flush()

    facts: dict = {"saved": True, "document_type": "receipt", "receipt_id": str(receipt.id),
                   "supplier": draft.get("supplier_name") or draft.get("supplier_text"),
                   "skipped": [q["name_read"] for q in draft["questions"]], "stock_effects": []}
    if draft["matched"]:
        received = await receive_goods(
            session, business.id, supplier_id=supplier_id, received_by=received_by,
            notes=f"dari foto nota {image_path}", received_at=occurred_at,
            lines=[
                GrLineSpec(item_id=uuid.UUID(m["item_id"]), quantity=Decimal(m["quantity"]),
                           unit_cost=Decimal(m["unit_price"]), uom_id=uuid.UUID(m["uom_id"]) if m.get("uom_id") else None)
                for m in draft["matched"]
            ],
        )
        facts["goods_receipt_id"] = str(received.receipt.id)
        facts["goods_receipt_number"] = received.receipt.number
        for line, m in zip(received.lines, draft["matched"]):
            item = await session.get(Item, line.item_id)
            facts["stock_effects"].append({
                "item": m["item_name"], "action": "added", "added": float(line.quantity_item_unit),
                "stock": float(item.current_stock) if item else None, "unit": m["item_unit"],
                "unit_cost": float(line.unit_cost_item_unit),
            })

    total = _dec(draft.get("total_amount"))
    if total > 0:
        session.add(Expense(
            business_id=business.id, amount=total, category="bahan baku",
            description=f"Nota {draft.get('supplier_name') or draft.get('supplier_text') or 'pembelian'}",
            source="receipt", receipt_id=receipt.id, occurred_at=occurred_at,
        ))
        facts["expense_recorded"] = True
        facts["total_amount"] = float(total)
    await session.flush()

    try:
        from app.services.rag import embed_receipt

        await embed_receipt(session, receipt)
    except Exception:  # embedding degrades search, never the commit
        import logging

        logging.getLogger("invoice_draft").exception("Embedding failed for receipt %s (non-fatal)", receipt.id)
    return facts
