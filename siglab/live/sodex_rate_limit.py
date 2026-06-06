"""Backward-compatible re-export. Canonical location: siglab.data.sodex_rate_limit."""
from siglab.data.sodex_rate_limit import (  # noqa: F401
    SODEX_DEFAULT_ENDPOINT_WEIGHT,
    SODEX_ENDPOINT_WEIGHTS,
    SODEX_WEIGHT_BUDGET_PER_MINUTE,
    SoDEXWeightLimitError,
    SoDEXWeightMetrics,
    SoDEXWeightScheduler,
)

__all__ = [
    "SODEX_DEFAULT_ENDPOINT_WEIGHT",
    "SODEX_ENDPOINT_WEIGHTS",
    "SODEX_WEIGHT_BUDGET_PER_MINUTE",
    "SoDEXWeightLimitError",
    "SoDEXWeightMetrics",
    "SoDEXWeightScheduler",
]
