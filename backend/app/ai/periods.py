"""Named period → UTC datetime range, computed in the business's timezone.

"today" for a Jakarta café means Jakarta's today, not the server's — every
period boundary is derived in the business tz, then converted to UTC for
querying timestamptz columns.
"""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

PERIODS = [
    "today",
    "yesterday",
    "this_week",
    "last_week",
    "this_month",
    "last_month",
    "last_7_days",
    "last_30_days",
]


def period_range(period: str, tz_name: str, now: datetime | None = None) -> tuple[datetime, datetime, str]:
    """Returns (start_utc_inclusive, end_utc_exclusive, human_label)."""
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Asia/Jakarta")
    now_local = (now or datetime.now(timezone.utc)).astimezone(tz)
    today = datetime.combine(now_local.date(), time.min, tzinfo=tz)

    if period == "today":
        start, end, label = today, today + timedelta(days=1), "hari ini"
    elif period == "yesterday":
        start, end, label = today - timedelta(days=1), today, "kemarin"
    elif period == "this_week":  # Monday-based
        start = today - timedelta(days=now_local.weekday())
        start, end, label = start, start + timedelta(days=7), "minggu ini"
    elif period == "last_week":
        this_monday = today - timedelta(days=now_local.weekday())
        start, end, label = this_monday - timedelta(days=7), this_monday, "minggu lalu"
    elif period == "this_month":
        start = today.replace(day=1)
        end = (start + timedelta(days=32)).replace(day=1)
        label = "bulan ini"
    elif period == "last_month":
        this_first = today.replace(day=1)
        start = (this_first - timedelta(days=1)).replace(day=1)
        start, end, label = start, this_first, "bulan lalu"
    elif period == "last_7_days":
        start, end, label = today - timedelta(days=6), today + timedelta(days=1), "7 hari terakhir"
    elif period == "last_30_days":
        start, end, label = today - timedelta(days=29), today + timedelta(days=1), "30 hari terakhir"
    else:
        # Unknown period string from the model — default to today rather than
        # erroring the whole conversation; the label makes the fallback visible.
        start, end, label = today, today + timedelta(days=1), "hari ini"

    return start.astimezone(timezone.utc), end.astimezone(timezone.utc), label
