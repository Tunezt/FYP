"""Make backend/app/seed_receipt_embeddings.json: one stored vector per demo receipt.

    cd backend && ./.venv/Scripts/python.exe ../scripts/demo_receipt_embeddings.py

Run this ONCE by hand (it calls Gemini's embedding model, one call per receipt) whenever
a nota in backend/app/seed_data.py changes. The seed itself never calls Gemini: it only
looks the vector up by the hash of the receipt text, so the demo can answer "pernah beli
... dari ...?" the moment it is seeded. A receipt with no matching vector is saved
without one and the seed says so.
"""
import asyncio
import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from app.ai.gemini import embed_text  # noqa: E402
from app.models import Receipt  # noqa: E402
from app.seed import EMBEDDINGS_FILE, nota_parsed  # noqa: E402
from app.seed_data import NOTAS  # noqa: E402
from app.services.rag import receipt_to_document_text  # noqa: E402


async def main() -> None:
    out = {}
    for nota in NOTAS:
        parsed, when, total = nota_parsed(nota)
        text = receipt_to_document_text(Receipt(parsed_data=parsed, supplier=nota[1], total_amount=total, occurred_at=when))
        out[hashlib.sha256(text.encode()).hexdigest()] = [float(x) for x in await embed_text(text)]
        print("embedded", nota[0], len(out))
        await asyncio.sleep(2)
    EMBEDDINGS_FILE.write_text(json.dumps(out), encoding="utf-8")
    print("wrote", EMBEDDINGS_FILE)


if __name__ == "__main__":
    asyncio.run(main())
