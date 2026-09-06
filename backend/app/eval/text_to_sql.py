"""The naive text-to-SQL baseline (roadmap M9-T6) — for comparison only.

This is the thing the project argues *against*: hand the model the schema,
let it write a query, run it, read the number. It exists so the evaluation
can measure its silent-error rate next to the fixed tool set's, under the
same questions on the same data.

It is behind a flag (`EVAL_TEXT_TO_SQL=1`) and is never reachable from the
WhatsApp path: only `app.eval.harness` imports it. Even here it runs with the
strongest guards a text-to-SQL system can have — one SELECT, read-only
transaction, statement timeout, tenant session — because the point is not
that it can be made to write DROP TABLE; it is that a *plausible, well-formed,
wrong* query returns a number that looks right.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Base

_FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|call|do|set|vacuum|analyze|refresh|reindex|cluster|lock|comment)\b", re.I)


class BaselineDisabled(RuntimeError):
    pass


class UnsafeSQL(ValueError):
    pass


def schema_summary() -> str:
    """The schema as a text-to-SQL prompt would carry it: every business table
    with its columns and types, generated from the models so it cannot drift."""
    skip = {"login_otps", "request_logs", "pending_confirmations", "sales_legacy", "metric_baselines"}
    out = []
    for table in Base.metadata.sorted_tables:
        if table.name in skip:
            continue
        cols = ", ".join(f"{c.name} {c.type.compile(dialect=__import__('sqlalchemy.dialects.postgresql', fromlist=['dialect']).dialect())}" for c in table.columns)
        out.append(f"{table.name}({cols})")
    out.append("sales(id, business_id, item_id, quantity, unit_price, total_price, staff_id, sold_at)  -- a view over order_lines joined to orders")
    return "\n".join(out)


PROMPT = """You are a SQL assistant for a small café's PostgreSQL database. Write ONE SELECT
statement that answers the owner's question. Return only the SQL, no explanation,
no markdown. Timestamps are timestamptz in UTC; the café is in Asia/Jakarta.
Money columns are numeric. Row-level security already restricts rows to this
business, so do not filter by business_id.

Schema:
{schema}

Question: {question}
SQL:"""


def guard(sql: str) -> str:
    """One statement, SELECT only, no side effects."""
    cleaned = sql.strip().strip("`")
    cleaned = re.sub(r"^sql\s*", "", cleaned, flags=re.I).strip()
    if cleaned.endswith(";"):
        cleaned = cleaned[:-1].strip()
    if ";" in cleaned:
        raise UnsafeSQL("multiple statements")
    if not re.match(r"^(select|with)\b", cleaned, re.I):
        raise UnsafeSQL("not a SELECT")
    if _FORBIDDEN.search(cleaned):
        raise UnsafeSQL("forbidden keyword")
    return cleaned


@dataclass
class BaselineAnswer:
    sql: str | None
    value: float | None
    rows: list
    error: str | None

    @property
    def answered(self) -> bool:
        return self.error is None and self.value is not None


async def generate_sql(question: str) -> str:
    from app.ai.gemini import generate_text

    return await generate_text(system="You write PostgreSQL.", message=PROMPT.format(schema=schema_summary(), question=question), temperature=0.0)


async def run_baseline(session: AsyncSession, question: str, *, sql: str | None = None, timeout_ms: int = 3000) -> BaselineAnswer:
    """Generate (or take) the SQL, guard it, run it read-only, and read the first
    numeric value of the first row — which is what a naive integration does."""
    if not get_settings().eval_text_to_sql:
        raise BaselineDisabled("EVAL_TEXT_TO_SQL is off — the baseline is for evaluation only")
    try:
        raw = sql if sql is not None else await generate_sql(question)
    except Exception as exc:  # the model call itself failed
        return BaselineAnswer(None, None, [], f"generation failed: {exc}")
    try:
        safe = guard(raw)
    except UnsafeSQL as exc:
        return BaselineAnswer(raw, None, [], f"unsafe: {exc}")
    try:
        await session.execute(text("set transaction read only"))
        await session.execute(text(f"set local statement_timeout = {int(timeout_ms)}"))
        result = await session.execute(text(safe))
        rows = [tuple(r) for r in result.fetchmany(20)]
    except Exception as exc:
        cause = getattr(exc, "orig", None) or exc
        head = " ".join(str(cause).split())[:120]
        return BaselineAnswer(safe, None, [], f"sql error: {type(cause).__name__}: {head}")
    value = None
    for row in rows:
        for cell in row:
            if isinstance(cell, (int, float)) and not isinstance(cell, bool):
                value = float(cell)
                break
            try:
                from decimal import Decimal
                if isinstance(cell, Decimal):
                    value = float(cell)
                    break
            except Exception:
                pass
        if value is not None:
            break
    return BaselineAnswer(safe, value, rows, None)
