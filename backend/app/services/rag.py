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
import uuid
from datetime import datetime

from langchain_core.documents import Document
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gemini import embed_text
from app.models import Receipt

logger = logging.getLogger("rag")

TOP_K = 5
# Cosine distance beyond this is noise, not a match (distance = 1 - similarity).
MAX_DISTANCE = 0.65


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
    session: AsyncSession, business_id: uuid.UUID, query: str, k: int = TOP_K
) -> list[Document]:
    """Top-k receipt matches for a natural-language question, as LangChain
    Documents (page_content = the embedded text, metadata = provenance)."""
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
        if dist is not None and float(dist) > MAX_DISTANCE:
            continue
        documents.append(
            Document(
                page_content=receipt_to_document_text(receipt),
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
