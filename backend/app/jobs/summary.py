"""The owner's daily summary, and on Monday the weekly one too (fin-p1-3, CP1 §1.5).

Proactive alerts only say what went wrong. A quiet day deserves one line as
well, or the owner learns nothing from the bot unless something breaks. Every
number comes from the metric registry — the same ones the dashboard and the
chat answer with — so this message can never disagree with them.

Called from the nightly job after the alerts. Rules, deliberately small:
  * a day with no sales sends no daily summary (a closed day is not news);
  * Monday adds last week (Mon-Sun) against the week before, in the same message;
  * the demo number is never messaged (same guard as alert delivery).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.periods import business_day, period_range
from app.core.config import DEMO_OWNER_PHONE, get_settings
from app.metrics import compare_with_previous, compute
from app.models import Business
from app.whatsapp.client import send_template, send_text

logger = logging.getLogger("jobs.summary")

_DAYS = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]


def rp(value) -> str:
    return "Rp " + f"{int(Decimal(value or 0).quantize(Decimal('1'))):,}".replace(",", ".")


def _change(now_value, before) -> str:
    """'naik 12% dari kemarin' style fragment; empty when there is nothing to compare."""
    now_value, before = Decimal(now_value or 0), Decimal(before or 0)
    if before <= 0:
        return ""
    pct = (now_value - before) / before * 100
    if abs(pct) < 1:
        return "sama"
    return f"{'naik' if pct > 0 else 'turun'} {abs(int(pct.quantize(Decimal('1'))))}%"


def _date(d) -> str:
    return f"{d.day} {_MONTHS[d.month - 1]}"


async def _revenue_and_count(session, business, **window) -> tuple[Decimal, int]:
    revenue = await compute(session, business, "revenue", **window)
    count = await compute(session, business, "transaction_count", **window)
    return Decimal(revenue.value or 0), int(count.value or 0)


async def build_summary(session: AsyncSession, business: Business, now: datetime | None = None) -> str | None:
    """The message text, or None when there is nothing to report. The session
    must be scoped to the business."""
    now = now or datetime.now(timezone.utc)
    today = business_day(now, business.timezone, business.day_start_hour)
    parts: list[str] = []

    revenue, count = await _revenue_and_count(session, business, period="today", now=now)
    if count:
        # Yesterday up to the same hour: the message goes out while the day is still running.
        yest_cmp = await compare_with_previous(session, business, "revenue", period="today", now=now)
        yest = yest_cmp.previous.value if yest_cmp.previous else 0
        avg = revenue / count
        lines = [
            f"📊 *Ringkasan hari ini, {_DAYS[today.weekday()]} {_date(today)}*",
            f"_{business.name}_",
            "",
            f"Penjualan *{rp(revenue)} dari {count} transaksi*",
            f"Rata-rata {rp(avg)} per transaksi",
        ]
        change = _change(revenue, yest)
        if change:
            lines.append(f"Dibanding kemarin jam yang sama: {change} ({rp(yest)})")
        expense = await compute(session, business, "expense_total", period="today", now=now)
        profit = await compute(session, business, "net_profit", period="today", now=now)
        if expense.value:
            lines.append(f"Pengeluaran {rp(expense.value)} · laba bersih *{rp(profit.value)}*")
        top = await compute(session, business, "top_items_by_revenue", period="today", limit=3, now=now)
        if top.rows:
            lines.append("Terlaris: " + ", ".join(f"{r['name']} ({r['quantity']:g})" for r in top.rows))
        parts.append("\n".join(lines))

    if today.weekday() == 0:  # Monday: the week that just ended
        start, end, _ = period_range("last_week", business.timezone, now=now, day_start_hour=business.day_start_hour)
        w_rev, w_count = await _revenue_and_count(session, business, since=start, until=end)
        if w_count:
            p_rev, _ = await _revenue_and_count(session, business, since=start - timedelta(days=7), until=start)
            first = business_day(start, business.timezone, business.day_start_hour)
            last = first + timedelta(days=6)
            lines = [
                f"📅 *Ringkasan minggu lalu, {_date(first)} - {_date(last)}*",
                "",
                f"Penjualan *{rp(w_rev)} dari {w_count} transaksi*",
            ]
            change = _change(w_rev, p_rev)
            if change:
                lines.append(f"Dibanding minggu sebelumnya: {change} ({rp(p_rev)})")
            top = await compute(session, business, "top_items_by_revenue", since=start, until=end, limit=3)
            if top.rows:
                lines.append("Terlaris: " + ", ".join(f"{r['name']} ({r['quantity']:g})" for r in top.rows))
            parts.append("\n".join(lines))

    return "\n\n".join(parts) or None


async def send_summary(session: AsyncSession, business: Business, now: datetime | None = None) -> bool:
    """Builds and sends. True when a message went out."""
    if business.owner_phone == DEMO_OWNER_PHONE:
        return False
    text = await build_summary(session, business, now)
    if text is None:
        return False
    settings = get_settings()
    if settings.whatsapp_provider == "gateway":
        # WhatsApp Web has no 24-hour window or templates: plain text is allowed.
        await send_text(business.owner_phone, text)
    else:
        # Cloud API: outside the session window only the approved Utility
        # template goes through, so the summary rides in its detail slot.
        await send_template(
            business.owner_phone, settings.whatsapp_alert_template,
            ["ringkasan", business.name, text.replace("\n\n", " | ").replace("\n", " ")],
            language=business.language_preference,
        )
    logger.info("business=%s summary sent", business.id)
    return True
