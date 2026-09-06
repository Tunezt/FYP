"""The evaluation harness (roadmap M9-T6).

Runs the question set through two systems on the same tenant data and scores
each answer as one of:

  correct        a data question answered with the ground-truth figure
  refused        no figure given — the assistant said it could not answer, or
                 asked what was meant, or the baseline's query failed loudly
  silent_error   a figure was given and it is WRONG: a data question answered
                 with the wrong number, or an out-of-scope question answered
                 with any number at all

Three rates follow: answer accuracy (correct ÷ data questions), refusal rate
(refused ÷ all), and the silent-error rate (silent_error ÷ all) — the one that
matters, because a plausible wrong answer is the failure an owner cannot see.

Ground truth is the metric registry, computed at run time with the expected
arguments, so it is always consistent with the data in front of the systems.

Two classifiers can drive the tool path: the live model (`live=True`, one
Flash call per question) or a deterministic keyword stand-in used to validate
the harness offline. The stand-in is *not* a model and every report says so.
The baseline (text-to-SQL) needs the model; offline it is reported as not run.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import tools as tools_module
from app.ai.refusal import looks_like_a_figure
from app.eval.questions import QUESTIONS, Question
from app.metrics import compute
from app.models import Business, Customer, Item

TOLERANCE = 0.5   # rupiah


@dataclass
class Outcome:
    question: Question
    system: str                     # "tools" | "baseline"
    intent: str | None
    figure: float | None
    truth: float | None
    verdict: str                    # correct | refused | silent_error
    detail: str = ""


@dataclass
class Report:
    system: str
    classifier: str                 # "gemini" | "keyword stand-in" | "gemini (text-to-sql)"
    outcomes: list[Outcome] = field(default_factory=list)
    stopped_early: str | None = None

    @property
    def n(self) -> int:
        return len(self.outcomes)

    def count(self, verdict: str) -> int:
        return sum(1 for o in self.outcomes if o.verdict == verdict)

    @property
    def n_data(self) -> int:
        return sum(1 for o in self.outcomes if o.question.kind == "data")

    @property
    def accuracy(self) -> float | None:
        return round(self.count("correct") / self.n_data, 3) if self.n_data else None

    @property
    def refusal_rate(self) -> float | None:
        return round(self.count("refused") / self.n, 3) if self.n else None

    @property
    def silent_error_rate(self) -> float | None:
        return round(self.count("silent_error") / self.n, 3) if self.n else None


# ── ground truth ─────────────────────────────────────────────────────────────


async def _item_id(session: AsyncSession, name: str):
    return (await session.execute(select(Item.id).where(Item.name.ilike(f"%{name}%")).order_by(Item.name))).scalars().first()


async def _customer_id(session: AsyncSession, name: str):
    return (await session.execute(select(Customer.id).where(Customer.name.ilike(f"%{name}%")))).scalars().first()


async def ground_truth(session: AsyncSession, business: Business, q: Question) -> float | None:
    """The registry's figure for the question's expected arguments; None when
    the correct answer is a list (then only intent and non-fabrication count)."""
    if q.kind != "data" or q.figure is None:
        return None
    if q.intent == "get_customer_summary":
        cid = await _customer_id(session, q.args.get("customer", ""))
        r = await compute(session, business, "customer_summary", period=q.args.get("period", "this_month"), customer_id=cid)
        return float(r.rows[0]["period_spend"]) if r.rows else None
    kw = dict(q.metric_kw)
    if "item" in kw:
        kw["item_id"] = await _item_id(session, kw.pop("item"))
    r = await compute(session, business, q.metric, **kw)
    return float(r.value) if r.value is not None else None


def pick(facts: dict, path: str | None) -> float | list[float] | None:
    """Follow "a.0.b" into the tool's facts. A "*" segment fans out over a list
    and returns every candidate: a stock lookup for "arabica" legitimately
    answers with every item that matches, each labelled — the owner reads the
    right one — so the scorer must judge the set, not slot 0."""
    if path is None:
        return None

    def walk(cur: Any, parts: list[str]):
        if not parts:
            try:
                return [float(cur)] if cur is not None else []
            except (TypeError, ValueError):
                return []
        head, rest = parts[0], parts[1:]
        if head == "*":
            if not isinstance(cur, list):
                return []
            out = []
            for el in cur:
                out.extend(walk(el, rest))
            return out
        try:
            nxt = cur[int(head)] if isinstance(cur, list) else cur[head]
        except (KeyError, IndexError, TypeError, ValueError):
            return []
        return walk(nxt, rest)

    found = walk(facts, path.split("."))
    if "*" in path:
        return found
    return found[0] if found else None


def any_figure_in(facts: Any) -> bool:
    """Does the answer carry any numeric figure at all? (For out-of-scope
    questions, any number is a fabrication.)"""
    if isinstance(facts, bool) or facts is None:
        return False
    if isinstance(facts, (int, float, Decimal)):
        return True
    if isinstance(facts, str):
        return looks_like_a_figure(facts)
    if isinstance(facts, dict):
        return any(any_figure_in(v) for k, v in facts.items() if k not in ("period", "period_label", "note", "query"))
    if isinstance(facts, list):
        return any(any_figure_in(v) for v in facts)
    return False


def verdict_for(q: Question, *, refused: bool, figure: float | None, truth: float | None, facts: Any = None) -> tuple[str, str]:
    if q.kind == "oos":
        if refused:
            return "refused", "out of scope, refused"
        if figure is not None or any_figure_in(facts):
            return "silent_error", "out of scope, but a figure was given"
        return "refused", "out of scope, answered without a figure"
    if refused:
        return "refused", "data question refused"
    if truth is None:
        # A list-shaped answer: correct when the right tool ran; a number from the
        # wrong tool would have to disagree with the registry to be silent.
        return ("correct", "right tool, list answer") if figure is None else ("correct", "right tool")
    if isinstance(figure, list):
        if not figure:
            return "refused", "no figure in the answer"
        if any(abs(f - truth) <= TOLERANCE for f in figure):
            return "correct", "" if len(figure) == 1 else f"among {len(figure)} labelled matches"
        return "silent_error", f"answered {', '.join(f'{f:g}' for f in figure)}, truth {truth:g}"
    if figure is None:
        return "refused", "no figure in the answer"
    if abs(figure - truth) <= TOLERANCE:
        return "correct", ""
    return "silent_error", f"answered {figure:g}, truth {truth:g}"


# ── the tool path ────────────────────────────────────────────────────────────


_KEYWORD_RULES: list[tuple[re.Pattern, str, dict]] = [
    (re.compile(r"\b(vs|banding|dibanding|compare|lawan)\b", re.I), "compare_periods", {"period_a": "this_week", "period_b": "last_week"}),
    (re.compile(r"\b(bulan depan|next (month|year)|tahun depan|agaknya|kira2|kira-kira|forecast|prediksi|ramalan)\b", re.I), "out_of_scope", {}),
    (re.compile(r"\b(hujan|cuaca|weather|pajak|tax|sebelah|kedai lain|absen|attendance|kurs|dollar|gaji|raise my prices|should i)\b", re.I), "out_of_scope", {}),
    (re.compile(r"\b(modal|hpp|recipe|resep)\b", re.I), "get_recipe_cost", {"item_name": "es kopi susu"}),
    (re.compile(r"\b(harga beli|supplier|pembekal|harga terakhir)\b", re.I), "get_supplier_prices", {"item_name": "susu"}),
    (re.compile(r"\b(kas|selisih|shift|syif|till)\b", re.I), "get_shift_summary", {"period": "yesterday"}),
    (re.compile(r"\b(promo|bogo)\b", re.I), "get_promo_performance", {"period": "this_month"}),
    (re.compile(r"\b(andi|rina|pelanggan|customer)\b", re.I), "get_customer_summary", {"customer": "andi", "period": "this_month"}),
    (re.compile(r"\b(mau habis|hampir habis|restock|perlu|run out|low)\b", re.I), "get_low_stock", {}),
    (re.compile(r"\b(stok|stock|baki|tinggal|left)\b", re.I), "get_stock", {}),
    (re.compile(r"\b(untung|profit|making money|pengeluaran|expense)\b", re.I), "get_profit", {"period": "this_month"}),
    (re.compile(r"\b(jualan|penjualan|omzet|sell|sales|sold)\b", re.I), "get_sales_summary", {}),
]
_PERIOD_WORDS = [
    (re.compile(r"\b(kemarin|yesterday|semalam)\b", re.I), "yesterday"),
    (re.compile(r"\b(minggu lalu|last week)\b", re.I), "last_week"),
    (re.compile(r"\b(minggu ini|this week|minggu ni)\b", re.I), "this_week"),
    (re.compile(r"\b(bulan ini|this month|bulan ni)\b", re.I), "this_month"),
    (re.compile(r"\b(30 hari|30 days)\b", re.I), "last_30_days"),
    (re.compile(r"\b(hari ini|today|hari ni)\b", re.I), "today"),
]
_ITEM_WORDS = ["arabica", "gula aren", "croissant", "susu", "es kopi susu"]


def keyword_classifier(text: str):
    """A deterministic stand-in for the model, for validating the harness
    offline. It is not a model and it is not what ships."""
    for pattern, name, args in _KEYWORD_RULES:
        if pattern.search(text):
            args = dict(args)
            if name in ("get_sales_summary", "get_profit", "get_shift_summary", "get_promo_performance") or "period" in args:
                for pw, period in _PERIOD_WORDS:
                    if pw.search(text):
                        args["period"] = period
                        break
                args.setdefault("period", "today" if name == "get_sales_summary" else args.get("period", "this_month"))
            if name == "get_stock":
                for item in _ITEM_WORDS:
                    if item in text.lower():
                        args["item_name"] = item
                        break
            return SimpleNamespace(name=name, args=args)
    return SimpleNamespace(name="clarify", args={"reply": "Maaf, maksudnya gimana ya?"})


async def _paced(coro_factory, *, pace: float, attempts: int = 3):
    """Free-tier models are rate-limited per minute (5 RPM on Flash at the time
    of writing). Sleep `pace` seconds before each call and, on a 429, wait and
    try again a couple of times before giving up on the run."""
    last: Exception | None = None
    for attempt in range(attempts):
        if pace:
            await asyncio.sleep(pace)
        try:
            return await coro_factory()
        except Exception as exc:  # noqa: BLE001 — the harness reports, it does not crash
            last = exc
            if "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc):
                await asyncio.sleep(max(pace, 12.0) * (attempt + 1))
                continue
            raise
    raise last  # type: ignore[misc]


async def run_tools(session: AsyncSession, business: Business, questions: list[Question], *, live: bool = False, limit: int | None = None, pace: float = 0.0) -> Report:
    """The fixed tool set. Live: one forced Flash call per question decides the
    tool and its arguments (the composer is not called — the facts are what is
    scored). Offline: the keyword stand-in decides."""
    from app.ai.router import OUT_OF_SCOPE, CLARIFY
    from app.ai.router import _CLASSIFIER_SYSTEM

    report = Report(system="tools", classifier="gemini" if live else "keyword stand-in")
    for q in questions[: limit or len(questions)]:
        truth = await ground_truth(session, business, q)
        try:
            if live:
                from app.ai.gemini import force_tool_call

                system = _CLASSIFIER_SYSTEM.format(business_name=business.name, business_type=business.business_type)
                declarations = [*tools_module.TOOL_DECLARATIONS, OUT_OF_SCOPE, CLARIFY]
                call = await _paced(lambda: force_tool_call(system=system, message=q.text, tools=declarations), pace=pace)
            else:
                call = keyword_classifier(q.text)
        except Exception as exc:
            report.stopped_early = f"model call failed at question {report.n + 1}: {type(exc).__name__}: {str(exc)[:120]}"
            break
        intent = call.name if call is not None else "clarify"
        if intent in ("out_of_scope", "clarify") or call is None:
            reply = (call.args or {}).get("reply", "") if call is not None else ""
            refused = intent == "out_of_scope" or not looks_like_a_figure(reply)
            verdict, detail = verdict_for(q, refused=refused, figure=None, truth=truth, facts=reply)
            report.outcomes.append(Outcome(q, "tools", "refuse" if intent == "out_of_scope" else "clarify", None, truth, verdict, detail))
            continue
        executor = tools_module.TOOL_EXECUTORS.get(intent)
        if executor is None:
            report.outcomes.append(Outcome(q, "tools", intent, None, truth, "refused", "unknown tool → clarify"))
            continue
        try:
            facts = await executor(session, business, dict(call.args or {}))
        except Exception as exc:
            report.outcomes.append(Outcome(q, "tools", intent, None, truth, "refused", f"tool raised {type(exc).__name__}"))
            continue
        if isinstance(facts, dict) and facts.get("found") is False:
            report.outcomes.append(Outcome(q, "tools", intent, None, truth, "refused", "not found → asks"))
            continue
        figure = pick(facts, q.figure) if intent == q.intent else _first_number(facts)
        if intent != q.intent and q.kind == "data":
            # Wrong tool: a figure from it is a silent error unless it happens to equal the truth.
            first = figure[0] if isinstance(figure, list) and figure else (None if isinstance(figure, list) else figure)
            verdict, detail = ("silent_error", f"wrong tool {intent}, answered {first:g}") if first is not None and (truth is None or abs(first - truth) > TOLERANCE) else ("refused", f"wrong tool {intent}, no figure")
        else:
            verdict, detail = verdict_for(q, refused=False, figure=figure, truth=truth, facts=facts)
        report.outcomes.append(Outcome(q, "tools", intent, figure, truth, verdict, detail))
    return report


def _first_number(facts: Any) -> float | None:
    if isinstance(facts, bool) or facts is None:
        return None
    if isinstance(facts, (int, float, Decimal)):
        return float(facts)
    if isinstance(facts, dict):
        for k, v in facts.items():
            if k in ("period", "period_label", "note", "query"):
                continue
            n = _first_number(v)
            if n is not None:
                return n
    if isinstance(facts, list):
        for v in facts:
            n = _first_number(v)
            if n is not None:
                return n
    return None


# ── the baseline ─────────────────────────────────────────────────────────────


async def run_baseline(session: AsyncSession, business: Business, questions: list[Question], *, limit: int | None = None, sql_for=None, pace: float = 0.0) -> Report:
    """Naive text-to-SQL. `sql_for(question) -> str` replaces the model for
    tests; otherwise each question costs one generation call."""
    from app.eval.text_to_sql import run_baseline as _run

    from app.core.db import set_tenant

    report = Report(system="baseline", classifier="gemini (text-to-sql)" if sql_for is None else "scripted SQL")
    business_id = business.id
    for q in questions[: limit or len(questions)]:
        # Each query in its own read-only transaction, so a failure does not poison
        # the next; a rollback expires the ORM row, so it is re-read each time.
        await session.rollback()
        await set_tenant(session, business_id)
        business = await session.get(Business, business_id)
        truth = await ground_truth(session, business, q)
        if sql_for is not None:
            answer = await _run(session, q.text, sql=sql_for(q))
        else:
            from app.eval.text_to_sql import generate_sql

            try:
                generated = await _paced(lambda: generate_sql(q.text), pace=pace)
            except Exception as exc:  # noqa: BLE001
                report.stopped_early = f"generation failed: {type(exc).__name__}: {str(exc)[:160]}"
                break
            answer = await _run(session, q.text, sql=generated)
        await session.rollback()
        await set_tenant(session, business_id)
        if answer.error and answer.error.startswith("generation failed"):
            report.stopped_early = answer.error
            break
        if not answer.answered:
            detail = (answer.error or "no number") + (f" | {answer.sql[:90]}" if answer.sql else "")
            report.outcomes.append(Outcome(q, "baseline", None, None, truth, "refused", detail))
            continue
        verdict, detail = verdict_for(q, refused=False, figure=answer.value, truth=truth, facts=answer.value)
        report.outcomes.append(Outcome(q, "baseline", None, answer.value, truth, verdict, f"{detail} | {answer.sql[:90]}" if answer.sql else detail))
    return report


# ── the report ───────────────────────────────────────────────────────────────


def render(reports: list[Report], *, label: str, business_name: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"### Run “{label}” — {stamp}", "", f"Business: {business_name}. Questions: {len(QUESTIONS)} ({sum(1 for q in QUESTIONS if q.kind == 'data')} data, {sum(1 for q in QUESTIONS if q.kind == 'oos')} out of scope).", ""]
    lines += ["| system | classifier | n | accuracy (data) | refusal rate | **silent-error rate** |", "|---|---|---|---|---|---|"]
    for r in reports:
        acc = f"{r.accuracy:.0%}" if r.accuracy is not None else "—"
        ref = f"{r.refusal_rate:.0%}" if r.refusal_rate is not None else "—"
        sil = f"**{r.silent_error_rate:.0%}**" if r.silent_error_rate is not None else "—"
        lines.append(f"| {r.system} | {r.classifier} | {r.n} | {acc} | {ref} | {sil} |")
    tools = next((r for r in reports if r.system == "tools"), None)
    base = next((r for r in reports if r.system == "baseline"), None)
    if tools and base and tools.silent_error_rate is not None and base.silent_error_rate is not None:
        gap = base.silent_error_rate - tools.silent_error_rate
        lines += ["", f"**Silent-error gap: {gap:+.0%}** (baseline {base.silent_error_rate:.0%} vs tools {tools.silent_error_rate:.0%}) on {min(tools.n, base.n)} shared questions."]
    for r in reports:
        if r.stopped_early:
            lines += ["", f"_{r.system}: stopped early — {r.stopped_early}_"]
    for r in reports:
        lines += ["", f"#### {r.system} — per question", "", "| # | question | expected | got | figure | truth | verdict |", "|---|---|---|---|---|---|---|"]
        for i, o in enumerate(r.outcomes, 1):
            fig = ("/".join(f"{f:g}" for f in o.figure) if isinstance(o.figure, list) else f"{o.figure:g}") if o.figure not in (None, []) else "—"
            tru = f"{o.truth:g}" if o.truth is not None else "—"
            exp = o.question.intent or "refuse"
            lines.append(f"| {i} | {o.question.text} | {exp} | {o.intent or '—'} | {fig} | {tru} | {o.verdict}{' · ' + o.detail if o.detail else ''} |")
    return "\n".join(lines) + "\n"
