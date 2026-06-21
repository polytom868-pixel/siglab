"""Portfolio Risk Guardian Module."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# Default weights for composite risk score

DEFAULT_RISK_WEIGHTS: dict[str, float] = {
    "sharpe": 0.25,
    "drawdown": 0.30,
    "concentration": 0.25,
    "correlation_risk": 0.20,
}

# Clipping bounds for sub-scores before combination
SHARPE_MIN: float = -20.0
SHARPE_MAX: float = 20.0
DRAWDOWN_MIN: float = -1.0  # -100 % max
DRAWDOWN_MAX: float = 0.0
CONCENTRATION_MIN: float = 0.0
CONCENTRATION_MAX: float = 1.0
CORRELATION_MIN: float = 0.0
CORRELATION_MAX: float = 1.0

# Score normalisation targets for composite calculation
SHARPE_TARGET: float = 3.0  # Sharpe ≥ 3 → full score
DRAWDOWN_TARGET: float = -0.20  # ≤ -20 % drawdown → zero score
CONCENTRATION_TARGET: float = 0.20  # ≥ 20 % deviation from limit → zero score
CORRELATION_TARGET: float = 0.70  # ≥ 0.70 avg correlation → zero score


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class AlertSeverity(Enum):
    """Severity levels for risk alerts."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class AlertEvent:
    """A risk alert event with timestamp, metric, severity, and value."""

    timestamp: str
    metric: str
    severity: AlertSeverity
    value: float
    threshold: float
    message: str = ""


@dataclass
class BreachReport:
    """Report of a concentration limit breach."""

    breached: bool
    allocation: dict[str, float]
    limits: dict[str, float]
    breaches: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class DrawdownEvent:
    """A historical drawdown event with peak, trough, and recovery timestamps."""

    start_date: str
    peak_date: str
    trough_date: str
    recovery_date: str | None = None
    max_drawdown_pct: float = 0.0


@dataclass
class CircuitBreakerState:
    """Tracks trading risk state for circuit breaker pattern."""

    equity: float = 0.0
    daily_start_equity: float = 0.0
    peak_equity: float = 0.0
    consecutive_losses: int = 0
    max_risk_per_trade_pct: float = 0.02  # 2 % per trade
    max_daily_drawdown_pct: float = 0.05  # 5 % daily max drawdown
    max_consecutive_losses: int = 3  # 3 losses → cooldown
    max_position_pct: float = 0.20  # 20 % per asset concentration

    def check_circuit_breakers(self) -> tuple[bool, str]:
        """Returns (passed, reason).  Refuses trades if any breaker tripped."""
        if self.daily_start_equity <= 0.0:
            return True, ""
        daily_dd = (self.equity - self.daily_start_equity) / self.daily_start_equity
        if daily_dd < -self.max_daily_drawdown_pct:
            return False, (
                f"Daily drawdown {daily_dd:.1%} exceeds limit "
                f"{self.max_daily_drawdown_pct:.0%}"
            )
        if self.consecutive_losses >= self.max_consecutive_losses:
            return False, (
                f"{self.consecutive_losses} consecutive losses exceeds limit "
                f"{self.max_consecutive_losses}"
            )
        return True, ""

    def compute_position_size(self, entry_price: float, stop_loss_price: float) -> int:
        """Fixed-fractional position sizing: risk_amount = equity * risk_percent."""
        risk_amount = self.equity * self.max_risk_per_trade_pct
        risk_per_unit = abs(entry_price - stop_loss_price)
        if risk_per_unit == 0.0:
            return 0
        return int(risk_amount / risk_per_unit)


# ---------------------------------------------------------------------------
# Composite Risk Score
# ---------------------------------------------------------------------------


def _normalize_sharpe_score(sharpe: float) -> float:
    clipped = max(SHARPE_MIN, min(SHARPE_MAX, sharpe))
    if clipped >= SHARPE_TARGET:
        return 1.0
    if clipped <= 0.0:
        return 0.0
    return clipped / SHARPE_TARGET


def _normalize_drawdown_score(drawdown: float) -> float:
    clipped = max(DRAWDOWN_MIN, min(DRAWDOWN_MAX, drawdown))
    if clipped >= 0.0:
        return 1.0
    if clipped <= DRAWDOWN_TARGET:
        return 0.0
    return 1.0 - abs(clipped) / abs(DRAWDOWN_TARGET)


def _score_below_target(clipped: float, target: float) -> float:
    """Map a clipped value to [0, 1] where 0 = at/over target, 1 = at/below 0."""
    if clipped <= 0.0:
        return 1.0
    if clipped >= target:
        return 0.0
    return 1.0 - clipped / target


def _normalize_concentration_score(deviation: float) -> float:
    clipped = max(CONCENTRATION_MIN, min(CONCENTRATION_MAX, deviation))
    return _score_below_target(clipped, CONCENTRATION_TARGET)


def _normalize_correlation_score(avg_correlation: float) -> float:
    clipped = max(CORRELATION_MIN, min(CORRELATION_MAX, avg_correlation))
    return _score_below_target(clipped, CORRELATION_TARGET)


def compute_composite_score(
    sharpe: float = 0.0,
    drawdown: float = 0.0,
    concentration: float = 0.0,
    correlation_risk: float = 0.0,
    weights: dict[str, float] | None = None,
) -> float:
    """Compute a composite portfolio risk score as a weighted sum."""
    w = weights if weights is not None else dict(DEFAULT_RISK_WEIGHTS)

    scores = {
        "sharpe": _normalize_sharpe_score(sharpe),
        "drawdown": _normalize_drawdown_score(drawdown),
        "concentration": _normalize_concentration_score(concentration),
        "correlation_risk": _normalize_correlation_score(correlation_risk),
    }

    # Only consider recognised keys
    recognised = {k: v for k, v in w.items() if k in scores}
    total_weight = sum(recognised.values())
    if total_weight <= 0.0:
        return 0.0

    composite = sum(scores[k] * recognised[k] for k in recognised) / total_weight
    return max(0.0, min(1.0, composite))


# ---------------------------------------------------------------------------
# Drawdown Calculations
# ---------------------------------------------------------------------------


def _drawdown_series(equity_curve: np.ndarray) -> np.ndarray:
    if not isinstance(equity_curve, np.ndarray) or equity_curve.size == 0:
        return np.array([], dtype=float)
    peak = np.maximum.accumulate(equity_curve)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(peak > 0, (equity_curve - peak) / peak, 0.0)


def max_drawdown(equity_curve: np.ndarray) -> float:
    """Compute the maximum drawdown of an equity curve."""
    drawdown = _drawdown_series(equity_curve)
    if drawdown.size == 0:
        return 0.0
    return float(np.min(drawdown))


def current_drawdown(equity_curve: np.ndarray) -> float:
    """Compute the current drawdown from the most recent peak."""
    if not isinstance(equity_curve, np.ndarray) or equity_curve.size == 0:
        return 0.0

    peak = np.maximum.accumulate(equity_curve)
    latest_val = equity_curve[-1]
    latest_peak = peak[-1]

    if latest_peak <= 0.0:
        return 0.0

    return float((latest_val - latest_peak) / latest_peak)


def recovery_time(equity_curve: np.ndarray) -> int | None:
    """Compute the number of periods from trough to full recovery."""
    if not isinstance(equity_curve, np.ndarray) or equity_curve.size < 2:
        return None

    peak = np.maximum.accumulate(equity_curve)

    # Check if any drawdown actually occurred
    if np.all(equity_curve >= peak):
        # No drawdown — series is monotonic or flat
        return None

    drawdown = (equity_curve - peak) / np.where(peak > 0, peak, 1.0)

    # Find the global maximum drawdown point (trough)
    trough_idx = int(np.argmin(drawdown))
    # Find the most recent peak before the trough
    pre_peak = float(np.max(equity_curve[: trough_idx + 1]))
    if pre_peak <= 0.0:
        return None

    # Check if we've recovered (equity >= pre_peak after trough)
    post_trough = equity_curve[trough_idx:]
    recovery_indices = np.where(post_trough >= pre_peak)[0]

    if len(recovery_indices) == 0:
        # Still in drawdown
        return None

    recovery_idx = int(recovery_indices[0]) + trough_idx
    return recovery_idx - trough_idx


# ---------------------------------------------------------------------------
# Correlation Analysis
# ---------------------------------------------------------------------------


def correlation_matrix(strategy_returns: list[np.ndarray]) -> np.ndarray:
    """Compute an N×N correlation matrix from N strategy return series."""
    n = len(strategy_returns)
    if n < 2:
        return np.empty((0, 0))

    # Check each series has enough data
    for i, series in enumerate(strategy_returns):
        if not isinstance(series, np.ndarray) or series.size < 2:
            return np.empty((0, 0))

    matrix = np.eye(n)

    for i in range(n):
        for j in range(i + 1, n):
            a = strategy_returns[i]
            b = strategy_returns[j]

            # Use overlapping periods only
            min_len = min(len(a), len(b))
            if min_len < 2:
                corr = 0.0
            else:
                aa = a[-min_len:]
                bb = b[-min_len:]
                # Check for constant series (std = 0)
                std_a = float(np.std(aa))
                std_b = float(np.std(bb))
                if std_a <= 0.0 or std_b <= 0.0:
                    corr = 0.0
                else:
                    corr = float(np.corrcoef(aa, bb)[0, 1])
                    # Handle NaN (can happen with edge cases)
                    if np.isnan(corr) or np.isinf(corr):
                        corr = 0.0

            matrix[i, j] = corr
            matrix[j, i] = corr

    return matrix


# ---------------------------------------------------------------------------
# Risk Limits & Alerts
# ---------------------------------------------------------------------------


def check_concentration(
    allocation: dict[str, float],
    limits: dict[str, float],
) -> BreachReport:
    """Check if strategy/category allocations exceed configured limits."""
    breaches: list[dict[str, Any]] = []
    default_limit = limits.get("default")

    for name, alloc in allocation.items():
        limit = limits.get(name, default_limit)
        if limit is None:
            continue
        if alloc > limit:
            breaches.append({
                "strategy": name,
                "allocation": alloc,
                "limit": limit,
                "excess": alloc - limit,
            })

    return BreachReport(
        breached=len(breaches) > 0,
        allocation=dict(allocation),
        limits=dict(limits),
        breaches=breaches,
    )


def check_risk_thresholds(
    metrics: dict[str, float],
    thresholds: dict[str, dict[str, Any]],
) -> list[AlertEvent]:
    """Check risk metrics against configured alert thresholds."""
    now = datetime.now(UTC).isoformat()
    events: list[AlertEvent] = []

    for metric_name, value in metrics.items():
        if metric_name not in thresholds:
            continue

        config = thresholds[metric_name]
        direction = config.get("direction", "above")

        # Iterate severity tiers from lowest to highest (info, warning, critical)
        # so that triggered events are returned in ascending order of severity.
        for severity_key, severity_enum, verb in (
            ("info", AlertSeverity.INFO, "passed"),
            ("warning", AlertSeverity.WARNING, "exceeded"),
            ("critical", AlertSeverity.CRITICAL, "exceeded"),
        ):
            tier_threshold = config.get(severity_key)
            if tier_threshold is None:
                continue
            triggered = (
                (value > tier_threshold) if direction == "above"
                else (value < tier_threshold)
            )
            if not triggered:
                continue
            events.append(AlertEvent(
                timestamp=now,
                metric=metric_name,
                severity=severity_enum,
                value=float(value),
                threshold=float(tier_threshold),
                message=(
                    f"{metric_name} = {value} {verb} {severity_key} threshold "
                    f"{tier_threshold} (direction: {direction})"
                ),
            ))

    return events


# ---------------------------------------------------------------------------
# Position Sizing
# ---------------------------------------------------------------------------


def compute_position_size(
    risk_budget: float,
    volatility: float,
    max_size: float,
) -> float:
    """Compute a risk-budget-aware position size."""
    if risk_budget < 0.0:
        risk_budget = 0.0
    if volatility <= 0.0:
        return 0.0
    if max_size < 0.0:
        return 0.0

    size = risk_budget / volatility
    return max(0.0, min(max_size, size))


# ---------------------------------------------------------------------------
# Historical Drawdown Tracking
# ---------------------------------------------------------------------------


def track_drawdown_events(equity_curve: np.ndarray) -> list[DrawdownEvent]:
    """Track all significant drawdown events from an equity curve."""
    if not isinstance(equity_curve, np.ndarray) or equity_curve.size < 2:
        return []

    def _build_event(recovery: int | None) -> DrawdownEvent:
        dd_pct = float((equity_curve[trough_idx] - equity_curve[peak_idx]) / equity_curve[peak_idx])
        return DrawdownEvent(
            start_date=f"period_{peak_idx}",
            peak_date=f"period_{peak_idx}",
            trough_date=f"period_{trough_idx}",
            recovery_date=None if recovery is None else f"period_{recovery}",
            max_drawdown_pct=dd_pct,
        )

    events: list[DrawdownEvent] = []
    n = len(equity_curve)

    peak_idx = 0
    trough_idx = 0
    in_drawdown = False

    for i in range(1, n):
        if equity_curve[i] > equity_curve[peak_idx]:
            # New peak reached
            if in_drawdown:
                # Recovery! Record event up to this point
                events.append(_build_event(i))
                in_drawdown = False
            peak_idx = i
        elif equity_curve[i] < equity_curve[peak_idx]:
            # Below peak — might be in drawdown
            if not in_drawdown:
                # Start of a new drawdown
                in_drawdown = True
                trough_idx = i
            elif equity_curve[i] < equity_curve[trough_idx]:
                # Deeper drawdown
                trough_idx = i

    # Handle final drawdown if still active
    if in_drawdown:
        events.append(_build_event(None))

    return events

