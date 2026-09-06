"""The metric layer (roadmap M9): one declarative registry, one implementation
per metric, and nothing else in the app computing these numbers.

    from app.metrics import compute, list_metrics, MetricNotFound
"""
from app.metrics.registry import (  # noqa: F401
    MetricContext,
    MetricNotFound,
    MetricResult,
    MetricSpec,
    REGISTRY,
    compute,
    get_metric,
    list_metrics,
    metric,
)
from app.metrics import catalogue  # noqa: E402,F401  (registers the standard metrics)
