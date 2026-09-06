"""Exception rules over the metric registry (roadmap M10-T1).

The nightly job stops computing anything itself: every rule reads the
registry, decides whether a condition holds, and writes at most one alert per
(rule, subject, local day) — the `rule_key`. Running a rule twice on the same
data writes nothing the second time, which is what lets M10-T2 build real
deduplication and rate limiting on top.

Rules and their thresholds (deterministic; the model only narrates):

  margin_drop      gross margin over the last 7 days is at least
                   MARGIN_DROP_POINTS below the margin of the 30 days before
                   that, with enough revenue in both windows to mean anything
  stockout_risk    an item's days remaining (locked velocity formula) is
                   shorter than the next likely delivery — the typical gap
                   between that item's last goods receipts, or
                   DEFAULT_DELIVERY_DAYS when it has none
  void_rate        one cashier voided at least VOID_MIN voids and
                   VOID_RATE_MIN of their orders in the last 30 days, and at
                   least VOID_RATE_MULTIPLE times the rest of the team's rate
  supplier_price   the last price paid for an item to a supplier, bought
                   within the last day, moved at least PRICE_MOVE_PCT from the
                   price before it
  takings_anomaly  today's revenue is more than Z_THRESHOLD standard
                   deviations from the 30-day daily mean (the z-score rule,
                   rebuilt on the registry's daily series)

Every threshold is a module constant so a test can manufacture the condition
exactly; every message is Indonesian. Writing goes through `alert_policy`
(M10-T2): dedup by rule_key, suppress a subject alerted in the last week,
at most MAX_NEW_PER_NIGHT new alerts a night.
"""
from __future__ import annotations

import statistics
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.periods import period_range
from app.metrics import compute, local_day_windows, series
from app.models import Alert, Business, GoodsReceipt, GoodsReceiptLine, Order, Staff

MARGIN_DROP_POINTS = Decimal(10)      # percentage points
MARGIN_MIN_REVENUE = Decimal(500000)  # per window, or the margin is noise
DEFAULT_DELIVERY_DAYS = 7
VOID_MIN = 3
VOID_RATE_MIN = 0.10
VOID_RATE_MULTIPLE = 2.0
VOID_MIN_ORDERS = 10
PRICE_MOVE_PCT = 10.0
Z_THRESHOLD = 3.0
BASELINE_DAYS = 30


@dataclass
class RuleAlert:
    type: str
    rule_key: str
    severity: str
    message: str
    metric: str | None = None
    related_item_id: uuid.UUID | None = None
    details: dict[str, Any] | None = None


def _rp(v) -> str:
    return f"Rp {float(v):,.0f}".replace(",", ".")


def local_day(business: Business, now: datetime | None = None) -> str:
    moment = now or datetime.now(timezone.utc)
    return moment.astimezone(ZoneInfo(business.timezone)).date().isoformat()




# ── the rules ────────────────────────────────────────────────────────────────


async def rule_margin_drop(session: AsyncSession, business: Business, now: datetime | None = None) -> list[RuleAlert]:
    moment = now or datetime.now(timezone.utc)
    today_start, today_end, _ = period_range("today", business.timezone, now=moment)
    recent_since = today_start - timedelta(days=6)
    before_since = recent_since - timedelta(days=30)
    recent = await compute(session, business, "gross_margin_pct", since=recent_since, until=today_end)
    before = await compute(session, business, "gross_margin_pct", since=before_since, until=recent_since)
    rev_recent = await compute(session, business, "revenue", since=recent_since, until=today_end)
    rev_before = await compute(session, business, "revenue", since=before_since, until=recent_since)
    if recent.value is None or before.value is None:
        return []
    if (rev_recent.value or 0) < MARGIN_MIN_REVENUE or (rev_before.value or 0) < MARGIN_MIN_REVENUE:
        return []
    drop = Decimal(before.value) - Decimal(recent.value)
    if drop < MARGIN_DROP_POINTS:
        return []
    return [RuleAlert(
        type="margin_drop", rule_key=f"margin_drop:{local_day(business, moment)}", metric="gross_margin_pct",
        severity="high" if drop >= 2 * MARGIN_DROP_POINTS else "medium",
        message=f"Margin kotor 7 hari terakhir {recent.value}% — turun {drop}% poin dari {before.value}% pada 30 hari sebelumnya. Cek harga bahan atau harga jual.",
        details={"recent_pct": float(recent.value), "before_pct": float(before.value), "drop_points": float(drop)},
    )]


async def _typical_delivery_days(session: AsyncSession, item_id: uuid.UUID) -> int:
    """Median gap between the item's last goods receipts, or the default."""
    dates = (await session.execute(
        select(GoodsReceipt.received_at).join(GoodsReceiptLine, GoodsReceiptLine.receipt_id == GoodsReceipt.id)
        .where(GoodsReceiptLine.item_id == item_id).order_by(GoodsReceipt.received_at.desc()).limit(6)
    )).scalars().all()
    if len(dates) < 2:
        return DEFAULT_DELIVERY_DAYS
    gaps = [(dates[i] - dates[i + 1]).total_seconds() / 86400 for i in range(len(dates) - 1)]
    return max(1, int(round(statistics.median(gaps))))


async def rule_stockout_risk(session: AsyncSession, business: Business, now: datetime | None = None) -> list[RuleAlert]:
    moment = now or datetime.now(timezone.utc)
    reading = await compute(session, business, "stock_days_remaining", now=moment)
    found: list[RuleAlert] = []
    for r in reading.rows:
        days = r["days_remaining"]
        if days is None:
            continue
        horizon = await _typical_delivery_days(session, r["item_id"])
        if days >= horizon:
            continue
        found.append(RuleAlert(
            type="stockout_risk", rule_key=f"stock:{r['item_id']}:{local_day(business, moment)}", metric="days_remaining",
            related_item_id=r["item_id"], severity="high" if days <= horizon / 2 else "medium",
            message=f"{r['name']}: stok habis dalam ±{days:g} hari, pengiriman berikutnya biasanya {horizon} hari lagi ({r['stock']:g} {r['unit']} @ {r['daily_usage']:g}/hari). Pesan sekarang.",
            details={"days_remaining": days, "delivery_days": horizon, "stock": r["stock"], "daily_usage": r["daily_usage"]},
        ))
    return found


async def rule_void_rate(session: AsyncSession, business: Business, now: datetime | None = None) -> list[RuleAlert]:
    moment = now or datetime.now(timezone.utc)
    since = moment - timedelta(days=30)
    rows = (await session.execute(
        select(Staff.id, Staff.name, func.count(Order.id), func.count(Order.id).filter(Order.status == "voided"))
        .join(Order, Order.staff_id == Staff.id)
        .where(Order.sold_at >= since).group_by(Staff.id, Staff.name)
    )).all()
    if not rows:
        return []
    total_orders = sum(int(n) for _i, _n, n, _v in rows)
    total_voids = sum(int(v) for _i, _n, _o, v in rows)
    found: list[RuleAlert] = []
    for staff_id, name, n_orders, n_voids in rows:
        n_orders, n_voids = int(n_orders), int(n_voids)
        if n_orders < VOID_MIN_ORDERS or n_voids < VOID_MIN:
            continue
        rate = n_voids / n_orders
        others_orders, others_voids = total_orders - n_orders, total_voids - n_voids
        others_rate = (others_voids / others_orders) if others_orders else 0.0
        if rate < VOID_RATE_MIN or (others_rate > 0 and rate < VOID_RATE_MULTIPLE * others_rate):
            continue
        found.append(RuleAlert(
            type="void_rate", rule_key=f"void_rate:{staff_id}:{local_day(business, moment)}", metric="void_rate",
            severity="high" if rate >= 2 * VOID_RATE_MIN else "medium",
            message=f"{name} membatalkan {n_voids} dari {n_orders} transaksi 30 hari terakhir ({rate:.0%}); kasir lain {others_rate:.0%}. Perlu dicek.",
            details={"staff_id": str(staff_id), "voids": n_voids, "orders": n_orders, "rate": round(rate, 3), "others_rate": round(others_rate, 3)},
        ))
    return found


async def rule_supplier_price(session: AsyncSession, business: Business, now: datetime | None = None) -> list[RuleAlert]:
    moment = now or datetime.now(timezone.utc)
    prices = await compute(session, business, "supplier_prices", now=moment)
    found: list[RuleAlert] = []
    for r in prices.rows:
        if r["previous_price"] is None or r["change_pct"] is None:
            continue
        if r["last_bought_at"] < moment - timedelta(days=1):
            continue
        if abs(r["change_pct"]) < PRICE_MOVE_PCT:
            continue
        direction = "naik" if r["change_pct"] > 0 else "turun"
        found.append(RuleAlert(
            type="supplier_price", rule_key=f"supplier_price:{r['item_id']}:{r['supplier_id']}:{local_day(business, moment)}", metric="supplier_price",
            related_item_id=r["item_id"], severity="medium",
            message=f"Harga {r['item']} dari {r['supplier'] or 'supplier'} {direction} {abs(r['change_pct']):g}%: {_rp(r['previous_price'])} → {_rp(r['last_price'])} per {r['unit']}.",
            details={"supplier_id": str(r["supplier_id"]), "last_price": r["last_price"], "previous_price": r["previous_price"], "change_pct": r["change_pct"]},
        ))
    return found


_ANOMALY_SERIES = (("daily_revenue", "revenue", "Penjualan"), ("daily_expenses", "expense_total", "Pengeluaran"))


async def rule_takings_anomaly(session: AsyncSession, business: Business, now: datetime | None = None) -> list[RuleAlert]:
    """The 30-day z-score on the registry's daily series — revenue and
    recorded expenses (the two the legacy detector watched), one alert each."""
    moment = now or datetime.now(timezone.utc)
    windows = local_day_windows(business, BASELINE_DAYS + 1, now=moment)
    found: list[RuleAlert] = []
    for metric_name, registry_metric, label in _ANOMALY_SERIES:
        daily = await series(session, business, registry_metric, windows)
        history = [float(r.value or 0) for _k, r in daily[:-1]]
        today = float(daily[-1][1].value or 0)
        if len(history) < 2:
            continue
        mean, stddev = statistics.fmean(history), statistics.stdev(history)
        if stddev <= 0:
            continue
        z = (today - mean) / stddev
        if abs(z) <= Z_THRESHOLD:
            continue
        direction = "di atas" if z > 0 else "di bawah"
        found.append(RuleAlert(
            type="anomaly", rule_key=f"anomaly:{metric_name}:{local_day(business, moment)}", metric=metric_name,
            severity="high" if abs(z) > 4 else "medium",
            message=f"{label} hari ini {_rp(today)} — jauh {direction} normal (rata-rata 30 hari {_rp(mean)}, z={z:.1f})",
            details={"today": today, "mean": round(mean, 2), "stddev": round(stddev, 2), "z": round(z, 2)},
        ))
    return found


RULES = (rule_margin_drop, rule_stockout_risk, rule_void_rate, rule_supplier_price, rule_takings_anomaly)


async def collect_findings(session: AsyncSession, business: Business, now: datetime | None = None) -> list[RuleAlert]:
    found: list[RuleAlert] = []
    for rule in RULES:
        found.extend(await rule(session, business, now))
    return found


async def run_rules(session: AsyncSession, business: Business, now: datetime | None = None) -> list[Alert]:
    """Every rule, one pass, through the alert policy; returns what was written."""
    from app.jobs.alert_policy import apply_policy

    result = await apply_policy(session, business, await collect_findings(session, business, now), now=now)
    return result.written
