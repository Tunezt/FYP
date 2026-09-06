"""Named period → UTC datetime range, computed in the business's timezone and
on the business's day boundary.

"today" for a Jakarta café means Jakarta's today, not the server's — every
period boundary is derived in the business tz, then converted to UTC for
querying timestamptz columns.

A café that closes at 23:30 settles its last bill after midnight, so a
*calendar* day cuts the night in half (roadmap M15-T4). `day_start_hour`
moves the boundary: with 4, the business day runs 04:00 → 04:00 and a sale at
00:15 belongs to the day before, which is the day the owner counted the till
for. Every period is anchored on it — the week starts on the Monday business
day, the month on the 1st at `day_start_hour` — so the pieces still abut with
no gap and no overlap. The default 0 is the plain calendar day.

A business day is named by the date it *starts* on: the day beginning
Monday 04:00 and ending Tuesday 04:00 is Monday.
"""
from datetime import date, datetime, time, timedelta, timezone
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


def _zone(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("Asia/Jakarta")


def _hour(day_start_hour: int | None) -> int:
    """Out-of-range or missing means the calendar day. The DDL constrains the
    column to 0..23; this keeps a bad value from silently shifting the books."""
    try:
        h = int(day_start_hour or 0)
    except (TypeError, ValueError):
        return 0
    return h if 0 <= h <= 23 else 0


def business_day(at_utc: datetime, tz_name: str, day_start_hour: int = 0) -> date:
    """The business-local day an instant belongs to. With `day_start_hour = 4`,
    Tuesday 00:15 is Monday."""
    local = at_utc.astimezone(_zone(tz_name))
    return local.date() - timedelta(days=1) if local.hour < _hour(day_start_hour) else local.date()


def day_bounds(day: date, tz_name: str, day_start_hour: int = 0) -> tuple[datetime, datetime]:
    """[start, end) of one business day, in UTC."""
    tz = _zone(tz_name)
    start = datetime.combine(day, time(hour=_hour(day_start_hour)), tzinfo=tz)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def period_range(
    period: str, tz_name: str, now: datetime | None = None, day_start_hour: int = 0
) -> tuple[datetime, datetime, str]:
    """Returns (start_utc_inclusive, end_utc_exclusive, human_label)."""
    tz = _zone(tz_name)
    start_hour = _hour(day_start_hour)
    moment = now or datetime.now(timezone.utc)
    today_date = business_day(moment, tz_name, start_hour)
    today = datetime.combine(today_date, time(hour=start_hour), tzinfo=tz)

    if period == "today":
        start, end, label = today, today + timedelta(days=1), "hari ini"
    elif period == "yesterday":
        start, end, label = today - timedelta(days=1), today, "kemarin"
    elif period == "this_week":  # Monday-based, on the business day
        start = today - timedelta(days=today_date.weekday())
        start, end, label = start, start + timedelta(days=7), "minggu ini"
    elif period == "last_week":
        this_monday = today - timedelta(days=today_date.weekday())
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
