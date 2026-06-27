from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, cast
from collections.abc import Awaitable, Callable, Sequence


def percentile(values: list[float], percentile: int) -> float | None:
    """Calculate percentile using R-7 linear interpolation."""
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    if n == 1:
        return float(ordered[0])
    rank = percentile / 100.0 * (n - 1)
    lower_idx = min(max(math.floor(rank), 0), n - 1)
    upper_idx = min(max(math.ceil(rank), 0), n - 1)
    if lower_idx == upper_idx:
        return float(ordered[lower_idx])
    frac = rank - lower_idx
    return float(ordered[lower_idx] + frac * (ordered[upper_idx] - ordered[lower_idx]))


def safe_float(
    value: float | str | None,
    *,
    digits: int = 8,
    default: float | None = None,
) -> float | None:
    """Convert value to float safely. Returns default on failure, None, or NaN."""
    if value is None:
        return default
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(numeric):
        return default
    return round(numeric, digits)


def int_or_zero(value: str | int | None) -> int:
    """Convert value to non-negative int. Returns 0 on failure or negative."""
    if value is None:
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


_sha256 = hashlib.sha256


def feature_hash(features: list[str], length: int = 16) -> str:
    """Deterministic hash of a feature list. Order-independent."""
    payload = "|".join(sorted(str(f) for f in features))
    return _sha256(payload.encode("utf-8")).hexdigest()[:length]


def short_hash(payload: str, length: int = 16) -> str:
    """Truncated SHA-256 hex digest."""
    return _sha256(payload.encode("utf-8")).hexdigest()[:length]


async def _get_url(url: str, **kw: Any) -> dict[str, Any]:
    import aiohttp

    async with aiohttp.ClientSession() as session:
        async with session.get(url, **kw) as resp:
            return cast(dict[str, Any], await resp.json())


async def _post_url(url: str, payload: dict[str, Any], **kw: Any) -> dict[str, Any]:
    import aiohttp

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, **kw) as resp:
            return cast(dict[str, Any], await resp.json())


async def run_with_backoff(
    coro_factory: Callable[[], Awaitable[Any]],
    *,
    max_retries: int = 3,
    backoff_s: float = 1.0,
) -> Any:
    import asyncio

    attempt = 0
    while True:
        try:
            return await coro_factory()
        except Exception:
            attempt += 1
            if attempt >= max_retries:
                raise
            import logging

            logging.getLogger(__name__).exception(
                "run_with_backoff attempt %d/%d failed, retrying",
                attempt,
                max_retries,
            )
            await asyncio.sleep(backoff_s * 2 ** (attempt - 1))


async def async_limiter_call(
    callable: Callable[[], Awaitable[Any]],
    *,
    rate_limit: int = 20,
) -> Any:
    import asyncio

    sem = asyncio.Semaphore(rate_limit)
    async with sem:
        return await callable()


def _now_iso() -> str:
    """Current UTC timestamp as ISO-8601 string (microsecond precision)."""
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _compact_scalar(value: object) -> object:
    if isinstance(value, str) and len(value) > 2200:
        return value[:2199].rstrip() + "…"
    return value


def _estimate_message_tokens(messages: Sequence[dict[str, Any]]) -> int:
    """Conservative cheap token estimate from JSON serialization length."""
    import json as _json

    chars = len(_json.dumps(list(messages), ensure_ascii=True, default=str))
    return max(1, (chars + 3) // 4)


def dget(d: dict | None, *keys: str, default: object = None) -> object:
    """Safe nested dict access without intermediate copies."""
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k)
        if d is None:
            return default
    return d


def load_json_path(
    value: str | Path | None,
    *,
    root_dir: Path | None = None,
) -> dict[str, Any] | None:
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute() and root_dir is not None:
        path = (root_dir / path).resolve()
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def write_json(
    path: Path,
    payload: object,
    *,
    indent: int = 2,
    ensure_ascii: bool = True,
) -> None:
    path.write_text(
        json.dumps(payload, indent=indent, ensure_ascii=ensure_ascii, default=str),
    )


def json_clone(value: object) -> object:
    return json.loads(json.dumps(value, ensure_ascii=True, default=str))


def json_safe(value: object) -> object:
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    if isinstance(value, float) and (not math.isfinite(value)):
        return None
    return value

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
import logging
import pickle
import time
from pathlib import Path
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


logger = logging.getLogger(__name__)
STALE_THRESHOLD_SECONDS = 7 * 24 * 3600


def load_equity_curves(sessions_dir: Path) -> list[tuple[str, np.ndarray]]:
    """Load all .npy session files and extract equity curves."""
    npy_files = sorted(sessions_dir.glob("*.npy"))
    curves: list[tuple[str, np.ndarray]] = []
    for npy_file in npy_files:
        mtime = npy_file.stat().st_mtime
        if time.time() - mtime > STALE_THRESHOLD_SECONDS:
            logger.warning(
                "Session %s is stale (last modified %ds ago), skipping",
                npy_file.stem,
                int(time.time() - mtime),
            )
            continue
        try:
            data = np.load(npy_file, allow_pickle=True)
            if isinstance(data, np.ndarray) and data.size > 0:
                if data.dtype.names is not None and "equity" in data.dtype.names:
                    eq = data["equity"]
                    if isinstance(eq, np.ndarray) and eq.size > 0:
                        curves.append((npy_file.stem, eq.astype(float)))
                elif data.dtype in (np.float64, np.float32):
                    curves.append((npy_file.stem, data))
        except (OSError, ValueError, TypeError, pickle.UnpicklingError):
            logger.debug("Failed to load npy equity curve %s", npy_file)
            continue
    return curves


def empty_risk_response() -> dict[str, Any]:
    """Return an empty risk response with all fields set to None/empty."""
    return {
        "composite_score": None,
        "max_drawdown": None,
        "correlation_matrix": None,
        "strategy_count": 0,
        "strategy_names": [],
        "sub_scores": {},
        "current_drawdown": None,
        "recovery_periods": None,
        "drawdown_history": [],
        "alerts": [],
        "sharpe_ratio": 0.0,
    }


def compute_risk_metrics(
    sessions_dir: Path,
    *,
    periods_per_year: int = 365,
) -> dict[str, Any]:
    """Compute full risk metrics from session data."""
    curves = load_equity_curves(sessions_dir)
    if not curves:
        return empty_risk_response()
    session_names = [name for name, _ in curves]
    equity_arrays = [eq for _, eq in curves]
    if equity_arrays:
        all_max_dds = [float(max_drawdown(eq)) for eq in equity_arrays]
        all_cur_dds = [float(current_drawdown(eq)) for eq in equity_arrays]
        max_dd = min(all_max_dds)
        cur_dd = min(all_cur_dds)
        worst_idx = all_max_dds.index(max_dd)
        rec_time = recovery_time(equity_arrays[worst_idx])
    else:
        max_dd = cur_dd = 0.0
        rec_time = None
        worst_idx = 0
    first_eq = equity_arrays[worst_idx] if equity_arrays else np.array([])
    dd_series = _drawdown_series(first_eq)
    if dd_series.size > 0:
        n = len(dd_series)
        if n > 60:
            step = n / 60
            dd_history: list[float] = [
                float(dd_series[int(i * step)]) for i in range(60)
            ]
        else:
            dd_history = dd_series.tolist()
    else:
        dd_history = []
    returns_list = []
    for eq in equity_arrays:
        if eq.size >= 2:
            rets = np.diff(eq) / np.where(eq[:-1] != 0, eq[:-1], 1.0)
            returns_list.append(rets)
    sharpe = 0.0
    if returns_list:
        sharpes = []
        for rets in returns_list:
            s = np.std(rets, ddof=1)
            if s > 0:
                sharpes.append(float(np.mean(rets) / s * np.sqrt(periods_per_year)))
        sharpe = float(np.mean(sharpes)) if sharpes else 0.0
    corr_matrix: list[list[float]] | None = None
    if len(returns_list) >= 2:
        matrix = correlation_matrix(returns_list)
        if matrix.size > 0:
            corr_matrix = matrix.tolist()
    avg_corr = 0.0
    if corr_matrix is not None and len(corr_matrix) >= 2:
        num = len(corr_matrix)
        corr_values = []
        for i in range(num):
            for j in range(i + 1, num):
                corr_values.append(corr_matrix[i][j])
        avg_corr = float(np.mean(corr_values)) if corr_values else 0.0
    n = len(returns_list)
    hhi = 1.0 / n if n > 0 else 1.0
    concentration = 1.0 - hhi
    sub_scores = {
        "sharpe": _normalize_sharpe_score(sharpe),
        "drawdown": _normalize_drawdown_score(max_dd),
        "concentration": _normalize_concentration_score(concentration),
        "correlation_risk": _normalize_correlation_score(avg_corr),
    }
    composite: float | None = None
    composite = float(
        compute_composite_score(
            sharpe=sharpe,
            drawdown=max_dd,
            concentration=concentration,
            correlation_risk=avg_corr,
        ),
    )
    alerts: list[dict[str, Any]] = []
    worst_eq = (
        equity_arrays[worst_idx]
        if equity_arrays and worst_idx < len(equity_arrays)
        else np.array([])
    )
    events = track_drawdown_events(worst_eq) if worst_eq.size > 0 else []
    for event in events[-20:]:
        sev = "warning" if abs(event.max_drawdown_pct) < 0.15 else "critical"
        alerts.append(
            {
                "timestamp": event.trough_date,
                "metric": "drawdown",
                "severity": sev,
                "value": event.max_drawdown_pct,
                "threshold": 0.0,
                "message": f"Drawdown {event.max_drawdown_pct * 100:.1f}% ({event.peak_date} → {event.trough_date})",
            },
        )
    return {
        "composite_score": composite,
        "max_drawdown": max_dd,
        "correlation_matrix": corr_matrix,
        "strategy_count": len(equity_arrays),
        "strategy_names": session_names,
        "sub_scores": sub_scores,
        "current_drawdown": cur_dd,
        "recovery_periods": rec_time,
        "drawdown_history": dd_history,
        "alerts": alerts,
        "sharpe_ratio": sharpe,
    }

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
        max(CONCENTRATION_MIN, min(CONCENTRATION_MAX, deviation)),
        CONCENTRATION_TARGET,
    )


def _norm_corr(avg_correlation: float) -> float:
    return _score_below_target(
        max(CORRELATION_MIN, min(CORRELATION_MAX, avg_correlation)),
        CORRELATION_TARGET,
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


def track_drawdown_events(equity_curve: np.ndarray) -> list[DrawdownEvent]:
    if not isinstance(equity_curve, np.ndarray) or equity_curve.size < 2:
        return []

    def _build_event(recovery: int | None) -> DrawdownEvent:
        dd_pct = float(
            (equity_curve[trough_idx] - equity_curve[peak_idx])
            / equity_curve[peak_idx],
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


def check_concentration(
    allocation: dict[str, float],
    limits: dict[str, float],
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
                },
            )
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
                ),
            )
    return events


def compute_position_size(
    risk_budget: float,
    volatility: float,
    max_size: float,
) -> float:
    risk_budget = max(risk_budget, 0.0)
    if volatility <= 0.0:
        return 0.0
    if max_size < 0.0:
        return 0.0
    return max(0.0, min(max_size, risk_budget / volatility))
