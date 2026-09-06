"""Run the evaluation harness (roadmap M9-T6) and append the report to docs/evaluation.md.

    python -m app.eval --mode offline                 # keyword stand-in classifier, no model calls
    python -m app.eval --mode live --limit 10         # the real classifier, N questions (quota!)
    python -m app.eval --mode live --limit 10 --baseline   # + the text-to-SQL baseline (EVAL_TEXT_TO_SQL=1)

The seeded business (owner 628120001111) is the subject; the local Postgres
must be up and seeded.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import pathlib

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal, set_tenant
from app.eval.harness import Report, render, run_baseline, run_tools
from app.eval.questions import QUESTIONS
from app.models import Business

DOC = pathlib.Path(__file__).resolve().parents[3] / "docs" / "evaluation.md"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["offline", "live"], default="offline")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--baseline", action="store_true", help="also run the text-to-SQL baseline (needs EVAL_TEXT_TO_SQL=1 and the model)")
    ap.add_argument("--label", default=None)
    ap.add_argument("--phone", default="628120001111")
    ap.add_argument("--no-append", action="store_true")
    ap.add_argument("--pace", type=float, default=13.0, help="seconds between model calls (free tier: 5 per minute)")
    ap.add_argument("--systems", default="tools,baseline", help="comma-separated subset: tools, baseline")
    ap.add_argument("--offset", type=int, default=0, help="skip the first N questions (continue a run the quota cut short)")
    args = ap.parse_args()

    async with SessionLocal() as session:
        business = (await session.execute(select(Business).where(Business.owner_phone == args.phone))).scalar_one()
        business_name = business.name
        await set_tenant(session, business.id)
        systems = {s.strip() for s in args.systems.split(",")}
        subset = QUESTIONS[args.offset:]
        reports: list[Report] = []
        if "tools" in systems:
            reports.append(await run_tools(session, business, subset, live=args.mode == "live", limit=args.limit, pace=args.pace if args.mode == "live" else 0.0))
        if args.baseline and "baseline" in systems:
            if not get_settings().eval_text_to_sql:
                print("EVAL_TEXT_TO_SQL is off; set it to 1 to run the baseline")
            else:
                reports.append(await run_baseline(session, business, subset, limit=args.limit, pace=args.pace))
        await session.rollback()

    model = get_settings().gemini_flash_model
    label = (args.label or f"{args.mode}{' + baseline' if args.baseline else ''}{f' (questions {args.offset + 1}–{args.offset + (args.limit or len(subset))})' if (args.limit or args.offset) else ''}") + f" — model {model}"
    body = render(reports, label=label, business_name=business_name)
    print(body)
    if not args.no_append:
        with DOC.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write("\n" + body)
        print(f"appended to {DOC}")


if __name__ == "__main__":
    asyncio.run(main())
