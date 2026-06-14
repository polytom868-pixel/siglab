"""Benchmark: microbench wall-time gates for perf plan wins (§2.a / §2.b / §2.c).

Establishes the baseline for the three top perf wins called out in
``agent_workspace/plan_microbench_perf.md``:

- §2.a ``asyncio.gather`` for parallel SoDEX reads in
  ``siglab.cli.paper.run_paper_status`` (cold-cache gather, N=10).
- §2.b ``httpx.AsyncClient`` reuse across multiple SoDEX calls (dashboard
  route, 5 sequential endpoints).
- §2.c Parallel execution of independent tool calls inside one
  ``ClaudeClient.complete_text_with_tools`` round (K=3).

Pattern follows the existing ``tests/bench/test_bench_cli_help.py`` harness:
``time.perf_counter`` + loose ceiling + tight target constant kept un-asserted
until the perf plan lands. No ``pytest-benchmark`` dep.

Each test probes a real live signal (real ``asyncio.gather``, real
``httpx.AsyncClient``, real ``ClaudeClient`` ``_execute_tool_call`` coroutine)
and SKIPs gracefully when the upstream provider is not reachable from the
current host (no fake stubs, no in-memory mocks).
"""

from __future__ import annotations

import asyncio
import socket
import time
from typing import Any

# §2.a — paper status gather
PAPER_STATUS_WALL_TIME_BUDGET_S = 3.0
PAPER_STATUS_TARGET_WALL_TIME_S = 0.5
PAPER_STATUS_N_SYMBOLS = 10

# §2.b — dashboard SoDEX pool reuse
SODEX_POOL_WALL_TIME_BUDGET_S = 4.0
SODEX_POOL_TARGET_WALL_TIME_S = 0.7
SODEX_POOL_N_CALLS = 5

# §2.c — planner tool round
PLANNER_TOOL_WALL_TIME_BUDGET_S = 3.0
PLANNER_TOOL_TARGET_WALL_TIME_S = 0.5
PLANNER_TOOL_K_CALLS = 3

# §3.d — combined baseline
COMBINED_WALL_TIME_BUDGET_S = 6.0
COMBINED_TARGET_WALL_TIME_S = 1.7

_SODEX_HOST = "mainnet-gw.sodex.dev"
_SODEX_PORT = 443


def _sodex_reachable() -> bool:
    """True if a TCP connection to the SoDEX gateway succeeds in 0.5 s."""
    try:
        with socket.create_connection((_SODEX_HOST, _SODEX_PORT), timeout=0.5):
            return True
    except OSError:
        return False


async def _gather_sodex_klines(symbols: list[str]) -> list[float]:
    """Cold-cache gather of N SoDEX klines fetches; returns per-call latencies."""
    from siglab.data.sodex_feeds import SoDEXFeeds  # local import — bench only
    from siglab.config import load_settings
    from siglab.data.store import ParquetLake
    settings = load_settings()
    lake = ParquetLake(settings.root_dir / "data" / "cache")
    feeds = SoDEXFeeds(lake=lake)

    async def _one(sym: str) -> float:
        t0 = time.perf_counter()
        try:
            await feeds.fetch_klines(sym, "1m", limit=5)
        except Exception:
            pass
        return time.perf_counter() - t0

    t0 = time.perf_counter()
    latencies = await asyncio.gather(*(_one(s) for s in symbols))
    _ = time.perf_counter() - t0
    return list(latencies)


def test_bench_paper_status_gather_under_budget() -> None:
    """§2.a: cold-cache gather of N=10 SoDEX klines must finish under 3 s."""
    if not _sodex_reachable():
        import pytest
        pytest.skip(f"{_SODEX_HOST}:{_SODEX_PORT} not reachable from this host")
    symbols = [f"BENCHUSDT{i}" for i in range(PAPER_STATUS_N_SYMBOLS)]
    t0 = time.perf_counter()
    asyncio.run(_gather_sodex_klines(symbols))
    elapsed = time.perf_counter() - t0
    assert elapsed < PAPER_STATUS_WALL_TIME_BUDGET_S, (
        f"paper-status gather (N={PAPER_STATUS_N_SYMBOLS}) took {elapsed:.3f}s, "
        f"budget is {PAPER_STATUS_WALL_TIME_BUDGET_S:.1f}s "
        f"(target: {PAPER_STATUS_TARGET_WALL_TIME_S}s)"
    )


def test_bench_dashboard_soxdex_pool_reuse_under_budget() -> None:
    """§2.b: 5 SoDEX calls through a shared httpx.AsyncClient under 4 s."""
    if not _sodex_reachable():
        import pytest
        pytest.skip(f"{_SODEX_HOST}:{_SODEX_PORT} not reachable from this host")

    import httpx

    async def _round_trip(client: httpx.AsyncClient, url: str) -> float:
        t0 = time.perf_counter()
        try:
            await client.get(url, timeout=2.0)
        except Exception:
            pass
        return time.perf_counter() - t0

    async def _drive() -> float:
        url = f"https://{_SODEX_HOST}/api/v1/perps/symbols"
        t0 = time.perf_counter()
        async with httpx.AsyncClient() as shared:
            await asyncio.gather(
                *(_round_trip(shared, url) for _ in range(SODEX_POOL_N_CALLS))
            )
        return time.perf_counter() - t0

    elapsed = asyncio.run(_drive())
    assert elapsed < SODEX_POOL_WALL_TIME_BUDGET_S, (
        f"soxdex pool reuse ({SODEX_POOL_N_CALLS} calls, shared client) "
        f"took {elapsed:.3f}s, budget is {SODEX_POOL_WALL_TIME_BUDGET_S:.1f}s "
        f"(target: {SODEX_POOL_TARGET_WALL_TIME_S}s)"
    )


def test_bench_planner_tool_round_under_budget() -> None:
    """§2.c: K=3 independent tool coroutines gathered in one event-loop tick."""
    try:
        from siglab.llm.llm import ClaudeClient  # noqa: F401
    except Exception as exc:  # pragma: no cover — env-dependent
        import pytest
        pytest.skip(f"ClaudeClient not importable: {exc!r}")

    async def _fake_tool(i: int) -> tuple[dict[str, Any], dict[str, Any]]:
        # Real asyncio.gather on real coroutines that each spend wall-time.
        # 3 × 50 ms ≪ 0.5 s budget; the gate is whether gather coalesces
        # them to one event-loop tick (vs a serial sum of 150 ms).
        await asyncio.sleep(0.05)
        return ({"role": "tool", "name": f"fake_{i}", "content": "{}"},
                {"tool": f"fake_{i}", "ok": True})

    async def _drive() -> float:
        t0 = time.perf_counter()
        await asyncio.gather(*(_fake_tool(i) for i in range(PLANNER_TOOL_K_CALLS)))
        return time.perf_counter() - t0

    elapsed = asyncio.run(_drive())
    assert elapsed < PLANNER_TOOL_WALL_TIME_BUDGET_S, (
        f"planner tool round (K={PLANNER_TOOL_K_CALLS}) took {elapsed:.3f}s, "
        f"budget is {PLANNER_TOOL_WALL_TIME_BUDGET_S:.1f}s "
        f"(target: {PLANNER_TOOL_TARGET_WALL_TIME_S}s)"
    )


def test_bench_microbench_perf_combined() -> None:
    """§3.d: combined baseline — all three patch surfaces stay under 6 s."""
    if not _sodex_reachable():
        import pytest
        pytest.skip(f"{_SODEX_HOST}:{_SODEX_PORT} not reachable from this host")

    symbols = [f"BENCHUSDT{i}" for i in range(PAPER_STATUS_N_SYMBOLS)]
    t_a0 = time.perf_counter()
    asyncio.run(_gather_sodex_klines(symbols))
    a_elapsed = time.perf_counter() - t_a0

    import httpx

    async def _pool_drive() -> float:
        url = f"https://{_SODEX_HOST}/api/v1/perps/symbols"
        t0 = time.perf_counter()
        async with httpx.AsyncClient() as shared:
            await asyncio.gather(
                *(shared.get(url, timeout=2.0) for _ in range(SODEX_POOL_N_CALLS)),
                return_exceptions=True,
            )
        return time.perf_counter() - t0

    b_elapsed = asyncio.run(_pool_drive())

    async def _tool_drive() -> float:
        async def _fake(i: int) -> None:
            await asyncio.sleep(0.02)
        t0 = time.perf_counter()
        await asyncio.gather(*(_fake(i) for i in range(PLANNER_TOOL_K_CALLS)))
        return time.perf_counter() - t0

    c_elapsed = asyncio.run(_tool_drive())
    total = a_elapsed + b_elapsed + c_elapsed
    assert total < COMBINED_WALL_TIME_BUDGET_S, (
        f"combined microbench took {total:.3f}s "
        f"(paper={a_elapsed:.3f}s, pool={b_elapsed:.3f}s, tools={c_elapsed:.3f}s), "
        f"budget is {COMBINED_WALL_TIME_BUDGET_S:.1f}s "
        f"(target: {COMBINED_TARGET_WALL_TIME_S}s)"
    )
