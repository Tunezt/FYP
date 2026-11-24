"""The metric layer (roadmap M9): one declarative registry, one implementation
per metric, and nothing else in the app computing these numbers.

    from app.metrics import compute, list_metrics, MetricNotFound
"""
from app.metrics.registry import (  # noqa: F401
    Comparison,
    MetricContext,
    MetricNotFound,
    MetricResult,
    MetricSpec,
    REGISTRY,
    compare_with_previous,
    compute,
    get_metric,
    list_metrics,
    local_day_windows,
    local_month_windows,
    metric,
    series,
)
from app.metrics import catalogue  # noqa: E402,F401  (registers the standard metrics)
