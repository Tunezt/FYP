"""Task-success rate from request_logs (CP1 §3.7, audit fin-eval / P1-1a).

CP1's definition: a request is a success when it was completed correctly on
the first attempt. The log has no "was it right" column, so two signals stand
in for it, both readable from rows that already exist:

  * status = 'error'  -> the owner got the "Waduh" apology instead of an answer;
  * the same business asks the same thing again within 2 minutes (a rephrase or
    an identical repeat) -> the first answer did not do the job. The EARLIER
    request is the failed one: the owner had to try again.

Only WhatsApp text questions count (image and file messages have no question
text). This is a lower bound on failure: a wrong answer the owner accepted looks
like a success, and a deliberate follow-up ("and yesterday?") is not similar
enough to be mistaken for a repeat.

    cd backend && ./.venv/Scripts/python.exe ../scripts/eval_task_success.py [--since 2026-11-01] [--phone 62812...]
"""
from __future__ import annotations

import argparse
import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher

WINDOW = timedelta(minutes=2)
SIMILAR = 0.8  # SequenceMatcher ratio at which two questions count as the same ask


@dataclass(frozen=True)
class Row:
    business_id: object
    created_at: datetime
    raw_query: str | None
    status: str
    channel: str = "whatsapp"
    path: str | None = "text"


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", (text or "").lower()).strip()


def evaluate(rows: list[Row]) -> dict:
    """{'total', 'success', 'failed', 'errors', 'repeats', 'rate'} over the question rows."""
    questions = sorted(
        (r for r in rows if r.channel == "whatsapp" and r.path == "text" and _norm(r.raw_query or "")),
        key=lambda r: (str(r.business_id), r.created_at),
    )
    failed: set[int] = set()
    errors = repeats = 0
    for i, r in enumerate(questions):
        if r.status == "error":
            failed.add(i)
            errors += 1
        # Look ahead inside the window for a repeat of this question.
        for j in range(i + 1, len(questions)):
            n = questions[j]
            if n.business_id != r.business_id or n.created_at - r.created_at > WINDOW:
                break
            if SequenceMatcher(None, _norm(r.raw_query), _norm(n.raw_query)).ratio() >= SIMILAR:
                if i not in failed:
                    repeats += 1
                failed.add(i)
                break
    total = len(questions)
    return {
        "total": total, "success": total - len(failed), "failed": len(failed),
        "errors": errors, "repeats": repeats,
        "rate": (total - len(failed)) / total if total else None,
    }


async def _main() -> None:
    from sqlalchemy import select

    from app.core.db import SessionLocal, engine
    from app.models import Business, RequestLog

    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="YYYY-MM-DD")
    ap.add_argument("--phone", help="owner phone: one business only")
    args = ap.parse_args()
    async with SessionLocal() as s:
        businesses = (await s.execute(select(Business).order_by(Business.created_at))).scalars().all()
    for b in businesses:
        if args.phone and b.owner_phone != args.phone:
            continue
        async with SessionLocal() as s:
            from app.core.db import set_tenant

            await set_tenant(s, b.id)
            q = select(RequestLog).where(RequestLog.business_id == b.id)
            if args.since:
                q = q.where(RequestLog.created_at >= datetime.fromisoformat(args.since))
            logs = (await s.execute(q)).scalars().all()
        res = evaluate([Row(r.business_id, r.created_at, r.raw_query, r.status, r.channel, r.path) for r in logs])
        if not res["total"]:
            continue
        print(f"{b.name}: {res['success']}/{res['total']} first-attempt = {res['rate']:.0%} "
              f"({res['errors']} error replies, {res['repeats']} repeated within 2 min)")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
