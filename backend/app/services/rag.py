"""RAG over receipt history — gemini-embedding-001 (768 dims) + pgvector cosine
search on receipts.embedding, always inside the tenant-scoped session.

DEVIATION (flagged, see docs/progress.md Phase 4): LangChain's
SupabaseVectorStore was evaluated and rejected — it retrieves through a
PostgREST RPC using the service-role key, which would bypass the per-request
`SET LOCAL app.current_business_id` RLS pattern, and it expects a
documents-shaped table + match function that conflicts with the locked
`receipts` schema. pgvector is used exactly as specified; retrieved chunks are
returned as langchain_core Documents so the orchestration layer stays on
LangChain abstractions.
"""
import logging
import re
import unicodedata
import uuid
from difflib import SequenceMatcher
from datetime import datetime

from langchain_core.documents import Document
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gemini import embed_text
from app.models import Receipt

logger = logging.getLogger("rag")

TOP_K = 5
# Cosine distance beyond this is noise, not a match (distance = 1 - similarity).
# Chosen by the sweep in docs/evaluation.md (`python -m app.eval.rag --sweep`); `verify` below does the rejecting.
MAX_DISTANCE = 0.45


# -- Verifying a match (fin-rag-judge) ---------------------------------------
# A vector search always returns its nearest receipts, even when the thing asked
# about was never bought. Each candidate within the cut-off is verified:
#   1. fast accept: every key word of the question (item or supplier) occurs in
#      the receipt text -> accepted, no model call;
#   2. the rest go to ONE cheap model call that answers yes/no per receipt
#      ("does this receipt answer the question?").
# The word match alone was the whole guard until fin-rag-judge: 100% on the
# questions it was written for, 0% recall on a blind set (docs/evaluation.md),
# because real phrasing carries dates, amounts and English no receipt contains.
_STOP = set("""
pernah beli membeli dibeli ngebeli belanja bought buy ever did have has had we i you my our kita saya aku
kapan terakhir last when biasanya usually mana dimana di dari ke ada ga gak nggak tidak apa apakah yang dan atau
aja saja ya sih dong brp berapa harga price much how many supplier suppliers nota struk receipt bon order pesan
toko pt cv ud before from the a an of for to is are was were do does sudah udah belum lagi itu ini with any
where what which who bisa tolong cek lihat tampilkan show find cari history riwayat
""".split())
# English and category words the owner may use for what is written in Indonesian on the receipt.
_SYNONYMS = {
    "cheese": ["keju"], "milk": ["susu"], "sugar": ["gula"], "egg": ["telur"], "eggs": ["telur"],
    "butter": ["mentega"], "coffee": ["kopi"], "flour": ["tepung"], "cream": ["krimer"],
    "cup": ["gelas"], "cups": ["gelas"], "straw": ["sedotan"], "straws": ["sedotan"],
    "sayur": ["sayuran", "selada", "tomat", "wortel", "bayam", "kol"],
    "vegetable": ["sayur", "selada", "tomat"], "vegetables": ["sayur", "selada", "tomat"],
    "tomato": ["tomat"], "lettuce": ["selada"],
}


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def key_words(query: str) -> list[str]:
    """The words of a question that name the thing asked about (stop words dropped)."""
    seen: list[str] = []
    for w in re.findall(r"[a-z0-9]+", _fold(query)):
        if w not in _STOP and len(w) >= 3 and w not in seen:
            seen.append(w)
    return seen


def _close(a: str, b: str) -> bool:
    if a == b:
        return True
    if min(len(a), len(b)) < 4:
        return False
    if (a.startswith(b) or b.startswith(a)) and abs(len(a) - len(b)) <= 3:  # sayur / sayuran
        return True
    return SequenceMatcher(None, a, b).ratio() >= 0.85  # one-letter slips: arabika / arabica


def mentions(text: str, words: list[str]) -> bool:
    """True if every key word (or a synonym, or a close spelling) occurs in `text`.
    No key words (a question that names nothing) = nothing to check = True.
    shortcut: ALL words must match, so a half-right name ("Bangunan Jaya" vs "Sinar Jaya")
    is rejected; the synonym list is tiny, widen it if real questions miss."""
    have = re.findall(r"[a-z0-9]+", _fold(text))
    return all(any(_close(alt, h) for alt in [w, *_SYNONYMS.get(w, [])] for h in have) for w in words)


JUDGE_MODEL = "gemini-3.5-flash-lite"  # a yes/no needs no big model; the chain stays behind it
_JUDGE_SYSTEM = (
    "You check search results for a cafe owner's question about past purchases. For each numbered receipt, "
    "answer true only if that receipt is a purchase the question asks about: the item (in any language, "
    "spelling or category word, e.g. 'palm sugar' = gula aren, 'telor' = telur, 'sayur' covers selada) and "
    "EVERY other detail the question states (supplier name, date, amount, variety, quantity) must match the "
    "receipt. A different supplier, a similar but different item or variety (gula batu is not gula pasir, oat "
    "milk is not susu UHT), or a supplier name that only looks alike is false. Filler words, slang and "
    "politeness do not matter. Answer for every receipt, in order."
)
_JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"answers": {"type": "array", "items": {"type": "boolean"}}},
    "required": ["answers"],
}


class ReceiptCheckFailed(Exception):
    """The judge could not answer. Nothing is cited, and the owner is told the
    receipts could not be checked: never a silent "no receipt found"."""


async def judge(query: str, texts: list[str]) -> list[bool]:
    """One model call: does each receipt answer the question? A network error or
    a malformed answer raises ReceiptCheckFailed; QuotaExhausted passes through
    (the WhatsApp layer already says plainly that the AI quota is out)."""
    from app.ai.gemini import QuotaExhausted, generate_json

    listing = "\n\n".join(f"Receipt {i + 1}:\n{t}" for i, t in enumerate(texts))
    try:
        out = await generate_json(
            system=_JUDGE_SYSTEM, message=f"Question: {query}\n\n{listing}",
            response_schema=_JUDGE_SCHEMA, first_model=JUDGE_MODEL,
        )
    except QuotaExhausted:
        raise
    except Exception as exc:  # noqa: BLE001 - 503s past the chain, network, bad JSON
        logger.warning("rag judge failed: %s", exc, exc_info=True)
        raise ReceiptCheckFailed(str(exc)) from exc
    answers = out.get("answers") if isinstance(out, dict) else None
    if not isinstance(answers, list) or len(answers) != len(texts):
        logger.warning("rag judge gave %r for %d receipts", answers, len(texts))
        raise ReceiptCheckFailed(f"malformed answer {answers!r}")
    return [a is True for a in answers]


async def verify(query: str, texts: list[str]) -> list[bool]:
    """Which candidate receipts answer `query`: word match first, the judge for
    the rest (one call, none when everything is word-matched or nothing is left)."""
    words = key_words(query)
    fast = [bool(words) and mentions(t, words) for t in texts]
    rest = [i for i, ok in enumerate(fast) if not ok]
    if rest:
        for i, ok in zip(rest, await judge(query, [texts[i] for i in rest])):
            fast[i] = ok
    return fast


def receipt_to_document_text(receipt: Receipt) -> str:
    """Deterministic textual rendering of a receipt for embedding — supplier,
    date, and every line item, so item- and supplier-level questions both hit."""
    parsed = receipt.parsed_data or {}
    parts: list[str] = []
    doc_type = parsed.get("document_type", "receipt")
    parts.append("Nota pembelian" if doc_type == "receipt" else "Catatan stok")
    if receipt.supplier:
        parts.append(f"dari {receipt.supplier}")
    occurred = receipt.occurred_at
    if isinstance(occurred, datetime):
        parts.append(f"tanggal {occurred.date().isoformat()}")
    lines = [" ".join(parts) + "."]
    for item in parsed.get("items", []):
        name = item.get("name", "")
        qty = item.get("quantity", "")
        unit = item.get("unit", "")
        price = item.get("line_total") or item.get("unit_price") or 0
        line = f"{qty} {unit} {name}"
        if price:
            line += f" Rp {price}"
        lines.append(line)
    if receipt.total_amount:
        lines.append(f"Total Rp {receipt.total_amount}")
    return "\n".join(lines)


async def embed_receipt(session: AsyncSession, receipt: Receipt) -> None:
    """Upsert the embedding for a committed receipt. Failures are the caller's
    to swallow — embedding must never block a data write."""
    text = receipt_to_document_text(receipt)
    receipt.embedding = await embed_text(text)
    await session.flush()


async def search_receipts(
    session: AsyncSession, business_id: uuid.UUID, query: str, k: int = TOP_K,
    max_distance: float | None = None, lexical: bool = True,
) -> list[Document]:
    """Top-k receipt matches for a natural-language question, as LangChain
    Documents (page_content = the embedded text, metadata = provenance)."""
    cutoff = MAX_DISTANCE if max_distance is None else max_distance
    query_vec = await embed_text(query)
    distance = Receipt.embedding.cosine_distance(query_vec).label("distance")
    rows = (
        await session.execute(
            select(Receipt, distance)
            .where(Receipt.business_id == business_id, Receipt.embedding.is_not(None))
            .order_by(distance)
            .limit(k)
        )
    ).all()

    candidates = [(r, d, receipt_to_document_text(r)) for r, d in rows if d is None or float(d) <= cutoff]
    keep = await verify(query, [t for _r, _d, t in candidates]) if lexical else [True] * len(candidates)
    documents: list[Document] = []
    for (receipt, dist, text), ok in zip(candidates, keep):
        if not ok:
            continue
        documents.append(
            Document(
                page_content=text,
                metadata={
                    "receipt_id": str(receipt.id),
                    "supplier": receipt.supplier,
                    "occurred_at": receipt.occurred_at.isoformat() if receipt.occurred_at else None,
                    "total_amount": float(receipt.total_amount) if receipt.total_amount else None,
                    "distance": float(dist) if dist is not None else None,
                },
            )
        )
    return documents
