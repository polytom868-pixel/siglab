from __future__ import annotations

import math


def percentile(values: list[float], percentile: int) -> float | None:
    """Calculate percentile using R-7 linear interpolation.

    R-7 is the default interpolation method used by NumPy, R, and most
    statistical software. It linearly interpolates between adjacent
    ranked values rather than selecting the nearest rank.

    Args:
        values: List of numeric values (will be sorted internally).
        percentile: Percentile to compute (0-100).

    Returns:
        The interpolated percentile value, or None if values is empty.
    """
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    if n == 1:
        return float(ordered[0])

    rank = (percentile / 100.0) * (n - 1)
    lower_idx = int(math.floor(rank))
    upper_idx = int(math.ceil(rank))

    if lower_idx == upper_idx:
        return float(ordered[lower_idx])

    frac = rank - lower_idx
    return float(ordered[lower_idx] + frac * (ordered[upper_idx] - ordered[lower_idx]))
