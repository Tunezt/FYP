"""Receipt / stock-ledger photo parsing — Gemini Pro with structured output.

Handwritten Bahasa Indonesia/Malay stock books are the single biggest technical
risk in this project, so the schema demands a confidence signal + explicit
ambiguity list, and app/services/receipts.py gates low-confidence extractions
behind a WhatsApp confirmation instead of writing them.
"""
from app.ai.gemini import generate_json_from_image, generate_text

RECEIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "document_type": {
            "type": "string",
            "enum": ["receipt", "stock_ledger", "other"],
            "description": "receipt = purchase receipt/invoice/nota; stock_ledger = handwritten stock book / inventory list",
        },
        "supplier": {"type": "string", "description": "Store/supplier name if visible, else empty"},
        "date": {"type": "string", "description": "Document date as YYYY-MM-DD if readable, else empty"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "quantity": {"type": "number"},
                    "unit": {
                        "type": "string",
                        "description": "ONLY the unit written next to the quantity (kg, pcs, liter, dus, bungkus, …). Empty string if none is written.",
                    },
                    "unit_written": {
                        "type": "boolean",
                        "description": "true only if a unit is physically written on this line",
                    },
                    "unit_price": {
                        "type": "number",
                        "description": "Price per unit. Copy it if shown; if only a line total is shown you may divide line_total by quantity, but then unit_price_written must be false. 0 if neither is shown.",
                    },
                    "unit_price_written": {
                        "type": "boolean",
                        "description": "true only if a per-unit price is physically written on this line (e.g. '@12.500' or '2 x 12.500')",
                    },
                    "line_total": {"type": "number", "description": "0 if not shown"},
                },
                "required": ["name", "quantity", "unit", "unit_written", "unit_price", "unit_price_written"],
            },
        },
        "total_amount": {"type": "number", "description": "Grand total in rupiah; 0 if not shown"},
        "confidence": {
            "type": "string",
            "enum": ["high", "medium", "low"],
            "description": "high ONLY if every item name, quantity and unit is clearly legible and unambiguous",
        },
        "ambiguities": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Every uncertain reading, e.g. 'quantity for gula could be 2 or 7'",
        },
    },
    "required": ["document_type", "items", "confidence", "ambiguities"],
}

_SYSTEM = """You extract structured data from photos of Indonesian/Malaysian
small-business documents: printed receipts, invoices (nota/faktur), and
handwritten stock books (buku stok).

Rules:
- Transcribe item names as written (Indonesian/Malay/English) — do not translate.
- Numbers: Indonesian formats use . as thousands separator (12.500 = 12500).
  'rb'/'ribu' = ×1000, 'jt'/'juta' = ×1000000.
- Handwriting is often messy. If a quantity, unit, or name could plausibly read
  two ways, list it in ambiguities and set confidence to medium or low.
- Units: copy ONLY a unit that is written next to the quantity, and set
  unit_written=true. If no unit is written, leave unit empty and set
  unit_written=false. NEVER infer a unit from the product name: "Minyak goreng
  2L" with quantity 1 is 1 item, not 2 liters and not 1 liter.
- Unit prices: if a per-unit price is written (e.g. "@12.500" or "2 x 12.500"),
  copy it and set unit_price_written=true. If only a line total is written, you
  may set unit_price = line_total ÷ quantity but MUST set
  unit_price_written=false. Never invent a price that cannot be derived.
- confidence=high is a promise that nothing needs the owner's double-checking.
  When in ANY doubt, do not claim high.
- If the photo is not a business document at all, use document_type=other with
  an empty items list."""

# Server-side guards (M1-T3). The model's self-reported confidence missed every
# fabricated field in the M1-T2 baseline, so what is stored is decided here:
#   * a unit the model did not see written is dropped (commit falls back to "pcs")
#   * a unit price with no written basis and no line total to derive it from is zeroed
#   * arithmetic the document contradicts becomes an explicit ambiguity, which the
#     confirmation gate (app/services/receipts.py) turns into a question to the owner
_TOLERANCE = 1  # rupiah; handwritten totals are whole numbers


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def normalize_parse(parsed: dict) -> dict:
    """Apply the guards above in place and return the parse. Idempotent, and
    tolerant of parses produced before the *_written flags existed (a pending
    confirmation payload, a revision): a missing flag means 'written'."""
    parsed.setdefault("items", [])
    parsed.setdefault("ambiguities", [])
    parsed.setdefault("confidence", "low")
    parsed.setdefault("document_type", "other")
    ambiguities: list[str] = list(parsed["ambiguities"])

    lines_sum = 0.0
    any_line_total = False
    for item in parsed["items"]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip() or "?"
        qty = _num(item.get("quantity"))
        line_total = _num(item.get("line_total"))
        unit_price = _num(item.get("unit_price"))

        if item.get("unit_written") is False:
            item["unit"] = ""
        if item.get("unit_price_written") is False and unit_price > 0 and line_total <= 0:
            # Nothing on the page to derive it from — a guess. Do not store it.
            item["unit_price"] = 0
            unit_price = 0.0
        if unit_price > 0 and line_total > 0 and qty > 0 and abs(unit_price * qty - line_total) > _TOLERANCE:
            ambiguities.append(
                f"Baris '{name}': {qty:g} × {unit_price:,.0f} tidak sama dengan total baris {line_total:,.0f}"
            )
        if line_total > 0:
            any_line_total = True
            lines_sum += line_total

    total = _num(parsed.get("total_amount"))
    if total > 0 and any_line_total and abs(lines_sum - total) > _TOLERANCE:
        ambiguities.append(
            f"Jumlah semua baris ({lines_sum:,.0f}) tidak sama dengan total yang tertulis ({total:,.0f})"
        )

    # Dedupe while keeping order (idempotence on re-normalisation).
    seen: set[str] = set()
    parsed["ambiguities"] = [a for a in ambiguities if not (a in seen or seen.add(a))]
    return parsed


async def parse_business_document(image_bytes: bytes, mime_type: str) -> dict:
    parsed = await generate_json_from_image(
        system=_SYSTEM,
        prompt="Extract this document.",
        image_bytes=image_bytes,
        mime_type=mime_type,
        response_schema=RECEIPT_SCHEMA,
    )
    return normalize_parse(parsed)


# ── Menu photo → draft catalogue (roadmap M5-T5) ─────────────────────────────

MENU_SCHEMA = {
    "type": "object",
    "properties": {
        "is_menu": {"type": "boolean", "description": "true if the photo is a menu / price list of products for sale"},
        "products": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Product name as written"},
                    "category": {"type": "string", "description": "Section heading it appears under, if any; else empty"},
                    "variants": {
                        "type": "array",
                        "description": "Every size/option with its own price. A product with one price has one variant named 'Standar'.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "Size/option name as written, or 'Standar'"},
                                "price": {"type": "number", "description": "Price in rupiah; 0 if not shown"},
                                "price_written": {"type": "boolean"},
                            },
                            "required": ["name", "price", "price_written"],
                        },
                    },
                },
                "required": ["name", "variants"],
            },
        },
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "ambiguities": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["is_menu", "products", "confidence", "ambiguities"],
}

_MENU_SYSTEM = """You extract a menu or price list from a photo taken in an
Indonesian/Malaysian small F&B business (café, warung, stall).

Rules:
- Transcribe product and size names as written — do not translate or tidy.
- Numbers: Indonesian formats use . as thousands separator (12.500 = 12500);
  'k' or 'rb' after a number means ×1000 (15k = 15000).
- A product with several prices (Regular/Large, Hot/Ice, S/M/L) has one variant
  per price, named as written. A product with one price has exactly one variant
  named 'Standar'.
- If a price is not readable, set price to 0 and price_written to false — never
  guess a price.
- If the photo is not a menu at all, set is_menu=false with an empty products list.
- confidence=high only if every name and price is clearly legible; list every
  doubtful reading in ambiguities."""


async def parse_menu_photo(image_bytes: bytes, mime_type: str) -> dict:
    parsed = await generate_json_from_image(
        system=_MENU_SYSTEM,
        prompt="Extract this menu.",
        image_bytes=image_bytes,
        mime_type=mime_type,
        response_schema=MENU_SCHEMA,
    )
    parsed.setdefault("is_menu", False)
    parsed.setdefault("products", [])
    parsed.setdefault("confidence", "low")
    parsed.setdefault("ambiguities", [])
    for product in parsed["products"]:
        if not product.get("variants"):
            product["variants"] = [{"name": "Standar", "price": 0, "price_written": False}]
        for v in product["variants"]:
            if v.get("price_written") is False:
                v["price"] = 0
    return parsed


_REVISE_SYSTEM = """A parsed extraction from a photographed business document was
shown to the owner for confirmation, and the owner replied with a correction or
something else. Update the extraction according to the owner's message.

Return JSON: {"unrelated": bool, "parsed": <same schema as the extraction>}.
- If the owner's message is NOT about correcting this extraction (they're asking
  an unrelated business question), set unrelated=true and return the extraction
  unchanged.
- If it corrects the extraction, apply exactly what they said, keep everything
  else, and set confidence to "high" for the corrected fields' overall document
  ONLY if nothing ambiguous remains; list any remaining ambiguities."""


async def revise_parse(parsed: dict, owner_message: str) -> dict:
    """Returns {"unrelated": bool, "parsed": dict}."""
    import json

    reply = await generate_text(
        system=_REVISE_SYSTEM,
        message=(
            f"Current extraction:\n{json.dumps(parsed, ensure_ascii=False)}\n\n"
            f"Owner's reply:\n{owner_message}\n\nReturn only the JSON."
        ),
        temperature=0.0,
    )
    # The model sometimes wraps JSON in a code fence.
    text = reply.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        data = json.loads(text)
        revised = data.get("parsed", parsed)
        if isinstance(revised, dict):
            revised = normalize_parse(revised)
        return {
            "unrelated": bool(data.get("unrelated", False)),
            "parsed": revised,
        }
    except json.JSONDecodeError:
        return {"unrelated": True, "parsed": parsed}
