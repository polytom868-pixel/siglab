"""
Backward-compat shim — delegates to ``siglab.evaluation.events``.
"""

from siglab.evaluation.events import (  # noqa: F401
    apply_roll_exit_days,
    classify_pt_market_state,
    summarize_pt_universe,
    detect_pt_roll_events,
)
