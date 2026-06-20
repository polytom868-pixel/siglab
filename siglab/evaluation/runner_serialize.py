"""Serialization helpers extracted from runner.py."""

from __future__ import annotations

from typing import Any, Callable, cast

import numpy as np
import pandas as pd

from siglab.utils import safe_float as _safe_float


def _policy_summary_spec(policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "entry_abs_score": float(policy["entry_abs_score"]),
        "exit_abs_score": float(policy["exit_abs_score"]),
        "flip_abs_score": float(policy["flip_abs_score"]),
        "max_holding_bars": int(policy["max_holding_bars"]),
        "cooldown_bars": int(policy["cooldown_bars"]),
    }


def _unique_float_values(values: list[float], *, low: float, high: float) -> list[float]:
    cleaned: list[float] = []
    seen: set[float] = set()
    for value in values:
        numeric = round(max(low, min(high, float(value))), 6)
        if numeric in seen:
            continue
        seen.add(numeric)
        cleaned.append(numeric)
    return cleaned


def _unique_int_values(values: list[int], *, low: int, high: int) -> list[int]:
    cleaned: list[int] = []
    seen: set[int] = set()
    for value in values:
        numeric = max(low, min(high, int(value)))
        if numeric in seen:
            continue
        seen.add(numeric)
        cleaned.append(numeric)
    return cleaned


def _serialize_series(series: pd.Series, digits: int = 8) -> dict[str, Any]:
    clean = pd.to_numeric(series, errors="coerce")
    return {
        "index": [timestamp.isoformat() for timestamp in clean.index],
        "values": [_safe_float(value, digits=digits) for value in clean.tolist()],
    }


def _serialize_metrics_frame(frame: pd.DataFrame, digits: int = 8) -> dict[str, Any]:
    normalized = frame.copy()
    if "fee_amount" not in normalized.columns:
        if "cost" in normalized.columns:
            normalized["fee_amount"] = pd.to_numeric(normalized["cost"], errors="coerce").fillna(0.0)
        else:
            normalized["fee_amount"] = 0.0
    if "funding_amount" not in normalized.columns:
        normalized["funding_amount"] = 0.0
    if "cash_balance" not in normalized.columns:
        normalized["cash_balance"] = np.nan
    if "margin_headroom" not in normalized.columns:
        normalized["margin_headroom"] = np.nan
    return {
        "index": [timestamp.isoformat() for timestamp in normalized.index],
        "columns": list(normalized.columns),
        "rows": [
            [_safe_float(value, digits=digits) for value in row]
            for row in normalized.itertuples(index=False, name=None)
        ],
    }


def _serialize_weight_changes(
    frame: pd.DataFrame,
    *,
    digits: int = 6,
    epsilon: float = 1e-9,
) -> dict[str, Any]:
    changes: list[dict[str, Any]] = []
    previous: dict[str, float] | None = None
    for timestamp_raw, row in frame.iterrows():
        timestamp = cast(pd.Timestamp, timestamp_raw)
        current = cast(
            dict[str, float],
            {
                column: round(float(value), digits)
                for column, value in row.items()
                if pd.notna(value) and abs(float(value)) > epsilon
            },
        )
        if current == previous:
            continue
        changes.append(
            {
                "timestamp": timestamp.isoformat(),
                "weights": current,
            }
        )
        previous = current
    return {
        "index": [timestamp.isoformat() for timestamp in frame.index],
        "columns": list(frame.columns),
        "changes": changes,
    }


def _serialize_trades(
    trades: list[dict[str, Any]],
    *,
    regime_state: dict[str, Any] | None = None,
    target_weights: pd.DataFrame | None = None,
) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for trade in trades:
        item: dict[str, Any] = {}
        for key, value in trade.items():
            if hasattr(value, "isoformat"):
                item[key] = value.isoformat()
            elif isinstance(value, (int, float)):
                item[key] = _safe_float(value)
            else:
                item[key] = value
        if regime_state and regime_state.get("available"):
            from siglab.evaluation.runner_regime import _pair_regime_snapshot
            item["regime_snapshot"] = _pair_regime_snapshot(
                regime_state=regime_state,
                timestamp=trade.get("timestamp"),
                target_weights=target_weights,
            )
        serialized.append(item)
    return serialized


def _serialize_canonical_run(
    *,
    result: Any,
    target_weights: pd.DataFrame,
    visual_split: dict[str, Any],
    evaluation_windows: list[dict[str, Any]],
    regime_diagnostics: dict[str, Any],
    regime_state: dict[str, Any],
    trade_episodes: list[dict[str, Any]],
) -> dict[str, Any]:
    equity_curve = result.equity_curve.astype(float)
    drawdown_curve = equity_curve.div(equity_curve.cummax()).sub(1.0).fillna(0.0)
    return {
        "window": "full",
        "leverage": 1.0,
        "visual_split": visual_split,
        "evaluation_windows": evaluation_windows,
        "equity_curve": _serialize_series(equity_curve),
        "returns": _serialize_series(result.returns.astype(float)),
        "drawdown_curve": _serialize_series(drawdown_curve),
        "metrics_by_period": _serialize_metrics_frame(result.metrics_by_period),
        "target_weights": _serialize_weight_changes(target_weights),
        "trades": _serialize_trades(
            result.trades,
            regime_state=regime_state,
            target_weights=target_weights,
        ),
        "trade_count": len(result.trades),
        "trade_episodes": trade_episodes,
        "regime_diagnostics": regime_diagnostics,
        "liquidated": bool(result.liquidated),
        "liquidation_timestamp": (
            result.liquidation_timestamp.isoformat()
            if result.liquidation_timestamp is not None
            else None
        ),
    }


def _serialize_window_ranges(
    full_index: pd.Index,
    windows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    if len(full_index) == 0:
        return serialized
    optional_keys: tuple[tuple[str, Callable[[Any], Any]], ...] = (
        ("train_start_idx", int),
        ("train_end_idx", int),
        ("train_start_timestamp", str),
        ("train_end_timestamp", str),
        ("validation_start_timestamp", str),
        ("validation_end_timestamp", str),
    )
    for window_spec in windows:
        start_idx = int(window_spec["start_idx"])
        end_idx = int(window_spec["end_idx"])
        if start_idx >= end_idx or start_idx >= len(full_index):
            continue
        end_pos = min(len(full_index) - 1, max(start_idx, end_idx - 1))
        entry: dict[str, Any] = {
            "label": str(window_spec["label"]),
            "role": str(window_spec["role"]),
            "start_idx": start_idx,
            "end_idx": end_idx,
            "start_timestamp": full_index[start_idx].isoformat(),
            "end_timestamp": full_index[end_pos].isoformat(),
        }
        for key, coerce in optional_keys:
            if key in window_spec:
                entry[key] = coerce(window_spec[key])
        serialized.append(entry)
    return serialized
