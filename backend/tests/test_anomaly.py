import statistics
from types import SimpleNamespace

from app.services.anomaly import (
    BASELINE_DAYS,
    Z_THRESHOLD,
    _day_fenceposts,
    _severity,
    compute_baseline,
    z_score,
)


def test_baseline_mean_and_sample_stddev():
    values = [100.0, 120.0, 80.0, 110.0, 90.0]
    mean, stddev = compute_baseline(values)
    assert mean == statistics.fmean(values)
    assert stddev == statistics.stdev(values)


def test_baseline_degenerate_inputs():
    assert compute_baseline([]) == (0.0, 0.0)
    assert compute_baseline([42.0]) == (42.0, 0.0)


def test_z_score_formula():
    # (130 - 100) / 10 = 3 exactly — at the threshold, NOT beyond it.
    assert z_score(130, 100, 10) == 3.0
    assert not (abs(z_score(130, 100, 10)) > Z_THRESHOLD)
    assert abs(z_score(131, 100, 10)) > Z_THRESHOLD
    assert z_score(70, 100, 10) == -3.0


def test_z_score_flat_baseline_returns_none():
    assert z_score(500, 100, 0) is None
    assert z_score(500, 100, -1) is None


def test_severity_tiers():
    assert _severity(3.5) == "medium"
    assert _severity(-3.5) == "medium"
    assert _severity(4.5) == "high"
    assert _severity(-4.5) == "high"


def test_fenceposts_cover_30_full_days_plus_today():
    business = SimpleNamespace(timezone="Asia/Jakarta")
    posts = _day_fenceposts(business, BASELINE_DAYS)
    # 30 history days need 31 fenceposts; +1 more for today's end.
    assert len(posts) == BASELINE_DAYS + 2
    deltas = [(posts[i + 1] - posts[i]).total_seconds() for i in range(len(posts) - 1)]
    assert all(d == 86400 for d in deltas)
