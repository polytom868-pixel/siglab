"""Trade episode extraction functions extracted from runner.py."""

from __future__ import annotations

from typing import Any, cast

import numpy as np
import pandas as pd

from siglab.utils import safe_float as _safe_float


def _row_position_signature(row: pd.Series, *, epsilon: float = 1e-9) -> tuple[tuple[str, int], ...]:
    clean = pd.to_numeric(row, errors="coerce").fillna(0.0)
    active = [
        (str(column), int(np.sign(value)))
        for column, value in clean.items()
        if abs(float(value)) > epsilon
    ]
    active.sort(key=lambda item: item[0])
    return tuple(active)


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


def _pair_position_episodes(
    *,
    target_weights: pd.DataFrame,
    returns: pd.Series,
) -> list[dict[str, Any]]:
    if target_weights.empty:
        return []
    signatures = pd.Series(
        [_row_position_signature(row) for _, row in target_weights.iterrows()],
        index=target_weights.index,
        dtype=object,
    )
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
