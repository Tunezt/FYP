from datetime import datetime, timezone

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
