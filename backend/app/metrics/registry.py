"""The metric registry (roadmap M9-T1).

A metric is declared once — name, Indonesian and English description, the
unit of its value, the dimensions it can be sliced by, the time grains it
supports — and implemented once. The assistant's tools (M9-T2) and the
dashboard (M9-T3) both call `compute`, so they cannot disagree: there is no
second copy of the arithmetic to drift.

Two kinds of grain:

  period    the metric is a sum or ratio over [since, until) — a named
            period from app.ai.periods, or a custom range
  instant   the metric describes the state now (stock on hand, days
            remaining) and ignores the window

A metric returns a single `value` (rupiah, count, pct, …) and/or `rows` for
list-shaped metrics (top items, stock per item, revenue by hour). Unknown
metric names raise `MetricNotFound`, never guess.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.periods import PERIODS, period_range
from app.models import Business

UNITS = ("rupiah", "count", "pct", "qty", "days", "hour", "list")
GRAINS = ("period", "instant")
DIMENSIONS = ("item",)


class MetricNotFound(Exception):
    def __init__(self, name: str):
        self.name = name
        super().__init__(name)


class MetricArgumentInvalid(Exception):
    """`code`: period, range, dimension."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}:{detail}")


@dataclass(frozen=True)
class MetricContext:
    business: Business
    since: datetime
    until: datetime
    period: str | None = None          # the named period, when one was used
    period_label: str = ""
    item_id: uuid.UUID | None = None   # the `item` dimension
    limit: int = 5
    now: datetime | None = None

    @property
    def tz(self) -> str:
        return self.business.timezone


@dataclass
class MetricResult:
    name: str
    unit: str
    value: Decimal | int | None = None
    rows: list[dict[str, Any]] = field(default_factory=list)
    since: datetime | None = None
    until: datetime | None = None
    period: str | None = None
    period_label: str = ""
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "unit": self.unit,
            "value": (float(self.value) if isinstance(self.value, Decimal) else self.value),
            "rows": self.rows, "since": self.since, "until": self.until,
            "period": self.period, "period_label": self.period_label, "note": self.note,
        }


Implementation = Callable[[AsyncSession, MetricContext], Awaitable[MetricResult]]


@dataclass(frozen=True)
class MetricSpec:
    name: str
    description_id: str
    description_en: str
    unit: str
    grains: tuple[str, ...] = ("period",)
    dimensions: tuple[str, ...] = ()
    implementation: Implementation | None = None

    def __post_init__(self) -> None:
        assert self.unit in UNITS, self.unit
        assert self.grains and all(g in GRAINS for g in self.grains), self.grains
        assert all(d in DIMENSIONS for d in self.dimensions), self.dimensions
        assert self.description_id.strip() and self.description_en.strip(), self.name

    @property
    def is_instant(self) -> bool:
        return self.grains == ("instant",)

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name, "description_id": self.description_id, "description_en": self.description_en,
            "unit": self.unit, "grains": list(self.grains), "dimensions": list(self.dimensions),
        }


REGISTRY: dict[str, MetricSpec] = {}


def metric(name: str, *, description_id: str, description_en: str, unit: str,
           grains: tuple[str, ...] = ("period",), dimensions: tuple[str, ...] = ()):
    """Declare a metric and bind its one implementation. Declaring a name
    twice is a programming error, not an override."""

    def register(fn: Implementation) -> Implementation:
        if name in REGISTRY:
            raise RuntimeError(f"metric {name!r} is already registered")
        REGISTRY[name] = MetricSpec(
            name=name, description_id=description_id, description_en=description_en, unit=unit,
            grains=grains, dimensions=dimensions, implementation=fn,
        )
        return fn

    return register


def list_metrics() -> list[MetricSpec]:
    return [REGISTRY[k] for k in sorted(REGISTRY)]


def get_metric(name: str) -> MetricSpec:
    spec = REGISTRY.get((name or "").strip())
    if spec is None:
        raise MetricNotFound(name)
    return spec


def resolve_window(
    business: Business, *, period: str | None = None, since: datetime | None = None,
    until: datetime | None = None, now: datetime | None = None,
) -> tuple[datetime, datetime, str | None, str]:
    """A named period in the business's timezone, or an explicit [since, until)."""
    if period:
        if period not in PERIODS:
            raise MetricArgumentInvalid("period", period)
        start, end, label = period_range(period, business.timezone, now=now)
        return start, end, period, label
    if since is None or until is None:
        raise MetricArgumentInvalid("range", "since and until are required without a period")
    if until <= since:
        raise MetricArgumentInvalid("range", "until must be after since")
    return since, until, None, ""


async def compute(
    session: AsyncSession, business: Business, name: str, *, period: str | None = None,
    since: datetime | None = None, until: datetime | None = None, item_id: uuid.UUID | None = None,
    limit: int = 5, now: datetime | None = None,
) -> MetricResult:
    """The one entry point. Instant metrics ignore the window (and accept none)."""
    spec = get_metric(name)
    if item_id is not None and "item" not in spec.dimensions:
        raise MetricArgumentInvalid("dimension", f"{name} has no item dimension")
    if spec.is_instant:
        moment = now or datetime.now(timezone.utc)
        ctx = MetricContext(business=business, since=moment, until=moment, period=None, period_label="sekarang",
                            item_id=item_id, limit=limit, now=moment)
    else:
        start, end, named, label = resolve_window(business, period=period, since=since, until=until, now=now)
        ctx = MetricContext(business=business, since=start, until=end, period=named, period_label=label,
                            item_id=item_id, limit=limit, now=now)
    result = await spec.implementation(session, ctx)  # type: ignore[misc]
    result.name = spec.name
    result.unit = spec.unit
    result.since, result.until, result.period, result.period_label = ctx.since, ctx.until, ctx.period, ctx.period_label
    return result
