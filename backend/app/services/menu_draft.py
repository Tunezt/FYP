"""Menu photo → draft catalogue (roadmap M5-T5).

The same machinery as the invoice draft (M5-T4), pointed at onboarding: the
owner photographs the menu board, the model reads products, sizes and prices
(app/ai/vision.py::parse_menu_photo), this module turns that into a reviewable
draft parked in `pending_confirmations`, and one YA creates the items and their
variants. Products that already exist are reported as "sudah ada" and left
alone — a photo never silently reprices the catalogue. Unreadable prices are
questions, never guesses (the item is still created at price 0 only if the
owner confirms, and the summary says so).
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Business, Item
from app.services import catalog
from app.services.invoice_draft import _norm, match_item

DEFAULT_UNIT = "porsi"


def _dec(value) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(0)


def _rp(value) -> str:
    return f"Rp {Decimal(str(value)):,.0f}".replace(",", ".")


async def build_menu_draft(session: AsyncSession, business: Business, parsed: dict) -> dict:
    items = (await session.execute(select(Item).order_by(Item.name))).scalars().all()
    new_products: list[dict] = []
    existing: list[dict] = []
    questions: list[dict] = []
    for product in parsed.get("products", []) or []:
        name = str(product.get("name") or "").strip()
        if not name:
            continue
        variants = []
        for v in product.get("variants") or []:
            vname = str(v.get("name") or "Standar").strip() or "Standar"
            price = _dec(v.get("price")) if v.get("price_written", True) else Decimal(0)
            variants.append({"name": vname, "price": str(price), "price_written": bool(v.get("price_written", True)) and price > 0})
        if not variants:
            variants = [{"name": "Standar", "price": "0", "price_written": False}]
        match, _ = await match_item(session, name, items)
        entry = {"name": name, "category": str(product.get("category") or "").strip(), "variants": variants}
        if match is not None and _norm(match.name) == _norm(name):
            existing.append({**entry, "item_id": str(match.id), "item_name": match.name})
            continue
        unpriced = [v["name"] for v in variants if not v["price_written"]]
        if unpriced:
            questions.append({**entry, "reason": "harga tidak terbaca untuk: " + ", ".join(unpriced)})
        new_products.append(entry)
    return {
        "kind": "menu_draft",
        "new_products": new_products,
        "existing": existing,
        "questions": questions,
        "unit": DEFAULT_UNIT,
        "confidence": parsed.get("confidence"),
        "ambiguities": list(parsed.get("ambiguities") or []),
    }


def menu_draft_summary(draft: dict) -> str:
    lines: list[str] = []
    for p in draft["new_products"]:
        if len(p["variants"]) == 1:
            v = p["variants"][0]
            price = _rp(v["price"]) if v["price_written"] else "harga ❓"
            lines.append(f"🆕 {p['name']} — {price}")
        else:
            sizes = ", ".join(f"{v['name']} {_rp(v['price']) if v['price_written'] else '❓'}" for v in p["variants"])
            lines.append(f"🆕 {p['name']} — {sizes}")
    for e in draft["existing"]:
        lines.append(f"↩️ {e['name']} — sudah ada di daftar barang, tidak diubah")
    for q in draft["questions"]:
        lines.append(f"❓ {q['name']}: {q['reason']} — akan dibuat dengan harga 0 kalau kamu balas YA, atau tulis harganya")
    for a in draft.get("ambiguities") or []:
        lines.append(f"⚠️ {a}")
    n = len(draft["new_products"])
    if n:
        tail = (
            f"Balas *YA* untuk membuat {n} barang baru (satuan '{draft['unit']}', stok 0 — isi stoknya nanti), "
            "atau tulis koreksinya."
        )
    else:
        tail = "Semua produk di foto ini sudah ada di daftar barang — tidak ada yang perlu dibuat."
    return "\n".join(lines) + ("\n\n" if lines else "") + tail


async def confirm_menu_draft(session: AsyncSession, business: Business, draft: dict) -> dict:
    """YA: create each new product as an item (stock 0, unit `porsi`) with its
    default variant at the first price and one variant per extra size."""
    created: list[dict] = []
    for p in draft["new_products"]:
        first = p["variants"][0]
        item = Item(
            business_id=business.id, name=p["name"], unit=draft.get("unit") or DEFAULT_UNIT,
            current_stock=Decimal(0), cost_price=Decimal(0), sell_price=Decimal(first["price"]),
        )
        session.add(item)
        await session.flush()
        default = await catalog.ensure_default_variant(session, item)
        if _norm(first["name"]) not in ("standar", ""):
            await catalog.update_variant(session, default, item, name=first["name"])
        variant_names = [first["name"]]
        for v in p["variants"][1:]:
            try:
                await catalog.create_variant(session, item, name=v["name"], sell_price=Decimal(v["price"]))
                variant_names.append(v["name"])
            except catalog.VariantInvalid:
                continue  # duplicate size name on the board: keep the first
        created.append({"item": item.name, "item_id": str(item.id), "variants": variant_names,
                        "price": float(first["price"])})
    return {
        "saved": True, "created": created, "created_count": len(created),
        "existing_skipped": [e["item_name"] for e in draft["existing"]],
        "unpriced": [q["name"] for q in draft["questions"]],
    }
