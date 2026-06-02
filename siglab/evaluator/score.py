"""
Backward-compat shim — delegates to ``siglab.evaluation.score``.
"""

from siglab.evaluation.score import (  # noqa: F401
    serialize_stats,
    summarize_window_results,
    _safe_nanmedian,
    _safe_nanmin,
    _bounded,
)
