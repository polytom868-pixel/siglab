from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

import numpy as np
import pandas as pd

from siglab.data.feeds import MarketDataProvider
from siglab.evaluation.events import (
    classify_pt_market_state,
    detect_pt_roll_events,
    summarize_pt_universe,
)
from siglab.evaluation.feature_dsl import load_feature_spec, resolve_feature_frames
from siglab.families import (
    family_capabilities,
    family_diagnostic_adapter,
    family_execution_profile,
    family_policy_schema,
    load_family_spec,
)
from siglab.schemas import SignalSpec, CompiledChild
from siglab.config import SiglabConfig
from siglab.evaluation.backtest import convert_to_spot

PAIR_TRADE_FAMILIES = {
    "perp_pair_trade_unlevered",
    "perp_pair_trade_levered",
}
PERP_EXECUTION_PROFILES = {
    "ranked_directional",
    "basket_neutral_spread",
    "ranked_carry",
}
PAIR_STATEFUL_POLICY_SCHEMA = "pair_stateful"


def _cross_sectional_zscore(frame: pd.DataFrame) -> pd.DataFrame:
    mean = frame.mean(axis=1)
    std = frame.std(axis=1).replace(0.0, np.nan)
    scored = frame.sub(mean, axis=0).div(std, axis=0)
    return scored.replace([np.inf, -np.inf], np.nan).fillna(0.0)


def _time_series_zscore(
    frame: pd.DataFrame,
    *,
    window: int,
) -> pd.DataFrame:
    min_periods = max(8, window // 4)
    rolling_mean = frame.rolling(window, min_periods=min_periods).mean()
    rolling_std = frame.rolling(window, min_periods=min_periods).std().replace(0.0, np.nan)
    scored = frame.sub(rolling_mean).div(rolling_std)
    return scored.replace([np.inf, -np.inf], np.nan).fillna(0.0)


def _weighted_score(
    feature_frames: dict[str, pd.DataFrame],
    selected_features: list[str],
    feature_weights: dict[str, float],
    *,
    normalization: str = "cross_sectional",
    z_window: int = 72,
) -> pd.DataFrame:
    chosen = [feature for feature in selected_features if feature in feature_frames]
    if not chosen:
        raise ValueError("Spec did not reference any compiled features")

    weight_total = sum(abs(float(feature_weights.get(name, 1.0))) for name in chosen)
    if weight_total == 0:
        weight_total = float(len(chosen))

    score = None
    for name in chosen:
        if normalization == "time_series":
            component = _time_series_zscore(feature_frames[name], window=z_window)
        else:
            component = _cross_sectional_zscore(feature_frames[name])
        weight = float(feature_weights.get(name, 1.0)) / weight_total
        score = component * weight if score is None else score.add(component * weight, fill_value=0.0)

    assert score is not None
    return score.fillna(0.0)


def _weighted_component_frames(
    feature_frames: dict[str, pd.DataFrame],
    selected_features: list[str],
    feature_weights: dict[str, float],
    *,
    normalization: str = "cross_sectional",
    z_window: int = 72,
) -> dict[str, pd.DataFrame]:
    chosen = [feature for feature in selected_features if feature in feature_frames]
    if not chosen:
        return {}

    weight_total = sum(abs(float(feature_weights.get(name, 1.0))) for name in chosen)
    if weight_total == 0:
        weight_total = float(len(chosen))

    components: dict[str, pd.DataFrame] = {}
    for name in chosen:
        if normalization == "time_series":
            component = _time_series_zscore(feature_frames[name], window=z_window)
        else:
            component = _cross_sectional_zscore(feature_frames[name])
        weight = float(feature_weights.get(name, 1.0)) / weight_total
        components[name] = (component * weight).fillna(0.0)
    return components


def _align_cross_sectional_frame(
    frame: pd.DataFrame,
    *,
    tradable_symbols: list[str],
) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(0.0, index=frame.index, columns=tradable_symbols)
    clean = frame.apply(pd.to_numeric, errors="coerce")
    symbol_columns = [column for column in tradable_symbols if column in clean.columns]
    extra_columns = [column for column in clean.columns if column not in tradable_symbols]
    if extra_columns:
        extras = clean[extra_columns]
        if not extras.empty:
            # Market-wide one-column features like GLOBAL should act as a common
            # overlay across tradable symbols, not as synthetic assets.
            overlay = extras.mean(axis=1)
            broadcast = pd.DataFrame(
                {symbol: overlay for symbol in tradable_symbols},
                index=clean.index,
            )
        else:
            broadcast = pd.DataFrame(0.0, index=clean.index, columns=tradable_symbols)
    else:
        broadcast = pd.DataFrame(0.0, index=clean.index, columns=tradable_symbols)

    aligned = clean.reindex(columns=symbol_columns)
    aligned = aligned.reindex(columns=tradable_symbols)
    aligned = aligned.fillna(0.0).add(broadcast, fill_value=0.0)
    return aligned.reindex(columns=tradable_symbols).fillna(0.0)


def _align_cross_sectional_components(
    components: dict[str, pd.DataFrame],
    *,
    tradable_symbols: list[str],
) -> dict[str, pd.DataFrame]:
    return {
        name: _align_cross_sectional_frame(frame, tradable_symbols=tradable_symbols)
        for name, frame in components.items()
    }


def _mask_feature_frames(
    feature_frames: dict[str, pd.DataFrame],
    eligible: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    return {
        name: frame.where(eligible.reindex_like(frame).fillna(False))
        for name, frame in feature_frames.items()
    }


def _ensure_single_eligible_scores(
    score: pd.DataFrame,
    eligible: pd.DataFrame,
) -> pd.DataFrame:
    adjusted = score.copy()
    eligible_aligned = eligible.reindex_like(adjusted).fillna(False)
    counts = eligible_aligned.sum(axis=1)
    for timestamp in counts[counts == 1].index:
        labels = list(eligible_aligned.columns[eligible_aligned.loc[timestamp]])
        if not labels:
            continue
        adjusted.loc[timestamp, labels[0]] = max(
            float(adjusted.loc[timestamp, labels[0]]),
            1.0,
        )
    return adjusted


def _build_ranked_positions(
    score: pd.DataFrame,
    *,
    long_count: int,
    short_count: int,
    gross_target: float,
    max_asset_weight: float,
    require_positive_longs: bool = False,
    min_abs_score: float = 0.0,
    require_both_sides: bool = False,
    regime_gate_mask: pd.Series | None = None,
) -> pd.DataFrame:
    target = pd.DataFrame(0.0, index=score.index, columns=score.columns)
    if regime_gate_mask is not None:
        gate_mask = (
            pd.Series(regime_gate_mask, index=regime_gate_mask.index)
            .reindex(score.index)
            .ffill()
            .fillna(False)
            .astype(bool)
        )
    else:
        gate_mask = pd.Series(True, index=score.index, dtype=bool)

    for ts, row in score.iterrows():
        if not bool(gate_mask.loc[ts]):
            continue
        clean = row.dropna()
        if clean.empty:
            continue
        longs = pd.Series(dtype=float)
        shorts = pd.Series(dtype=float)

        if long_count > 0:
            longs = clean.nlargest(min(long_count, len(clean)))
            if require_positive_longs:
                longs = longs[longs > 0.0]
            if min_abs_score > 0.0:
                longs = longs[longs >= min_abs_score]

        if short_count > 0:
            shorts = clean.nsmallest(min(short_count, len(clean)))
            if min_abs_score > 0.0:
                shorts = shorts[shorts <= -min_abs_score]

        overlap = set(longs.index) & set(shorts.index)
        for label in overlap:
            value = float(clean.get(label, 0.0))
            if value >= 0.0:
                shorts = shorts.drop(label, errors="ignore")
            else:
                longs = longs.drop(label, errors="ignore")

        has_longs = not longs.empty
        has_shorts = not shorts.empty
        if require_both_sides and not (has_longs and has_shorts):
            continue
        if not has_longs and not has_shorts:
            continue

        if has_longs and has_shorts:
            long_budget = gross_target / 2.0
            short_budget = gross_target / 2.0
        elif has_longs:
            long_budget = gross_target
            short_budget = 0.0
        else:
            long_budget = 0.0
            short_budget = gross_target

        if has_longs:
            long_weight = min(max_asset_weight, long_budget / len(longs))
            target.loc[ts, longs.index] = long_weight

        if has_shorts:
            short_weight = min(max_asset_weight, short_budget / len(shorts))
            target.loc[ts, shorts.index] = -short_weight

    return target.ffill().fillna(0.0)


def _build_pair_positions(
    score: pd.DataFrame,
    *,
    selection_count: int,
    gross_target: float,
    max_asset_weight: float,
    regime_gate_mask: pd.Series | None = None,
) -> pd.DataFrame:
    columns = []
    for symbol in score.columns:
        columns.extend([f"{symbol}_SPOT", f"{symbol}_PERP"])

    target = pd.DataFrame(0.0, index=score.index, columns=columns)
    if regime_gate_mask is not None:
        gate_mask = (
            pd.Series(regime_gate_mask, index=regime_gate_mask.index)
            .reindex(score.index)
            .ffill()
            .fillna(False)
            .astype(bool)
        )
    else:
        gate_mask = pd.Series(True, index=score.index, dtype=bool)

    for ts, row in score.iterrows():
        if not bool(gate_mask.loc[ts]):
            continue
        clean = row.dropna()
        if clean.empty:
            continue
        selected = clean[clean > 0.0].nlargest(min(selection_count, len(clean)))
        if selected.empty:
            continue
        pair_weight = min(max_asset_weight, gross_target / (2.0 * len(selected)))
        for symbol in selected.index:
            target.loc[ts, f"{symbol}_SPOT"] = pair_weight
            target.loc[ts, f"{symbol}_PERP"] = -pair_weight

    return target.ffill().fillna(0.0)


def _build_pair_trade_positions(
    score: pd.DataFrame,
    *,
    asset_1_symbol: str,
    asset_2_symbol: str,
    gross_target: float,
    max_gross_target: float,
    max_asset_weight: float,
    entry_abs_score: float,
    exit_abs_score: float,
    flip_abs_score: float,
    max_holding_bars: int,
    cooldown_bars: int,
    signal_leverage_scale: float,
    regime_gate_mask: pd.Series | None = None,
    exit_on_regime_break: bool = True,
) -> pd.DataFrame:
    target = pd.DataFrame(0.0, index=score.index, columns=[asset_1_symbol, asset_2_symbol])
    pair_column = score.columns[0]
    position_state = 0
    holding_bars = 0
    cooldown_remaining = 0
    if regime_gate_mask is not None:
        gate_mask = (
            pd.Series(regime_gate_mask, index=regime_gate_mask.index)
            .reindex(score.index)
            .ffill()
            .fillna(False)
            .astype(bool)
        )
    else:
        gate_mask = pd.Series(True, index=score.index, dtype=bool)

    for ts, value in score[pair_column].items():
        signal_value = 0.0 if pd.isna(value) else float(value)
        exit_now = False
        regime_ok = bool(gate_mask.loc[ts])

        if position_state == 0:
            holding_bars = 0
            if cooldown_remaining > 0:
                cooldown_remaining -= 1
            if cooldown_remaining <= 0 and regime_ok:
                if signal_value >= entry_abs_score:
                    position_state = 1
                    holding_bars = 0
                elif signal_value <= -entry_abs_score:
                    position_state = -1
                    holding_bars = 0
        else:
            holding_bars += 1
            timed_out = max_holding_bars > 0 and holding_bars >= max_holding_bars
            if exit_on_regime_break and not regime_ok:
                exit_now = True
            elif position_state > 0:
                if signal_value <= -flip_abs_score:
                    position_state = -1
                    holding_bars = 0
                elif timed_out or abs(signal_value) < exit_abs_score:
                    exit_now = True
            else:
                if signal_value >= flip_abs_score:
                    position_state = 1
                    holding_bars = 0
                elif timed_out or abs(signal_value) < exit_abs_score:
                    exit_now = True

            if exit_now:
                position_state = 0
                holding_bars = 0
                cooldown_remaining = cooldown_bars

        if position_state == 0:
            continue

        signal_strength = max(0.0, abs(signal_value) - entry_abs_score)
        leverage_fraction = min(1.0, signal_strength / max(signal_leverage_scale, 1e-6))
        gross_target_now = gross_target + ((max_gross_target - gross_target) * leverage_fraction)
        leg_weight = min(max_asset_weight, gross_target_now / 2.0)
        if position_state > 0:
            target.loc[ts, asset_1_symbol] = leg_weight
            target.loc[ts, asset_2_symbol] = -leg_weight
        else:
            target.loc[ts, asset_1_symbol] = -leg_weight
            target.loc[ts, asset_2_symbol] = leg_weight

    return target.fillna(0.0)


def _pair_policy_parameters(
    *,
    family: str,
    params: dict[str, Any],
    defaults: dict[str, Any],
) -> dict[str, Any]:
    gross_target = float(params.get("gross_target", defaults.get("gross_target", 1.0)))
    max_gross_cap = 1.0 if family == "perp_pair_trade_unlevered" else 3.0
    max_gross_target = float(
        params.get(
            "max_gross_target",
            defaults.get("max_gross_target", gross_target),
        )
    )
    max_gross_target = max(gross_target, min(max_gross_cap, max_gross_target))

    entry_default = defaults.get("entry_abs_score", defaults.get("min_abs_score", 0.0))
    entry_abs_score = float(
        params.get(
            "entry_abs_score",
            params.get("min_abs_score", entry_default),
        )
    )
    entry_abs_score = max(0.0, min(1.5, entry_abs_score))

    exit_default = defaults.get("exit_abs_score", max(0.0, entry_abs_score * 0.5))
    exit_abs_score = float(params.get("exit_abs_score", exit_default))
    exit_abs_score = max(0.0, min(entry_abs_score, exit_abs_score))

    flip_default = defaults.get("flip_abs_score", entry_abs_score)
    flip_abs_score = float(params.get("flip_abs_score", flip_default))
    flip_abs_score = max(entry_abs_score, min(2.5, flip_abs_score))

    max_holding_bars = int(params.get("max_holding_bars", defaults.get("max_holding_bars", 0)))
    max_holding_bars = max(0, min(24 * 14, max_holding_bars))

    cooldown_bars = int(params.get("cooldown_bars", defaults.get("cooldown_bars", 0)))
    cooldown_bars = max(0, min(24 * 7, cooldown_bars))

    signal_leverage_scale = float(
        params.get(
            "signal_leverage_scale",
            defaults.get("signal_leverage_scale", 0.75),
        )
    )
    signal_leverage_scale = max(0.25, min(3.0, signal_leverage_scale))

    return {
        "gross_target": gross_target,
        "max_gross_target": max_gross_target,
        "entry_abs_score": entry_abs_score,
        "exit_abs_score": exit_abs_score,
        "flip_abs_score": flip_abs_score,
        "max_holding_bars": max_holding_bars,
        "cooldown_bars": cooldown_bars,
        "signal_leverage_scale": signal_leverage_scale,
        "min_abs_score": entry_abs_score,
    }


def _ranked_policy_parameters(
    *,
    params: dict[str, Any],
    defaults: dict[str, Any],
    long_enabled_default: bool,
    short_enabled_default: bool,
) -> dict[str, Any]:
    gross_target = float(params.get("gross_target", defaults.get("gross_target", 1.0)))
    min_abs_score = float(params.get("min_abs_score", defaults.get("min_abs_score", 0.0)))
    long_count = int(params.get("long_count", defaults.get("long_count", 0)))
    short_count = int(params.get("short_count", defaults.get("short_count", 0)))
    return {
        "gross_target": max(0.1, min(3.0, gross_target)),
        "min_abs_score": max(0.0, min(1.5, min_abs_score)),
        "long_count": max(0, min(8, long_count)),
        "short_count": max(0, min(8, short_count)),
        "long_enabled": bool(params.get("long_enabled", long_enabled_default)),
        "short_enabled": bool(params.get("short_enabled", short_enabled_default)),
    }


def _gate_mask_from_frame(
    frame: pd.DataFrame,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> pd.Series:
    numeric = frame.apply(pd.to_numeric, errors="coerce")
    if minimum is None and maximum is None:
        return numeric.fillna(0.0).gt(0.0).all(axis=1)
    mask = pd.Series(True, index=numeric.index, dtype=bool)
    if minimum is not None:
        mask &= numeric.ge(float(minimum)).all(axis=1)
    if maximum is not None:
        mask &= numeric.le(float(maximum)).all(axis=1)
    return mask.fillna(False)


def _resolve_regime_gates(
    regime_gates: dict[str, Any],
    *,
    aliases: dict[str, str],
    raw_frames: dict[str, pd.DataFrame],
) -> tuple[pd.Series | None, dict[str, Any]]:
    payload = dict(regime_gates or {})
    entry_specs = list(payload.get("entry") or [])
    exit_on_break = bool(payload.get("exit_on_break", True))
    if not entry_specs:
        return None, {"configured": False, "entry": [], "exit_on_break": exit_on_break}

    normalized_specs: list[dict[str, Any]] = []
    expressions: list[str] = []
    for spec in entry_specs:
        if isinstance(spec, str):
            expression = spec.strip()
            minimum = None
            maximum = None
        elif isinstance(spec, dict):
            expression = str(spec.get("expression") or spec.get("feature") or "").strip()
            minimum = spec.get("min")
            maximum = spec.get("max")
        else:
            continue
        if not expression:
            continue
        normalized: dict[str, str | float] = {"expression": expression}
        if minimum is not None:
            normalized["min"] = float(minimum)
        if maximum is not None:
            normalized["max"] = float(maximum)
        normalized_specs.append(normalized)
        expressions.append(expression)

    if not expressions:
        return None, {"configured": False, "entry": [], "exit_on_break": exit_on_break}

    resolved = resolve_feature_frames(
        expressions,
        aliases=aliases,
        raw_frames=raw_frames,
    )

    combined_mask: pd.Series | None = None
    entry_details: list[dict[str, Any]] = []
    for spec in normalized_specs:
        frame = resolved.get(spec["expression"])
        if frame is None or frame.empty:
            continue
        gate_mask = _gate_mask_from_frame(
            frame,
            minimum=spec.get("min"),
            maximum=spec.get("max"),
        )
        combined_mask = gate_mask if combined_mask is None else (combined_mask & gate_mask)
        entry_details.append(
            {
                **spec,
                "active_fraction": float(gate_mask.mean()) if len(gate_mask.index) else 0.0,
            }
        )

    if combined_mask is None:
        return None, {"configured": False, "entry": [], "exit_on_break": exit_on_break}

    return combined_mask.fillna(False), {
        "configured": True,
        "entry": entry_details,
        "exit_on_break": exit_on_break,
        "combined_active_fraction": float(combined_mask.mean()) if len(combined_mask.index) else 0.0,
    }


def _single_column_frame(series: pd.Series, *, name: str) -> pd.DataFrame:
    clean = pd.to_numeric(series, errors="coerce")
    return clean.rename(name).to_frame()


def _mean_pairwise_rolling_corr(
    returns: pd.DataFrame,
    *,
    window: int,
) -> pd.Series:
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


def _perp_global_raw_frames(
    prices: pd.DataFrame,
    funding: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    prices = prices.sort_index()
    funding = funding.reindex(prices.index).ffill().fillna(0.0)
    returns_1h = prices.pct_change()
    market_price_mean = prices.mean(axis=1)
    market_funding_mean = funding.mean(axis=1)
    market_funding_dispersion = funding.std(axis=1)
    market_breadth_24h = prices.pct_change(24).gt(0.0).mean(axis=1)
    market_co_movement_72h = _mean_pairwise_rolling_corr(returns_1h, window=72)
    market_realized_vol_168h = returns_1h.rolling(168).std().mean(axis=1)
    return {
        "market_price_mean": _single_column_frame(market_price_mean, name="GLOBAL"),
        "market_funding_mean": _single_column_frame(market_funding_mean, name="GLOBAL"),
        "market_funding_dispersion": _single_column_frame(
            market_funding_dispersion,
            name="GLOBAL",
        ),
        "market_breadth_24h": _single_column_frame(market_breadth_24h, name="GLOBAL"),
        "market_co_movement_72h": _single_column_frame(market_co_movement_72h, name="GLOBAL"),
        "market_realized_vol_168h": _single_column_frame(
            market_realized_vol_168h,
            name="GLOBAL",
        ),
    }


def _perp_raw_frames(
    prices: pd.DataFrame,
    funding: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    return {
        "price": prices,
        "funding": funding,
        **_perp_global_raw_frames(prices, funding),
    }


def _pair_raw_frames(
    *,
    prices: pd.DataFrame,
    funding: pd.DataFrame,
    asset_1_symbol: str,
    asset_2_symbol: str,
) -> dict[str, pd.DataFrame]:
    asset_1_price = prices[asset_1_symbol].replace([np.inf, -np.inf], np.nan)
    asset_2_price = prices[asset_2_symbol].replace([np.inf, -np.inf], np.nan)
    asset_1_funding = funding[asset_1_symbol].replace([np.inf, -np.inf], np.nan)
    asset_2_funding = funding[asset_2_symbol].replace([np.inf, -np.inf], np.nan)
    ratio = asset_1_price.div(asset_2_price).replace([np.inf, -np.inf], np.nan)
    funding_spread = asset_1_funding.sub(asset_2_funding, fill_value=0.0)
    index = prices.index
    return {
        "asset_1_price": asset_1_price.rename("PAIR").to_frame().reindex(index),
        "asset_2_price": asset_2_price.rename("PAIR").to_frame().reindex(index),
        "asset_1_funding": asset_1_funding.rename("PAIR").to_frame().reindex(index),
        "asset_2_funding": asset_2_funding.rename("PAIR").to_frame().reindex(index),
        "price_ratio": ratio.rename("PAIR").to_frame().reindex(index),
        "funding_spread": funding_spread.rename("PAIR").to_frame().reindex(index),
        **_perp_global_raw_frames(prices[[asset_1_symbol, asset_2_symbol]], funding[[asset_1_symbol, asset_2_symbol]]),
    }


def _pt_raw_frames(
    prices: pd.DataFrame,
    implied_apy: pd.DataFrame,
    underlying_apy: pd.DataFrame,
    total_tvl: pd.DataFrame,
    days_to_expiry: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    return {
        "pt_price": prices,
        "implied_apy": implied_apy,
        "underlying_apy": underlying_apy,
        "total_tvl": total_tvl,
        "days_to_expiry": days_to_expiry.clip(lower=1.0),
    }


def _prepare_pt_market_frames(
    provider: MarketDataProvider,
    markets: list[dict[str, Any]],
    histories: dict[str, pd.DataFrame],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    labels = list(histories)

    def _metric_frame(column: str) -> pd.DataFrame:
        frame = pd.concat(
            [pd.to_numeric(histories[label][column], errors="coerce").rename(label) for label in labels],
            axis=1,
        ).sort_index()
        return _ffill_within_observed_window(frame)

    prices = _metric_frame("ptPrice")
    implied_apy = _metric_frame("impliedApy")
    underlying_apy = _metric_frame("underlyingApy")
    total_tvl = _metric_frame("totalTvl")

    expiry_by_label = {
        provider.market_label(row): pd.Timestamp(str(row["expiry"])).tz_localize(None)
        for row in markets
        if provider.market_label(row) in labels
    }
    days_to_expiry = pd.DataFrame(index=prices.index, columns=prices.columns, dtype=float)
    for label in prices.columns:
        expiry = expiry_by_label[label]
        days_to_expiry[label] = (
            (expiry - prices.index.to_series()).dt.total_seconds() / 86400.0
        ).values

    valid_rows = prices.notna().any(axis=1)
    prices = prices.loc[valid_rows]
    implied_apy = implied_apy.reindex(prices.index)
    underlying_apy = underlying_apy.reindex(prices.index)
    total_tvl = total_tvl.reindex(prices.index)
    days_to_expiry = days_to_expiry.reindex(prices.index)
    return prices, implied_apy, underlying_apy, total_tvl, days_to_expiry


def _ffill_within_observed_window(frame: pd.DataFrame) -> pd.DataFrame:
    filled = frame.sort_index().ffill()
    for column in filled.columns:
        observed = frame[column].dropna()
        if observed.empty:
            filled[column] = np.nan
            continue
        filled.loc[filled.index < observed.index.min(), column] = np.nan
        filled.loc[filled.index > observed.index.max(), column] = np.nan
    return filled


def _build_pt_hedge_positions(
    pt_positions: pd.DataFrame,
    *,
    market_to_hedge_symbol: dict[str, str],
    hedge_symbols: list[str],
    hedge_ratio: float,
) -> pd.DataFrame:
    columns = [f"{symbol}_PERP" for symbol in hedge_symbols]
    hedge_positions = pd.DataFrame(0.0, index=pt_positions.index, columns=columns)
    for market_label, hedge_symbol in market_to_hedge_symbol.items():
        if market_label not in pt_positions.columns or not hedge_symbol:
            continue
        hedge_column = f"{hedge_symbol}_PERP"
        if hedge_column not in hedge_positions.columns:
            continue
        hedge_positions[hedge_column] = hedge_positions[hedge_column].add(
            -pt_positions[market_label] * hedge_ratio,
            fill_value=0.0,
        )
    return hedge_positions.fillna(0.0)


def _build_lending_price_frames(
    root_prices: pd.DataFrame,
    carry_apy: pd.DataFrame,
) -> pd.DataFrame:
    if root_prices.empty:
        return root_prices
    dt_years = (
        root_prices.index.to_series().diff().dt.total_seconds().fillna(0.0)
        / (365.25 * 24.0 * 60.0 * 60.0)
    )
    positive_dt = dt_years[dt_years > 0]
    fallback_dt = float(positive_dt.median()) if not positive_dt.empty else 0.0
    dt_years = dt_years.replace(0.0, np.nan).fillna(fallback_dt)
    carry_factor = (
        1.0 + carry_apy.shift(1).fillna(0.0).mul(dt_years, axis=0).clip(lower=-0.99)
    ).cumprod()
    return root_prices.reindex(carry_factor.index).ffill().mul(carry_factor)


def _feature_hash(features: list[str]) -> str:
    payload = "|".join(sorted(features))
    return sha256(payload.encode("utf-8")).hexdigest()[:16]


def _history_bounds(frame: pd.DataFrame) -> dict[str, str | None]:
    if frame.empty:
        return {"history_start": None, "history_end": None}
    return {
        "history_start": frame.index.min().isoformat(),
        "history_end": frame.index.max().isoformat(),
    }


def _pt_lifecycle_metadata(
    *,
    spec: SignalSpec,
    prices: pd.DataFrame,
    days_to_expiry: pd.DataFrame,
    eligible: pd.DataFrame,
    inside_roll_window: pd.DataFrame,
    expired_or_untradable: pd.DataFrame,
    roll_events: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "lifecycle_policy": {
            "open_ended_policy": "continuous_rotation",
            "pt_universe_policy": "dynamic_eligible_set",
            "roll_target_policy": "strategy_ranked_replacement",
            "roll_cost_model": "simple_trading_cost",
            "roll_days_before_expiry": spec.risk.roll_days_before_expiry,
            "min_days_to_expiry": spec.universe.min_days_to_expiry,
            "max_days_to_expiry": spec.universe.max_days_to_expiry,
        },
        "pt_strategy_badges": ["open-ended", "dynamic universe", "roll-forward"],
        "roll_event_count": len(roll_events),
        "roll_events": roll_events,
        **summarize_pt_universe(
            prices=prices,
            eligible=eligible,
            inside_roll_window=inside_roll_window,
            expired_or_untradable=expired_or_untradable,
        ),
        "days_to_expiry_latest": {
            column: float(value)
            for column, value in days_to_expiry.iloc[-1].dropna().to_dict().items()
        }
        if not days_to_expiry.empty
        else {},
    }


def _lending_raw_frames(
    *,
    lending_prices: pd.DataFrame,
    combined_supply_apy: pd.DataFrame,
    supply_apr: pd.DataFrame,
    supply_reward_apr: pd.DataFrame,
    base_yield_apy: pd.DataFrame,
    utilization: pd.DataFrame,
    supply_tvl_usd: pd.DataFrame,
    borrow_apr: pd.DataFrame,
    borrow_tvl_usd: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    return {
        "lending_price": lending_prices,
        "combined_supply_apy": combined_supply_apy,
        "supply_apr": supply_apr,
        "supply_reward_apr": supply_reward_apr,
        "base_yield_apy": base_yield_apy,
        "utilization": utilization,
        "supply_tvl_usd": supply_tvl_usd,
        "borrow_apr": borrow_apr,
        "borrow_tvl_usd": borrow_tvl_usd,
    }


async def compile_spec(
    settings: SiglabConfig,
    provider: MarketDataProvider,
    spec: SignalSpec,
) -> CompiledChild:
    family_spec = load_family_spec(settings.root_dir, spec.track, spec.family)
    defaults = family_spec.get("defaults") or {}
    feature_weights = family_spec.get("feature_weights") or {}
    capabilities = family_capabilities(family_spec)
    execution_profile = family_execution_profile(family_spec)
    diagnostic_adapter = family_diagnostic_adapter(family_spec)
    policy_schema = family_policy_schema(family_spec)
    feature_spec = load_feature_spec(
        settings.root_dir,
        track=spec.track,
        family=spec.family,
    )
    feature_aliases = feature_spec.get("aliases") or {}

    if spec.track == "trend_signals" and spec.family in PAIR_TRADE_FAMILIES:
        requested_symbols = [str(symbol).upper() for symbol in spec.universe.basis_groups[:2]]
        symbols = await provider.discover_perp_symbols(
            requested_symbols,
            limit=2,
        )
        ordered_symbols = [symbol for symbol in requested_symbols if symbol in symbols]
        for symbol in symbols:
            if symbol not in ordered_symbols:
                ordered_symbols.append(symbol)
        if len(ordered_symbols) != 2:
            raise ValueError("Pair trade family requires exactly two supported symbols")
        asset_1_symbol, asset_2_symbol = ordered_symbols[0], ordered_symbols[1]
        bundle = await provider.fetch_perp_bundle(
            symbols=ordered_symbols,
            lookback_days=spec.universe.lookback_days,
            interval=spec.universe.interval,
        )
        raw_frames = _pair_raw_frames(
            prices=bundle["prices"],
            funding=bundle["funding"],
            asset_1_symbol=asset_1_symbol,
            asset_2_symbol=asset_2_symbol,
        )
        feature_frames = resolve_feature_frames(
            spec.features,
            aliases=feature_aliases,
            raw_frames=raw_frames,
        )
        score = _weighted_score(
            feature_frames,
            spec.features,
            feature_weights,
            normalization="time_series",
            z_window=72,
        )
        signal_components = _weighted_component_frames(
            feature_frames,
            spec.features,
            feature_weights,
            normalization="time_series",
            z_window=72,
        )
        pair_policy = _pair_policy_parameters(
            family=spec.family,
            params=spec.params,
            defaults=defaults,
        )
        regime_gate_mask, regime_gate_metadata = _resolve_regime_gates(
            spec.regime_gates,
            aliases=feature_aliases,
            raw_frames=raw_frames,
        )
        positions = _build_pair_trade_positions(
            score,
            asset_1_symbol=asset_1_symbol,
            asset_2_symbol=asset_2_symbol,
            gross_target=pair_policy["gross_target"],
            max_gross_target=pair_policy["max_gross_target"],
            max_asset_weight=spec.risk.max_asset_weight,
            entry_abs_score=pair_policy["entry_abs_score"],
            exit_abs_score=pair_policy["exit_abs_score"],
            flip_abs_score=pair_policy["flip_abs_score"],
            max_holding_bars=pair_policy["max_holding_bars"],
            cooldown_bars=pair_policy["cooldown_bars"],
            signal_leverage_scale=pair_policy["signal_leverage_scale"],
            regime_gate_mask=regime_gate_mask,
            exit_on_regime_break=bool(regime_gate_metadata.get("exit_on_break", True)),
        )
        return CompiledChild(
            prices=bundle["prices"][ordered_symbols],
            target_positions=positions,
            funding_rates=bundle["funding"][ordered_symbols],
            metadata={
                "track": spec.track,
                "family": spec.family,
                "capabilities": capabilities,
                "execution_profile": execution_profile,
                "diagnostic_adapter": diagnostic_adapter,
                "policy_schema": policy_schema,
                "symbols": ordered_symbols,
                "asset_1_symbol": asset_1_symbol,
                "asset_2_symbol": asset_2_symbol,
                "features": list(spec.features),
                "feature_hash": _feature_hash(spec.features),
                "gross_target": pair_policy["gross_target"],
                "max_gross_target": pair_policy["max_gross_target"],
                "entry_abs_score": pair_policy["entry_abs_score"],
                "exit_abs_score": pair_policy["exit_abs_score"],
                "flip_abs_score": pair_policy["flip_abs_score"],
                "max_holding_bars": pair_policy["max_holding_bars"],
                "cooldown_bars": pair_policy["cooldown_bars"],
                "min_abs_score": pair_policy["min_abs_score"],
                "signal_leverage_scale": pair_policy["signal_leverage_scale"],
                "regime_gates": regime_gate_metadata,
                "leverage_profile": (
                    "unlevered"
                    if spec.family == "perp_pair_trade_unlevered"
                    else "levered"
                ),
                "source": bundle["source"],
                "bundle_as_of": bundle.get("bundle_as_of"),
                "asset_breadth": len(ordered_symbols),
                "signal_timing": "next_bar",
                "compiled_at": datetime.now(UTC).isoformat(),
                **_history_bounds(bundle["prices"]),
            },
            signal_score=score,
            signal_components=signal_components,
            regime_gate_mask=regime_gate_mask,
        )

    if spec.track == "trend_signals" and execution_profile in PERP_EXECUTION_PROFILES:
        symbols = await provider.discover_perp_symbols(
            spec.universe.basis_groups,
            limit=spec.universe.max_symbols,
        )
        bundle = await provider.fetch_perp_bundle(
            symbols=symbols,
            lookback_days=spec.universe.lookback_days,
            interval=spec.universe.interval,
        )
        raw_frames = _perp_raw_frames(bundle["prices"], bundle["funding"])
        feature_frames = resolve_feature_frames(
            spec.features,
            aliases=feature_aliases,
            raw_frames=raw_frames,
        )
        score = _weighted_score(
            feature_frames,
            spec.features,
            feature_weights,
            normalization="time_series",
            z_window=72,
        )
        signal_components = _weighted_component_frames(
            feature_frames,
            spec.features,
            feature_weights,
            normalization="time_series",
            z_window=72,
        )
        score = _align_cross_sectional_frame(score, tradable_symbols=symbols)
        signal_components = _align_cross_sectional_components(
            signal_components,
            tradable_symbols=symbols,
        )
        regime_gate_mask, regime_gate_metadata = _resolve_regime_gates(
            spec.regime_gates,
            aliases=feature_aliases,
            raw_frames=raw_frames,
        )
        long_enabled_default = True
        short_enabled_default = execution_profile != "ranked_directional"
        policy = _ranked_policy_parameters(
            params=spec.params,
            defaults=defaults,
            long_enabled_default=long_enabled_default,
            short_enabled_default=short_enabled_default,
        )
        require_positive_longs = execution_profile == "ranked_directional"
        require_both_sides = execution_profile in {"basket_neutral_spread", "ranked_carry"}
        positions = _build_ranked_positions(
            score,
            long_count=(policy["long_count"] if policy["long_enabled"] else 0),
            short_count=(policy["short_count"] if policy["short_enabled"] else 0),
            gross_target=policy["gross_target"],
            max_asset_weight=spec.risk.max_asset_weight,
            require_positive_longs=require_positive_longs,
            min_abs_score=policy["min_abs_score"],
            require_both_sides=require_both_sides,
            regime_gate_mask=regime_gate_mask,
        )
        return CompiledChild(
            prices=bundle["prices"],
            target_positions=positions,
            funding_rates=bundle["funding"],
            metadata={
                "track": spec.track,
                "family": spec.family,
                "capabilities": capabilities,
                "execution_profile": execution_profile,
                "diagnostic_adapter": diagnostic_adapter,
                "policy_schema": policy_schema,
                "symbols": symbols,
                "features": list(spec.features),
                "feature_hash": _feature_hash(spec.features),
                "long_enabled": policy["long_enabled"],
                "short_enabled": policy["short_enabled"],
                "long_count": policy["long_count"],
                "short_count": policy["short_count"],
                "min_abs_score": policy["min_abs_score"],
                "gross_target": policy["gross_target"],
                "regime_gates": regime_gate_metadata,
                "source": bundle["source"],
                "bundle_as_of": bundle.get("bundle_as_of"),
                "asset_breadth": len(symbols),
                "signal_timing": "next_bar",
                "compiled_at": datetime.now(UTC).isoformat(),
                **_history_bounds(bundle["prices"]),
            },
            signal_score=score,
            signal_components=signal_components,
            regime_gate_mask=regime_gate_mask,
        )

    if spec.family == "basis_spread":
        symbols = await provider.discover_perp_symbols(
            spec.universe.basis_groups,
            limit=spec.universe.max_symbols,
        )
        bundle = await provider.fetch_perp_bundle(
            symbols=symbols,
            lookback_days=spec.universe.lookback_days,
            interval=spec.universe.interval,
        )
        raw_frames = _perp_raw_frames(bundle["prices"], bundle["funding"])
        feature_frames = resolve_feature_frames(
            spec.features,
            aliases=feature_aliases,
            raw_frames=raw_frames,
        )
        score = _weighted_score(feature_frames, spec.features, feature_weights)
        signal_components = _weighted_component_frames(
            feature_frames,
            spec.features,
            feature_weights,
        )
        regime_gate_mask, regime_gate_metadata = _resolve_regime_gates(
            spec.regime_gates,
            aliases=feature_aliases,
            raw_frames=raw_frames,
        )
        pair_positions = _build_pair_positions(
            score,
            selection_count=int(
                spec.params.get("selection_count", defaults.get("selection_count", 2))
            ),
            gross_target=float(spec.params.get("gross_target", defaults.get("gross_target", 1.0))),
            max_asset_weight=spec.risk.max_asset_weight,
            regime_gate_mask=regime_gate_mask,
        )
        spot_prices, spot_funding = convert_to_spot(bundle["prices"])
        prices = pd.concat(
            [spot_prices.add_suffix("_SPOT"), bundle["prices"].add_suffix("_PERP")],
            axis=1,
        ).sort_index()
        funding = pd.concat(
            [spot_funding.add_suffix("_SPOT"), bundle["funding"].add_suffix("_PERP")],
            axis=1,
        ).sort_index()
        funding = funding.reindex(prices.index).ffill().fillna(0.0)
        return CompiledChild(
            prices=prices,
            target_positions=pair_positions.reindex(prices.index).ffill().fillna(0.0),
            funding_rates=funding,
            metadata={
                "track": spec.track,
                "family": spec.family,
                "capabilities": capabilities,
                "execution_profile": execution_profile,
                "diagnostic_adapter": diagnostic_adapter,
                "policy_schema": policy_schema,
                "symbols": symbols,
                "features": list(spec.features),
                "feature_hash": _feature_hash(spec.features),
                "selection_count": int(
                    spec.params.get("selection_count", defaults.get("selection_count", 2))
                ),
                "gross_target": float(
                    spec.params.get("gross_target", defaults.get("gross_target", 1.0))
                ),
                "regime_gates": regime_gate_metadata,
                "source": bundle["source"],
                "bundle_as_of": bundle.get("bundle_as_of"),
                "asset_breadth": len(symbols),
                "signal_timing": "next_bar",
                "compiled_at": datetime.now(UTC).isoformat(),
                **_history_bounds(prices),
            },
            signal_score=score,
            signal_components=signal_components,
            regime_gate_mask=regime_gate_mask,
        )

    if spec.family == "stable_pt_ladder":
        markets = await provider.discover_stable_pt_markets(
            spec.universe,
            limit=spec.universe.max_symbols,
        )
        histories = await provider.fetch_pt_histories(
            markets,
            lookback_days=spec.universe.lookback_days,
        )
        if not histories:
            raise ValueError("No stable PT histories available for this spec")

        prices, implied_apy, underlying_apy, total_tvl, days_to_expiry = _prepare_pt_market_frames(
            provider,
            markets,
            histories,
        )
        feature_frames = resolve_feature_frames(
            spec.features,
            aliases=feature_aliases,
            raw_frames=_pt_raw_frames(
                prices=prices,
                implied_apy=implied_apy,
                underlying_apy=underlying_apy,
                total_tvl=total_tvl,
                days_to_expiry=days_to_expiry,
            ),
        )
        pt_state = classify_pt_market_state(
            prices=prices,
            days_to_expiry=days_to_expiry,
            required_frames=[implied_apy, underlying_apy, total_tvl],
            roll_days_before_expiry=spec.risk.roll_days_before_expiry,
            min_days_to_expiry=spec.universe.min_days_to_expiry,
            max_days_to_expiry=spec.universe.max_days_to_expiry,
        )
        feature_frames = _mask_feature_frames(feature_frames, pt_state["eligible"])
        score = _ensure_single_eligible_scores(
            _weighted_score(feature_frames, spec.features, feature_weights),
            pt_state["eligible"],
        )
        positions = _build_ranked_positions(
            score,
            long_count=int(
                spec.params.get("selection_count", defaults.get("selection_count", 3))
            ),
            short_count=0,
            gross_target=float(spec.params.get("gross_target", defaults.get("gross_target", 0.9))),
            max_asset_weight=spec.risk.max_asset_weight,
            require_positive_longs=True,
        )
        roll_events = detect_pt_roll_events(
            positions,
            eligible=pt_state["eligible"],
            inside_roll_window=pt_state["inside_roll_window"],
            expired_or_untradable=pt_state["expired_or_untradable"],
            days_to_expiry=days_to_expiry,
        )
        funding = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
        return CompiledChild(
            prices=prices,
            target_positions=positions.reindex(prices.index).ffill().fillna(0.0),
            funding_rates=funding,
            metadata={
                "track": spec.track,
                "family": spec.family,
                "capabilities": capabilities,
                "execution_profile": execution_profile,
                "diagnostic_adapter": diagnostic_adapter,
                "policy_schema": policy_schema,
                "markets": list(prices.columns),
                "features": list(spec.features),
                "feature_hash": _feature_hash(spec.features),
                "selection_count": int(
                    spec.params.get("selection_count", defaults.get("selection_count", 3))
                ),
                "gross_target": float(
                    spec.params.get("gross_target", defaults.get("gross_target", 0.9))
                ),
                "source": "pendle_public",
                "bundle_as_of": prices.index.max().isoformat() if not prices.empty else None,
                "asset_breadth": len(prices.columns),
                "signal_timing": "next_bar",
                "compiled_at": datetime.now(UTC).isoformat(),
                **_pt_lifecycle_metadata(
                    spec=spec,
                    prices=prices,
                    days_to_expiry=days_to_expiry,
                    eligible=pt_state["eligible"],
                    inside_roll_window=pt_state["inside_roll_window"],
                    expired_or_untradable=pt_state["expired_or_untradable"],
                    roll_events=roll_events,
                ),
                **_history_bounds(prices),
            },
        )

    if spec.family == "pt_yield_rotation":
        markets = await provider.discover_pt_markets(
            spec.universe,
            limit=spec.universe.max_symbols,
        )
        histories = await provider.fetch_pt_histories(
            markets,
            lookback_days=spec.universe.lookback_days,
        )
        if not histories:
            raise ValueError("No PT histories available for this spec")

        prices, implied_apy, underlying_apy, total_tvl, days_to_expiry = _prepare_pt_market_frames(
            provider,
            markets,
            histories,
        )
        feature_frames = resolve_feature_frames(
            spec.features,
            aliases=feature_aliases,
            raw_frames=_pt_raw_frames(
                prices=prices,
                implied_apy=implied_apy,
                underlying_apy=underlying_apy,
                total_tvl=total_tvl,
                days_to_expiry=days_to_expiry,
            ),
        )
        pt_state = classify_pt_market_state(
            prices=prices,
            days_to_expiry=days_to_expiry,
            required_frames=[implied_apy, underlying_apy, total_tvl],
            roll_days_before_expiry=spec.risk.roll_days_before_expiry,
            min_days_to_expiry=spec.universe.min_days_to_expiry,
            max_days_to_expiry=spec.universe.max_days_to_expiry,
        )
        feature_frames = _mask_feature_frames(feature_frames, pt_state["eligible"])
        score = _ensure_single_eligible_scores(
            _weighted_score(feature_frames, spec.features, feature_weights),
            pt_state["eligible"],
        )
        pt_positions = _build_ranked_positions(
            score,
            long_count=int(
                spec.params.get("selection_count", defaults.get("selection_count", 2))
            ),
            short_count=0,
            gross_target=float(spec.params.get("gross_target", defaults.get("gross_target", 0.8))),
            max_asset_weight=spec.risk.max_asset_weight,
            require_positive_longs=True,
        )
        roll_events = detect_pt_roll_events(
            pt_positions,
            eligible=pt_state["eligible"],
            inside_roll_window=pt_state["inside_roll_window"],
            expired_or_untradable=pt_state["expired_or_untradable"],
            days_to_expiry=days_to_expiry,
        )

        funding = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
        combined_prices = prices.copy()
        combined_positions = pt_positions.reindex(prices.index).ffill().fillna(0.0)
        hedge_mode = str(spec.params.get("hedge_mode", defaults.get("hedge_mode", "none"))).lower()
        hedge_ratio = float(spec.params.get("hedge_ratio", defaults.get("hedge_ratio", 1.0)))
        hedge_symbols: list[str] = []
        source = "pendle_public"

        if hedge_mode == "perp":
            market_to_hedge_symbol = {
                provider.market_label(row): str(row.get("hedgeSymbol"))
                for row in markets
                if provider.market_label(row) in prices.columns
                and row.get("hedgeSymbol")
                and str(row.get("hedgeSymbol")) != "USD"
            }
            hedge_symbols = sorted(set(market_to_hedge_symbol.values()))
            if hedge_symbols:
                hedge_bundle = await provider.fetch_perp_bundle(
                    symbols=hedge_symbols,
                    lookback_days=spec.universe.lookback_days,
                    interval="1h",
                )
                hedge_prices = hedge_bundle["prices"].reindex(prices.index).ffill().add_suffix("_PERP")
                hedge_funding = (
                    hedge_bundle["funding"]
                    .reindex(prices.index)
                    .ffill()
                    .fillna(0.0)
                    .add_suffix("_PERP")
                )
                hedge_positions = _build_pt_hedge_positions(
                    combined_positions,
                    market_to_hedge_symbol=market_to_hedge_symbol,
                    hedge_symbols=hedge_symbols,
                    hedge_ratio=hedge_ratio,
                )
                combined_prices = pd.concat([prices, hedge_prices], axis=1).sort_index()
                combined_positions = (
                    pd.concat([combined_positions, hedge_positions], axis=1)
                    .reindex(combined_prices.index)
                    .ffill()
                    .fillna(0.0)
                )
                funding = (
                    pd.concat([funding, hedge_funding], axis=1)
                    .reindex(combined_prices.index)
                    .ffill()
                    .fillna(0.0)
                )
                source = f"pendle_public+{hedge_bundle['source']}"

        return CompiledChild(
            prices=combined_prices,
            target_positions=combined_positions,
            funding_rates=funding,
            metadata={
                "track": spec.track,
                "family": spec.family,
                "capabilities": capabilities,
                "execution_profile": execution_profile,
                "diagnostic_adapter": diagnostic_adapter,
                "policy_schema": policy_schema,
                "markets": list(prices.columns),
                "features": list(spec.features),
                "feature_hash": _feature_hash(spec.features),
                "hedge_mode": hedge_mode,
                "hedge_ratio": hedge_ratio,
                "hedge_symbols": hedge_symbols,
                "selection_count": int(
                    spec.params.get("selection_count", defaults.get("selection_count", 2))
                ),
                "gross_target": float(
                    spec.params.get("gross_target", defaults.get("gross_target", 0.8))
                ),
                "source": source,
                "bundle_as_of": prices.index.max().isoformat() if not prices.empty else None,
                "asset_breadth": len(prices.columns),
                "signal_timing": "next_bar",
                "compiled_at": datetime.now(UTC).isoformat(),
                **_pt_lifecycle_metadata(
                    spec=spec,
                    prices=prices,
                    days_to_expiry=days_to_expiry,
                    eligible=pt_state["eligible"],
                    inside_roll_window=pt_state["inside_roll_window"],
                    expired_or_untradable=pt_state["expired_or_untradable"],
                    roll_events=roll_events,
                ),
                **_history_bounds(combined_prices),
            },
        )

    if spec.family == "lending_carry_rotation":
        markets = await provider.discover_lending_markets(
            spec.universe,
            limit=spec.universe.max_symbols,
        )
        lending_bundle = await provider.fetch_lending_bundle(
            markets,
            lookback_days=spec.universe.lookback_days,
        )
        if lending_bundle["prices"].empty:
            raise ValueError("No lending histories available for this spec")

        lending_prices = _build_lending_price_frames(
            lending_bundle["prices"],
            lending_bundle["combined_supply_apy"],
        )
        raw_frames = _lending_raw_frames(
            lending_prices=lending_prices,
            combined_supply_apy=lending_bundle["combined_supply_apy"],
            supply_apr=lending_bundle["supply_apr"],
            supply_reward_apr=lending_bundle["supply_reward_apr"],
            base_yield_apy=lending_bundle["base_yield_apy"],
            utilization=lending_bundle["utilization"],
            supply_tvl_usd=lending_bundle["supply_tvl_usd"],
            borrow_apr=lending_bundle["borrow_apr"],
            borrow_tvl_usd=lending_bundle["borrow_tvl_usd"],
        )
        feature_frames = resolve_feature_frames(
            spec.features,
            aliases=feature_aliases,
            raw_frames=raw_frames,
        )
        score = _weighted_score(feature_frames, spec.features, feature_weights)
        signal_components = _weighted_component_frames(
            feature_frames,
            spec.features,
            feature_weights,
        )
        regime_gate_mask, regime_gate_metadata = _resolve_regime_gates(
            spec.regime_gates,
            aliases=feature_aliases,
            raw_frames=raw_frames,
        )
        lending_positions = _build_ranked_positions(
            score,
            long_count=int(
                spec.params.get("selection_count", defaults.get("selection_count", 2))
            ),
            short_count=0,
            gross_target=float(spec.params.get("gross_target", defaults.get("gross_target", 0.8))),
            max_asset_weight=spec.risk.max_asset_weight,
            require_positive_longs=True,
            regime_gate_mask=regime_gate_mask,
        )

        funding = pd.DataFrame(0.0, index=lending_prices.index, columns=lending_prices.columns)
        combined_prices = lending_prices.copy()
        combined_positions = lending_positions.reindex(lending_prices.index).ffill().fillna(0.0)
        hedge_mode = str(spec.params.get("hedge_mode", defaults.get("hedge_mode", "none"))).lower()
        hedge_ratio = float(spec.params.get("hedge_ratio", defaults.get("hedge_ratio", 1.0)))
        local_hedge_symbols: list[str] = []
        source = lending_bundle["source"]

        if hedge_mode == "perp":
            market_to_hedge_symbol = {
                label: symbol
                for label, symbol in lending_bundle["hedge_symbols"].items()
                if symbol and symbol != "USD"
            }
            local_hedge_symbols = sorted(set(market_to_hedge_symbol.values()))
            if local_hedge_symbols:
                hedge_bundle = await provider.fetch_perp_bundle(
                    symbols=local_hedge_symbols,
                    lookback_days=spec.universe.lookback_days,
                    interval="1h",
                )
                hedge_prices = hedge_bundle["prices"].reindex(lending_prices.index).ffill().add_suffix("_PERP")
                hedge_funding = (
                    hedge_bundle["funding"]
                    .reindex(lending_prices.index)
                    .ffill()
                    .fillna(0.0)
                    .add_suffix("_PERP")
                )
                hedge_positions = _build_pt_hedge_positions(
                    combined_positions,
                    market_to_hedge_symbol=market_to_hedge_symbol,
                    hedge_symbols=local_hedge_symbols,
                    hedge_ratio=hedge_ratio,
                )
                combined_prices = pd.concat([lending_prices, hedge_prices], axis=1).sort_index()
                combined_positions = (
                    pd.concat([combined_positions, hedge_positions], axis=1)
                    .reindex(combined_prices.index)
                    .ffill()
                    .fillna(0.0)
                )
                funding = (
                    pd.concat([funding, hedge_funding], axis=1)
                    .reindex(combined_prices.index)
                    .ffill()
                    .fillna(0.0)
                )
                source = f"{source}+{hedge_bundle['source']}"

        return CompiledChild(
            prices=combined_prices,
            target_positions=combined_positions,
            funding_rates=funding,
            metadata={
                "track": spec.track,
                "family": spec.family,
                "capabilities": capabilities,
                "execution_profile": execution_profile,
                "diagnostic_adapter": diagnostic_adapter,
                "policy_schema": policy_schema,
                "markets": list(lending_prices.columns),
                "features": list(spec.features),
                "feature_hash": _feature_hash(spec.features),
                "hedge_mode": hedge_mode,
                "hedge_ratio": hedge_ratio,
                "hedge_symbols": local_hedge_symbols,
                "selection_count": int(
                    spec.params.get("selection_count", defaults.get("selection_count", 2))
                ),
                "gross_target": float(
                    spec.params.get("gross_target", defaults.get("gross_target", 0.8))
                ),
                "regime_gates": regime_gate_metadata,
                "source": source,
                "bundle_as_of": lending_bundle.get("bundle_as_of"),
                "asset_breadth": len(lending_prices.columns),
                "signal_timing": "next_bar",
                "compiled_at": datetime.now(UTC).isoformat(),
                **_history_bounds(combined_prices),
            },
            signal_score=score,
            signal_components=signal_components,
            regime_gate_mask=regime_gate_mask,
        )

    raise ValueError(f"Unsupported spec family: {spec.family}")


