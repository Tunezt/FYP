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
                    "unit": {"type": "string", "description": "kg, pcs, liter, dus, …"},
                    "unit_price": {"type": "number", "description": "0 if not shown"},
                    "line_total": {"type": "number", "description": "0 if not shown"},
                },
                "required": ["name", "quantity", "unit"],
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
- confidence=high is a promise that nothing needs the owner's double-checking.
  When in ANY doubt, do not claim high.
- If the photo is not a business document at all, use document_type=other with
  an empty items list."""


async def parse_business_document(image_bytes: bytes, mime_type: str) -> dict:
    parsed = await generate_json_from_image(
        system=_SYSTEM,
        prompt="Extract this document.",
        image_bytes=image_bytes,
        mime_type=mime_type,
        response_schema=RECEIPT_SCHEMA,
    )
    parsed.setdefault("items", [])
    parsed.setdefault("ambiguities", [])
    parsed.setdefault("confidence", "low")
    parsed.setdefault("document_type", "other")
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
        return {
            "unrelated": bool(data.get("unrelated", False)),
            "parsed": data.get("parsed", parsed),
        }
    except json.JSONDecodeError:
        return {"unrelated": True, "parsed": parsed}
