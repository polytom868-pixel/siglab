from __future__ import annotations

import math
import hashlib
from typing import Any


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
    lower_idx = min(max(int(math.floor(rank)), 0), n - 1)
    upper_idx = min(max(int(math.ceil(rank)), 0), n - 1)

    if lower_idx == upper_idx:
        return float(ordered[lower_idx])

    frac = rank - lower_idx
    return float(ordered[lower_idx] + frac * (ordered[upper_idx] - ordered[lower_idx]))


def safe_float(
    value: Any,
    *,
    digits: int = 8,
    default: float | None = None,
) -> float | None:
    """Convert value to float safely. Returns default on failure, None, or NaN."""
    if value is None:
        return default
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(numeric):
        return default
    return round(numeric, digits)


h = hashlib.sha256


def feature_hash(features: list[str], length: int = 16) -> str:
    """Deterministic hash of a feature list. Order-independent."""
    payload = "|".join(sorted(str(f) for f in features))
    return h(payload.encode("utf-8")).hexdigest()[:length]


def short_hash(payload: str, length: int = 16) -> str:
    """Truncated SHA-256 hex digest."""
    return h(payload.encode("utf-8")).hexdigest()[:length]


async def _get_url(url, **kw) -> dict:
    import aiohttp
    async with aiohttp.ClientSession() as session:
        async with session.get(url, **kw) as resp:
            return await resp.json()


async def _post_url(url, payload, **kw) -> dict:
    import aiohttp
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, **kw) as resp:
            return await resp.json()


async def run_with_backoff(coro_factory, *, max_retries=3, backoff_s=1.0) -> Any:
    import asyncio
    attempt = 0
    while True:
        try:
            return await coro_factory()
        except Exception:
            attempt += 1
            if attempt >= max_retries:
                raise
            await asyncio.sleep(backoff_s * (2 ** (attempt - 1)))
