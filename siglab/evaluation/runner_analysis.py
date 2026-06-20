"""Pre-audit analysis functions extracted from runner.py."""

from __future__ import annotations

from typing import Any, cast

import numpy as np
import pandas as pd

from siglab.evaluation.analysis_utils import pre_audit_trade_episodes as _pre_audit_trade_episodes_from_canonical
from siglab.evaluation.runner_episodes import _row_direction_label, _row_position_signature
from siglab.evaluation.runner_regime import (
    _equity_window_trade_stats,
    _lookup_timestamp,
    _trade_regime_pack,
    _window_regime_summary,
)
from siglab.evaluation.runner_utils import _series_from_payload
from siglab.utils import safe_float as _safe_float


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
        direction_series = pd.Series(
            [_row_direction_label(row) for _, row in window_positions.iterrows()],
            index=window_positions.index,
            dtype=object,
        )
        for direction, count in direction_series.value_counts().to_dict().items():
            if str(direction) == "flat":
                continue
            direction_counts[str(direction)] = int(count)
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
        score_frame = signal_score.iloc[:limit].apply(pd.to_numeric, errors="coerce").fillna(0.0)
        score_window = score_frame.iloc[peak_idx : trough_idx + 1]
        aligned_values: list[float] = []
        support_scores: list[float] = []
        trough_support = None
        if score_frame.shape[1] == 1 and window_positions.shape[1] >= 2:
            primary_sign = np.sign(
                pd.to_numeric(window_positions.iloc[:, 0], errors="coerce").fillna(0.0)
            )
            support_series = score_window.iloc[:, 0].mul(primary_sign.loc[score_window.index], fill_value=0.0)
            active_series = primary_sign.loc[score_window.index].abs() > 0
            if bool(active_series.any()):
                active_support = support_series[active_series]
                support_scores.extend(float(value) for value in active_support.tolist())
                aligned_values.extend(float(value > 0.0) for value in active_support.tolist())
            trough_timestamp = score_frame.index[trough_idx]
            trough_support = _safe_float(support_series.get(trough_timestamp), default=None)
        else:
            signed = np.sign(window_positions.reindex(columns=score_window.columns).fillna(0.0))
            position_sign: pd.DataFrame = pd.DataFrame(signed, index=score_window.index, columns=score_window.columns)
            for timestamp in score_window.index:
                active_cols = list(score_window.columns[position_sign.loc[timestamp].abs() > 0])
                if not active_cols:
                    continue
                signed_support = (
                    score_window.loc[timestamp, active_cols] * position_sign.loc[timestamp, active_cols]
                )
                support_scores.append(float(signed_support.mean()))
                aligned_values.append(float((signed_support > 0.0).mean()))
            trough_timestamp = score_frame.index[trough_idx]
            active_cols = list(score_frame.columns[position_sign.loc[trough_timestamp].abs() > 0])
            if active_cols:
                trough_support = _safe_float(
                    float(
                        (
                            score_frame.loc[trough_timestamp, active_cols]
                            * position_sign.loc[trough_timestamp, active_cols]
                        ).mean()
                    )
                )
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
        component = frame.iloc[:limit].apply(pd.to_numeric, errors="coerce").fillna(0.0)
        component_window = component.iloc[peak_idx : trough_idx + 1]
        comp_aligned_values: list[float] = []
        comp_support_scores: list[float] = []
        trough_component = None
        if component.shape[1] == 1 and window_positions.shape[1] >= 2:
            primary_sign = np.sign(
                pd.to_numeric(window_positions.iloc[:, 0], errors="coerce").fillna(0.0)
            )
            support_series = component_window.iloc[:, 0].mul(primary_sign.loc[component_window.index], fill_value=0.0)
            active_series = primary_sign.loc[component_window.index].abs() > 0
            if bool(active_series.any()):
                active_support = support_series[active_series]
                comp_support_scores.extend(float(value) for value in active_support.tolist())
                comp_aligned_values.extend(float(value > 0.0) for value in active_support.tolist())
            trough_timestamp = component.index[trough_idx]
            trough_component = _safe_float(support_series.get(trough_timestamp), default=None)
        else:
            signed = np.sign(window_positions.reindex(columns=component_window.columns).fillna(0.0))
            position_sign = pd.DataFrame(signed, index=component_window.index, columns=component_window.columns)
            for timestamp in component_window.index:
                active_cols = list(component_window.columns[position_sign.loc[timestamp].abs() > 0])
                if not active_cols:
                    continue
                signed_support = (
                    component_window.loc[timestamp, active_cols]
                    * position_sign.loc[timestamp, active_cols]
                )
                comp_support_scores.append(float(signed_support.mean()))
                comp_aligned_values.append(float((signed_support > 0.0).mean()))
            trough_timestamp = component.index[trough_idx]
            active_cols = list(component.columns[position_sign.loc[trough_timestamp].abs() > 0])
            if active_cols:
                trough_component = _safe_float(
                    float(
                        (
                            component.loc[trough_timestamp, active_cols]
                            * position_sign.loc[trough_timestamp, active_cols]
                        ).mean()
                    )
                )
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
    score_frame = signal_score.iloc[:limit].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    target_frame = target_weights.iloc[:limit].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    score_sign = np.sign(score_frame)
    position_sign = np.sign(target_frame)
    active_mask = target_frame.abs().sum(axis=1) > 1e-9
    flat_mask = ~active_mask

    entry_abs_score = float(compiled_metadata.get("entry_abs_score", compiled_metadata.get("min_abs_score", 0.0)))
    exit_abs_score = float(compiled_metadata.get("exit_abs_score", max(0.0, entry_abs_score * 0.5)))
    flip_abs_score = float(compiled_metadata.get("flip_abs_score", entry_abs_score))
    score_entry_mask = score_frame.abs().ge(entry_abs_score)
    score_flip_mask = score_frame.abs().ge(flip_abs_score)
    score_exit_band = score_frame.abs().lt(exit_abs_score)

    pair_mode = score_frame.shape[1] == 1 and target_frame.shape[1] >= 2
    position_signature = pd.Series(
        [_row_position_signature(row) for _, row in target_frame.iterrows()],
        index=target_frame.index,
        dtype=object,
    )
    if pair_mode:
        active_score_signature = pd.Series(
            [
                ("long_asset_1_short_asset_2",)
                if float(row.iloc[0]) >= entry_abs_score
                else ("short_asset_1_long_asset_2",)
                if float(row.iloc[0]) <= -entry_abs_score
                else tuple()
                for _, row in score_frame.iterrows()
            ],
            index=score_frame.index,
            dtype=object,
        )
    else:
        active_score_signature = pd.Series(
            [
                tuple(
                    sorted(
                        (str(column), int(np.sign(value)))
                        for column, value in row.items()
                        if abs(float(value)) >= entry_abs_score
                    )
                )
                for _, row in score_frame.iterrows()
            ],
            index=score_frame.index,
            dtype=object,
        )
    score_flips = (
        active_score_signature != active_score_signature.shift(1)
    ) & active_score_signature.astype(bool) & active_score_signature.shift(1).astype(bool)
    position_flips = (
        position_signature != position_signature.shift(1)
    ) & position_signature.astype(bool) & position_signature.shift(1).astype(bool)

    aligned_active_fraction = None
    active_alignment: list[float] = []
    if pair_mode:
        primary_sign = np.sign(
            pd.to_numeric(target_frame.iloc[:, 0], errors="coerce").fillna(0.0)
        )
        aligned = score_sign.iloc[:, 0].mul(primary_sign, fill_value=0.0)
        active_alignment = [float(value > 0.0) for value in aligned[active_mask].tolist()]
    else:
        for timestamp in score_frame.index[active_mask]:
            active_cols = target_frame.columns[target_frame.loc[timestamp].abs() > 1e-9]
            if len(active_cols) == 0:
                continue
            aligned = (
                score_sign.loc[timestamp, active_cols]
                == position_sign.loc[timestamp, active_cols]
            )
            active_alignment.append(float(aligned.mean()))
    if active_alignment:
        aligned_active_fraction = _safe_float(float(np.mean(active_alignment)))

    bottleneck_tags: list[str] = []
    entry_fraction = float(score_entry_mask.any(axis=1).mean())
    active_fraction = float(active_mask.mean())
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
                float((~gate_mask)[flat_mask].mean()) if bool(flat_mask.any()) else None
            ),
            "broken_while_active_fraction": _safe_float(
                float((~gate_mask)[active_mask].mean()) if bool(active_mask.any()) else None
            ),
            "exit_on_break": bool(
                dict(compiled_metadata.get("regime_gates") or {}).get("exit_on_break", True)
            ),
            "entry": list(dict(compiled_metadata.get("regime_gates") or {}).get("entry") or []),
        }
        active_frac_gate = _safe_float(regime_gate_summary.get("active_fraction"))
        if active_frac_gate is not None and active_frac_gate < 0.30:
            bottleneck_tags.append("restrictive_regime_gate")

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
        "flat_bar_fraction": _safe_float(float(flat_mask.mean())),
        "entry_signal_bar_fraction": _safe_float(entry_fraction),
        "flip_signal_bar_fraction": _safe_float(float(score_flip_mask.any(axis=1).mean())),
        "inside_exit_band_fraction": _safe_float(float(score_exit_band.all(axis=1).mean())),
        "score_sign_flip_rate": _safe_float(float(score_flips.mean()) if len(score_flips.index) > 1 else 0.0),
        "position_flip_rate": _safe_float(position_flip_rate),
        "entry_signal_while_flat_fraction": _safe_float(
            float(score_entry_mask.any(axis=1)[flat_mask].mean()) if bool(flat_mask.any()) else None
        ),
        "score_alignment_when_active": aligned_active_fraction,
        "median_active_asset_count": _safe_float(
            float(
                (pd.Series(2.0, index=target_frame.index) if pair_mode
                 else target_frame.abs().gt(1e-9).sum(axis=1).astype(float)
                )[active_mask].median()
            ) if bool(active_mask.any()) else None
        ),
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
