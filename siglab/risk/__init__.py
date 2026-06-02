"""
SigLab Risk Guardian Module.

Provides portfolio risk analysis: composite scoring, drawdown monitoring,
cross-strategy correlation, concentration limits, position sizing,
and historical drawdown tracking.
"""

from siglab.risk.guardian import (
    AlertEvent,
    AlertSeverity,
    BreachReport,
    DrawdownEvent,
    check_concentration,
    check_risk_thresholds,
    compute_composite_score,
    compute_position_size,
    correlation_matrix,
    current_drawdown,
    max_drawdown,
    recovery_time,
    track_drawdown_events,
)

__all__ = [
    "AlertEvent",
    "AlertSeverity",
    "BreachReport",
    "DrawdownEvent",
    "check_concentration",
    "check_risk_thresholds",
    "compute_composite_score",
    "compute_position_size",
    "correlation_matrix",
    "current_drawdown",
    "max_drawdown",
    "recovery_time",
    "track_drawdown_events",
]
