"""Base class for API data providers with retry, backoff, circuit breaker, and metrics."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from abc import ABC
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class _Metrics:
    """Per-endpoint metrics tracked by DataProvider subclasses."""

    latencies_ms: list[float] = field(default_factory=list)
    attempts: int = 0
    successes: int = 0
    retries: int = 0
    rate_limits: int = 0
    transport_failures: int = 0
    circuit_breaks: int = 0


class CircuitBreaker:
    """Simple circuit breaker: 5 consecutive failures -> open for 60s -> half-open.

    State machine:
        closed (normal) -> 5 consecutive failures -> open (block requests for 60s)
        open -> 60s elapsed -> half-open (allow one probe request)
        half-open -> success -> closed
        half-open -> failure -> open (60s again)
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout_s: float = 60.0,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout_s = recovery_timeout_s
        self._consecutive_failures: dict[str, int] = {}
        self._open_until: dict[str, float] = {}

    def state(self, endpoint: str) -> str:
        if endpoint not in self._open_until:
            return "closed"
        if time.monotonic() >= self._open_until[endpoint]:
            return "half-open"
        return "open"

    def acquire(self, endpoint: str) -> None:
        """Raise CircuitBreakerOpenError if circuit is open (non-blocking)."""
        st = self.state(endpoint)
        if st == "open":
            remaining = self._open_until[endpoint] - time.monotonic()
            raise CircuitBreakerOpenError(endpoint, remaining)

    def on_success(self, endpoint: str) -> None:
        self._consecutive_failures.pop(endpoint, None)
        self._open_until.pop(endpoint, None)


    def snapshot(self) -> dict[str, Any]:
        states: dict[str, dict[str, Any]] = {}
        endpoints = set(self._consecutive_failures) | set(self._open_until)
        for ep in endpoints:
            states[ep] = {
                "state": self.state(ep),
                "consecutive_failures": self._consecutive_failures.get(ep, 0),
            }
        return {
            "failure_threshold": self._failure_threshold,
            "recovery_timeout_s": self._recovery_timeout_s,
            "endpoints": states,
        }


class CircuitBreakerOpenError(RuntimeError):
    """Raised when a request is blocked by an open circuit breaker."""

    def __init__(self, endpoint: str, remaining_s: float) -> None:
        super().__init__(
            f"Circuit breaker open for {endpoint!r}, {remaining_s:.0f}s remaining",
        )
        self.endpoint = endpoint
        self.remaining_s = remaining_s


class DataProvider(ABC):
    """Abstract base for API clients with retry, backoff, metrics, and circuit breaker.

    Subclasses must implement:
      - _do_request(endpoint, ...) -> dict
      - Error classification methods

    The retry loop is provided by _request_with_retry().
    """

    def __init__(self, *, retries: int = 3) -> None:
        self.retries = max(0, int(retries))
        self._metrics_store: dict[str, _Metrics] = {}
        self._circuit_breaker = CircuitBreaker()

    # --- Subclass hooks ---

    async def _do_request(self, endpoint: str) -> dict[str, Any]:
        """Execute one HTTP request attempt. Override in subclass or use _request_with_retry directly."""
        raise NotImplementedError(
            f"{type(self).__name__} must implement _do_request or override _request_with_retry",
        )

    def _is_rate_limit_error(self, exc: Exception) -> bool:
        return False

    def _is_transport_error(self, exc: Exception) -> bool:
        return False

    def _is_retryable_error(self, exc: Exception) -> bool:
        return False

    def _retry_after_from(self, exc: Exception) -> float | None:
        return None

    # --- Shared retry loop ---

    async def _request_with_retry(self, endpoint: str) -> dict[str, Any]:
        """Retry loop wrapping _do_request() with metrics, backoff, and circuit breaker."""
        metrics = self._metrics_for(endpoint)

        # Check circuit breaker before first attempt
        try:
            self._circuit_breaker.acquire(endpoint)
        except CircuitBreakerOpenError:
            metrics.circuit_breaks += 1
            raise

        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            metrics.attempts += 1
            started = time.perf_counter()
            try:
                result = await self._do_request(endpoint)
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                metrics.latencies_ms.append(elapsed_ms)
                metrics.successes += 1
                self._circuit_breaker.on_success(endpoint)
                return result
            except CircuitBreakerOpenError:
                raise
            except Exception as exc:
                if self._is_rate_limit_error(exc):
                    metrics.rate_limits += 1
                    last_error = exc
                elif self._is_transport_error(exc):
                    metrics.transport_failures += 1
                    last_error = exc
                elif self._is_retryable_error(exc):
                    last_error = exc
                else:
                    raise

            if attempt >= self.retries:
                break
            metrics.retries += 1
            self._circuit_breaker.on_failure(endpoint)

            # Use server-provided retry-after if available, else exponential backoff
            retry_after = self._retry_after_from(last_error) if last_error is not None else None
            if retry_after is not None and retry_after > 0:
                await asyncio.sleep(float(retry_after))
            else:
                await asyncio.sleep(self._backoff_s(attempt))

        raise last_error or RuntimeError(f"{endpoint} request failed without a captured error")

    def _backoff_s(self, attempt: int) -> float:
        """Exponential backoff with jitter, capped at 2s."""
        base = min(2.0, 0.25 * 2**attempt)
        return base + random.uniform(0.0, base * 0.25)

    # --- Metrics ---

    def _metrics_for(self, endpoint: str) -> _Metrics:
        if endpoint not in self._metrics_store:
            self._metrics_store[endpoint] = _Metrics()
        return self._metrics_store[endpoint]

    def metrics_snapshot(self) -> dict[str, Any]:
        """Build a metrics snapshot dict. Subclasses can extend this."""
        endpoints: dict[str, Any] = {}
        all_latencies: list[float] = []
        totals = {
            "attempts": 0,
            "successes": 0,
            "retries": 0,
            "rate_limits": 0,
            "transport_failures": 0,
            "circuit_breaks": 0,
        }
        for name, metrics in self._metrics_store.items():
            latencies = sorted(metrics.latencies_ms)
            all_latencies.extend(latencies)
            for k in totals:
                totals[k] += getattr(metrics, k, 0)
            ep: dict[str, Any] = {
                "p50_ms": _percentile(latencies, 50),
                "p95_ms": _percentile(latencies, 95),
                "attempts": metrics.attempts,
                "successes": metrics.successes,
                "success_rate": metrics.successes / max(1, metrics.attempts),
                "retries": metrics.retries,
                "rate_limits": metrics.rate_limits,
                "transport_failures": metrics.transport_failures,
                "circuit_breaks": metrics.circuit_breaks,
            }
            endpoints[name] = ep
        all_latencies.sort()
        attempts = max(1, totals["attempts"])
        return {
            "p50_ms": _percentile(all_latencies, 50),
            "p95_ms": _percentile(all_latencies, 95),
            "attempts": totals["attempts"],
            "successes": totals["successes"],
            "success_rate": totals["successes"] / attempts,
            "retries": totals["retries"],
            "rate_limits": totals["rate_limits"],
            "transport_failures": totals["transport_failures"],
            "circuit_breaks": totals["circuit_breaks"],
            "circuit_breaker": self._circuit_breaker.snapshot(),
            "endpoints": endpoints,
        }

    # --- Lifecycle ---

    async def close(self) -> None:
        """Release resources. Subclasses with HTTP clients should override and call super()."""


def _percentile(sorted_values: list[float], p: int) -> float | None:
    """Compute the p-th percentile from a sorted list."""
    if not sorted_values:
        return None
    k = (p / 100.0) * (len(sorted_values) - 1)
    f = int(k)
    c = k - f
    if f + 1 < len(sorted_values):
        return sorted_values[f] * (1 - c) + sorted_values[f + 1] * c
    return sorted_values[-1]
