"""Regime state, snapshot, and diagnostics functions extracted from runner.py."""

from __future__ import annotations

import math
from typing import Any, cast

import numpy as np
import pandas as pd

from siglab.evaluation.analysis_utils import mean_pairwise_rolling_corr as _mean_pairwise_rolling_corr
from siglab.evaluation.runner_episodes import (
    _episode_direction_counts,
    _holding_period_buckets,
    _pair_position_episodes,
    _row_direction_label,
)
from siglab.utils import safe_float as _safe_float


def _annualized_sharpe(returns: pd.Series, *, periods_per_year: float = 365.25 * 24.0) -> float | None:
    clean = pd.to_numeric(returns, errors="coerce").dropna()
    if clean.empty:
        return None
    volatility = float(clean.std())
    if not math.isfinite(volatility) or volatility <= 0.0:
        return None
    return _safe_float((float(clean.mean()) / volatility) * math.sqrt(periods_per_year))


def _max_drawdown(returns: pd.Series) -> float | None:
    clean = pd.to_numeric(returns, errors="coerce").dropna()
    if clean.empty:
        return None
    equity = (1.0 + clean).cumprod()
    drawdown = equity.div(equity.cummax()).sub(1.0)
    return _safe_float(drawdown.min())


def _slice_performance_stats(
    *,
    returns: pd.Series,
    gross_exposure: pd.Series,
    mask: pd.Series,
    label: str,
) -> dict[str, Any]:
    aligned_mask = mask.reindex(returns.index).fillna(False).astype(bool)
    subset = pd.to_numeric(returns[aligned_mask], errors="coerce").dropna()
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
    return {
        "label": label,
        "available": True,
        "sample_bars": int(subset.shape[0]),
        "active_bars": active_bars,
        "active_bar_fraction": _safe_float(active_bars / max(1, int(subset.shape[0]))),
        "avg_gross_exposure": _safe_float(exposure_subset.mean()),
        "mean_return": _safe_float(subset.mean()),
        "total_return": _safe_float(total_return),
        "sharpe": _annualized_sharpe(subset),
        "max_drawdown": _max_drawdown(subset),
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
    position_direction = pd.Series(
        [_row_direction_label(row) for _, row in target_aligned.iterrows()],
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
