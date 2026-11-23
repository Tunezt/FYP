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
# Chosen by the sweep in docs/evaluation.md (`python -m app.eval.rag --sweep`); the lexical guard below does the rejecting.
MAX_DISTANCE = 0.45


# -- Lexical guard ----------------------------------------------------------
# A vector search always returns its nearest receipts, even when the thing asked
# about was never bought. A match is only accepted if the question's key words
# (item or supplier) really occur in that receipt's text.
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
    words = key_words(query) if lexical else []
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

    documents: list[Document] = []
    for receipt, dist in rows:
        if dist is not None and float(dist) > cutoff:
            continue
        text = receipt_to_document_text(receipt)
        if not mentions(text, words):
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
