from datetime import datetime, timedelta, timezone

from app.ai.periods import PERIODS, period_range

# Fixed reference: 2026-07-07 is a Tuesday. 03:00 UTC = 10:00 WIB (Jakarta).
NOW = datetime(2026, 7, 7, 3, 0, tzinfo=timezone.utc)
TZ = "Asia/Jakarta"


def test_today_is_business_local_day():
    start, end, _ = period_range("today", TZ, NOW)
    # Jakarta midnight = 17:00 UTC previous day
    assert start == datetime(2026, 7, 6, 17, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 7, 7, 17, 0, tzinfo=timezone.utc)


def test_today_crosses_utc_date_line():
    # 20:00 UTC Monday = 03:00 WIB Tuesday — "today" must be Tuesday in Jakarta.
    late = datetime(2026, 7, 6, 20, 0, tzinfo=timezone.utc)
    start, _, _ = period_range("today", TZ, late)
    assert start == datetime(2026, 7, 6, 17, 0, tzinfo=timezone.utc)


def test_yesterday_abuts_today():
    y_start, y_end, _ = period_range("yesterday", TZ, NOW)
    t_start, _, _ = period_range("today", TZ, NOW)
    assert y_end == t_start
    assert (y_end - y_start).days == 1


def test_this_week_starts_monday():
    start, end, _ = period_range("this_week", TZ, NOW)
    # Tuesday 7 July → Monday 6 July 00:00 WIB
    assert start == datetime(2026, 7, 5, 17, 0, tzinfo=timezone.utc)
    assert (end - start).days == 7


def test_last_week_abuts_this_week():
    lw_start, lw_end, _ = period_range("last_week", TZ, NOW)
    tw_start, _, _ = period_range("this_week", TZ, NOW)
    assert lw_end == tw_start
    assert (lw_end - lw_start).days == 7


def test_month_boundaries():
    start, end, _ = period_range("this_month", TZ, NOW)
    assert start == datetime(2026, 6, 30, 17, 0, tzinfo=timezone.utc)  # 1 July WIB
    assert end == datetime(2026, 7, 31, 17, 0, tzinfo=timezone.utc)  # 1 Aug WIB
    lm_start, lm_end, _ = period_range("last_month", TZ, NOW)
    assert lm_end == start
    assert lm_start == datetime(2026, 5, 31, 17, 0, tzinfo=timezone.utc)  # 1 June WIB


def test_rolling_windows():
    start, end, _ = period_range("last_7_days", TZ, NOW)
    assert (end - start).days == 7
    start30, end30, _ = period_range("last_30_days", TZ, NOW)
    assert (end30 - start30).days == 30


def test_unknown_period_falls_back_to_today():
    assert period_range("fortnight", TZ, NOW) == period_range("today", TZ, NOW)


def test_invalid_timezone_falls_back_to_jakarta():
    assert period_range("today", "Not/AZone", NOW) == period_range("today", TZ, NOW)


def test_all_declared_periods_resolve():
    for p in PERIODS:
        start, end, label = period_range(p, TZ, NOW)
        assert start < end and label


# ── fair comparison: a part-finished period meets the same part of the last one ──
from app.ai.periods import pct_change, previous_range  # noqa: E402

# 2026-11-24 10:00 WIB (03:00 UTC) — a Tuesday, 23 days and 10 hours into November.
MID = datetime(2026, 11, 24, 3, 0, tzinfo=timezone.utc)


def test_mid_month_compares_1_to_24_nov_with_1_to_24_oct():
    cur_start, _, _ = period_range("this_month", TZ, MID)
    start, end, label = previous_range("this_month", TZ, MID)
    assert start == datetime(2026, 9, 30, 17, 0, tzinfo=timezone.utc)      # 1 Oct 00:00 WIB
    # same elapsed span as Nov 1 00:00 -> Nov 24 10:00 WIB (23 d 10 h), not all of October
    assert end - start == MID - cur_start
    assert end == datetime(2026, 10, 24, 3, 0, tzinfo=timezone.utc)        # 24 Oct 10:00 WIB
    assert "periode sama" in label


def test_previous_month_span_is_capped_at_the_months_end():
    # 31 Mar 12:00 WIB against February (28 days): the whole of February, no spill into March.
    mar31 = datetime(2026, 3, 31, 5, 0, tzinfo=timezone.utc)
    start, end, _ = previous_range("this_month", TZ, mar31)
    assert start == datetime(2026, 1, 31, 17, 0, tzinfo=timezone.utc)      # 1 Feb WIB
    assert end == datetime(2026, 2, 28, 17, 0, tzinfo=timezone.utc)        # 1 Mar WIB


def test_today_meets_yesterday_up_to_the_same_hour():
    start, end, label = previous_range("today", TZ, MID)
    assert start == datetime(2026, 11, 22, 17, 0, tzinfo=timezone.utc)     # 23 Nov 00:00 WIB
    assert end == datetime(2026, 11, 23, 3, 0, tzinfo=timezone.utc)        # 23 Nov 10:00 WIB
    assert "jam yang sama" in label


def test_this_week_meets_last_week_up_to_the_same_moment():
    cur_start, _, _ = period_range("this_week", TZ, MID)
    start, end, _ = previous_range("this_week", TZ, MID)
    assert start == cur_start - timedelta(days=7)
    assert end == MID - timedelta(days=7)


def test_finished_periods_meet_the_whole_period_before():
    for period, days in (("yesterday", 1), ("last_week", 7), ("last_7_days", 7), ("last_30_days", 30)):
        cur_start, cur_end, _ = period_range(period, TZ, MID)
        start, end, _ = previous_range(period, TZ, MID)
        assert end == cur_start and end - start == cur_end - cur_start, period
    start, end, _ = previous_range("last_month", TZ, MID)                 # October's neighbour is September
    assert start == datetime(2026, 8, 31, 17, 0, tzinfo=timezone.utc) and end == datetime(2026, 9, 30, 17, 0, tzinfo=timezone.utc)


def test_percent_change_rules():
    assert pct_change(110, 100) == 10.0
    assert pct_change(50, 100) == -50.0
    assert pct_change(5, 0) is None and pct_change(5, None) is None
    assert pct_change(-50, -100) == 50.0          # a loss that halves is an improvement
