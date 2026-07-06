"""Stock velocity / reorder-point math (PROJECT_BRIEF Section 7 — locked formulas).

average_daily_usage = trailing-window sales quantity / window days
days_remaining      = current_stock / average_daily_usage

Deterministic backend calculation — the LLM only narrates the results. Runs
nightly for every business AND synchronously after each sale write, so a sudden
depletion surfaces immediately instead of waiting for the next cron run.
"""
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Alert, Item, Sale

VELOCITY_WINDOW_DAYS = 14


@dataclass
class VelocityReading:
    item_id: uuid.UUID
    item_name: str
    unit: str
    current_stock: Decimal
    average_daily_usage: Decimal
    days_remaining: Decimal | None  # None = no usage in window ("not moving")


async def compute_item_velocity(session: AsyncSession, item: Item) -> VelocityReading:
    since = datetime.now(timezone.utc) - timedelta(days=VELOCITY_WINDOW_DAYS)
    sold = (
        await session.execute(
            select(func.coalesce(func.sum(Sale.quantity), 0)).where(
                Sale.item_id == item.id, Sale.sold_at >= since
            )
        )
    ).scalar_one()
    avg_daily = Decimal(sold) / VELOCITY_WINDOW_DAYS
    days_remaining = (
        (item.current_stock / avg_daily).quantize(Decimal("0.1")) if avg_daily > 0 else None
    )
    return VelocityReading(
        item_id=item.id,
        item_name=item.name,
        unit=item.unit,
        current_stock=item.current_stock,
        average_daily_usage=avg_daily.quantize(Decimal("0.001")),
        days_remaining=days_remaining,
    )


def _severity(days_remaining: Decimal) -> str:
    if days_remaining <= 1:
        return "high"
    if days_remaining <= 2:
        return "medium"
    return "low"


async def check_low_stock_for_item(
    session: AsyncSession, business_id: uuid.UUID, item_id: uuid.UUID
) -> Alert | None:
    """Post-sale synchronous check. Inserts a low_stock alert when the item
    drops below the configured days-remaining threshold — unless an
    unacknowledged low_stock alert for this item already exists (no nightly
    re-spam, no alert-per-sale storms)."""
    item = await session.get(Item, item_id)
    if item is None:
        return None
    reading = await compute_item_velocity(session, item)
    threshold = Decimal(str(get_settings().low_stock_days_threshold))
    if reading.days_remaining is None or reading.days_remaining > threshold:
        return None

    existing = (
        await session.execute(
            select(Alert.id).where(
                Alert.related_item_id == item_id,
                Alert.type == "low_stock",
                Alert.is_acknowledged.is_(False),
            )
        )
    ).first()
    if existing:
        return None

    alert = Alert(
        business_id=business_id,
        type="low_stock",
        related_item_id=item_id,
        metric="days_remaining",
        severity=_severity(reading.days_remaining),
        message=(
            f"{item.name}: ±{reading.days_remaining} hari tersisa "
            f"({item.current_stock} {item.unit} @ {reading.average_daily_usage}/hari)"
        ),
    )
    session.add(alert)
    await session.flush()
    return alert
