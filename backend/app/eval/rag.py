"""M9-T7 - retrieval precision of `search_history` (the RAG path).

    cd backend && ./.venv/Scripts/python.exe -m app.eval.rag [--routing] [--no-append]

Inserts the ten receipts of `RAG_RECEIPTS` into the seeded cafe, embeds them
with the real embedding model, asks each of `RAG_QUESTIONS` through the same
`search_receipts` the bot uses, and scores what came back against the
questions' own answer sets. Everything is rolled back afterwards.

  precision   relevant results / results returned (mean over answerable questions)
  hit@1       the top result is a relevant receipt
  recall      relevant receipts returned / relevant receipts that exist
  MRR         1 / rank of the first relevant result
  false match for the unanswerable questions, any result returned at all

`--routing` adds one classifier call per question: does the model pick
`search_history`, or another tool (e.g. get_purchase_history)?
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone

from sqlalchemy import select

from app.eval.questions import RAG_QUESTIONS, RAG_RECEIPTS, RagQuestion


def score(questions: list[RagQuestion], retrieved: list[list[str]]) -> dict:
    """`retrieved[i]` = receipt keys returned for question i, best first."""
    answerable = [(q, r) for q, r in zip(questions, retrieved) if q.relevant]
    empty = [(q, r) for q, r in zip(questions, retrieved) if not q.relevant]
    prec, rec, mrr, hit1 = [], [], [], 0
    for q, r in answerable:
        good = [k for k in r if k in q.relevant]
        prec.append(len(good) / len(r) if r else 0.0)
        rec.append(len(set(good)) / len(q.relevant))
        mrr.append(next((1 / (i + 1) for i, k in enumerate(r) if k in q.relevant), 0.0))
        hit1 += bool(r and r[0] in q.relevant)
    n = len(answerable)
    return {
        "answerable": n, "unanswerable": len(empty),
        "precision": sum(prec) / n if n else None, "recall": sum(rec) / n if n else None,
        "mrr": sum(mrr) / n if n else None, "hit@1": hit1 / n if n else None,
        "false_match": sum(1 for _q, r in empty if r) / len(empty) if empty else None,
    }


def sweep(questions: list[RagQuestion], ranked: list[list[tuple[str, float, bool]]], cutoffs: list[float], lexical: bool) -> list[dict]:
    """Re-score the same retrieved (key, distance, lexical-ok) lists at each cut-off: no model calls."""
    rows = []
    for c in cutoffs:
        s = score(questions, [[k for k, d, ok in r if d <= c and (ok or not lexical)] for r in ranked])
        rows.append({"cutoff": c, **s})
    return rows


def sweep_table(alone: list[dict], guarded: list[dict]) -> str:
    pct = lambda v: "n/a" if v is None else f"{v:.0%}"  # noqa: E731
    out = ["| cut-off | alone: hit@1 | recall | false match | + lexical: hit@1 | recall | precision | false match |",
           "|---|---|---|---|---|---|---|---|"]
    for a, g in zip(alone, guarded):
        out.append(f"| {a['cutoff']:.2f} | {pct(a['hit@1'])} | {pct(a['recall'])} | {pct(a['false_match'])} | "
                   f"{pct(g['hit@1'])} | {pct(g['recall'])} | {pct(g['precision'])} | {pct(g['false_match'])} |")
    return "\n".join(out)


async def run(pace: float, routing: bool, do_sweep: bool = False) -> str:
    from app.ai import tools as tools_module
    from app.ai.gemini import force_tool_call
    from app.ai.router import CLARIFY, OUT_OF_SCOPE, _CLASSIFIER_SYSTEM
    from app.core.config import get_settings
    from app.core.db import SessionLocal, set_tenant
    from app.eval.harness import _paced
    from app.models import Business, Receipt
    from app.services.rag import MAX_DISTANCE, embed_receipt, key_words, mentions, search_receipts

    async with SessionLocal() as session:
        business = (await session.execute(select(Business).where(Business.owner_phone == "628120001111"))).scalar_one()
        await set_tenant(session, business.id)
        by_id: dict[str, str] = {}
        for r in RAG_RECEIPTS:
            rec = Receipt(
                business_id=business.id, image_url=f"eval://{r.key}", supplier=r.supplier,
                total_amount=sum(i[3] for i in r.items),
                occurred_at=datetime.fromisoformat(r.day).replace(tzinfo=timezone.utc),
                parsed_data={"document_type": "receipt", "items": [
                    {"name": n, "unit": u, "quantity": q, "line_total": t} for n, u, q, t in r.items]},
            )
            session.add(rec)
            await session.flush()
            by_id[str(rec.id)] = r.key
            await embed_receipt(session, rec)
            await asyncio.sleep(pace if do_sweep else 1.0)
        retrieved, rows, ranked = [], [], []
        for q in RAG_QUESTIONS:
            # one embedding call per question; the cut-off is applied afterwards, so a sweep costs no more
            docs = await search_receipts(session, business.id, q.text, max_distance=2.0, lexical=False)
            words = key_words(q.text)
            ranked.append([(by_id[d.metadata["receipt_id"]], d.metadata["distance"], mentions(d.page_content, words))
                           for d in docs if d.metadata["receipt_id"] in by_id])
            keys = [k for k, dist, ok in ranked[-1] if dist <= MAX_DISTANCE and ok]
            retrieved.append(keys)
            route = ""
            if routing:
                system = _CLASSIFIER_SYSTEM.format(business_name=business.name, business_type=business.business_type)
                declarations = [*tools_module.TOOL_DECLARATIONS, OUT_OF_SCOPE, CLARIFY]
                call = await _paced(lambda: force_tool_call(system=system, message=q.text, tools=declarations), pace=pace)
                route = call.name if call is not None else "clarify"
            rows.append((q, keys, route))
            await asyncio.sleep(pace if do_sweep else 1.0)
        await session.rollback()

    s = score(RAG_QUESTIONS, retrieved)

    def pct(v):
        return "n/a" if v is None else f"{v:.0%}"

    settings = get_settings()
    out = [
        f"### M9-T7 RAG run - {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC",
        "",
        f"Embedding model: {settings.gemini_embedding_model}; classifier model: "
        f"{settings.gemini_flash_model if routing else 'not called (retrieval only)'}. "
        f"{len(RAG_RECEIPTS)} receipts, {s['answerable']} answerable + {s['unanswerable']} unanswerable "
        f"questions, top-5, distance cut-off {MAX_DISTANCE} + lexical guard.",
        "",
        "| metric | value |", "|---|---|",
        f"| precision of returned results | {pct(s['precision'])} |",
        f"| hit@1 | {pct(s['hit@1'])} |",
        f"| recall | {pct(s['recall'])} |",
        f"| MRR | {'n/a' if s['mrr'] is None else format(s['mrr'], '.2f')} |",
        f"| false match on unanswerable | {pct(s['false_match'])} |",
        "", "| question | relevant | returned (best first) | routed to |", "|---|---|---|---|",
    ]
    for q, keys, route in rows:
        out.append(f"| {q.text} | {','.join(sorted(q.relevant)) or 'none'} | {','.join(keys) or 'none'} | {route or '-'} |")
    if do_sweep:
        cutoffs = [round(0.30 + 0.05 * i, 2) for i in range(8)]
        out += ["", f"Cut-off sweep over the same retrieved distances ({len(RAG_QUESTIONS)} questions, "
                    f"{len(RAG_RECEIPTS)} receipts, {len(RAG_RECEIPTS) + len(RAG_QUESTIONS)} embedding calls):", "",
                sweep_table(sweep(RAG_QUESTIONS, ranked, cutoffs, False), sweep(RAG_QUESTIONS, ranked, cutoffs, True)), "", "Distances (best first), A = answerable, U = unanswerable, (x) = key word missing from that receipt:", ""]
        for (q, _k, _r), r in zip(rows, ranked):
            out.append(f"- {'A' if q.relevant else 'U'} {q.text}: " + ", ".join(f"{k}={d:.2f}{'' if ok else '(x)'}" for k, d, ok in r))
    return "\n".join(out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--routing", action="store_true")
    ap.add_argument("--sweep", action="store_true", help="also sweep the distance cut-off 0.30-0.65")
    ap.add_argument("--no-append", action="store_true")
    a = ap.parse_args()
    body = asyncio.run(run(7.0 if a.sweep else 13.0, a.routing, a.sweep))
    print(body)
    if not a.no_append:
        import pathlib

        doc = pathlib.Path(__file__).resolve().parents[3] / "docs" / "evaluation.md"
        with doc.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write("\n" + body + "\n")
