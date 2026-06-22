"""PT (perpetual futures) market event analysis and gate evaluation."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pandas as pd

from siglab.track_registry import resolve_track

__all__ = ['classify_pt_market_state', 'summarize_pt_universe', 'detect_pt_roll_events', 'evaluate_gates']

def classify_pt_market_state(*, prices: pd.DataFrame, days_to_expiry: pd.DataFrame, required_frames: list[pd.DataFrame], roll_days_before_expiry: int, min_days_to_expiry: int, max_days_to_expiry: int) -> dict[str, pd.DataFrame]:
    """Classify each market into eligible, roll-window, or expired state."""
    availability = prices.notna()
    for frame in required_frames:
        availability = availability & frame.reindex_like(prices).notna()
    inside_roll_window = availability & days_to_expiry.gt(0.0) & days_to_expiry.le(float(roll_days_before_expiry))
    expired_or_untradable = availability & days_to_expiry.le(0.0)
    within_maturity_range = days_to_expiry.ge(float(min_days_to_expiry)) & days_to_expiry.le(float(max_days_to_expiry))
    eligible = availability & within_maturity_range & days_to_expiry.gt(float(roll_days_before_expiry))
    return {'availability': availability, 'eligible': eligible, 'inside_roll_window': inside_roll_window, 'expired_or_untradable': expired_or_untradable}

def summarize_pt_universe(*, prices: pd.DataFrame, eligible: pd.DataFrame, inside_roll_window: pd.DataFrame, expired_or_untradable: pd.DataFrame) -> dict[str, Any]:
    """Summarize the PT market universe counts."""
    eligible_counts = eligible.sum(axis=1)
    dynamic_entries = [column for column in prices.columns if prices[column].dropna().index.min() > prices.index.min()]
    return {'eligible_market_count_min': int(eligible_counts.min()) if not eligible_counts.empty else 0, 'eligible_market_count_max': int(eligible_counts.max()) if not eligible_counts.empty else 0, 'eligible_market_count_median': float(eligible_counts.median()) if not eligible_counts.empty else 0.0, 'eligible_market_count_latest': int(eligible_counts.iloc[-1]) if not eligible_counts.empty else 0, 'inside_roll_market_count_latest': int(inside_roll_window.iloc[-1].sum()) if not inside_roll_window.empty else 0, 'expired_market_count_latest': int(expired_or_untradable.iloc[-1].sum()) if not expired_or_untradable.empty else 0, 'markets_entered_during_backtest': dynamic_entries}

def detect_pt_roll_events(pt_positions: pd.DataFrame, *, eligible: pd.DataFrame, inside_roll_window: pd.DataFrame, expired_or_untradable: pd.DataFrame, days_to_expiry: pd.DataFrame) -> list[dict[str, Any]]:
    """Detect roll events where positions move from one contract to another."""
    events: list[dict[str, Any]] = []
    positive_positions = pt_positions.fillna(0.0).gt(0.0)
    for index in range(1, len(pt_positions.index)):
        timestamp = pt_positions.index[index]
        previous = positive_positions.iloc[index - 1]
        current = positive_positions.iloc[index]
        exited = list(previous[previous & ~current].index)
        entered = list(current[current & ~previous].index)
        if not exited:
            continue
        exited_due_to_expiry = any((bool(expired_or_untradable.iloc[index].get(label, False)) for label in exited))
        exited_due_to_roll = any((bool(inside_roll_window.iloc[index].get(label, False)) for label in exited))
        if not exited_due_to_expiry and (not exited_due_to_roll):
            continue
        reason = 'expired_or_untradable' if exited_due_to_expiry else 'inside_roll_window'
        events.append({'timestamp': timestamp.isoformat(), 'reason': reason, 'from_markets': exited, 'to_markets': entered, 'eligible_market_count': int(eligible.iloc[index].sum()), 'selected_market_count': int(current.sum()), 'from_days_to_expiry': {label: float(days_to_expiry.iloc[index].get(label, 0.0)) for label in exited if pd.notna(days_to_expiry.iloc[index].get(label))}, 'to_days_to_expiry': {label: float(days_to_expiry.iloc[index].get(label, 0.0)) for label in entered if pd.notna(days_to_expiry.iloc[index].get(label))}})
    return events

def evaluate_gates(track: str, summary: dict[str, Any]) -> tuple[bool, list[str]]:
    """Evaluate all gates for a given track and evaluation summary."""
    track = cast(str, resolve_track(track))
    reasons: list[str] = []
    if int(summary.get('liquidation_count', 0)) > 0:
        reasons.append('liquidation')
    if float(summary.get('median_total_return', 0.0)) <= 0.0:
        reasons.append('non_positive_median_return')
    if float(summary.get('median_sharpe', 0.0)) <= 0.0:
        reasons.append('non_positive_median_sharpe')
    pre_audit_canonical_total_return = summary.get('pre_audit_canonical_total_return')
    if pre_audit_canonical_total_return is not None and float(pre_audit_canonical_total_return) <= 0.0:
        reasons.append('non_positive_pre_audit_canonical_return')
    if not bool(summary.get('canonical_series_valid', True)):
        reasons.append('invalid_canonical_series')
    drawdown_limit = -0.35 if track == 'trend_signals' else -0.25
    if float(summary.get('worst_max_drawdown', 0.0)) < drawdown_limit:
        reasons.append('drawdown_limit')
    breadth = int(summary.get('asset_breadth', 0))
    if breadth < 2 and track == 'trend_signals':
        reasons.append('insufficient_breadth')
    if breadth < 1 and track == 'yield_flows':
        reasons.append('insufficient_breadth')
    bundle_as_of = summary.get('bundle_as_of')
    if bundle_as_of is not None:
        try:
            if isinstance(bundle_as_of, str):
                data_ts = datetime.fromisoformat(bundle_as_of)
            else:
                data_ts = datetime.fromisoformat(str(bundle_as_of))
            age_seconds = (datetime.now(UTC) - data_ts).total_seconds()
            if age_seconds > 3600:
                reasons.append(f'stale_data_{int(age_seconds)}s')
        except (ValueError, TypeError):
            reasons.append('unparseable_data_timestamp')
    leak_checks = summary.get('leak_checks_passed')
    if leak_checks is not None and (not bool(leak_checks)):
        reasons.append('lookahead_bias_detected')
    config_path = summary.get('position_sizing_config_path')
    if config_path is not None:
        resolved = Path(str(config_path)).expanduser().resolve()
        if not resolved.exists():
            reasons.append(f'position_sizing_config_missing:{resolved}')
    return (not reasons, reasons)
