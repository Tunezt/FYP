"""Anomaly detection (PROJECT_BRIEF Section 7 — locked formulas).

Nightly: refresh metric_baselines with the 30-day rolling mean/stddev per
business per metric, then z = (today - mean) / stddev; |z| > 3 inserts an
alerts row. The baseline is CACHED — WhatsApp/dashboard reads use the cached
row, never a live 30-day recompute.

Since M10-T1 the daily totals come from the metric registry (`revenue`,
`expense_total` per business-local day), the same implementation the
dashboard and the assistant read — this module holds no sums of its own.

Deterministic math; the LLM only narrates results.
"""
import statistics
import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.periods import period_range
from app.metrics import compute
from app.models import Alert, Business, MetricBaseline

Z_THRESHOLD = 3.0
BASELINE_DAYS = 30

METRICS = ("daily_revenue", "daily_expenses")


@dataclass
class AnomalyFinding:
    metric: str
    today_value: float
    rolling_mean: float
    rolling_stddev: float
    z: float
    severity: str
    message: str


def compute_baseline(values: list[float]) -> tuple[float, float]:
    """(mean, sample stddev). Fewer than 2 points → stddev 0 (no z possible)."""
    if not values:
        return 0.0, 0.0
    if len(values) < 2:
        return float(values[0]), 0.0
    return float(statistics.fmean(values)), float(statistics.stdev(values))


def z_score(today_value: float, mean: float, stddev: float) -> float | None:
    """None when stddev is 0 — a flat baseline can't flag anomalies."""
    if stddev <= 0:
        return None
    return (today_value - mean) / stddev


def _severity(z: float) -> str:
    return "high" if abs(z) > 4 else "medium"


_REGISTRY_METRIC = {"daily_revenue": "revenue", "daily_expenses": "expense_total"}


async def _daily_totals(
    session: AsyncSession, business: Business, metric: str, day_starts: list
) -> list[float]:
    """The registry's figure per local day for the given day boundaries
    (len = n+1 fenceposts) — one `compute` per day, no sums of our own."""
    totals: list[float] = []
    for i in range(len(day_starts) - 1):
        start, end = day_starts[i], day_starts[i + 1]
        result = await compute(session, business, _REGISTRY_METRIC[metric], since=start, until=end)
        totals.append(float(result.value or 0))
    return totals


def _day_fenceposts(business: Business, days: int) -> list:
    """UTC datetimes marking business-day boundaries for the trailing `days`
    full business days (excluding today), oldest→newest, plus today's bounds.
    The boundary is `day_start_hour`, not midnight (M15-T4), so a baseline for a
    late-closing café is not learned from nights cut in half."""
    today_start, today_end, _ = period_range(
        "today", business.timezone, day_start_hour=business.day_start_hour
    )
    posts = [today_start - timedelta(days=offset) for offset in range(days, 0, -1)]
    posts.append(today_start)
    posts.append(today_end)
    return posts


async def refresh_baselines(session: AsyncSession, business: Business) -> dict[str, tuple[float, float]]:
    """Recompute + upsert the 30-day rolling baselines (excluding today)."""
    posts = _day_fenceposts(business, BASELINE_DAYS)
    history_posts = posts[:-1]  # drop today's end; last window ends at today 00:00

    results: dict[str, tuple[float, float]] = {}
    for metric in METRICS:
        values = await _daily_totals(session, business, metric, history_posts)
        mean, stddev = compute_baseline(values)
        stmt = (
            pg_insert(MetricBaseline)
            .values(
                business_id=business.id,
                metric=metric,
                rolling_mean=Decimal(str(round(mean, 4))),
                rolling_stddev=Decimal(str(round(stddev, 4))),
                computed_at=func.now(),
            )
            .on_conflict_do_update(
                index_elements=["business_id", "metric"],
                set_={
                    "rolling_mean": Decimal(str(round(mean, 4))),
                    "rolling_stddev": Decimal(str(round(stddev, 4))),
                    "computed_at": func.now(),
                },
            )
        )
        await session.execute(stmt)
        results[metric] = (mean, stddev)
    await session.flush()
    return results


async def detect_anomalies(session: AsyncSession, business: Business) -> list[AnomalyFinding]:
    """Compare today (business-local) against the cached baselines; |z| > 3
    inserts an alert. De-duped: one anomaly alert per metric per local day."""
    today_start, today_end, _ = period_range(
        "today", business.timezone, day_start_hour=business.day_start_hour
    )
    findings: list[AnomalyFinding] = []

    for metric in METRICS:
        baseline = await session.get(MetricBaseline, (business.id, metric))
        if baseline is None:
            continue
        mean = float(baseline.rolling_mean)
        stddev = float(baseline.rolling_stddev)
        today_value = (
            await _daily_totals(session, business, metric, [today_start, today_end])
        )[0]
        z = z_score(today_value, mean, stddev)
        if z is None or abs(z) <= Z_THRESHOLD:
            continue

        already = (
            await session.execute(
                select(Alert.id).where(
                    Alert.metric == metric,
                    Alert.type == "anomaly",
                    Alert.created_at >= today_start,
                )
            )
        ).first()
        if already:
            continue

        direction = "di atas" if z > 0 else "di bawah"
        label = "Penjualan" if metric == "daily_revenue" else "Pengeluaran"
        message = (
            f"{label} hari ini Rp {today_value:,.0f} — jauh {direction} normal "
            f"(rata-rata 30 hari Rp {mean:,.0f}, z={z:.1f})"
        ).replace(",", ".")
        finding = AnomalyFinding(
            metric=metric,
            today_value=today_value,
            rolling_mean=mean,
            rolling_stddev=stddev,
            z=z,
            severity=_severity(z),
            message=message,
        )
        session.add(
            Alert(
                business_id=business.id,
                type="anomaly",
                metric=metric,
                severity=finding.severity,
                message=message,
            )
        )
        findings.append(finding)

    await session.flush()
    return findings
