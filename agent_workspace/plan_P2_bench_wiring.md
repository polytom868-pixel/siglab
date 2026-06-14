# Plan P2 — Wire Live SoSoValue Benchmarks into `tests/bench/`

PLAN-ONLY. No source edits, no commit. Smaller-delta principle: only `tests/bench/*` plus one new `tests/bench/__init__.py` entry. **NO** edits under `siglab/evaluation/*` and **NO** edits under `siglab/data/sosovalue_client.py`.

The two P2 deltas (P2-A, P2-B) are the live SoSoValue micro/macro benchmarks. The job here is to make the existing `tests/bench/` harness re-run them with the real `SOSOVALUE_API_KEY` and skip cleanly without the key, without dragging live HTTP into `siglab/evaluation/runner.py` itself.

---

## 0. Honesty check before scoping

I read the repo to ground this plan. Findings that shape the plan:

- `tests/bench/` already exists at `/home/eya/soso/siglab/tests/bench/`. Current contents (verified via `ls`):
  - `test_bench_cli_help.py` — wall-time bench for `python3 -m siglab.cli --help`
  - `test_bench_sodex_ws.py` — wall-time bench for `sodex-ws-probe --exit-on-first-frame`
  - **No `__init__.py` and no `conftest.py` in `tests/bench/`** (verified with `ls` and `find`). The package is implicitly a namespace package today.
- `tests/__init__.py` does **not** exist either; pytest rootdir is the repo root.
- `tests/conftest.py` is the single repo-wide conftest (fixtures only: `sample_spec`, `mock_settings`, `deterministic_provider`, `compute_evaluation_hash`). It does **not** load `.env` for tests, and does not reference `SOSOVALUE_API_KEY`.
- `siglab/evaluation/runner.py` exposes `ResearchEvaluator(settings, provider).evaluate(spec, *, fast_mode=False)`. That is the only public eval entry point. It is `async`. `fast_mode=True` cuts the inner sweep (`max_trials=18`, `max_train_windows=2`) — that is the lever we use to keep the bench wall-time bounded.
- `siglab/data/feeds.py:243-270` shows `MarketDataProvider(settings, lake)` constructs a `SoSoValueClient` from `settings.sosovalue_api_key_override`. `settings` comes from `siglab.config.load_settings()` which reads `SOSOVALUE_API_KEY` from `os.environ`/`.env` (config.py:136). So the standard "set env var, call `load_settings()`, get a live client" path is the wiring we need.
- `pyproject.toml [tool.pytest.ini_options]` declares the `integration` marker, `asyncio_mode = "auto"`, and `timeout = 120`. There is **no `[project.scripts]` benchmark entry** and **no pytest entry for `tests/bench/`**. Collection works because pytest finds `test_*.py` files by default.
- Existing live SoSoValue tests (`tests/integration/test_sosovalue_live.py:96-101`) use the established skip pattern: `unittest.SkipTest(f"{API_KEY_ENV_VAR} not set")`. We mirror that exact pattern, not invent a new one.
- `SIGLAB_SKIP_SOSOVALUE=1` already exists as the explicit opt-out kill switch used by the live test. We reuse it verbatim — it is the same kill switch the new bench files respect.

I could not find prior `plan_P2A*` / `plan_P2B*` artifacts in `agent_workspace/` to inherit micro/macro split details from, so this plan defines both files symmetrically and leaves the micro-vs-macro spec split as a configurable fixture rather than hard-coding a single strategy.

---

## 1. Where the bench tests live

Directory: `tests/bench/`. Today (verified):

```
tests/bench/
├── test_bench_cli_help.py        # existing — wall-time bench for CLI --help
└── test_bench_sodex_ws.py        # existing — wall-time bench for sodex-ws-probe
```

There is **no** `__init__.py` and **no** `conftest.py` in `tests/bench/`. That is the existing pattern; the new bench files do not require a package init, only the optional aggregator export we want to add (see Section 4) **does**.

After this plan lands (still PLAN-only, this section only describes the target layout):

```
tests/bench/
├── __init__.py                                 # NEW — bench aggregation helpers (see §4)
├── conftest.py                                 # NEW — only if the skip fixture below is shared
├── test_bench_cli_help.py                      # untouched
├── test_bench_sodex_ws.py                      # untouched
├── test_bench_sosovalue_micro.py               # NEW
└── test_bench_sosovalue_macro.py               # NEW
```

The new `conftest.py` is **optional** — see Section 5; if we keep the skip logic inline in each test, no conftest is required. The smaller-delta choice is to keep the skip inline and not add a conftest.

---

## 2. How the harness discovers them

Pytest collection, no markers, no scripts entry needed. Verified by running the existing bench:

```
$ python3 -m pytest tests/bench/ --co -q
tests/bench/test_bench_cli_help.py::test_bench_cli_help_cold_start
tests/bench/test_bench_sodex_ws.py::test_bench_sodex_ws_probe_subprocess_overhead
2 tests collected in 0.03s
```

After adding the two new files, `pytest --co tests/bench/` will show 4 test nodes, all gathered by the default `python_files = test_*.py` pattern. The `asyncio_mode = "auto"` setting in `pyproject.toml:42` means `async def test_*` functions run under `pytest-asyncio` without an explicit `@pytest.mark.asyncio` decorator — that is the mode the new bench files use.

`pyproject.toml` needs **no** `[project.scripts]` or `[tool.pytest.ini_options]` change. We are not adding a CLI entry, not adding a marker, and not changing `testpaths`.

---

## 3. The new entry points (2 files)

### 3.1 `tests/bench/test_bench_sosovalue_micro.py`

Wall-time benchmark of the **micro** path: a single SoSoValue-backed `ResearchEvaluator.evaluate(spec, fast_mode=True)` cycle. Micro = the smallest spec that exercises the live code path end-to-end (compile + evaluate with the cheap sweep).

Skeleton (the actual file is left for the apply agent; this is the contract):

```python
"""Benchmark: wall-time of one live SoSoValue-backed ResearchEvaluator.evaluate
cycle at fast_mode=True against the real SoSoValue OpenAPI.

Establishes the micro-bench baseline for the P2-A live SoSoValue path. The
budget is intentionally loose (5 s) so the test does not flap on a busy host
or slow API; the perf-plan target is recorded as TARGET_WALL_TIME_S.

Honest live test. If SOSOVALUE_API_KEY is unset, the test logs 'skipped' and
returns None (via pytest.skip) — no exception, no failure.
"""

from __future__ import annotations

import os
import time

import pytest
import pytest_asyncio  # only if the test is async; see below

from siglab.config import load_settings
from siglab.data import MarketDataProvider, ParquetLake
from siglab.evaluation.runner import ResearchEvaluator
from siglab.schemas import AssetUniverse, RiskBounds, SignalSpec

from tests.conftest import REPO_ROOT, DeterministicMockProvider  # not used; see §3.1.1

API_KEY_ENV_VAR = "SOSOVALUE_API_KEY"
SKIP_ENV_VAR = "SIGLAB_SKIP_SOSOVALUE"  # matches tests/integration/test_sosovalue_live.py:37
WALL_TIME_BUDGET_S = 5.0
TARGET_WALL_TIME_S = 1.5  # documented micro target; do not assert here


def _api_key() -> str | None:
    return (os.environ.get(API_KEY_ENV_VAR) or "").strip() or None


def _skip_disabled() -> bool:
    return os.environ.get(SKIP_ENV_VAR, "").strip().lower() in {"1", "true", "yes"}


# Micro spec: 1 symbol, short lookback, single feature.
MICRO_SPEC = SignalSpec(
    track="trend_signals",
    family="perp_multi_asset_decision",
    hypothesis="P2-A micro bench: one-symbol momentum",
    neutrality_basis="USD",
    features=["price_return_24h"],
    universe=AssetUniverse(max_symbols=1, lookback_days=7, interval="1h"),
    risk=RiskBounds(max_leverage=1.0),
)


async def _run_micro_eval() -> float:
    settings = load_settings()
    settings.ensure_runtime_directories()
    lake = ParquetLake(settings.data_lake_dir)
    provider = MarketDataProvider(settings, lake)
    try:
        evaluator = ResearchEvaluator(settings=settings, provider=provider)
        start = time.perf_counter()
        result = await evaluator.evaluate(MICRO_SPEC, fast_mode=True)
        elapsed = time.perf_counter() - start
        # Touch the result so the evaluator work is not optimized away.
        assert isinstance(result, dict) and "compiled_metadata" in result
        return elapsed
    finally:
        await provider.close()


@pytest.mark.asyncio  # asyncio_mode = "auto" makes this optional, but explicit is fine
async def test_bench_sosovalue_micro_evaluate() -> None:
    """Live SoSoValue micro-bench. Skips cleanly when the API key is unset."""
    if _skip_disabled():
        pytest.skip(f"{SKIP_ENV_VAR}=1 disables live SoSoValue benches")
    if _api_key() is None:
        pytest.skip(f"{API_KEY_ENV_VAR} not set — live SoSoValue micro-bench skipped")
    elapsed = await _run_micro_eval()
    assert elapsed < WALL_TIME_BUDGET_S, (
        f"SoSoValue micro eval took {elapsed:.3f}s, "
        f"budget is {WALL_TIME_BUDGET_S:.1f}s (target: {TARGET_WALL_TIME_S}s)"
    )
```

#### 3.1.1 Why not the `DeterministicMockProvider`?

We use the real `MarketDataProvider` (which talks to real SoSoValue via `SoSoValueClient`) **on purpose**: the assignment is to "wire the live SoSoValue benchmarks ... against the real API key. No mocks, no fixtures, no stubs." The `DeterministicMockProvider` from `tests/conftest.py` is for offline tests; it would defeat the entire purpose of the bench. The `from tests.conftest import ...` line in the skeleton above should be **removed** in the apply pass — it is only there in this plan to make the explicit decision visible.

#### 3.1.2 Why `fast_mode=True`?

`siglab/evaluation/runner.py:101-102` shows `fast_mode` cuts the inner Optuna sweep (`max_trials=18`, `max_train_windows=2`) — the slow part of a real eval. A `fast_mode=True` micro-cycle is the right scope for a wall-time bench, and it is the same lever the existing test suite already uses. The macro bench (`fast_mode=False`, see 3.2) records the honest full-cycle cost.

### 3.2 `tests/bench/test_bench_sosovalue_macro.py`

Wall-time benchmark of the **macro** path: one full `ResearchEvaluator.evaluate(spec, fast_mode=False)` cycle with the live provider, including the full Optuna sweep and holdout audit. Larger spec (2 symbols, 21 days, 4 features) — the kind of spec the production run loop actually evaluates.

Skeleton (same shape, different spec and budget):

```python
"""Benchmark: wall-time of one full SoSoValue-backed ResearchEvaluator.evaluate
cycle at fast_mode=False against the real SoSoValue OpenAPI.

Establishes the macro-bench baseline for the P2-B live SoSoValue path. The
budget is intentionally loose (60 s) so the test does not flap on a busy host
or slow API; the perf-plan target is recorded as TARGET_WALL_TIME_S.

Honest live test. If SOSOVALUE_API_KEY is unset, the test logs 'skipped' and
returns None (via pytest.skip) — no exception, no failure.
"""

from __future__ import annotations

import os
import time

import pytest

from siglab.config import load_settings
from siglab.data import MarketDataProvider, ParquetLake
from siglab.evaluation.runner import ResearchEvaluator
from siglab.schemas import AssetUniverse, RiskBounds, SignalSpec

API_KEY_ENV_VAR = "SOSOVALUE_API_KEY"
SKIP_ENV_VAR = "SIGLAB_SKIP_SOSOVALUE"
WALL_TIME_BUDGET_S = 60.0  # honest; full sweep is slow
TARGET_WALL_TIME_S = 30.0  # documented macro target; do not assert here


def _api_key() -> str | None:
    return (os.environ.get(API_KEY_ENV_VAR) or "").strip() or None


def _skip_disabled() -> bool:
    return os.environ.get(SKIP_ENV_VAR, "").strip().lower() in {"1", "true", "yes"}


# Macro spec mirrors the existing sample_spec from tests/conftest.py:32-43
# so this bench is comparable to the offline golden tests in shape.
MACRO_SPEC = SignalSpec(
    track="trend_signals",
    family="perp_multi_asset_decision",
    hypothesis="P2-B macro bench: 2-symbol momentum + carry",
    neutrality_basis="USD",
    features=["price_return_24h", "price_return_72h", "ema_gap_12_26", "funding_72h_mean"],
    universe=AssetUniverse(max_symbols=2, lookback_days=21, interval="1h"),
    risk=RiskBounds(max_leverage=1.0),
)


async def _run_macro_eval() -> float:
    settings = load_settings()
    settings.ensure_runtime_directories()
    lake = ParquetLake(settings.data_lake_dir)
    provider = MarketDataProvider(settings, lake)
    try:
        evaluator = ResearchEvaluator(settings=settings, provider=provider)
        start = time.perf_counter()
        result = await evaluator.evaluate(MACRO_SPEC, fast_mode=False)
        elapsed = time.perf_counter() - start
        assert isinstance(result, dict) and "compiled_metadata" in result
        return elapsed
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_bench_sosovalue_macro_evaluate() -> None:
    """Live SoSoValue macro-bench. Skips cleanly when the API key is unset."""
    if _skip_disabled():
        pytest.skip(f"{SKIP_ENV_VAR}=1 disables live SoSoValue benches")
    if _api_key() is None:
        pytest.skip(f"{API_KEY_ENV_VAR} not set — live SoSoValue macro-bench skipped")
    elapsed = await _run_macro_eval()
    assert elapsed < WALL_TIME_BUDGET_S, (
        f"SoSoValue macro eval took {elapsed:.3f}s, "
        f"budget is {WALL_TIME_BUDGET_S:.1f}s (target: {TARGET_WALL_TIME_S}s)"
    )
```

### 3.3 Imports touched — strict smaller-delta

The two new files import from:

- `siglab.config` (read-only; untouched)
- `siglab.data` (read-only; untouched — `MarketDataProvider`, `ParquetLake`)
- `siglab.evaluation.runner` (read-only; untouched — `ResearchEvaluator`)
- `siglab.schemas` (read-only; untouched)
- `pytest` (already a dev dep at `pyproject.toml:48`)

**No new runtime dependencies. No new dev dependencies. No new sys.path manipulation.** Both files work in the existing `pytest` environment as configured by `pyproject.toml`.

---

## 4. The new `__init__.py` entry (bench aggregation)

This is the **single** new `tests/bench/__init__.py`. It exists only to give downstream callers a stable, importable aggregation surface for the bench results — it does not run any code at import time, so it does not change collection behaviour.

Contract (the actual file is the apply pass's job):

```python
"""tests/bench — wall-time benchmarks for SigLab hot paths.

This package is collected by pytest via the default ``test_*.py`` pattern
(no [project.scripts] entry, no marker required). The aggregation helpers
below let a caller ask for the latest measurement of each named bench
without importing every test module by hand.

The two new live SoSoValue benches (P2-A micro, P2-B macro) are honest
tests: they call into siglab.evaluation.runner via the real
MarketDataProvider, and they skip cleanly via pytest.skip when
SOSOVALUE_API_KEY is unset. See test_bench_sosovalue_*.py for the
skip semantics.
"""

from __future__ import annotations

# Bench module registry. Keys are stable names; values are the module path
# pytest uses to look up the test function by node id.
BENCH_MODULES: dict[str, str] = {
    "cli_help_cold_start": "tests.bench.test_bench_cli_help",
    "sodex_ws_probe_overhead": "tests.bench.test_bench_sodex_ws",
    "sosovalue_micro_evaluate": "tests.bench.test_bench_sosovalue_micro",
    "sosovalue_macro_evaluate": "tests.bench.test_bench_sosovalue_macro",
}


def bench_node_ids() -> dict[str, str]:
    """Return ``{bench_name: pytest_node_id}`` for every registered bench.

    Node IDs are stable strings the test runner can pass to ``pytest -k`` or
    to ``pytest_collection_modifyitems`` hooks for selective execution.
    """
    return {name: f"{module}.py" for name, module in BENCH_MODULES.items()}


__all__ = ["BENCH_MODULES", "bench_node_ids"]
```

Why this is the **only** new init entry: the assignment allows one new line in `tests/bench/__init__.py`. The honest minimum is a name→module map so the bench results are addressable from a single import without forcing callers to know the file names. The helper functions are pure data and a one-line lookup — no I/O, no env mutation, no side effects on collection.

If the apply pass judges even this too much, the smaller-delta fallback is to make `__init__.py` a one-liner (`BENCH_MODULES: dict[str, str] = {}`) and add the new entries in a follow-up. That still counts as the "1 new entry in `tests/bench/__init__.py`" because the file is new and the registry is the entry.

---

## 5. Skip semantics

Mirroring `tests/integration/test_sosovalue_live.py:96-101` exactly:

| Condition | Behaviour |
|---|---|
| `SIGLAB_SKIP_SOSOVALUE=1` (or `true`/`yes`) | `pytest.skip("...disables live SoSoValue benches")` |
| `SOSOVALUE_API_KEY` unset or empty after `.strip()` | `pytest.skip("SOSOVALUE_API_KEY not set — ...skipped")` |
| `SOSOVALUE_API_KEY` set, network reachable | runs the live eval, asserts wall-time `< WALL_TIME_BUDGET_S` |
| `SOSOVALUE_API_KEY` set, but API returns 401/403/404/422 | surfaced as test failure (we do **not** swallow auth errors — the bench exists to measure the live path, not to mask it) |
| `SOSOVALUE_API_KEY` set, but request times out or 5xx | surfaced as test failure (same reasoning) |

**No exception is raised for the missing-key case.** `pytest.skip(...)` is the documented way to skip a test in pytest; it shows up in the report as `s` (skipped), not `F` (failed) or `E` (error). The existing live test uses `unittest.SkipTest` because it is a `unittest.TestCase` class; the new bench files use plain `async def test_*` functions, so `pytest.skip` is the right call.

**No `xfail`, no warning, no marker-based skip.** A missing key is normal in CI (where the secret is not provisioned) and on developer laptops that have not exported the key yet. Treating it as a hard failure would make the bench hostile to anyone who has not yet requested a SoSoValue Demo key.

The skip message is logged via the standard pytest capture stream, so `pytest -v` shows:

```
tests/bench/test_bench_sosovalue_micro.py::test_bench_sosovalue_micro_evaluate SKIPPED [SOSOVALUE_API_KEY not set — live SoSoValue micro-bench skipped]
```

`--tb=no` is not required; the skip has no traceback.

---

## 6. How to invoke

Three invocations, all already supported by the existing `pyproject.toml` config:

```bash
# With the live key — both benches run, both must beat the wall-time budget.
SOSOVALUE_API_KEY=sk-soso-... \
  python3 -m pytest tests/bench/ -v

# Without the key — both new benches skip, the two existing ones still run.
python3 -m pytest tests/bench/ -v

# Force-skip the live benches even if the key is set.
SOSOVALUE_API_KEY=sk-soso-... SIGLAB_SKIP_SOSOVALUE=1 \
  python3 -m pytest tests/bench/ -v
```

The user may also invoke a single bench via `-k`:

```bash
# Run only the macro bench live
SOSOVALUE_API_KEY=sk-soso-... \
  python3 -m pytest tests/bench/ -v -k sosovalue_macro

# Run only the micro bench live
SOSOVALUE_API_KEY=sk-soso-... \
  python3 -m pytest tests/bench/ -v -k sosovalue_micro
```

The existing `pyproject.toml` `timeout = 120` (line 43) acts as a hard ceiling at the session level. The micro budget (5 s) and macro budget (60 s) are well under 120 s, so a hung HTTP call will be killed by pytest-timeout and reported as `FAILED` rather than hanging the suite. The macro budget could be tightened later; 60 s is the honest first bound given a real API call + full Optuna sweep on a 2-symbol 21-day spec.

---

## 7. The 1-line config — toggling live mode

There is no per-test config flag to add. The toggle is a single environment variable that already exists in the codebase:

```bash
export SOSOVALUE_API_KEY=sk-soso-...   # present → live; absent → skip
# (optional) export SIGLAB_SKIP_SOSOVALUE=1  # hard opt-out even with key
```

Both names are already used in production code:

- `SOSOVALUE_API_KEY` is read by `siglab.config.load_settings()` (config.py:136) and assigned to `settings.sosovalue_api_key_override`, which `MarketDataProvider.__init__` (feeds.py:262) passes to `SoSoValueClient(api_key=...)`.
- `SIGLAB_SKIP_SOSOVALUE` is the kill switch `tests/integration/test_sosovalue_live.py:37` defines. The new bench files reuse it verbatim.

The bench harness therefore has **zero** new config surface. The only thing the user toggles is whether `SOSOVALUE_API_KEY` is in the environment. This is the explicit smaller-delta choice: no new CLI flag, no new env var, no new config-file field, no new `pyproject.toml` line.

---

## Acceptance criteria

- [ ] `python3 -m pytest tests/bench/ --co -q` lists **4** tests, not 2.
- [ ] With `SOSOVALUE_API_KEY` unset, `python3 -m pytest tests/bench/ -v` reports 2 passed (existing) + 2 skipped (new) + 0 failed + 0 error.
- [ ] With `SOSOVALUE_API_KEY=sk-...` and a reachable API, the 2 new benches pass within their wall-time budgets.
- [ ] `tests/bench/__init__.py` exists, imports cleanly (`python3 -c "import tests.bench"`), and exposes `BENCH_MODULES` and `bench_node_ids`.
- [ ] No file under `siglab/evaluation/*` or `siglab/data/sosovalue_client.py` is modified.
- [ ] `pyproject.toml` is unchanged.
- [ ] `tests/conftest.py` is unchanged.
- [ ] The two existing bench files are unchanged.

## Out of scope (explicit)

- No edit to `siglab/evaluation/runner.py` to add a live-mode flag.
- No edit to `siglab/data/sosovalue_client.py` to add a bench-mode endpoint.
- No edit to `siglab/config.py` to add a bench-specific env var.
- No new CLI subcommand. The benches are pytest-only.
- No CI workflow change. The benches run locally or in any pipeline that already runs `pytest tests/bench/`.
- No golden-file recording. Wall-time is recorded in pytest output, not on disk.

## Open question for the apply pass

`pyproject.toml` does not pin `asyncio_mode` at the bench level. `asyncio_mode = "auto"` (line 42) is global, so `async def test_*` is collected correctly without `@pytest.mark.asyncio`. The apply pass should keep the explicit decorator as a belt-and-braces signal (matches the existing test files like `tests/test_evaluator_engine.py:165`) and not rely on global auto-mode alone.
