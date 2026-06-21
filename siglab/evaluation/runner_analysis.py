"""
Pre-audit analysis functions extracted from runner.py — merged module.

WARNING MERGED from analysis_utils, runner_utils, runner_episodes, runner_regime,
and runner_analysis into a single tightly-coupled module.

Backward-compat shims at each original path maintain import compatibility.
"""

from __future__ import annotations

import math
from typing import Any, cast

import numpy as np
import pandas as pd

from siglab.utils import safe_float as _safe_float

__all__ = [
    # analysis_utils
    "mean_pairwise_rolling_corr",
    "pre_audit_trade_episodes",
    # runner_utils
    "_series_has_finite_values",
    "_series_total_return",
    "_series_last_value",
    "_series_min_value",
    "_series_values",
    "_series_from_payload",
    "_pre_audit_end_idx",
    # runner_episodes
    "_row_position_signature",
    "_episode_asset_lists",
    "_row_direction_label",
    "_pair_position_episodes",
    "_holding_period_buckets",
    "_episode_direction_counts",
    # runner_regime
    "_slice_performance_stats",
    "_pair_regime_state",
    "_lookup_timestamp",
    "_regime_binary_label",
    "_pair_regime_snapshot",
    "_pair_trade_episodes_with_regime",
    "_pair_regime_diagnostics",
    "_trade_regime_pack",
    "_window_regime_summary",
    "_equity_window_trade_stats",
    # runner_analysis
    "_pre_audit_drawdown_pack",
    "_pre_audit_equity_shift_pack",
    "_pre_audit_time_bin_pack",
    "_entry_feature_contributors",
    "_pre_audit_exemplar_trades",
    "_pair_gate_diagnostics",
    "_policy_context_from_metadata",
    "_pre_audit_context_pack",
]

# =========================================================================
# analysis_utils — shared analysis utilities
# =========================================================================


def mean_pairwise_rolling_corr(
    returns: pd.DataFrame,
    *,
    window: int,
) -> pd.Series:
    """Compute rolling pairwise correlation mean across all column pairs."""
    columns = list(returns.columns)
    if not columns:
        return pd.Series(dtype=float)
    if len(columns) == 1:
        return pd.Series(1.0, index=returns.index, dtype=float)
    rows: list[pd.Series] = []
    for left_idx in range(len(columns)):
        for right_idx in range(left_idx + 1, len(columns)):
            rows.append(returns.iloc[:, left_idx].rolling(window).corr(returns.iloc[:, right_idx]))
    if not rows:
        return pd.Series(dtype=float)
    return pd.concat(rows, axis=1).mean(axis=1)


def pre_audit_trade_episodes(canonical_run: dict[str, Any]) -> list[dict[str, Any]]:
    """Filter trade episodes to pre-audit-holdout windows only."""
    episodes = list(canonical_run.get("trade_episodes") or [])
    if not episodes:
        return []
    visual_split = dict(canonical_run.get("visual_split") or {})
    audit_start = None
    for window in list(visual_split.get("ranges") or []):
        if str(window.get("kind") or "") == "audit_holdout":
            audit_start = pd.Timestamp(window.get("start_timestamp"))
            break
    if audit_start is None:
        return [episode for episode in episodes if isinstance(episode, dict)]

    filtered: list[dict[str, Any]] = []
    for episode in episodes:
        if not isinstance(episode, dict):
            continue
        end_timestamp = episode.get("end_timestamp") or episode.get("start_timestamp")
        if not end_timestamp:
            continue
        if pd.Timestamp(end_timestamp) >= audit_start:
            continue
        filtered.append(episode)
    return filtered


# Backward-compat aliases used by code originally in runner_regime / runner_analysis
_mean_pairwise_rolling_corr = mean_pairwise_rolling_corr
_pre_audit_trade_episodes_from_canonical = pre_audit_trade_episodes


# =========================================================================
# runner_utils — series helpers
# =========================================================================


def _series_has_finite_values(payload: dict[str, Any] | None) -> bool:
    return bool(_series_values(payload))


def _series_total_return(
    payload: dict[str, Any] | None,
    *,
    end_idx: int | None = None,
) -> float | None:
    values = _series_values(payload, end_idx=end_idx)
    return float(values[-1] / values[0] - 1.0) if len(values) >= 2 and values[0] != 0.0 else None


def _series_last_value(
    payload: dict[str, Any] | None,
    *,
    end_idx: int | None = None,
) -> float | None:
    values = _series_values(payload, end_idx=end_idx)
    return float(values[-1]) if values else None


def _series_min_value(
    payload: dict[str, Any] | None,
    *,
    end_idx: int | None = None,
) -> float | None:
    values = _series_values(payload, end_idx=end_idx)
    return float(min(values)) if values else None


def _series_values(
    payload: dict[str, Any] | None,
    *,
    end_idx: int | None = None,
) -> list[float]:
    values_raw = list((payload or {}).get("values") or [])
    if end_idx is not None:
        values_raw = values_raw[: max(0, min(len(values_raw), int(end_idx)))]
    return [float(value) for value in values_raw if value is not None]


def _series_from_payload(
    payload: dict[str, Any] | None,
    *,
    end_idx: int | None = None,
) -> pd.Series:
    index_values = list((payload or {}).get("index") or [])
    raw_values = list((payload or {}).get("values") or [])
    limit = len(raw_values) if end_idx is None else max(0, min(len(raw_values), int(end_idx)))
    if limit <= 0:
        return pd.Series(dtype=float)
    index = pd.to_datetime(index_values[:limit], errors="coerce")
    values = pd.to_numeric(pd.Series(raw_values[:limit], dtype="float64"), errors="coerce")
    series = pd.Series(values.to_numpy(), index=index)
    series = series[~pd.isna(series.index)]
    return series.sort_index()


def _pre_audit_end_idx(
    visual_split: dict[str, Any],
    series_payload: dict[str, Any] | None,
) -> int | None:
    for row in list((visual_split or {}).get("ranges") or []):
        if str(row.get("kind") or "") == "audit_holdout":
            return int(row.get("start_idx") or 0)
    values = list((series_payload or {}).get("values") or [])
    return len(values) if values else None


# =========================================================================
# runner_episodes — trade episode extraction
# =========================================================================


def _row_position_signature(row: pd.Series, *, epsilon: float = 1e-9) -> tuple[tuple[str, int], ...]:
    return tuple(
        sorted(
            (str(row.index[j]), int(np.sign(v)))
            for j, v in enumerate(row.to_numpy(dtype=float, na_value=np.nan))
            if not np.isnan(v) and abs(v) > epsilon
        )
    )


def _make_position_signatures(frame: pd.DataFrame, *, epsilon: float = 1e-9) -> pd.Series:
    """Vectorised version: compute position signatures for all rows at once."""
    arr = frame.to_numpy(dtype=float, na_value=np.nan)
    columns = list(frame.columns)
    result: list[tuple[tuple[str, int], ...]] = []
    for i in range(len(arr)):
        row = arr[i]
        active = [
            (columns[j], int(np.sign(row[j])))
            for j in range(len(columns))
            if not np.isnan(row[j]) and abs(row[j]) > epsilon
        ]
        if active:
            active.sort(key=lambda item: item[0])
        result.append(tuple(active))
    return pd.Series(result, index=frame.index, dtype=object)


def _episode_asset_lists(row: pd.Series, *, epsilon: float = 1e-9) -> tuple[list[str], list[str], list[str]]:
    clean = pd.to_numeric(row, errors="coerce").fillna(0.0)
    active = [str(column) for column, value in clean.items() if abs(float(value)) > epsilon]
    longs = [str(column) for column, value in clean.items() if float(value) > epsilon]
    shorts = [str(column) for column, value in clean.items() if float(value) < -epsilon]
    return active, longs, shorts


def _row_direction_label(row: pd.Series, *, epsilon: float = 1e-9) -> str:
    clean = pd.to_numeric(row, errors="coerce").fillna(0.0)
    active, longs, shorts = _episode_asset_lists(clean, epsilon=epsilon)
    if not active:
        return "flat"
    if len(clean.index) >= 2 and len(active) == 2 and set(active) == set(map(str, clean.index[:2])):
        first = float(clean.iloc[0])
        second = float(clean.iloc[1])
        if first > epsilon and second < -epsilon:
            return "long_asset_1_short_asset_2"
        if first < -epsilon and second > epsilon:
            return "short_asset_1_long_asset_2"
    gross = float(clean.abs().sum())
    net = float(clean.sum())
    if longs and shorts and gross > 0.0 and abs(net) <= gross * 0.2:
        return "market_neutral"
    if net > epsilon or (longs and not shorts):
        return "net_long"
    if net < -epsilon or (shorts and not longs):
        return "net_short"
    return "mixed"


def _row_direction_label_np(values: np.ndarray, columns: list[str], epsilon: float = 1e-9) -> str:
    """Numpy-optimised version of _row_direction_label for batch use."""
    vals = np.where(np.isfinite(values), values, 0.0)
    abs_mask = np.abs(vals) > epsilon
    if not abs_mask.any():
        return "flat"
    active_idxs = np.where(abs_mask)[0]
    longs_idxs = active_idxs[vals[active_idxs] > epsilon]
    shorts_idxs = active_idxs[vals[active_idxs] < -epsilon]
    active = [columns[j] for j in active_idxs]
    longs = [columns[j] for j in longs_idxs]
    shorts = [columns[j] for j in shorts_idxs]

    if len(columns) >= 2 and len(active) == 2 and set(active) == {columns[0], columns[1]}:
        first = vals[0]
        second = vals[1]
        if first > epsilon and second < -epsilon:
            return "long_asset_1_short_asset_2"
        if first < -epsilon and second > epsilon:
            return "short_asset_1_long_asset_2"
    gross = float(np.abs(vals).sum())
    net = float(vals.sum())
    if longs and shorts and gross > 0.0 and abs(net) <= gross * 0.2:
        return "market_neutral"
    if net > epsilon or (longs and not shorts):
        return "net_long"
    if net < -epsilon or (shorts and not longs):
        return "net_short"
    return "mixed"


def _pair_position_episodes(
    *,
    target_weights: pd.DataFrame,
    returns: pd.Series,
) -> list[dict[str, Any]]:
    if target_weights.empty:
        return []
    signatures = _make_position_signatures(target_weights)
    episodes: list[dict[str, Any]] = []
    current_signature: tuple[tuple[str, int], ...] = ()
    start_timestamp: pd.Timestamp | None = None
    previous_timestamp: pd.Timestamp | None = None

    def _append_episode(
        episode_start: pd.Timestamp,
        episode_end: pd.Timestamp,
        signature: tuple[tuple[str, int], ...],
    ) -> None:
        if not signature:
            return
        episode_target = target_weights.loc[episode_start:episode_end]
        if episode_target.empty:
            return
        episode_returns = pd.to_numeric(
            returns.loc[episode_start:episode_end],
            errors="coerce",
        ).dropna()
        start_row = episode_target.iloc[0]
        active_assets, long_assets, short_assets = _episode_asset_lists(start_row)
        gross_exposure = pd.to_numeric(episode_target.abs().sum(axis=1), errors="coerce").fillna(0.0)
        net_exposure = pd.to_numeric(episode_target.sum(axis=1), errors="coerce").fillna(0.0)
        active_asset_count = (
            episode_target.abs().gt(1e-9).sum(axis=1).astype(float)
            if not episode_target.empty
            else pd.Series(dtype=float)
        )
        episodes.append(
            {
                "direction": _row_direction_label(start_row),
                "start_timestamp": episode_start.isoformat(),
                "end_timestamp": episode_end.isoformat(),
                "bars": int(episode_returns.shape[0]),
                "total_return": _safe_float(
                    cast(Any, (1.0 + episode_returns).prod()) - 1.0
                    if not episode_returns.empty
                    else 0.0
                ),
                "active_assets": active_assets,
                "long_assets": long_assets,
                "short_assets": short_assets,
                "active_asset_count": _safe_float(active_asset_count.median()),
                "gross_exposure": _safe_float(gross_exposure.median()),
                "net_exposure": _safe_float(net_exposure.median()),
            }
        )

    for timestamp_raw, signature in signatures.items():
        timestamp: pd.Timestamp | None = cast(pd.Timestamp | None, timestamp_raw)
        if not current_signature and signature:
            current_signature = signature
            start_timestamp = timestamp
        elif current_signature and signature != current_signature:
            if start_timestamp is not None and previous_timestamp is not None:
                _append_episode(start_timestamp, previous_timestamp, current_signature)
            current_signature = signature
            start_timestamp = timestamp if signature else None
        previous_timestamp = timestamp

    if current_signature and start_timestamp is not None and previous_timestamp is not None:
        _append_episode(start_timestamp, previous_timestamp, current_signature)
    return episodes


def _holding_period_buckets(target_weights: pd.DataFrame, returns: pd.Series) -> list[dict[str, Any]]:
    episodes = _pair_position_episodes(target_weights=target_weights, returns=returns)
    bucket_specs = [
        ("bars_1_6", 1, 6),
        ("bars_7_24", 7, 24),
        ("bars_25_72", 25, 72),
        ("bars_73_plus", 73, None),
    ]
    rows: list[dict[str, Any]] = []
    for label, low, high in bucket_specs:
        matched = [
            episode
            for episode in episodes
            if int(episode["bars"]) >= low and (high is None or int(episode["bars"]) <= high)
        ]
        returns_bucket = [
            float(episode["total_return"])
            for episode in matched
            if episode.get("total_return") is not None
        ]
        rows.append(
            {
                "label": label,
                "trade_count": len(matched),
                "median_bars": _safe_float(np.median([int(episode["bars"]) for episode in matched]))
                if matched
                else None,
                "median_return": _safe_float(np.median(returns_bucket)) if returns_bucket else None,
                "win_rate": _safe_float(
                    sum(1 for value in returns_bucket if value > 0.0) / len(returns_bucket)
                )
                if returns_bucket
                else None,
                "direction_counts": _episode_direction_counts(matched),
            }
        )
    return rows


def _episode_direction_counts(trade_episodes: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for episode in trade_episodes:
        direction = str(episode.get("direction") or "").strip()
        if not direction:
            continue
        counts[direction] = counts.get(direction, 0) + 1
    return counts


# =========================================================================
# runner_regime — regime state, snapshot, diagnostics
# =========================================================================


def _slice_performance_stats(
    *,
    returns: pd.Series,
    gross_exposure: pd.Series,
    mask: pd.Series,
    label: str,
) -> dict[str, Any]:
    aligned_mask = mask.reindex(returns.index).fillna(False).astype(bool)
    subset = returns[aligned_mask].dropna()
    exposure_subset = gross_exposure.reindex(returns.index).fillna(0.0)[aligned_mask]
    if subset.empty:
        return {
            "label": label,
            "available": False,
            "sample_bars": 0,
            "active_bars": 0,
        }
    total_return = float(cast(Any, (1.0 + subset).prod()) - 1.0)
    active_bars = int((exposure_subset > 1e-9).sum())
    _sharpe_val: float | None = None
    _max_dd_val: float | None = None
    if not subset.empty:
        _vol = float(subset.std())
        if math.isfinite(_vol) and _vol > 0.0:
            _sharpe_val = _safe_float((float(subset.mean()) / _vol) * math.sqrt(365.25 * 24.0))
        _equity = (1.0 + subset).cumprod()
        _drawdown = _equity.div(_equity.cummax()).sub(1.0)
        _max_dd_val = _safe_float(_drawdown.min())
    return {
        "label": label,
        "available": True,
        "sample_bars": int(subset.shape[0]),
        "active_bars": active_bars,
        "active_bar_fraction": _safe_float(active_bars / max(1, int(subset.shape[0]))),
        "avg_gross_exposure": _safe_float(exposure_subset.mean()),
        "mean_return": _safe_float(subset.mean()),
        "total_return": _safe_float(total_return),
        "sharpe": _sharpe_val,
        "max_drawdown": _max_dd_val,
        "positive_bar_fraction": _safe_float((subset > 0.0).mean()),
    }


def _pair_regime_state(
    *,
    prices: pd.DataFrame,
    target_weights: pd.DataFrame,
    funding_rates: pd.DataFrame | None,
) -> dict[str, Any]:
    if prices.empty:
        return {"available": False}
    prices = prices.sort_index()
    returns_1h = prices.pct_change()
    returns_24h = prices.pct_change(24)
    funding = (
        funding_rates.reindex(prices.index).ffill().fillna(0.0)
        if funding_rates is not None
        else pd.DataFrame(np.nan, index=prices.index, columns=prices.columns)
    )
    target_aligned = target_weights.reindex(prices.index).ffill().fillna(0.0)

    market_trend = returns_24h.mean(axis=1)
    market_volatility = returns_1h.rolling(168).std().mean(axis=1)
    funding_level = funding.mean(axis=1)
    funding_dispersion = funding.std(axis=1)
    breadth = returns_24h.gt(0.0).mean(axis=1)
    co_movement = _mean_pairwise_rolling_corr(returns_1h, window=72)
    gross_exposure = target_aligned.abs().sum(axis=1)
    net_exposure = target_aligned.sum(axis=1)
    active_asset_count = target_aligned.abs().gt(1e-9).sum(axis=1).astype(float)
    abs_weights = target_aligned.abs()
    concentration = (
        abs_weights.div(abs_weights.sum(axis=1).replace(0.0, np.nan), axis=0).pow(2).sum(axis=1)
    ).fillna(0.0)
    target_arr = target_aligned.to_numpy(dtype=float, na_value=0.0)
    target_cols = list(target_aligned.columns)
    position_direction = pd.Series(
        [_row_direction_label_np(target_arr[i], target_cols) for i in range(len(target_arr))],
        index=target_aligned.index,
        dtype=object,
    )

    thresholds = {
        "market_volatility_median": _safe_float(market_volatility.dropna().median(), default=None),
        "funding_level_median": _safe_float(funding_level.dropna().median(), default=None),
        "funding_dispersion_median": _safe_float(funding_dispersion.dropna().median(), default=None),
        "breadth_median": _safe_float(breadth.dropna().median(), default=None),
        "co_movement_median": _safe_float(co_movement.dropna().median(), default=None),
        "concentration_median": _safe_float(concentration.dropna().median(), default=None),
    }
    state: dict[str, Any] = {
        "available": True,
        "index": prices.index,
        "market_trend": market_trend,
        "market_volatility": market_volatility,
        "funding_level": funding_level,
        "funding_dispersion": funding_dispersion,
        "breadth": breadth,
        "co_movement": co_movement,
        "gross_exposure": gross_exposure,
        "net_exposure": net_exposure,
        "active_asset_count": active_asset_count,
        "concentration": concentration,
        "position_direction": position_direction,
        "thresholds": thresholds,
    }

    columns = list(prices.columns)
    if len(columns) >= 2:
        asset_1_symbol = str(columns[0])
        asset_2_symbol = str(columns[1])
        asset_1_returns_1h = prices[asset_1_symbol].pct_change()
        asset_2_returns_1h = prices[asset_2_symbol].pct_change()
        pair_ratio = prices[asset_1_symbol].div(prices[asset_2_symbol]).replace([np.inf, -np.inf], np.nan)
        pair_volatility = pair_ratio.pct_change().rolling(72).std()
        pair_correlation = asset_1_returns_1h.rolling(72).corr(asset_2_returns_1h)
        pair_direction = prices[asset_1_symbol].pct_change(24).sub(prices[asset_2_symbol].pct_change(24))
        state.update(
            {
                "asset_1_symbol": asset_1_symbol,
                "asset_2_symbol": asset_2_symbol,
                "pair_volatility": pair_volatility,
                "pair_correlation": pair_correlation,
                "pair_direction": pair_direction,
            }
        )
        state["thresholds"].update(
            {
                "pair_volatility_median": _safe_float(pair_volatility.dropna().median(), default=None),
                "pair_correlation_median": _safe_float(pair_correlation.dropna().median(), default=None),
            }
        )
    return state


def _lookup_timestamp(index: pd.Index, timestamp: Any) -> pd.Timestamp | None:
    if len(index) == 0 or timestamp is None:
        return None
    ts = pd.Timestamp(timestamp)
    if isinstance(index, pd.DatetimeIndex):
        if index.tz is None:
            if ts.tzinfo is not None:
                ts = ts.tz_convert(None)
        else:
            if ts.tzinfo is None:
                ts = ts.tz_localize(index.tz)
            else:
                ts = ts.tz_convert(index.tz)
    if ts in index:
        return pd.Timestamp(ts)
    position = int(index.searchsorted(ts, side="right")) - 1
    if position < 0:
        return None
    if position >= len(index):
        position = len(index) - 1
    return pd.Timestamp(index[position])


def _regime_binary_label(
    value: float | None,
    threshold: float | None,
    high_label: str,
    low_label: str,
) -> str | None:
    if value is None:
        return None
    if threshold is None:
        return None
    return high_label if value >= threshold else low_label


def _pair_regime_snapshot(
    *,
    regime_state: dict[str, Any],
    timestamp: Any,
    target_weights: pd.DataFrame | None,
) -> dict[str, Any]:
    if not regime_state.get("available"):
        return {}
    aligned_timestamp = _lookup_timestamp(regime_state["index"], timestamp)
    if aligned_timestamp is None:
        return {}
    thresholds = dict(regime_state.get("thresholds") or {})
    market_trend_value = _safe_float(regime_state["market_trend"].get(aligned_timestamp))
    market_volatility_value = _safe_float(regime_state["market_volatility"].get(aligned_timestamp))
    funding_level_value = _safe_float(regime_state["funding_level"].get(aligned_timestamp))
    funding_dispersion_value = _safe_float(regime_state["funding_dispersion"].get(aligned_timestamp))
    breadth_value = _safe_float(regime_state["breadth"].get(aligned_timestamp))
    co_movement_value = _safe_float(regime_state["co_movement"].get(aligned_timestamp))
    gross_exposure_value = _safe_float(regime_state["gross_exposure"].get(aligned_timestamp))
    net_exposure_value = _safe_float(regime_state["net_exposure"].get(aligned_timestamp))
    active_asset_count_value = _safe_float(regime_state["active_asset_count"].get(aligned_timestamp))
    concentration_value = _safe_float(regime_state["concentration"].get(aligned_timestamp))
    position_direction = str(regime_state["position_direction"].get(aligned_timestamp) or "flat")

    market_vol_threshold = thresholds.get("market_volatility_median")
    funding_level_threshold = thresholds.get("funding_level_median")
    funding_threshold = thresholds.get("funding_dispersion_median")
    breadth_threshold = thresholds.get("breadth_median")
    co_movement_threshold = thresholds.get("co_movement_median")
    concentration_threshold = thresholds.get("concentration_median")
    if target_weights is not None and not target_weights.empty:
        exposure_row = target_weights.reindex(regime_state["index"]).ffill().fillna(0.0)
        gross_exposure_value = _safe_float(exposure_row.abs().sum(axis=1).get(aligned_timestamp))
        net_exposure_value = _safe_float(exposure_row.sum(axis=1).get(aligned_timestamp))

    snapshot: dict[str, Any] = {
        "market_trend_label": _regime_binary_label(market_trend_value, 0.0, "market_uptrend", "market_downtrend"),
        "market_trend_24h": market_trend_value,
        "market_volatility_label": _regime_binary_label(market_volatility_value, market_vol_threshold, "high_volatility", "low_volatility"),
        "market_volatility_168h": market_volatility_value,
        "funding_level_label": _regime_binary_label(funding_level_value, funding_level_threshold, "high_funding", "low_funding"),
        "funding_level_72h": funding_level_value,
        "funding_dispersion_label": _regime_binary_label(funding_dispersion_value, funding_threshold, "funding_dispersed", "funding_compressed"),
        "funding_dispersion_72h": funding_dispersion_value,
        "breadth_label": _regime_binary_label(breadth_value, breadth_threshold, "broad_participation", "weak_participation"),
        "breadth_24h": breadth_value,
        "co_movement_label": _regime_binary_label(co_movement_value, co_movement_threshold, "high_co_movement", "low_co_movement"),
        "co_movement_72h": co_movement_value,
        "concentration_label": _regime_binary_label(concentration_value, concentration_threshold, "concentrated", "diversified"),
        "concentration": concentration_value,
        "position_direction": position_direction,
        "position_structure_label": position_direction,
        "gross_exposure": gross_exposure_value,
        "net_exposure": net_exposure_value,
        "active_asset_count": active_asset_count_value,
    }
    if "pair_volatility" in regime_state:
        pair_volatility_value = _safe_float(regime_state["pair_volatility"].get(aligned_timestamp))
        pair_correlation_value = _safe_float(regime_state["pair_correlation"].get(aligned_timestamp))
        pair_direction_value = _safe_float(regime_state["pair_direction"].get(aligned_timestamp))
        pair_vol_threshold = thresholds.get("pair_volatility_median")
        correlation_threshold = thresholds.get("pair_correlation_median")
        snapshot.update(
            {
                "pair_volatility_label": _regime_binary_label(pair_volatility_value, pair_vol_threshold, "high_volatility", "low_volatility"),
                "pair_volatility_72h": pair_volatility_value,
                "pair_correlation_label": _regime_binary_label(pair_correlation_value, correlation_threshold, "high_correlation", "low_correlation"),
                "pair_correlation_72h": pair_correlation_value,
                "pair_direction_label": _regime_binary_label(pair_direction_value, 0.0, "asset_1_leading", "asset_2_leading"),
                "pair_direction_24h": pair_direction_value,
            }
        )
    return snapshot


def _pair_trade_episodes_with_regime(
    *,
    target_weights: pd.DataFrame,
    returns: pd.Series,
    regime_state: dict[str, Any],
) -> list[dict[str, Any]]:
    episodes = _pair_position_episodes(target_weights=target_weights, returns=returns)
    annotated: list[dict[str, Any]] = []
    for episode in episodes:
        start_timestamp = episode.get("start_timestamp")
        end_timestamp = episode.get("end_timestamp")
        annotated.append(
            {
                **episode,
                "entry_regime": _pair_regime_snapshot(
                    regime_state=regime_state,
                    timestamp=start_timestamp,
                    target_weights=target_weights,
                ),
                "exit_regime": _pair_regime_snapshot(
                    regime_state=regime_state,
                    timestamp=end_timestamp,
                    target_weights=target_weights,
                ),
            }
        )
    return annotated


def _pair_regime_diagnostics(
    *,
    prices: pd.DataFrame,
    target_weights: pd.DataFrame,
    funding_rates: pd.DataFrame | None,
    returns: pd.Series,
) -> dict[str, Any]:
    regime_state = _pair_regime_state(
        prices=prices,
        target_weights=target_weights,
        funding_rates=funding_rates,
    )
    if not regime_state.get("available"):
        return {"available": False}
    thresholds = dict(regime_state.get("thresholds") or {})
    bar_slices: dict[str, list[dict[str, Any]]] = {
        "market_trend": [
            _slice_performance_stats(
                returns=returns, gross_exposure=regime_state["gross_exposure"],
                mask=regime_state["market_trend"] >= 0.0, label="market_uptrend",
            ),
            _slice_performance_stats(
                returns=returns, gross_exposure=regime_state["gross_exposure"],
                mask=regime_state["market_trend"] < 0.0, label="market_downtrend",
            ),
        ],
        "market_volatility": [
            _slice_performance_stats(
                returns=returns, gross_exposure=regime_state["gross_exposure"],
                mask=regime_state["market_volatility"] >= float(thresholds["market_volatility_median"])
                if thresholds.get("market_volatility_median") is not None else pd.Series(False, index=prices.index),
                label="high_volatility",
            ),
            _slice_performance_stats(
                returns=returns, gross_exposure=regime_state["gross_exposure"],
                mask=regime_state["market_volatility"] < float(thresholds["market_volatility_median"])
                if thresholds.get("market_volatility_median") is not None else pd.Series(False, index=prices.index),
                label="low_volatility",
            ),
        ],
        "funding_level": [
            _slice_performance_stats(
                returns=returns, gross_exposure=regime_state["gross_exposure"],
                mask=regime_state["funding_level"] >= float(thresholds["funding_level_median"])
                if thresholds.get("funding_level_median") is not None else pd.Series(False, index=prices.index),
                label="high_funding",
            ),
            _slice_performance_stats(
                returns=returns, gross_exposure=regime_state["gross_exposure"],
                mask=regime_state["funding_level"] < float(thresholds["funding_level_median"])
                if thresholds.get("funding_level_median") is not None else pd.Series(False, index=prices.index),
                label="low_funding",
            ),
        ],
        "funding_dispersion": [
            _slice_performance_stats(
                returns=returns, gross_exposure=regime_state["gross_exposure"],
                mask=regime_state["funding_dispersion"] >= float(thresholds["funding_dispersion_median"])
                if thresholds.get("funding_dispersion_median") is not None else pd.Series(False, index=prices.index),
                label="funding_dispersed",
            ),
            _slice_performance_stats(
                returns=returns, gross_exposure=regime_state["gross_exposure"],
                mask=regime_state["funding_dispersion"] < float(thresholds["funding_dispersion_median"])
                if thresholds.get("funding_dispersion_median") is not None else pd.Series(False, index=prices.index),
                label="funding_compressed",
            ),
        ],
        "breadth": [
            _slice_performance_stats(
                returns=returns, gross_exposure=regime_state["gross_exposure"],
                mask=regime_state["breadth"] >= float(thresholds["breadth_median"])
                if thresholds.get("breadth_median") is not None else pd.Series(False, index=prices.index),
                label="broad_participation",
            ),
            _slice_performance_stats(
                returns=returns, gross_exposure=regime_state["gross_exposure"],
                mask=regime_state["breadth"] < float(thresholds["breadth_median"])
                if thresholds.get("breadth_median") is not None else pd.Series(False, index=prices.index),
                label="weak_participation",
            ),
        ],
        "co_movement": [
            _slice_performance_stats(
                returns=returns, gross_exposure=regime_state["gross_exposure"],
                mask=regime_state["co_movement"] >= float(thresholds["co_movement_median"])
                if thresholds.get("co_movement_median") is not None else pd.Series(False, index=prices.index),
                label="high_co_movement",
            ),
            _slice_performance_stats(
                returns=returns, gross_exposure=regime_state["gross_exposure"],
                mask=regime_state["co_movement"] < float(thresholds["co_movement_median"])
                if thresholds.get("co_movement_median") is not None else pd.Series(False, index=prices.index),
                label="low_co_movement",
            ),
        ],
        "concentration": [
            _slice_performance_stats(
                returns=returns, gross_exposure=regime_state["gross_exposure"],
                mask=regime_state["concentration"] >= float(thresholds["concentration_median"])
                if thresholds.get("concentration_median") is not None else pd.Series(False, index=prices.index),
                label="concentrated",
            ),
            _slice_performance_stats(
                returns=returns, gross_exposure=regime_state["gross_exposure"],
                mask=regime_state["concentration"] < float(thresholds["concentration_median"])
                if thresholds.get("concentration_median") is not None else pd.Series(False, index=prices.index),
                label="diversified",
            ),
        ],
    }
    if "pair_volatility" in regime_state:
        bar_slices["pair_volatility"] = [
            _slice_performance_stats(
                returns=returns, gross_exposure=regime_state["gross_exposure"],
                mask=regime_state["pair_volatility"] >= float(thresholds["pair_volatility_median"])
                if thresholds.get("pair_volatility_median") is not None else pd.Series(False, index=prices.index),
                label="high_volatility",
            ),
            _slice_performance_stats(
                returns=returns, gross_exposure=regime_state["gross_exposure"],
                mask=regime_state["pair_volatility"] < float(thresholds["pair_volatility_median"])
                if thresholds.get("pair_volatility_median") is not None else pd.Series(False, index=prices.index),
                label="low_volatility",
            ),
        ]
        bar_slices["pair_correlation"] = [
            _slice_performance_stats(
                returns=returns, gross_exposure=regime_state["gross_exposure"],
                mask=regime_state["pair_correlation"] >= float(thresholds["pair_correlation_median"])
                if thresholds.get("pair_correlation_median") is not None else pd.Series(False, index=prices.index),
                label="high_correlation",
            ),
            _slice_performance_stats(
                returns=returns, gross_exposure=regime_state["gross_exposure"],
                mask=regime_state["pair_correlation"] < float(thresholds["pair_correlation_median"])
                if thresholds.get("pair_correlation_median") is not None else pd.Series(False, index=prices.index),
                label="low_correlation",
            ),
        ]
        bar_slices["pair_direction"] = [
            _slice_performance_stats(
                returns=returns, gross_exposure=regime_state["gross_exposure"],
                mask=regime_state["pair_direction"] >= 0.0, label="asset_1_leading",
            ),
            _slice_performance_stats(
                returns=returns, gross_exposure=regime_state["gross_exposure"],
                mask=regime_state["pair_direction"] < 0.0, label="asset_2_leading",
            ),
        ]
    return {
        "available": True,
        "asset_1_symbol": regime_state.get("asset_1_symbol"),
        "asset_2_symbol": regime_state.get("asset_2_symbol"),
        "thresholds": thresholds,
        "bar_slices": bar_slices,
        "holding_period_buckets": _holding_period_buckets(target_weights, returns),
    }


def _trade_regime_pack(trade_episodes: list[dict[str, Any]]) -> dict[str, Any]:
    if not trade_episodes:
        return {}
    label_keys: list[str] = []
    for episode in trade_episodes:
        entry_regime = dict(episode.get("entry_regime") or {})
        label_keys.extend(
            key for key, value in entry_regime.items() if key.endswith("_label") and value
        )
    dimensions = {
        key.removesuffix("_label"): key
        for key in sorted(set(label_keys))
    }
    regime_pack: dict[str, Any] = {}
    for dimension, label_key in dimensions.items():
        rows: list[dict[str, Any]] = []
        by_label: dict[str, list[dict[str, Any]]] = {}
        for episode in trade_episodes:
            label = str((episode.get("entry_regime") or {}).get(label_key) or "").strip()
            if not label:
                continue
            by_label.setdefault(label, []).append(episode)
        for label, matched in by_label.items():
            returns = [
                float(episode["total_return"])
                for episode in matched
                if episode.get("total_return") is not None
            ]
            bars = [
                float(episode["bars"])
                for episode in matched
                if _safe_float(episode.get("bars"), default=None) is not None
            ]
            rows.append(
                {
                    "label": label,
                    "trade_count": len(matched),
                    "win_rate": _safe_float(
                        sum(1 for value in returns if value > 0.0) / len(returns)
                        if returns else None
                    ),
                    "avg_return": _safe_float(sum(returns) / len(returns) if returns else None),
                    "median_return": _safe_float(float(np.median(returns)) if returns else None),
                    "median_hold_bars": _safe_float(float(np.median(bars)) if bars else None),
                    "direction_counts": _episode_direction_counts(matched),
                }
            )
        rows.sort(
            key=lambda row: (float(row.get("avg_return") or -1e9), int(row.get("trade_count") or 0)),
            reverse=True,
        )
        if rows:
            regime_pack[dimension] = {
                "rows": rows,
                "best_label": rows[0]["label"],
                "worst_label": min(
                    rows,
                    key=lambda row: float(row.get("avg_return") or 1e9),
                )["label"],
            }
    return regime_pack


def _window_regime_summary(
    *,
    regime_state: dict[str, Any],
    start_timestamp: pd.Timestamp,
    end_timestamp: pd.Timestamp,
) -> dict[str, Any]:
    if not regime_state.get("available"):
        return {}
    index_value = regime_state.get("index")
    index = pd.DatetimeIndex(index_value if index_value is not None else [])
    if index.empty:
        return {}
    mask = (index >= start_timestamp) & (index <= end_timestamp)
    if not bool(mask.any()):
        return {}

    def _mean_value(series: pd.Series) -> float | None:
        values = pd.to_numeric(series.loc[mask], errors="coerce").dropna()
        if values.empty:
            return None
        return _safe_float(values.mean())

    thresholds = dict(regime_state.get("thresholds") or {})
    market_trend = _mean_value(regime_state["market_trend"])
    market_volatility = _mean_value(regime_state["market_volatility"])
    funding_level = _mean_value(regime_state["funding_level"])
    funding_dispersion = _mean_value(regime_state["funding_dispersion"])
    breadth = _mean_value(regime_state["breadth"])
    co_movement = _mean_value(regime_state["co_movement"])
    concentration = _mean_value(regime_state["concentration"])
    direction_series = pd.Series(regime_state["position_direction"], index=index).loc[mask]
    direction_counts = direction_series.value_counts().to_dict()
    dominant_position_direction = max(
        (
            (str(label), int(count))
            for label, count in direction_counts.items()
            if str(label) != "flat"
        ),
        key=lambda item: item[1],
        default=(None, 0),
    )[0]

    payload: dict[str, Any] = {
        "market_trend_label": (
            "market_uptrend" if market_trend is not None and market_trend >= 0.0
            else "market_downtrend" if market_trend is not None else None
        ),
        "avg_market_trend_24h": market_trend,
        "market_volatility_label": (
            "high_volatility" if market_volatility is not None
            and thresholds.get("market_volatility_median") is not None
            and market_volatility >= float(thresholds["market_volatility_median"])
            else "low_volatility"
            if market_volatility is not None and thresholds.get("market_volatility_median") is not None
            else None
        ),
        "avg_market_volatility_168h": market_volatility,
        "funding_level_label": (
            "high_funding" if funding_level is not None
            and thresholds.get("funding_level_median") is not None
            and funding_level >= float(thresholds["funding_level_median"])
            else "low_funding"
            if funding_level is not None and thresholds.get("funding_level_median") is not None
            else None
        ),
        "avg_funding_level_72h": funding_level,
        "funding_dispersion_label": (
            "funding_dispersed" if funding_dispersion is not None
            and thresholds.get("funding_dispersion_median") is not None
            and funding_dispersion >= float(thresholds["funding_dispersion_median"])
            else "funding_compressed"
            if funding_dispersion is not None and thresholds.get("funding_dispersion_median") is not None
            else None
        ),
        "avg_funding_dispersion_72h": funding_dispersion,
        "breadth_label": (
            "broad_participation" if breadth is not None
            and thresholds.get("breadth_median") is not None
            and breadth >= float(thresholds["breadth_median"])
            else "weak_participation"
            if breadth is not None and thresholds.get("breadth_median") is not None
            else None
        ),
        "avg_breadth_24h": breadth,
        "co_movement_label": (
            "high_co_movement" if co_movement is not None
            and thresholds.get("co_movement_median") is not None
            and co_movement >= float(thresholds["co_movement_median"])
            else "low_co_movement"
            if co_movement is not None and thresholds.get("co_movement_median") is not None
            else None
        ),
        "avg_co_movement_72h": co_movement,
        "concentration_label": (
            "concentrated" if concentration is not None
            and thresholds.get("concentration_median") is not None
            and concentration >= float(thresholds["concentration_median"])
            else "diversified"
            if concentration is not None and thresholds.get("concentration_median") is not None
            else None
        ),
        "avg_concentration": concentration,
        "dominant_position_direction": dominant_position_direction,
    }
    if "pair_correlation" in regime_state:
        pair_volatility = _mean_value(regime_state["pair_volatility"])
        pair_correlation = _mean_value(regime_state["pair_correlation"])
        pair_direction = _mean_value(regime_state["pair_direction"])
        payload.update(
            {
                "pair_volatility_label": (
                    "high_volatility" if pair_volatility is not None
                    and thresholds.get("pair_volatility_median") is not None
                    and pair_volatility >= float(thresholds["pair_volatility_median"])
                    else "low_volatility"
                    if pair_volatility is not None and thresholds.get("pair_volatility_median") is not None
                    else None
                ),
                "avg_pair_volatility_72h": pair_volatility,
                "pair_correlation_label": (
                    "high_correlation" if pair_correlation is not None
                    and thresholds.get("pair_correlation_median") is not None
                    and pair_correlation >= float(thresholds["pair_correlation_median"])
                    else "low_correlation"
                    if pair_correlation is not None and thresholds.get("pair_correlation_median") is not None
                    else None
                ),
                "avg_pair_correlation_72h": pair_correlation,
                "pair_direction_label": (
                    "asset_1_leading" if pair_direction is not None and pair_direction >= 0.0
                    else "asset_2_leading" if pair_direction is not None else None
                ),
                "avg_pair_direction_24h": pair_direction,
            }
        )
    return payload


def _equity_window_trade_stats(
    *,
    trade_episodes: list[dict[str, Any]],
    start_timestamp: pd.Timestamp,
    end_timestamp: pd.Timestamp,
) -> dict[str, Any]:
    matched: list[dict[str, Any]] = []
    for episode in trade_episodes:
        start = episode.get("start_timestamp")
        if not start:
            continue
        timestamp = pd.Timestamp(start)
        if start_timestamp <= timestamp <= end_timestamp:
            matched.append(episode)
    returns = [
        float(episode["total_return"])
        for episode in matched
        if episode.get("total_return") is not None
    ]
    bars = [
        float(episode["bars"])
        for episode in matched
        if _safe_float(episode.get("bars"), default=None) is not None
    ]
    days = max(1.0, (end_timestamp - start_timestamp).total_seconds() / 86400.0)
    direction_counts = _episode_direction_counts(matched)
    dominant_direction = (
        max(direction_counts.items(), key=lambda item: item[1])[0]
        if direction_counts
        else None
    )
    return {
        "trade_count": len(matched),
        "entries_per_day": _safe_float(len(matched) / days),
        "win_rate": _safe_float(
            sum(1 for value in returns if value > 0.0) / len(returns)
            if returns else None
        ),
        "avg_return": _safe_float(sum(returns) / len(returns) if returns else None),
        "median_return": _safe_float(float(np.median(returns)) if returns else None),
        "median_hold_bars": _safe_float(float(np.median(bars)) if bars else None),
        "dominant_direction": dominant_direction,
        "direction_counts": direction_counts,
    }


# =========================================================================
# runner_analysis — pre-audit analysis, gate diagnostics, context packing
# =========================================================================


def _pre_audit_drawdown_pack(
    *,
    canonical_run: dict[str, Any],
    target_weights: pd.DataFrame,
    signal_score: pd.DataFrame | None,
    signal_components: dict[str, pd.DataFrame] | None,
    end_idx: int | None,
) -> dict[str, Any]:
    def _empty_pack(
        *,
        bars: int,
        start_timestamp: Any = None,
        trough_timestamp: Any = None,
        equity_peak: float | None = None,
        equity_trough: float | None = None,
        dominant_direction: str | None = "flat",
    ) -> dict[str, Any]:
        return {
            "start_timestamp": (str(start_timestamp) if start_timestamp is not None else None),
            "trough_timestamp": (str(trough_timestamp) if trough_timestamp is not None else None),
            "bars": int(max(bars, 0)),
            "drawdown": 0.0,
            "equity_peak": _safe_float(equity_peak),
            "equity_trough": _safe_float(equity_trough),
            "dominant_position_direction": dominant_direction,
            "long_bar_fraction": 0.0,
            "short_bar_fraction": 0.0,
            "flat_bar_fraction": 1.0 if bars > 0 else None,
            "signal_story": {},
            "top_feature_contributors": [],
        }

    equity_payload = canonical_run.get("equity_curve")
    drawdown_payload = canonical_run.get("drawdown_curve")
    index_values = list((equity_payload or {}).get("index") or [])
    equity_values = list((equity_payload or {}).get("values") or [])
    drawdown_values = list((drawdown_payload or {}).get("values") or [])
    limit = len(equity_values) if end_idx is None else max(0, min(len(equity_values), int(end_idx)))
    if limit < 2:
        start_timestamp = index_values[0] if index_values else None
        last_timestamp = index_values[limit - 1] if limit and len(index_values) >= limit else start_timestamp
        last_equity = equity_values[limit - 1] if limit and len(equity_values) >= limit else None
        return _empty_pack(
            bars=limit,
            start_timestamp=start_timestamp,
            trough_timestamp=last_timestamp,
            equity_peak=last_equity,
            equity_trough=last_equity,
        )

    index_values = index_values[:limit]
    equity_values = equity_values[:limit]
    drawdown_values = drawdown_values[:limit]
    if not drawdown_values:
        return _empty_pack(
            bars=limit,
            start_timestamp=index_values[0] if index_values else None,
            trough_timestamp=index_values[-1] if index_values else None,
            equity_peak=equity_values[-1] if equity_values else None,
            equity_trough=equity_values[-1] if equity_values else None,
        )

    trough_idx = int(np.argmin(drawdown_values))
    trough_drawdown = _safe_float(drawdown_values[trough_idx])
    if trough_drawdown is None or trough_drawdown >= 0.0:
        flat_equity = equity_values[trough_idx] if equity_values else None
        return _empty_pack(
            bars=limit,
            start_timestamp=index_values[0] if index_values else None,
            trough_timestamp=index_values[trough_idx] if index_values else None,
            equity_peak=flat_equity,
            equity_trough=flat_equity,
        )
    peak_idx = int(np.argmax(equity_values[: trough_idx + 1]))
    if peak_idx >= trough_idx:
        flat_equity = equity_values[trough_idx] if equity_values else None
        return _empty_pack(
            bars=limit,
            start_timestamp=index_values[0] if index_values else None,
            trough_timestamp=index_values[trough_idx] if index_values else None,
            equity_peak=flat_equity,
            equity_trough=flat_equity,
        )

    window_bars = max(1, trough_idx - peak_idx + 1)
    positions = target_weights.iloc[:limit] if not target_weights.empty else pd.DataFrame()
    window_positions = positions.iloc[peak_idx : trough_idx + 1] if not positions.empty else positions
    direction_counts: dict[str, int] = {}
    if not window_positions.empty:
        wp_arr = window_positions.to_numpy(dtype=float, na_value=0.0)
        wp_cols = list(window_positions.columns)
        direction_counts_raw: dict[str, int] = {}
        for i in range(len(wp_arr)):
            label = _row_direction_label_np(wp_arr[i], wp_cols)
            if label != "flat":
                direction_counts_raw[label] = direction_counts_raw.get(label, 0) + 1
        direction_counts = direction_counts_raw
    dominant_direction = (
        max(direction_counts.items(), key=lambda item: item[1])[0]
        if direction_counts
        else None
    )
    active_mask = (
        window_positions.abs().sum(axis=1).gt(1e-9)
        if not window_positions.empty
        else pd.Series(dtype=bool)
    )
    long_bars = int(
        (window_positions.sum(axis=1) > 0.0).sum()
        if not window_positions.empty
        else 0
    )
    short_bars = int(
        (window_positions.sum(axis=1) < 0.0).sum()
        if not window_positions.empty
        else 0
    )
    flat_bars = int((~active_mask).sum()) if not active_mask.empty else 0

    signal_story: dict[str, Any] = {}
    if signal_score is not None and not signal_score.empty and not window_positions.empty:
        score_frame = signal_score.iloc[:limit].fillna(0.0)
        score_window = score_frame.iloc[peak_idx : trough_idx + 1]
        score_window_arr = score_window.to_numpy(dtype=float, na_value=0.0)
        win_pos_arr = window_positions.to_numpy(dtype=float, na_value=0.0)
        win_pos_cols = list(window_positions.columns)

        aligned_values: list[float] = []
        support_scores: list[float] = []
        trough_support = None
        if score_frame.shape[1] == 1 and window_positions.shape[1] >= 2:
            primary_sign = np.sign(win_pos_arr[:, 0])
            support_arr = score_window_arr[:, 0] * primary_sign
            active_idx = np.abs(primary_sign) > 0
            if active_idx.any():
                active_support = support_arr[active_idx]
                support_scores = active_support.tolist()
                aligned_values = (active_support > 0.0).tolist()
            trough_support = _safe_float(support_arr[trough_idx] if trough_idx < len(support_arr) else None, default=None)
        else:
            position_sign = np.sign(win_pos_arr)
            signed_support = score_window_arr * position_sign
            active_mask_arr = position_sign != 0
            row_counts = np.maximum(active_mask_arr.sum(axis=1), 1)
            row_support = (signed_support * active_mask_arr).sum(axis=1) / row_counts
            row_aligned = ((signed_support > 0.0) * active_mask_arr).sum(axis=1) / row_counts
            active_rows = active_mask_arr.any(axis=1)
            if active_rows.any():
                support_scores = row_support[active_rows].tolist()
                aligned_values = row_aligned[active_rows].tolist()
            if active_rows[trough_idx] if trough_idx < len(active_rows) else False:
                trough_support = _safe_float(row_support[trough_idx], default=None)

        signal_story = {
            "window_median_score": _safe_float(float(np.median(support_scores)) if support_scores else None),
            "trough_score": trough_support,
            "aligned_with_position_fraction": _safe_float(
                float(np.mean(aligned_values)) if aligned_values else None
            ),
        }

    feature_story: list[dict[str, Any]] = []
    for feature, frame in dict(signal_components or {}).items():
        if frame is None or frame.empty or window_positions.empty:
            continue
        component = frame.iloc[:limit].fillna(0.0)
        component_window = component.iloc[peak_idx : trough_idx + 1]
        comp_arr = component_window.to_numpy(dtype=float, na_value=0.0)
        comp_aligned_values: list[float] = []
        comp_support_scores: list[float] = []
        trough_component = None
        if component.shape[1] == 1 and window_positions.shape[1] >= 2:
            primary_sign = np.sign(win_pos_arr[:, 0])
            support_arr = comp_arr[:, 0] * primary_sign
            active_idx = np.abs(primary_sign) > 0
            if active_idx.any():
                active_support = support_arr[active_idx]
                comp_support_scores = active_support.tolist()
                comp_aligned_values = (active_support > 0.0).tolist()
            trough_component = _safe_float(support_arr[trough_idx] if trough_idx < len(support_arr) else None, default=None)
        else:
            position_sign = np.sign(win_pos_arr)
            signed_support = comp_arr * position_sign
            active_mask_arr = position_sign != 0
            row_counts = np.maximum(active_mask_arr.sum(axis=1), 1)
            row_support = (signed_support * active_mask_arr).sum(axis=1) / row_counts
            row_aligned = ((signed_support > 0.0) * active_mask_arr).sum(axis=1) / row_counts
            active_rows = active_mask_arr.any(axis=1)
            if active_rows.any():
                comp_support_scores = row_support[active_rows].tolist()
                comp_aligned_values = row_aligned[active_rows].tolist()
            if active_rows[trough_idx] if trough_idx < len(active_rows) else False:
                trough_component = _safe_float(row_support[trough_idx], default=None)
        feature_story.append(
            {
                "feature": str(feature),
                "window_median_component": _safe_float(
                    float(np.median(comp_support_scores)) if comp_support_scores else None
                ),
                "trough_component": trough_component,
                "aligned_with_position_fraction": _safe_float(
                    float(np.mean(comp_aligned_values)) if comp_aligned_values else None
                ),
            }
        )
    feature_story.sort(
        key=lambda row: abs(_safe_float(row.get("window_median_component")) or 0.0),
        reverse=True,
    )

    return {
        "start_timestamp": str(index_values[peak_idx]),
        "trough_timestamp": str(index_values[trough_idx]),
        "bars": window_bars,
        "drawdown": trough_drawdown,
        "equity_peak": _safe_float(equity_values[peak_idx]),
        "equity_trough": _safe_float(equity_values[trough_idx]),
        "dominant_position_direction": dominant_direction,
        "long_bar_fraction": _safe_float(long_bars / window_bars),
        "short_bar_fraction": _safe_float(short_bars / window_bars),
        "flat_bar_fraction": _safe_float(flat_bars / window_bars),
        "signal_story": signal_story,
        "top_feature_contributors": feature_story[:4],
    }


def _pre_audit_equity_shift_pack(
    *,
    equity_curve: pd.Series,
    trade_episodes: list[dict[str, Any]],
    regime_state: dict[str, Any],
) -> dict[str, Any]:
    clean = pd.to_numeric(equity_curve, errors="coerce").dropna()
    if clean.shape[0] < 2:
        return {}
    drawdown = clean.div(clean.cummax()).sub(1.0)
    peak_timestamp: pd.Timestamp = pd.Timestamp(clean.idxmax())
    trough_timestamp: pd.Timestamp = pd.Timestamp(drawdown.idxmin())
    drawdown_start: pd.Timestamp = pd.Timestamp(clean.loc[:trough_timestamp].idxmax())
    pre_peak = _equity_window_trade_stats(
        trade_episodes=trade_episodes,
        start_timestamp=pd.Timestamp(clean.index.min()),
        end_timestamp=peak_timestamp,
    )
    post_peak = _equity_window_trade_stats(
        trade_episodes=trade_episodes,
        start_timestamp=peak_timestamp,
        end_timestamp=pd.Timestamp(clean.index.max()),
    )
    drawdown_window = _equity_window_trade_stats(
        trade_episodes=trade_episodes,
        start_timestamp=drawdown_start,
        end_timestamp=trough_timestamp,
    )
    drawdown_window["regime"] = _window_regime_summary(
        regime_state=regime_state,
        start_timestamp=drawdown_start,
        end_timestamp=trough_timestamp,
    )
    return {
        "peak_timestamp": peak_timestamp.isoformat(),
        "peak_equity": _safe_float(clean.loc[peak_timestamp]),
        "max_drawdown_start": drawdown_start.isoformat(),
        "max_drawdown_end": trough_timestamp.isoformat(),
        "max_drawdown": _safe_float(drawdown.loc[trough_timestamp]),
        "pre_peak": pre_peak,
        "post_peak": post_peak,
        "drawdown_window": drawdown_window,
    }


def _pre_audit_time_bin_pack(
    *,
    returns: pd.Series,
    trade_episodes: list[dict[str, Any]],
    regime_state: dict[str, Any],
) -> dict[str, Any]:
    clean = pd.to_numeric(returns, errors="coerce").dropna()
    if clean.empty:
        return {}
    daily_returns = clean.resample("1D").apply(lambda values: float((1.0 + values).prod() - 1.0))
    if daily_returns.shape[0] < 14:
        return {}

    def _window_payload(window_days: int) -> dict[str, Any] | None:
        if daily_returns.shape[0] < window_days:
            return None
        rolling = (1.0 + daily_returns.fillna(0.0)).rolling(window_days).apply(np.prod, raw=True) - 1.0
        rolling = rolling.dropna()
        if rolling.empty:
            return None
        best_end = rolling.idxmax()
        worst_end = rolling.idxmin()

        def _summary(end_timestamp: pd.Timestamp, label: str) -> dict[str, Any]:
            end_loc = int(cast(int, daily_returns.index.get_loc(end_timestamp)))
            start_loc = max(0, end_loc - window_days + 1)
            start_timestamp = pd.Timestamp(daily_returns.index[start_loc])
            trade_stats = _equity_window_trade_stats(
                trade_episodes=trade_episodes,
                start_timestamp=start_timestamp,
                end_timestamp=end_timestamp,
            )
            trade_stats["regime"] = _window_regime_summary(
                regime_state=regime_state,
                start_timestamp=start_timestamp,
                end_timestamp=end_timestamp,
            )
            return {
                "label": label,
                "start_timestamp": start_timestamp.isoformat(),
                "end_timestamp": end_timestamp.isoformat(),
                "window_days": window_days,
                "total_return": _safe_float(float(cast(Any, rolling.loc[end_timestamp]))),
                **trade_stats,
            }

        return {
            "window_days": window_days,
            "best_window": _summary(pd.Timestamp(cast(Any, best_end)), "best"),
            "worst_window": _summary(pd.Timestamp(cast(Any, worst_end)), "worst"),
        }

    windows = [payload for payload in (_window_payload(14), _window_payload(30)) if payload]
    return {"windows": windows} if windows else {}


def _entry_feature_contributors(
    *,
    signal_components: dict[str, pd.DataFrame] | None,
    timestamp: Any,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for feature, frame in dict(signal_components or {}).items():
        if frame is None or frame.empty:
            continue
        aligned_timestamp = _lookup_timestamp(frame.index, timestamp)
        if aligned_timestamp is None:
            continue
        value = _safe_float(frame.iloc[:, 0].get(aligned_timestamp), default=None)
        if value is None:
            continue
        rows.append(
            {
                "feature": str(feature),
                "value": value,
                "abs_value": _safe_float(abs(value)),
            }
        )
    rows.sort(key=lambda row: abs(float(row.get("value") or 0.0)), reverse=True)
    return rows[:3]


def _pre_audit_exemplar_trades(
    *,
    trade_episodes: list[dict[str, Any]],
    signal_score: pd.DataFrame | None,
    signal_components: dict[str, pd.DataFrame] | None,
) -> dict[str, Any]:
    if not trade_episodes:
        return {}
    scored = [episode for episode in trade_episodes if episode.get("total_return") is not None]
    if not scored:
        return {}
    winners = sorted(scored, key=lambda episode: float(episode["total_return"]), reverse=True)[:2]
    losers = sorted(scored, key=lambda episode: float(episode["total_return"]))[:2]

    def _payload(episode: dict[str, Any]) -> dict[str, Any]:
        entry_timestamp = episode.get("start_timestamp")
        entry_score = None
        if signal_score is not None and not signal_score.empty and entry_timestamp:
            aligned_timestamp = _lookup_timestamp(signal_score.index, entry_timestamp)
            if aligned_timestamp is not None:
                entry_score = _safe_float(signal_score.iloc[:, 0].get(aligned_timestamp), default=None)
        return {
            "start_timestamp": entry_timestamp,
            "end_timestamp": episode.get("end_timestamp"),
            "direction": episode.get("direction"),
            "bars": _safe_float(episode.get("bars"), default=None),
            "total_return": _safe_float(episode.get("total_return"), default=None),
            "entry_score": entry_score,
            "entry_regime": dict(episode.get("entry_regime") or {}),
            "entry_feature_contributors": _entry_feature_contributors(
                signal_components=signal_components,
                timestamp=entry_timestamp,
            ),
        }

    return {
        "winners": [_payload(episode) for episode in winners],
        "losers": [_payload(episode) for episode in losers],
    }


def _pair_gate_diagnostics(
    *,
    signal_score: pd.DataFrame | None,
    target_weights: pd.DataFrame,
    compiled_metadata: dict[str, Any],
    end_idx: int | None,
    regime_gate_mask: pd.Series | None = None,
) -> dict[str, Any]:
    if signal_score is None or signal_score.empty or target_weights.empty:
        return {}
    limit = len(signal_score.index) if end_idx is None else max(0, min(len(signal_score.index), int(end_idx)))
    if limit <= 1:
        return {}
    score_frame = signal_score.iloc[:limit].fillna(0.0)
    target_frame = target_weights.iloc[:limit].fillna(0.0)
    score_arr = score_frame.to_numpy(dtype=float, na_value=0.0)
    target_arr = target_frame.to_numpy(dtype=float, na_value=0.0)
    score_sign = np.sign(score_arr)
    position_sign = np.sign(target_arr)
    target_abs = np.abs(target_arr)
    active_mask_1d = target_abs.sum(axis=1) > 1e-9
    flat_mask_1d = ~active_mask_1d

    entry_abs_score = float(compiled_metadata.get("entry_abs_score", compiled_metadata.get("min_abs_score", 0.0)))
    exit_abs_score = float(compiled_metadata.get("exit_abs_score", max(0.0, entry_abs_score * 0.5)))
    flip_abs_score = float(compiled_metadata.get("flip_abs_score", entry_abs_score))
    score_abs = np.abs(score_arr)
    score_entry_mask_2d = score_abs >= entry_abs_score
    score_flip_mask_2d = score_abs >= flip_abs_score
    score_exit_band_2d = score_abs < exit_abs_score

    pair_mode = score_frame.shape[1] == 1 and target_frame.shape[1] >= 2
    score_cols = list(score_frame.columns)
    target_cols = list(target_frame.columns)

    # position_signature via vectorised helper
    position_signature = _make_position_signatures(target_frame)

    # active_score_signature — numpy based
    if pair_mode:
        score_vals = score_arr[:, 0]
        active_sig: list[tuple[tuple[str, int], ...]] = []
        for v in score_vals:
            if v >= entry_abs_score:
                active_sig.append((("long_asset_1_short_asset_2", 1),))
            elif v <= -entry_abs_score:
                active_sig.append((("short_asset_1_long_asset_2", 1),))
            else:
                active_sig.append(())
        active_score_signature = pd.Series(active_sig, index=score_frame.index, dtype=object)
    else:
        active_sig_nonpair: list[tuple[tuple[str, int], ...]] = []
        for i in range(len(score_arr)):
            row = score_arr[i]
            active = [
                (score_cols[j], int(np.sign(row[j])))
                for j in range(len(score_cols))
                if abs(row[j]) >= entry_abs_score
            ]
            if active:
                active.sort(key=lambda x: x[0])
            active_sig_nonpair.append(tuple(active))
        active_score_signature = pd.Series(active_sig_nonpair, index=score_frame.index, dtype=object)

    sig_shifted = active_score_signature.shift(1)
    pos_sig_shifted = position_signature.shift(1)
    score_flips = (
        active_score_signature != sig_shifted
    ) & active_score_signature.astype(bool) & sig_shifted.astype(bool)
    position_flips = (
        position_signature != pos_sig_shifted
    ) & position_signature.astype(bool) & pos_sig_shifted.astype(bool)

    aligned_active_fraction = None
    if active_mask_1d.any():
        if pair_mode:
            primary_sign = np.sign(target_arr[:, 0])
            aligned_1d = (score_arr[:, 0] * primary_sign) > 0.0
            active_alignment = aligned_1d[active_mask_1d].tolist()
        else:
            # Fully vectorised alignment: element-wise sign equality masked by active columns
            sign_eq = (score_sign == position_sign).astype(float)
            # Zero out inactive columns per row
            active_col_mask = target_abs > 1e-9
            sign_eq_masked = sign_eq * active_col_mask
            row_counts = active_col_mask.sum(axis=1, where=~np.isnan(active_col_mask))
            row_counts = np.maximum(row_counts, 1)
            row_alignment = sign_eq_masked.sum(axis=1) / row_counts
            active_alignment = row_alignment[active_mask_1d].tolist()
        if active_alignment:
            aligned_active_fraction = _safe_float(float(np.mean(active_alignment)))

    bottleneck_tags: list[str] = []
    entry_fraction = float(score_entry_mask_2d.any(axis=1).mean())
    active_fraction = float(active_mask_1d.mean())
    position_flip_rate = float(position_flips.mean()) if len(position_flips.index) > 1 else 0.0
    if entry_fraction < 0.05:
        bottleneck_tags.append("sparse_entry_signal")
    if active_fraction < 0.10:
        bottleneck_tags.append("low_active_fraction")
    if position_flip_rate > 0.20:
        bottleneck_tags.append("high_position_flip_rate")
    if aligned_active_fraction is not None and aligned_active_fraction < 0.55:
        bottleneck_tags.append("weak_score_alignment")

    regime_gate_summary = None
    if regime_gate_mask is not None:
        gate_mask = regime_gate_mask.reindex(score_frame.index).ffill().fillna(False).astype(bool)
        regime_gate_summary = {
            "configured": True,
            "active_fraction": _safe_float(float(gate_mask.mean())),
            "blocked_while_flat_fraction": _safe_float(
                float((~gate_mask)[flat_mask_1d].mean()) if bool(flat_mask_1d.any()) else None
            ),
            "broken_while_active_fraction": _safe_float(
                float((~gate_mask)[active_mask_1d].mean()) if bool(active_mask_1d.any()) else None
            ),
            "exit_on_break": bool(
                dict(compiled_metadata.get("regime_gates") or {}).get("exit_on_break", True)
            ),
            "entry": list(dict(compiled_metadata.get("regime_gates") or {}).get("entry") or []),
        }
        active_frac_gate = _safe_float(regime_gate_summary.get("active_fraction"))
        if active_frac_gate is not None and active_frac_gate < 0.30:
            bottleneck_tags.append("restrictive_regime_gate")

    if bool(active_mask_1d.any()):
        if pair_mode:
            median_active_count_val = 2.0
        else:
            median_active_count_val = float(np.median((target_abs > 1e-9).sum(axis=1).astype(float)[active_mask_1d]))
    else:
        median_active_count_val = None

    return {
        "policy": {
            "entry_abs_score": _safe_float(entry_abs_score),
            "exit_abs_score": _safe_float(exit_abs_score),
            "flip_abs_score": _safe_float(flip_abs_score),
            "max_holding_bars": int(compiled_metadata.get("max_holding_bars", 0) or 0),
            "cooldown_bars": int(compiled_metadata.get("cooldown_bars", 0) or 0),
            "signal_leverage_scale": _safe_float(compiled_metadata.get("signal_leverage_scale")),
        },
        "active_bar_fraction": _safe_float(active_fraction),
        "flat_bar_fraction": _safe_float(float(flat_mask_1d.mean())),
        "entry_signal_bar_fraction": _safe_float(entry_fraction),
        "flip_signal_bar_fraction": _safe_float(float(score_flip_mask_2d.any(axis=1).mean())),
        "inside_exit_band_fraction": _safe_float(float(score_exit_band_2d.all(axis=1).mean())),
        "score_sign_flip_rate": _safe_float(float(score_flips.mean()) if len(score_flips.index) > 1 else 0.0),
        "position_flip_rate": _safe_float(position_flip_rate),
        "entry_signal_while_flat_fraction": _safe_float(
            float(score_entry_mask_2d.any(axis=1)[flat_mask_1d].mean()) if bool(flat_mask_1d.any()) else None
        ),
        "score_alignment_when_active": aligned_active_fraction,
        "median_active_asset_count": _safe_float(median_active_count_val),
        "regime_gates": regime_gate_summary,
        "bottleneck_tags": bottleneck_tags,
    }


def _policy_context_from_metadata(compiled_metadata: dict[str, Any]) -> dict[str, Any]:
    policy: dict[str, Any] = {
        "execution_profile": compiled_metadata.get("execution_profile"),
        "long_count": int(compiled_metadata.get("long_count", 0) or 0),
        "short_count": int(compiled_metadata.get("short_count", 0) or 0),
        "selection_count": int(compiled_metadata.get("selection_count", 0) or 0),
        "entry_abs_score": _safe_float(compiled_metadata.get("entry_abs_score"), default=None),
        "exit_abs_score": _safe_float(compiled_metadata.get("exit_abs_score"), default=None),
        "flip_abs_score": _safe_float(compiled_metadata.get("flip_abs_score"), default=None),
        "max_holding_bars": int(compiled_metadata.get("max_holding_bars", 0) or 0),
        "cooldown_bars": int(compiled_metadata.get("cooldown_bars", 0) or 0),
        "signal_leverage_scale": _safe_float(compiled_metadata.get("signal_leverage_scale"), default=None),
        "gross_target": _safe_float(compiled_metadata.get("gross_target"), default=None),
        "max_gross_target": _safe_float(compiled_metadata.get("max_gross_target"), default=None),
    }
    sweep = dict(compiled_metadata.get("pair_policy_sweep") or {})
    if sweep:
        policy["policy_sweep"] = {
            "applied": bool(sweep.get("applied")),
            "train_window_count": int(sweep.get("train_window_count", 0) or 0),
            "trial_count": int(sweep.get("trial_count", 0) or 0),
            "best_train_score": _safe_float(
                dict(sweep.get("best_train_summary") or {}).get("aggregate_score"),
                default=None,
            ),
            "best_train_return": _safe_float(
                dict(sweep.get("best_train_summary") or {}).get("median_total_return"),
                default=None,
            ),
        }
    return policy


def _pre_audit_context_pack(
    *,
    canonical_run: dict[str, Any],
    target_weights: pd.DataFrame,
    signal_score: pd.DataFrame | None,
    signal_components: dict[str, pd.DataFrame] | None,
    compiled_metadata: dict[str, Any],
    regime_gate_mask: pd.Series | None,
    regime_state: dict[str, Any],
    end_idx: int | None,
) -> dict[str, Any]:
    if not regime_state.get("available"):
        return {}
    trade_episodes = _pre_audit_trade_episodes_from_canonical(canonical_run)
    equity_curve = _series_from_payload(canonical_run.get("equity_curve"), end_idx=end_idx)
    returns = _series_from_payload(canonical_run.get("returns"), end_idx=end_idx)
    return {
        "trade_regime_pack": _trade_regime_pack(trade_episodes),
        "equity_shift_pack": _pre_audit_equity_shift_pack(
            equity_curve=equity_curve,
            trade_episodes=trade_episodes,
            regime_state=regime_state,
        ),
        "time_bin_pack": _pre_audit_time_bin_pack(
            returns=returns,
            trade_episodes=trade_episodes,
            regime_state=regime_state,
        ),
        "exemplar_trades": _pre_audit_exemplar_trades(
            trade_episodes=trade_episodes,
            signal_score=signal_score,
            signal_components=signal_components,
        ),
        "gate_diagnostics": _pair_gate_diagnostics(
            signal_score=signal_score,
            target_weights=target_weights,
            compiled_metadata=compiled_metadata,
            end_idx=end_idx,
            regime_gate_mask=regime_gate_mask,
        ),
        "policy_context": _policy_context_from_metadata(compiled_metadata),
    }
