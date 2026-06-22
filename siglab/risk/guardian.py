from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import numpy as np

DEFAULT_RISK_WEIGHTS: dict[str, float] = {
    "sharpe": 0.25,
    "drawdown": 0.30,
    "concentration": 0.25,
    "correlation_risk": 0.20,
}
SHARPE_MIN: float = -20.0
SHARPE_MAX: float = 20.0
DRAWDOWN_MIN: float = -1.0
DRAWDOWN_MAX: float = 0.0
CONCENTRATION_MIN: float = 0.0
CONCENTRATION_MAX: float = 1.0
CORRELATION_MIN: float = 0.0
CORRELATION_MAX: float = 1.0
SHARPE_TARGET: float = 3.0
DRAWDOWN_TARGET: float = -0.20
CONCENTRATION_TARGET: float = 0.20
CORRELATION_TARGET: float = 0.70


class AlertSeverity(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class AlertEvent:
    timestamp: str
    metric: str
    severity: AlertSeverity
    value: float
    threshold: float
    message: str = ""


@dataclass
class BreachReport:
    breached: bool
    allocation: dict[str, float]
    limits: dict[str, float]
    breaches: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class DrawdownEvent:
    start_date: str
    peak_date: str
    trough_date: str
    recovery_date: str | None = None
    max_drawdown_pct: float = 0.0


@dataclass
class CircuitBreakerState:
    equity: float = 0.0
    daily_start_equity: float = 0.0
    peak_equity: float = 0.0
    consecutive_losses: int = 0
    max_risk_per_trade_pct: float = 0.02
    max_daily_drawdown_pct: float = 0.05
    max_consecutive_losses: int = 3
    max_position_pct: float = 0.20

    def check_circuit_breakers(self) -> tuple[bool, str]:
        if self.daily_start_equity <= 0.0:
            return True, ""
        daily_dd = (self.equity - self.daily_start_equity) / self.daily_start_equity
        if daily_dd < -self.max_daily_drawdown_pct:
            return (
                False,
                f"Daily drawdown {daily_dd:.1%} exceeds limit {self.max_daily_drawdown_pct:.0%}",
            )
        if self.consecutive_losses >= self.max_consecutive_losses:
            return (
                False,
                f"{self.consecutive_losses} consecutive losses exceeds limit {self.max_consecutive_losses}",
            )
        return True, ""

    def compute_position_size(self, entry_price: float, stop_loss_price: float) -> int:
        risk_amount = self.equity * self.max_risk_per_trade_pct
        risk_per_unit = abs(entry_price - stop_loss_price)
        return 0 if risk_per_unit == 0.0 else int(risk_amount / risk_per_unit)


def _norm_sharpe(sharpe: float) -> float:
    clipped = max(SHARPE_MIN, min(SHARPE_MAX, sharpe))
    if clipped >= SHARPE_TARGET:
        return 1.0
    if clipped <= 0.0:
        return 0.0
    return clipped / SHARPE_TARGET


def _norm_dd(drawdown: float) -> float:
    clipped = max(DRAWDOWN_MIN, min(DRAWDOWN_MAX, drawdown))
    if clipped >= 0.0:
        return 1.0
    if clipped <= DRAWDOWN_TARGET:
        return 0.0
    return 1.0 - abs(clipped) / abs(DRAWDOWN_TARGET)


def _score_below_target(clipped: float, target: float) -> float:
    if clipped <= 0.0:
        return 1.0
    if clipped >= target:
        return 0.0
    return 1.0 - clipped / target


def _norm_conc(deviation: float) -> float:
    return _score_below_target(
        max(CONCENTRATION_MIN, min(CONCENTRATION_MAX, deviation)), CONCENTRATION_TARGET
    )


def _norm_corr(avg_correlation: float) -> float:
    return _score_below_target(
        max(CORRELATION_MIN, min(CORRELATION_MAX, avg_correlation)), CORRELATION_TARGET
    )


def compute_composite_score(
    sharpe: float = 0.0,
    drawdown: float = 0.0,
    concentration: float = 0.0,
    correlation_risk: float = 0.0,
    weights: dict[str, float] | None = None,
) -> float:
    w = weights if weights is not None else dict(DEFAULT_RISK_WEIGHTS)
    scores = {
        "sharpe": _norm_sharpe(sharpe),
        "drawdown": _norm_dd(drawdown),
        "concentration": _norm_conc(concentration),
        "correlation_risk": _norm_corr(correlation_risk),
    }
    recognised = {k: v for k, v in w.items() if k in scores}
    total_weight = sum(recognised.values())
    return (
        0.0
        if total_weight <= 0.0
        else max(
            0.0,
            min(1.0, sum(scores[k] * recognised[k] for k in recognised) / total_weight),
        )
    )


def _dd_series(equity_curve: np.ndarray) -> np.ndarray:
    if not isinstance(equity_curve, np.ndarray) or equity_curve.size == 0:
        return np.array([], dtype=float)
    peak = np.maximum.accumulate(equity_curve)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(peak > 0, (equity_curve - peak) / peak, 0.0)


def max_drawdown(equity_curve: np.ndarray) -> float:
    dd = _dd_series(equity_curve)
    return 0.0 if dd.size == 0 else float(np.min(dd))


def current_drawdown(equity_curve: np.ndarray) -> float:
    if not isinstance(equity_curve, np.ndarray) or equity_curve.size == 0:
        return 0.0
    peak = np.maximum.accumulate(equity_curve)
    latest_val = equity_curve[-1]
    latest_peak = peak[-1]
    return (
        0.0 if latest_peak <= 0.0 else float((latest_val - latest_peak) / latest_peak)
    )


def recovery_time(equity_curve: np.ndarray) -> int | None:
    if not isinstance(equity_curve, np.ndarray) or equity_curve.size < 2:
        return None
    peak = np.maximum.accumulate(equity_curve)
    if np.all(equity_curve >= peak):
        return None
    drawdown = (equity_curve - peak) / np.where(peak > 0, peak, 1.0)
    trough_idx = int(np.argmin(drawdown))
    pre_peak = float(np.max(equity_curve[: trough_idx + 1]))
    if pre_peak <= 0.0:
        return None
    recovery_indices = np.where(equity_curve[trough_idx:] >= pre_peak)[0]
    return (
        None
        if len(recovery_indices) == 0
        else int(recovery_indices[0]) + trough_idx - trough_idx
    )


def correlation_matrix(strategy_returns: list[np.ndarray]) -> np.ndarray:
    n = len(strategy_returns)
    if n < 2:
        return np.empty((0, 0))
    for i, series in enumerate(strategy_returns):
        if not isinstance(series, np.ndarray) or series.size < 2:
            return np.empty((0, 0))
    matrix = np.eye(n)
    for i in range(n):
        for j in range(i + 1, n):
            a = strategy_returns[i]
            b = strategy_returns[j]
            min_len = min(len(a), len(b))
            if min_len < 2:
                corr = 0.0
            else:
                aa, bb = a[-min_len:], b[-min_len:]
                std_a, std_b = float(np.std(aa)), float(np.std(bb))
                if std_a <= 0.0 or std_b <= 0.0:
                    corr = 0.0
                else:
                    corr = float(np.corrcoef(aa, bb)[0, 1])
                    if np.isnan(corr) or np.isinf(corr):
                        corr = 0.0
            matrix[i, j] = matrix[j, i] = corr
    return matrix


def check_concentration(
    allocation: dict[str, float], limits: dict[str, float]
) -> BreachReport:
    breaches: list[dict[str, Any]] = []
    default_limit = limits.get("default")
    for name, alloc in allocation.items():
        limit = limits.get(name, default_limit)
        if limit is None:
            continue
        if alloc > limit:
            breaches.append(
                {
                    "strategy": name,
                    "allocation": alloc,
                    "limit": limit,
                    "excess": alloc - limit,
                }
            )
    return BreachReport(
        breached=len(breaches) > 0,
        allocation=dict(allocation),
        limits=dict(limits),
        breaches=breaches,
    )


def check_risk_thresholds(
    metrics: dict[str, float], thresholds: dict[str, dict[str, Any]]
) -> list[AlertEvent]:
    now = datetime.now(UTC).isoformat()
    events: list[AlertEvent] = []
    for metric_name, value in metrics.items():
        if metric_name not in thresholds:
            continue
        config = thresholds[metric_name]
        direction = config.get("direction", "above")
        for severity_key, severity_enum, verb in (
            ("info", AlertSeverity.INFO, "passed"),
            ("warning", AlertSeverity.WARNING, "exceeded"),
            ("critical", AlertSeverity.CRITICAL, "exceeded"),
        ):
            tier_threshold = config.get(severity_key)
            if tier_threshold is None:
                continue
            triggered = (
                (value > tier_threshold)
                if direction == "above"
                else (value < tier_threshold)
            )
            if not triggered:
                continue
            events.append(
                AlertEvent(
                    timestamp=now,
                    metric=metric_name,
                    severity=severity_enum,
                    value=float(value),
                    threshold=float(tier_threshold),
                    message=f"{metric_name} = {value} {verb} {severity_key} threshold {tier_threshold} (direction: {direction})",
                )
            )
    return events


def compute_position_size(
    risk_budget: float, volatility: float, max_size: float
) -> float:
    if risk_budget < 0.0:
        risk_budget = 0.0
    if volatility <= 0.0:
        return 0.0
    if max_size < 0.0:
        return 0.0
    return max(0.0, min(max_size, risk_budget / volatility))


def track_drawdown_events(equity_curve: np.ndarray) -> list[DrawdownEvent]:
    if not isinstance(equity_curve, np.ndarray) or equity_curve.size < 2:
        return []

    def _build_event(recovery: int | None) -> DrawdownEvent:
        dd_pct = float(
            (equity_curve[trough_idx] - equity_curve[peak_idx]) / equity_curve[peak_idx]
        )
        return DrawdownEvent(
            start_date=f"period_{peak_idx}",
            peak_date=f"period_{peak_idx}",
            trough_date=f"period_{trough_idx}",
            recovery_date=None if recovery is None else f"period_{recovery}",
            max_drawdown_pct=dd_pct,
        )

    events: list[DrawdownEvent] = []
    n = len(equity_curve)
    peak_idx = trough_idx = 0
    in_drawdown = False
    for i in range(1, n):
        if equity_curve[i] > equity_curve[peak_idx]:
            if in_drawdown:
                events.append(_build_event(i))
                in_drawdown = False
            peak_idx = i
        elif equity_curve[i] < equity_curve[peak_idx]:
            if not in_drawdown:
                in_drawdown = True
                trough_idx = i
            elif equity_curve[i] < equity_curve[trough_idx]:
                trough_idx = i
    if in_drawdown:
        events.append(_build_event(None))
    return events


_drawdown_series = _dd_series
_normalize_sharpe_score = _norm_sharpe
_normalize_drawdown_score = _norm_dd
_normalize_concentration_score = _norm_conc
_normalize_correlation_score = _norm_corr
