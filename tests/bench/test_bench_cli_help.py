"""Benchmark: cold-start wall-time of `python3 -m siglab.cli --help`.

Establishes the baseline for the CLI startup hot path (perf plan #1):
observed 2.97 s on the working tree the perf plan was measured on, target
0.9 s. The bound here (<5 s) is intentionally loose so the test does not
flap on a noisy CI host while still catching a >70% regression.

Single deterministic sample so the test result is stable across runs. If
pytest-benchmark is ever added, this test can be trivially converted to
use the `benchmark` fixture for statistics; the harness is reusable once
cheaper-algorithm changes land.

Falls back to time.perf_counter because pytest-benchmark is not a project
runtime dependency.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

# Loose bound: current observed = 2.97 s, target = 0.9 s. 5 s is a sanity
# ceiling that catches a 70%+ regression without flapping on a busy host.
WALL_TIME_BUDGET_S = 5.0

# The full perf plan target is 0.9 s. We do not assert that here because
# the assignment explicitly says to *establish the baseline*, not gate it.
TARGET_WALL_TIME_S = 0.9


def _run_cli_help() -> float:
    """Spawn `python3 -m siglab.cli --help` and return its wall-time in seconds."""
    start = time.perf_counter()
    result = subprocess.run(
        [sys.executable, "-m", "siglab.cli", "--help"],
        capture_output=True,
        text=True,
        # Run from a clean CWD so any local config/`.env` files don't perturb
        # the import path; the CLI is environment-driven, not CWD-driven.
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    elapsed = time.perf_counter() - start
    assert result.returncode == 0, (
        f"`siglab.cli --help` exited {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    return elapsed


def test_bench_cli_help_cold_start() -> None:
    """Single cold-start measurement: cli --help must finish within 5 s."""
    elapsed = _run_cli_help()

    # Loose gate. Tighten once the perf plan lands.
    assert elapsed < WALL_TIME_BUDGET_S, (
        f"CLI --help cold start took {elapsed:.3f}s, "
        f"budget is {WALL_TIME_BUDGET_S:.1f}s "
        f"(current observed: 2.97s, target: {TARGET_WALL_TIME_S}s)"
    )
