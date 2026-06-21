from __future__ import annotations

import asyncio
import inspect
import time
from collections import deque
from dataclasses import dataclass
from typing import Awaitable, Callable

SODEX_WEIGHT_BUDGET_PER_MINUTE = 1200
SODEX_DEFAULT_ENDPOINT_WEIGHT = 20
SODEX_ENDPOINT_WEIGHTS: dict[str, int] = {
    "perps.symbols": 2,
    "perps.coins": 2,
    "perps.tickers": 2,
    "perps.mini_tickers": 2,
    "perps.mark_prices": 2,
    "perps.book_tickers": 2,
    "perps.orderbook": 20,
    "perps.klines": 20,
    "perps.trades": 20,
    "perps.account_balances": 20,
    "perps.account_orders": 20,
    "perps.account_positions": 20,
    "perps.account_state": 20,
    "perps.new_order": 1,
    "perps.cancel_order": 1,
    "perps.schedule_cancel": 1,
    "perps.update_leverage": 1,
    "perps.update_margin": 1,
}


class SoDEXError(RuntimeError):
    """Base class for all SoDEX errors (transport, rate-limit, upstream, weight)."""

    def __init__(self, message: str, *, status_code: int | None = None, payload: object = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class SoDEXWeightLimitError(SoDEXError):
    """SoDEX weight budget exhausted or exceeded."""


@dataclass
class SoDEXWeightMetrics:
    admitted: int = 0
    rejected: int = 0
    slept: int = 0
    total_weight: int = 0


class SoDEXWeightScheduler:
    def __init__(
        self,
        *,
        budget: int = SODEX_WEIGHT_BUDGET_PER_MINUTE,
        window_seconds: float = 60.0,
        now: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[None] | None] | None = None,
    ) -> None:
        self.budget = int(budget)
        self.window_seconds = float(window_seconds)
        self.now = now or time.monotonic
        self.sleep = sleep or asyncio.sleep
        self._events: deque[tuple[float, int]] = deque()
        self._lock = asyncio.Lock()
        self.metrics = SoDEXWeightMetrics()

    @staticmethod
    def documented_weight(endpoint: str, *, default: int = SODEX_DEFAULT_ENDPOINT_WEIGHT) -> int:
        return int(SODEX_ENDPOINT_WEIGHTS.get(endpoint, default))

    def can_admit(self, weight: int) -> bool:
        self._prune()
        return self._used_weight() + int(weight) <= self.budget

    async def acquire(self, weight: int, *, wait: bool = True) -> None:
        value = int(weight)
        if value <= 0:
            raise SoDEXWeightLimitError("SoDEX request weight must be positive")
        if value > self.budget:
            self.metrics.rejected += 1
            raise SoDEXWeightLimitError("SoDEX request weight exceeds the full minute budget")
        async with self._lock:
            while not self.can_admit(value):
                if not wait:
                    self.metrics.rejected += 1
                    raise SoDEXWeightLimitError("SoDEX request weight budget exhausted")
                self.metrics.slept += 1
                await self._sleep(max(0.0, self._seconds_until_available()))
            self._events.append((self.now(), value))
            self.metrics.admitted += 1
            self.metrics.total_weight += value

    def snapshot(self) -> dict[str, float | int]:
        self._prune()
        return {
            "budget": self.budget,
            "window_seconds": self.window_seconds,
            "used_weight": self._used_weight(),
            "available_weight": max(0, self.budget - self._used_weight()),
            "admitted": self.metrics.admitted,
            "rejected": self.metrics.rejected,
            "slept": self.metrics.slept,
            "total_weight": self.metrics.total_weight,
        }

    def _used_weight(self) -> int:
        return sum(weight for _, weight in self._events)

    def _prune(self) -> None:
        cutoff = self.now() - self.window_seconds
        while self._events and self._events[0][0] <= cutoff:
            self._events.popleft()

    def _seconds_until_available(self) -> float:
        self._prune()
        if not self._events:
            return 0.0
        return max(0.0, self.window_seconds - (self.now() - self._events[0][0]))

    async def _sleep(self, seconds: float) -> None:
        result = self.sleep(seconds)
        if inspect.isawaitable(result):
            await result
