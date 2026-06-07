from __future__ import annotations

import asyncio
import unittest

from siglab.data.sodex_rate_limit import SODEX_ENDPOINT_WEIGHTS, SoDEXWeightLimitError, SoDEXWeightScheduler


class SoDEXWeightSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_documented_weight_table_covers_implemented_endpoints(self) -> None:
        self.assertEqual(SoDEXWeightScheduler.documented_weight("perps.symbols"), 2)
        self.assertEqual(SoDEXWeightScheduler.documented_weight("perps.coins"), 2)
        self.assertEqual(SoDEXWeightScheduler.documented_weight("perps.tickers"), 2)
        self.assertEqual(SoDEXWeightScheduler.documented_weight("perps.orderbook"), 20)
        self.assertEqual(SoDEXWeightScheduler.documented_weight("perps.klines"), 20)
        self.assertEqual(SoDEXWeightScheduler.documented_weight("perps.trades"), 20)
        self.assertEqual(SoDEXWeightScheduler.documented_weight("perps.account_state"), 20)
        self.assertEqual(SoDEXWeightScheduler.documented_weight("unknown.endpoint"), 20)
        self.assertIn("perps.update_margin", SODEX_ENDPOINT_WEIGHTS)

    async def test_rejects_when_budget_exhausted_without_wait(self) -> None:
        now = 100.0
        scheduler = SoDEXWeightScheduler(budget=100, window_seconds=60, now=lambda: now)

        await scheduler.acquire(80, wait=False)

        with self.assertRaises(SoDEXWeightLimitError):
            await scheduler.acquire(30, wait=False)
        snapshot = scheduler.snapshot()
        self.assertEqual(snapshot["used_weight"], 80)
        self.assertEqual(snapshot["rejected"], 1)

    async def test_window_prunes_old_weight(self) -> None:
        current = 100.0
        scheduler = SoDEXWeightScheduler(budget=100, window_seconds=60, now=lambda: current)

        await scheduler.acquire(100, wait=False)
        current = 161.0

        await scheduler.acquire(100, wait=False)
        self.assertEqual(scheduler.snapshot()["used_weight"], 100)

    async def test_rejects_single_request_over_budget(self) -> None:
        scheduler = SoDEXWeightScheduler(budget=100)

        with self.assertRaises(SoDEXWeightLimitError):
            await scheduler.acquire(101)

    async def test_parallel_burst_admission_is_atomic(self) -> None:
        scheduler = SoDEXWeightScheduler(budget=100, window_seconds=60)

        results = await asyncio.gather(
            *(scheduler.acquire(30, wait=False) for _ in range(4)),
            return_exceptions=True,
        )

        rejected = [item for item in results if isinstance(item, SoDEXWeightLimitError)]
        snapshot = scheduler.snapshot()
        self.assertEqual(snapshot["admitted"], 3)
        self.assertEqual(snapshot["used_weight"], 90)
        self.assertEqual(snapshot["rejected"], 1)
        self.assertEqual(len(rejected), 1)

    async def test_wait_mode_sleeps_until_window_has_capacity(self) -> None:
        current = 100.0
        sleeps: list[float] = []

        def now() -> float:
            return current

        async def fake_sleep(seconds: float) -> None:
            nonlocal current
            sleeps.append(seconds)
            current += seconds

        scheduler = SoDEXWeightScheduler(budget=100, window_seconds=60, now=now, sleep=fake_sleep)

        await scheduler.acquire(100, wait=False)
        await scheduler.acquire(1, wait=True)

        self.assertEqual(sleeps, [60.0])
        snapshot = scheduler.snapshot()
        self.assertEqual(snapshot["admitted"], 2)
        self.assertEqual(snapshot["used_weight"], 1)
        self.assertEqual(snapshot["slept"], 1)


if __name__ == "__main__":
    unittest.main()
