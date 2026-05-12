from __future__ import annotations

from typing import Any

import pandas as pd


def apply_roll_exit_days(
    target_positions: pd.DataFrame,
    days_to_expiry: pd.DataFrame,
    roll_days_before_expiry: int,
) -> pd.DataFrame:
    aligned_days = days_to_expiry.reindex_like(target_positions).ffill()
    return target_positions.where(aligned_days > float(roll_days_before_expiry), 0.0)


def classify_pt_market_state(
    *,
    prices: pd.DataFrame,
    days_to_expiry: pd.DataFrame,
    required_frames: list[pd.DataFrame],
    roll_days_before_expiry: int,
    min_days_to_expiry: int,
    max_days_to_expiry: int,
) -> dict[str, pd.DataFrame]:
    availability = prices.notna()
    for frame in required_frames:
        availability = availability & frame.reindex_like(prices).notna()

    inside_roll_window = availability & days_to_expiry.gt(0.0) & days_to_expiry.le(
        float(roll_days_before_expiry)
    )
    expired_or_untradable = availability & days_to_expiry.le(0.0)
    within_maturity_range = days_to_expiry.ge(float(min_days_to_expiry)) & days_to_expiry.le(
        float(max_days_to_expiry)
    )
    eligible = availability & within_maturity_range & days_to_expiry.gt(
        float(roll_days_before_expiry)
    )
    return {
        "availability": availability,
        "eligible": eligible,
        "inside_roll_window": inside_roll_window,
        "expired_or_untradable": expired_or_untradable,
    }


def summarize_pt_universe(
    *,
    prices: pd.DataFrame,
    eligible: pd.DataFrame,
    inside_roll_window: pd.DataFrame,
    expired_or_untradable: pd.DataFrame,
) -> dict[str, Any]:
    eligible_counts = eligible.sum(axis=1)
    dynamic_entries = [
        column
        for column in prices.columns
        if prices[column].dropna().index.min() > prices.index.min()
    ]
    return {
        "eligible_market_count_min": int(eligible_counts.min()) if not eligible_counts.empty else 0,
        "eligible_market_count_max": int(eligible_counts.max()) if not eligible_counts.empty else 0,
        "eligible_market_count_median": float(eligible_counts.median())
        if not eligible_counts.empty
        else 0.0,
        "eligible_market_count_latest": int(eligible_counts.iloc[-1])
        if not eligible_counts.empty
        else 0,
        "inside_roll_market_count_latest": int(inside_roll_window.iloc[-1].sum())
        if not inside_roll_window.empty
        else 0,
        "expired_market_count_latest": int(expired_or_untradable.iloc[-1].sum())
        if not expired_or_untradable.empty
        else 0,
        "markets_entered_during_backtest": dynamic_entries,
    }


def detect_pt_roll_events(
    pt_positions: pd.DataFrame,
    *,
    eligible: pd.DataFrame,
    inside_roll_window: pd.DataFrame,
    expired_or_untradable: pd.DataFrame,
    days_to_expiry: pd.DataFrame,
) -> list[dict[str, Any]]:
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

        exited_due_to_expiry = any(
            bool(expired_or_untradable.iloc[index].get(label, False)) for label in exited
        )
        exited_due_to_roll = any(
            bool(inside_roll_window.iloc[index].get(label, False)) for label in exited
        )
        if not exited_due_to_expiry and not exited_due_to_roll:
            continue

        reason = "expired_or_untradable" if exited_due_to_expiry else "inside_roll_window"

        events.append(
            {
                "timestamp": timestamp.isoformat(),
                "reason": reason,
                "from_markets": exited,
                "to_markets": entered,
                "eligible_market_count": int(eligible.iloc[index].sum()),
                "selected_market_count": int(current.sum()),
                "from_days_to_expiry": {
                    label: float(days_to_expiry.iloc[index].get(label))
                    for label in exited
                    if pd.notna(days_to_expiry.iloc[index].get(label))
                },
                "to_days_to_expiry": {
                    label: float(days_to_expiry.iloc[index].get(label))
                    for label in entered
                    if pd.notna(days_to_expiry.iloc[index].get(label))
                },
            }
        )

    return events
