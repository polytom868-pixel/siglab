"""Shared analysis utilities extracted from compile.py and runner.py."""

from __future__ import annotations

from typing import Any

import pandas as pd


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
