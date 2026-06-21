"""Utility functions extracted from runner.py — series helpers and miscellany."""

from __future__ import annotations

from typing import Any

import pandas as pd



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
