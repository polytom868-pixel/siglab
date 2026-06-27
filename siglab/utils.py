from __future__ import annotations

import hashlib
import math
from typing import Any, cast
from collections.abc import Awaitable, Callable, Sequence


def percentile(values: list[float], percentile: int) -> float | None:
    """Calculate percentile using R-7 linear interpolation."""
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    if n == 1:
        return float(ordered[0])
    rank = percentile / 100.0 * (n - 1)
    lower_idx = min(max(math.floor(rank), 0), n - 1)
    upper_idx = min(max(math.ceil(rank), 0), n - 1)
    if lower_idx == upper_idx:
        return float(ordered[lower_idx])
    frac = rank - lower_idx
    return float(ordered[lower_idx] + frac * (ordered[upper_idx] - ordered[lower_idx]))


def safe_float(
    value: float | str | None,
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


def int_or_zero(value: str | int | None) -> int:
    """Convert value to non-negative int. Returns 0 on failure or negative."""
    if value is None:
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


_sha256 = hashlib.sha256


def feature_hash(features: list[str], length: int = 16) -> str:
    """Deterministic hash of a feature list. Order-independent."""
    payload = "|".join(sorted(str(f) for f in features))
    return _sha256(payload.encode("utf-8")).hexdigest()[:length]


def short_hash(payload: str, length: int = 16) -> str:
    """Truncated SHA-256 hex digest."""
    return _sha256(payload.encode("utf-8")).hexdigest()[:length]


async def _get_url(url: str, **kw: Any) -> dict[str, Any]:
    import aiohttp

    async with aiohttp.ClientSession() as session:
        async with session.get(url, **kw) as resp:
            return cast(dict[str, Any], await resp.json())


async def _post_url(url: str, payload: dict[str, Any], **kw: Any) -> dict[str, Any]:
    import aiohttp

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, **kw) as resp:
            return cast(dict[str, Any], await resp.json())


async def run_with_backoff(
    coro_factory: Callable[[], Awaitable[Any]],
    *,
    max_retries: int = 3,
    backoff_s: float = 1.0,
) -> Any:
    import asyncio

    attempt = 0
    while True:
        try:
            return await coro_factory()
        except Exception:
            attempt += 1
            if attempt >= max_retries:
                raise
            import logging

            logging.getLogger(__name__).exception(
                "run_with_backoff attempt %d/%d failed, retrying",
                attempt,
                max_retries,
            )
            await asyncio.sleep(backoff_s * 2 ** (attempt - 1))


async def async_limiter_call(
    callable: Callable[[], Awaitable[Any]],
    *,
    rate_limit: int = 20,
) -> Any:
    import asyncio

    sem = asyncio.Semaphore(rate_limit)
    async with sem:
        return await callable()


def _now_iso() -> str:
    """Current UTC timestamp as ISO-8601 string (microsecond precision)."""
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _compact_scalar(value: object) -> object:
    if isinstance(value, str) and len(value) > 2200:
        return value[:2199].rstrip() + "…"
    return value


def _estimate_message_tokens(messages: Sequence[dict[str, Any]]) -> int:
    """Conservative cheap token estimate from JSON serialization length."""
    import json as _json

    chars = len(_json.dumps(list(messages), ensure_ascii=True, default=str))
    return max(1, (chars + 3) // 4)


def dget(d: dict | None, *keys: str, default: object = None) -> object:
    """Safe nested dict access without intermediate copies."""
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k)
        if d is None:
            return default
    return d
