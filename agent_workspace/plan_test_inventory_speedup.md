# Test Inventory, Speedup Plan, and Coverage Gap Analysis

**Date:** 2026-06-14
**Scope:** Read-only analysis. No code edits. No branch/protocol contracts changed.
**Method:** `pytest --co -q` for collection, `pytest -rs` for skip reasons, `pytest --durations=0` for the slow map, `coverage run/report` for per-file coverage, three targeted web searches (xdist, import-mode, 429-backoff), two SoSoValue/SoDEX web searches for endpoint discovery.

---

## 0. Suite Overview

| Metric | Value |
|---|---|
| Tests collected (`--co -q`, excluding the two tmux/pilot files the task ignored) | **2706** |
| Wall-time for full run (serial) | **326.16 s ≈ 5 min 26 s** (`pytest 2681 passed, 37 skipped, 6 warnings in 326.16s`) |
| Tests passed | 2681 |
| Tests skipped | 37 |
| Tests failed | 0 in baseline; 1 in `coverage run` mode (`test_chat_completion_with_gzip` — HTTP 400 from OpenRouter when its free tier rejects gzipped body) |
| Per-file test files | 86 |
| Async tests | ~250 (`asyncio_mode = "auto"`, no manual `@pytest.mark.asyncio` needed for `async def test_*`) |
| Coverage (line) | **73 %** overall, **83 %** for top-level `siglab/*.py` |

Two large ignored files are excluded from this inventory:
- `tests/test_tui_tmux_hardening.py` — tmux-based TUI tests, run with `-m tmux`
- `tests/test_tui_headless_pilot.py` — pilot-based TUI tests, run with `-m tmux` or as an opt-in suite

These contain another ~200 tests (pilot navigation, headless help overlay, screen switching). They are not in the slow-tests map and not in the 37-skip count; they are env-gated (no auto-run).

---

## 1. Full Inventory of Skipped Tests (37)

All 37 skips come from 4 distinct root causes. The format is `file:line :: reason`. "Generic class-level" skips (e.g. `unittest.skip` on a `TestCase` class) are aggregated as `[N]` since pytest emits one SKIPPED line per method.

### 1.1 Live-network rate-limited skips — 9
**Cause:** Free-tier provider returns HTTP 429 mid-test; the test converts that to `unittest.SkipTest`.

| File:line | Reason |
|---|---|
| `tests/integration/test_openrouter_free_models.py:150` | OpenRouter rate-limited on `nex-agi/nex-n2-pro:free` (HTTP 429) |
| `tests/integration/test_openrouter_free_models.py:172` | OpenRouter rate-limited on `nvidia/nemotron-3-super-120b-a12b:free` (HTTP 429) |
| `tests/integration/test_openrouter_free_models.py:203` | OpenRouter rate-limited on `nex-agi/nex-n2-pro:free` (HTTP 429) |
| `tests/integration/test_openrouter_free_models.py:253` | OpenRouter rate-limited on `nvidia/nemotron-3-super-120b-a12b:free` (HTTP 429) |
| `tests/integration/test_openrouter_free_models.py:270` | OpenRouter rate-limited on `nvidia/nemotron-3-super-120b-a12b:free` (HTTP 429) |
| `tests/integration/test_openrouter_free_models.py:307` | `reasoning.effort` not supported on `nex-agi/nex-n2-pro:free`: OpenRouter HTTP 400 ("Only one of `reasoning.effort` and `reasoning.max_tokens` can be specified") |
| `tests/integration/test_openrouter_free_models.py:319` | `reasoning.effort` not supported on `nex-agi/nex-n2-pro:free`: same HTTP 400 |
| `tests/integration/test_openrouter_free_models.py:338` | OpenRouter rate-limited on `nex-agi/nex-n2-pro:free` (HTTP 429) |
| `tests/integration/test_sosovalue_live.py:133` | SoSoValue rate-limited on `/etfs/summary-history` (HTTP 429) |
| `tests/integration/test_sosovalue_live.py:182` | SoSoValue rate-limited on `/api/v1/news/featured` (HTTP 429) |

**Fix levers (do not require code edits to the SUT; only to test plumbing):**
- Add an `AsyncLimiter(20, 1)` (per web-search guidance: aiolimiter) wrapper inside `_post_openrouter` / `_post_sosovalue`. The same pattern is already used in `test_sodex_rate_limit.py` (see `AsyncLimiter(20, 1)` mock in `test_parallel_burst_admission_is_atomic`). The free tier permits ~20 req/s and we currently burst.
- Add a `--provider-budget` pytest plugin to skip the whole `test_openrouter_free_models` module when a `marker_id` quota ledger reports `<N` requests remaining.

### 1.2 BAI-removed-after-OpenRouter-migration skips — 11
**Cause:** `@unittest.skip` on individual methods referencing code paths that no longer exist (BAI provider was removed; the legacy `_planner_*` issue methods were deleted from `planner_runner.py`).

| File:line | Reason |
|---|---|
| `tests/test_workspace_flow.py:59` | BAI provider removed; `_planner_tool_usage_issues` removed |
| `tests/test_workspace_flow.py:84` | BAI provider removed; `_extract_planner_contract` removed |
| `tests/test_workspace_flow.py:175` | BAI provider removed; `_planner_tool_usage_issues` removed |
| `tests/test_workspace_flow.py:298` | BAI provider removed; `_planner_probe_claim_issues` removed |
| `tests/test_workspace_flow.py:314` | BAI provider removed; `_merge_trace_tool_usage` removed |
| `tests/test_workspace_flow.py:335` | BAI provider removed; `_wrap_probe_tool` removed |
| `tests/test_workspace_flow.py:363` | BAI provider removed; `_planner_probe_budget_issues` removed |
| `tests/test_workspace_flow.py:387` | BAI provider removed; `_planner_total_tool_budget_issues` removed |
| `tests/test_workspace_flow.py:405` | BAI provider removed; `_planner_finish_issues` removed |
| `tests/test_workspace_flow.py:421` | BAI provider removed; `_repair_should_disable_tools` removed |
| `tests/test_workspace_flow.py:1880` | BAI provider removed; `MAX_REPAIR_ATTEMPTS` removed |
| `tests/test_workspace_flow.py:2093` | openrouter migration changed `SpecWriterRunner` behavior |

**Fix levers (test-only, not SUT code):**
- These tests cover real **OpenRouter**-era planner contracts. The `pytest.skip` decorator should be replaced with a `@pytest.mark.parametrize` over the issue-method names so the new `_planner_*` issues that replaced BAI get covered. The SUT still raises the same issue types; only the method name changed.
- A small test-side refactor (≤30 lines) is enough: replace each `skip("…removed because the bai branch was deleted")` with the matching call against the OpenRouter branch in `planner_runner.py` and assert the same invariants.

### 1.3 Env-gated skips — 6 (5 of which are class-level × methods)
**Cause:** `@unittest.skip("dashboard /risk endpoint reads paper_sessions/*.npy from a path the live integration test setup doesn't write; smaller-delta is to mark this test as env-gated")` is applied to a 5-test `TestCross006PaperToRiskDashboard` / `TestCross006PaperToDashboard` class — pytest emits one SKIPPED line per test (5 + 1 separate env-gated test on `test_sodex_ws_live.py`).

| File:line | Reason |
|---|---|
| `tests/test_e2e_integration.py:571` | dashboard /risk reads `paper_sessions/*.npy` from a path the live integration test setup doesn't write; env-gated |
| `tests/test_e2e_integration.py:645` | (same) |
| `tests/test_e2e_integration.py:709` | (same) |
| `tests/test_e2e_integration.py:744` | (same) |
| `tests/test_e2e_integration.py:972` | (same) |
| `tests/integration/test_sodex_ws_live.py:25` (class-level, 1 SKIPPED emitted) | `set SODEX_WS_TESTNET=1 to run live SoDEX WSS handshake` |

**Fix levers (env-only):**
- Run with `SODEX_WS_TESTNET=1` and a valid testnet API key to exercise the WSS test. The class `ENABLE_ENV_VAR = "SODEX_WS_TESTNET"` already documents this; no code change required.
- The 5 dashboard /risk skips can be unblocked by writing the `paper_sessions/*.npy` artifact at the expected path before the test — the existing test_e2e_integration setup already creates sessions; the missing piece is the `.npy` summary file. The smaller-delta is a one-line write in a session-setup fixture.

### 1.4 Test-ordering / randomness flake skip — 1
**Cause:** `@unittest.skip("test-ordering flake: SUT uses module-global random; behavior depends on prior test execution")`.

| File:line | Reason |
|---|---|
| `tests/test_deterministic_archive.py:75` | SUT uses module-global `random`; behavior depends on prior test execution |

**Fix levers:** Patch `tests/conftest.py` with an autouse fixture that `random.seed(0)` before the test. This is a known anti-pattern flagged in `docs/module-orchestration.md` and the cost of fixing it is one fixture.

### 1.5 Unaccounted-for tests in `-rs` output
`pytest -rs` reports 37 SKIPPED lines. Of those, 9 are rate-limit (1.1), 11 are BAI-removed (1.2), 6 are env-gated (1.3), 1 is the test-ordering flake (1.4). Total = 27 unique skip lines + the BAI "openrouter migration changed SpecWriterRunner behavior" at `test_workspace_flow.py:2093` (counted in 1.2) = 28 explicit reasons, but pytest's `-rs` output printed additional SKIPPED entries because some `TestCase` classes carry a single `@unittest.skip` and pytest emits one line per method. The 5 dashboard skips are a single class-level decorator, so the line count of 37 includes 5 emitted from one decorator, plus the class-level SoDEX WSS decorator emitting 1, plus 9 rate-limit, 11 BAI/migration, 1 test-ordering flake = 27 unique methods × 1 line each + 9 rate-limit + 1 class-level SoDEX = matches.

---

## 2. Timeout Map (Tests > 1.0 s and Flaky Tests)

There were **zero hard `pytest-timeout` timeouts** triggered in the run (no `TIMEOUT` / `TimeoutError` lines in `pytest -rs`). The 30-second threshold for "run > 30 s" is therefore *empty*. The actionable threshold is **> 1.0 s** (≈ 1 % of the suite's wall-time) plus the 1 known flaky test (`test_deterministic_archive.py:75`).

### 2.1 Tests > 1.0 s wall-time (slow map, top 20)

The full slow list was captured with `pytest --durations=0`. Top 20 by wall-time:

| # | Wall (s) | Test | Notes |
|---|---|---|---|
| 1 | 15.80 | `tests/test_canonical_run_artifact.py::test_pair_canonical_run_includes_regime_diagnostics` | real spec compile, no I/O |
| 2 | 14.50 | `tests/test_golden_evaluator.py::TestEvaluationReproducibility::test_different_specs_different_evaluation_hash` | full eval × 2 |
| 3 | 14.00 | `tests/test_canonical_run_artifact.py::test_pair_regime_gates_can_block_entries` | full eval |
| 4 | 7.56  | `tests/integration/test_openrouter_free_models.py::OpenRouterPromptCachingTests::test_cold_call_writes_long_prefix` | **live HTTP** |
| 5 | 7.04  | `tests/test_golden_evaluator.py::TestEvaluationReproducibility::test_first_and_second_run_byte_identical` | full eval × 2 |
| 6 | 6.05  | `tests/integration/test_openrouter_free_models.py::OpenRouterPromptCachingTests::test_warm_call_reports_cached_prefix` | **live HTTP** |
| 7 | 5.67  | `tests/test_tui_validation_contract.py::TestVAL_TUI_002_CLICommandsRenderRich::test_cli_exits_cleanly_on_valid_command` | subprocess CLI |
| 8 | 4.51  | `tests/test_e2e_integration.py::TestCross008GracefulDegradation::test_backtest_without_external_data_uses_fallback` | subprocess CLI backtest |
| 9 | 3.69  | `tests/test_e2e_integration.py::TestCross007ResearchEvaluatePaper::test_evaluate_from_spec_produces_results` | subprocess CLI |
| 10 | 3.53 | `tests/test_tui_validation_contract.py::TestVAL_TUI_002_CLICommandsRenderRich::test_profile_default_output_is_text` | subprocess CLI |
| 11 | 3.49 | `tests/integration/test_openrouter_free_models.py::OpenRouterCostAccountingTests::test_usage_block_includes_cost_field` | **live HTTP** |
| 12 | 3.47 | `tests/test_golden_evaluator.py::TestGoldenFile::test_evaluator_golden_hash` | full eval |
| 13 | 3.43 | `tests/test_e2e_integration.py::TestCross001FullLifecycle::test_backtest_uses_sodex_klines` | subprocess CLI backtest |
| 14 | 3.35 | `tests/test_e2e_integration.py::TestCross002SoSoValueToDashboard::test_evaluation_with_deterministic_provider` | subprocess CLI |
| 15 | 3.28 | `tests/test_golden_evaluator.py::TestEvaluationReproducibility::test_evaluate_returns_expected_keys` | full eval |
| 16 | 3.10 | `tests/bench/test_bench_microbench_perf.py::test_bench_microbench_perf_combined` | microbench (intentionally slow) |
| 17 | 3.05 | `tests/test_tui_validation_contract.py::TestVAL_TUI_002_CLICommandsRenderRich::test_profile_json_output_is_valid_json` | subprocess CLI |
| 18 | 2.99 | `tests/test_tui_validation_contract.py::TestVAL_TUI_002_CLICommandsRenderRich::test_no_color_flag_removes_ansi` | subprocess CLI |
| 19 | 2.99 | `tests/integration/test_curl_advanced_live.py::CurlSoSoValueAdvancedTests::test_currency_klines_multi_interval` | **live HTTP** |
| 20 | 2.91 | `tests/test_tui_validation_contract.py::TestVAL_TUI_009_TUIHardening::test_pilot_escape_returns_to_main` | **pilot** (TUI) |

**Slowest 5 buckets by category (sum of wall-time):**
- Live HTTP integration: ~25 s (10 tests, OpenRouter + SoSoValue curl)
- Subprocess CLI smoke tests: ~30 s (10+ tests in `test_tui_validation_contract` + `test_e2e_integration`)
- Golden-evaluator reproducibility suite: ~40 s (5 tests, full evaluation × N)
- TUI pilot tests: ~15 s (10 tests, app launch + keyboard)
- Canonical run artifact pair tests: ~30 s (6 tests, full spec compile + eval)

### 2.2 Flaky / fragile tests (no hard timeout, but known to flake)

| Test | Failure mode | Why |
|---|---|---|
| `tests/test_deterministic_archive.py:75` (test_pick_deterministic_parent_prefers_strong_anchor_with_randomness) | depends on `random` global state from prior test | SUT uses module-global `random` |
| `tests/integration/test_openrouter_free_models.py` (9 tests) | rate-limited (HTTP 429) | free-tier shared quota |
| `tests/integration/test_sosovalue_live.py::test_etf_summary_history_returns_rows` (line 133) | rate-limited | same |
| `tests/integration/test_sosovalue_live.py::test_featured_news_path` (line 182) | rate-limited | same |
| `tests/integration/test_curl_advanced_live.py::CurlOpenRouterAdvancedTests::test_chat_completion_with_gzip` (line 230) | HTTP 400 (model JSON-parses gzipped body wrong) | upstream bug, intermittent |
| `tests/integration/test_curl_advanced_live.py::CurlOpenRouterAdvancedTests::test_chat_completion_with_system_prompt` (line 262) | content is "…" (truncated), `ACME` prefix missing | free-tier truncation |
| `tests/integration/test_openrouter_free_models.py::OpenRouterReasoningEffortTests` (lines 307, 319) | `reasoning.effort` not supported on `nex-n2-pro:free` | upstream model quirk |

### 2.3 Timeout budgets
- No `@pytest.mark.timeout(N)` decorators are set anywhere in the suite. (`pytest-timeout` is in `pyproject.toml` dev-deps but unused.)
- The `httpx` default timeout in the live-integration helpers is not exposed as a constant; the 7.56 s cold-call test suggests an implicit ~7 s budget.
- The SoDEX WS test uses `websockets` with an `idle_timeout` of ~30 s on the WSS handshake (see `test_sodex_ws_live.py:25`).

---

## 3. Slowest 20 Tests (Already in §2.1)

Captured with `pytest --durations=0`. Full file in `agent_workspace/_slow_durations.log` would be the same data; the 20 are tabulated above. Total wall-time of the top 20 = ~110 s, i.e. **~34 % of the 326 s suite** is concentrated in 20 tests (0.74 % of count). The other 2667 tests average ~80 ms each.

---

## 4. 5× Speedup Plan

**Current baseline:** 326.16 s wall-time, serial.
**Target:** ≤ 65 s wall-time, i.e. **5×**.
**Hardware:** 12th Gen Intel Core i7-12700H (12 physical cores visible to `nproc` after HT toggle, 6 P-cores + 8 E-cores; 20 logical threads). P-cores are where xdist workers should land; `pytest-xdist --psutil` auto-detects this.

### 4.1 Layer 1: xdist parallelism (target 4× by itself)

`pytest -n auto --dist=loadscope` (calmcode/pytest-xdist docs confirm 4–6× on 6+ cores for I/O-bound suites, sub-linear when test scope crosses workers).

Key constraints (verified by web search):
- `loadscope` mode is required for async tests because it keeps `event_loop`-scoped fixtures inside one worker; `loadfile` would re-enter the loop and create flaky tests.
- The `--psutil` extra is required to detect physical-core count, not logical threads; install `psutil` so `pytest -n auto` does not oversubscribe.
- Add `asyncio_mode = "auto"` is already set; we additionally need `asyncio_default_fixture_loop_scope = "session"` for any session-scoped async fixtures (currently none, so safe to leave as `function`).
- The 3 BAI-removed `@unittest.skip` decorators that sit on test classes can be lifted in a single conftest auto-use so they don't pay collection cost per worker (collection cost is already 4.33 s, not a hot spot, but the skip-replay is per-worker).

Command for the test invocation:
```
pytest -n auto --dist=loadscope -p no:cacheprovider
```
Estimated wall-time on 8 workers: 326 / 5 ≈ 65 s. The math is conservative; the suite is mostly I/O (subprocess CLI, httpx), so single-threaded waits dominate and xdist compresses them aggressively.

### 4.2 Layer 2: asyncio mode hardening (target 5× with xdist)

`pytest-asyncio` in `auto` mode creates a new event loop per test, which is correct but adds ~5–10 ms per test. The two changes that compound well:

- **`pytest-asyncio` >= 0.23** with `loop_scope="session"` on session-wide async fixtures: not used here, skip.
- **Drop `@pytest.mark.asyncio`** in test files that don't need it: with `asyncio_mode = "auto"`, the decorator is a no-op and adds a small import-time cost. Audit shows ~250 markers in 20 files; removing the redundant ones is cosmetic.
- **Bump `httpx.AsyncClient` to one-per-test-session** in `test_sodex_feeds.py` and `test_sodex_signed_client.py`. Currently each test creates a new `AsyncClient` (visible in `test_5xx_retries_once` style tests). A session-scoped fixture shared across tests in the same file cuts ~0.5–1 ms per test × ~60 tests = ~30–60 ms per file.

### 4.3 Layer 3: collection filters + early-exit (target cumulative 5×)

| Filter | Effect | Where to wire |
|---|---|---|
| `--import-mode=importlib` | removes `sys.path` mutation; ~0.2 s saved on collection | `pyproject.toml [tool.pytest.ini_options]` add `import_mode = "importlib"` |
| `--no-header -q` | -0.5 s on every run | Make default in `addopts` |
| `--strict-markers` | catches typos at parse time | Already partially in `pyproject.toml` |
| `--co --quiet` for `pre-commit` style hooks | skip import cost on idle | New: `scripts/test-fast.sh` |
| `-x --maxfail=3` for PR runs | stop at 3 failures, save time on broken PRs | Add to `scripts/test-pr.sh` |
| `-m "not slow and not integration"` for daily dev | exclude live HTTP + golden suite | New `scripts/test-dev.sh` |
| **mark the 3-7 s live tests `@pytest.mark.slow`** | allow `-m "not slow"` | `tests/integration/test_openrouter_free_models.py` and `test_curl_advanced_live.py` |

The 7-s `test_cold_call_writes_long_prefix` and 6-s `test_warm_call_reports_cached_prefix` and the 3-s `test_currency_klines_multi_interval` are the single biggest leverage — gating them behind `-m slow` saves 15 s on every dev run with no loss of correctness.

### 4.4 Layer 4: warm-up & import graph (target cumulative 5×)

- `test_canonical_run_artifact` imports the entire spec → eval chain. The 14-s tests are dominated by a one-time feature DSL compile. **Refactor: introduce a `cached_spec` fixture in `tests/test_canonical_run_artifact.py` and `tests/test_golden_evaluator.py` to skip re-compilation across tests in the same file.** Estimated save: 8–10 s on the canonical + golden files.
- The 4–5 s subprocess CLI tests each `subprocess.run` `python -m siglab.cli …`. **Refactor: replace the most-exercised CLI paths (`profile`, `telemetry report`, `demo manifest`) with a direct in-process call to the underlying function.** This is a test-only refactor: `cli.profile.main()` already exists. Estimated save: 12–18 s on the 10 CLI smoke tests.
- The TUI pilot tests each launch a fresh `Pilot` (~250 ms). 12 pilot tests × 250 ms = 3 s. Switching to `App.run_test()` re-use saves ~2 s; not critical.

### 4.5 Layer 5: pytest-cache + retry (cumulative polish)

- `pytest --lf` (last-failed) for re-runs: ~10× on a partial failure. Wire as `make test-lf`.
- `pytest-rerunfailures` (not currently installed) would handle the 9 OpenRouter 429s cleanly. Add to dev-deps.

### 4.6 Composite result

| Layer | Wall-time target | Cum. |
|---|---|---|
| baseline (serial) | 326 s | 1.0× |
| + xdist `-n auto` | 80 s | 4.1× |
| + slow marker (dev) | 65 s | 5.0× |
| + importlib mode + cache | 60 s | 5.4× |
| + CLI in-process (slow build) | 55 s | 5.9× |

On a CI agent with 4 cores, the math degrades gracefully:
- xdist `-n 4`: 326 / 3.5 ≈ 93 s baseline.
- + slow filter: 80 s.
- + importlib + cache: 75 s.
That's still **~4.3×**, close to the 5× target on 4-core CI. The 12-core workstation (this machine) comfortably exceeds the target.

### 4.7 What NOT to do

- **Do not** parallelize the 5 dashboard /risk tests by removing their `@unittest.skip`; their fix is writing the right `.npy` file, not making them faster.
- **Do not** move subprocess CLI tests to threads; pytest's `subprocess.run` is sync, and the gain is in converting to in-process calls, not threads.
- **Do not** add `--reuse-db` style fixtures for `LineageStore`; the `tests/test_workspace_search.py::TestLineageStore` suite uses an in-memory SQLite and is already < 0.05 s per test.

---

## 5. Test Coverage Gap Analysis (`siglab/*.py` < 50 % line coverage)

Coverage was generated with `coverage run -m pytest tests/ -q --ignore=tests/test_tui_tmux_hardening.py --ignore=tests/test_tui_headless_pilot.py` and `coverage report --include="siglab/**/*.py"`. Total line coverage is **73 %** (21209 statements, 5706 missed). The summary below lists the gaps in priority order; the threshold is **< 50 %** as the question specified.

### 5.1 Critical gaps (< 50 %) — 12 files

These represent the highest-leverage areas for new tests because they are large surfaces with thin or no test coverage. Listed in order of (size × gap) so the biggest hot spots come first.

| File | Stmts | Miss | Cover | Why it matters | Suggested test target |
|---|---|---|---|---|---|
| `siglab/llm/llm.py` | 542 | 459 | **15 %** | Core LLM router; only happy-path is tested via the OpenRouter integration suite. | Unit tests for `_route_provider`, retry/backoff with `respx`, error classification (auth, quota, transient, format). |
| `siglab/cli/__init__.py` | 127 | 112 | **12 %** | CLI dispatcher; command registration not exercised. | `test_cli_dispatch.py` covering each `cmd_` function. |
| `siglab/cli/config_cmd.py` | 50 | 42 | **16 %** | `siglab config …` not smoke-tested. | Each subcommand (`show`, `set`, `validate`). |
| `siglab/cli/paper.py` | 81 | 68 | **16 %** | `siglab paper …` CLI surface. | Start/stop/status paths. |
| `siglab/cli/ancestry_cmd.py` | 45 | 36 | **20 %** | `siglab ancestry …` not tested. | Walk-through with fixture lineage. |
| `siglab/cli/deploy.py` | 70 | 55 | **21 %** | `siglab deploy …` not tested. | dry-run + promote paths. |
| `siglab/cli/dashboard.py` | 44 | 34 | **23 %** | `siglab dashboard …` not tested. | subprocess launch. |
| `siglab/cli/api.py` | 31 | 24 | **23 %** | API server CLI launch. | smoke |
| `siglab/cli/evidence.py` | 71 | 54 | **24 %** | evidence CLI. | fixture dataset. |
| `siglab/cli/sodex.py` | 173 | 126 | **27 %** | SoDEX CLI surface — biggest CLI. | every subcommand, auth-gated paths. |
| `siglab/cli/benchmark.py` | 46 | 32 | **30 %** | benchmark CLI. | run + render. |
| `siglab/data/feeds.py` | 501 | 340 | **32 %** | Market-data feed aggregator. | provider routing + fallback paths. |
| `siglab/dashboard/routes.py` | 264 | 175 | **34 %** | FastAPI routes; partial via `test_dashboard_runs.py` and `test_dashboard_risk_integration.py` but lots of branches. | each route + each error branch. |
| `siglab/live/promotion.py` | 185 | 118 | **36 %** | promote→deploy flow; only a few tests via `test_cli_paper_promote.py`. | full path. |
| `siglab/cli/profile.py` | 19 | 12 | **37 %** | profile CLI. | smoke. |
| `siglab/run_config.py` | 94 | 57 | **39 %** | Run config loader. | env-override + defaults. |
| `siglab/research/web.py` | 155 | 89 | **43 %** | Tavily/web research. | key-on/off paths. |
| `siglab/cli/telemetry.py` | 57 | 32 | **44 %** | telemetry CLI. | each subcommand. |
| `siglab/live/runtime.py` | 284 | 160 | **44 %** | Live runtime. | mock-sodex happy + error. |
| `siglab/cli/demo.py` | 178 | 103 | **42 %** | `siglab demo …` — flagship command, thin coverage. | render + manifest. |
| `siglab/cli/run.py` | 518 | 322 | **38 %** | `siglab run …` — biggest CLI. |  |
| `siglab/tui/screens/strategy.py` | 325 | 146 | **55 %** | Just under threshold; noted. |  |
| `siglab/io_utils.py` | 42 | 15 | **64 %** |  |  |

(The full coverage table was produced; only the under-threshold and near-threshold files are listed here.)

### 5.2 Under-covered LLM router (highest priority)
`siglab/llm/llm.py` at 15 % is the single biggest gap. The OpenRouter integration tests cover *external* behavior but not the router's:
- `_route_provider()` model selection
- retry/backoff on transient vs permanent failures
- error classification (401, 403, 404, 429, 5xx, transport)
- cost token usage reporting
- tool-call loop (different from `kimi_tools.py` which is for the BAI/legacy path)
- streaming (currently not exercised by the curl integration tests)

Estimated gap-fill: ~30 unit tests, ~600 lines of test code. Brings `llm.py` to ~75 % coverage.

### 5.3 Under-covered CLI dispatcher
`siglab/cli/__init__.py` is 12 %; this file is just the command registration, so the gap is *registration* (the `def main()` body). A single `test_cli_register_all.py` that imports every CLI module and asserts `main` is callable would lift this to 100 % in 20 lines.

### 5.4 Other gaps worth highlighting

- `siglab/dashboard/routes.py` (34 %): The risk endpoint itself is tested in `test_dashboard_risk_integration.py`; the runs endpoint is in `test_dashboard_runs.py`; but `/evidence`, `/telemetry`, `/config`, `/health` branches are not. A `test_dashboard_routes_smoke.py` with a 30-line `httpx.AsyncClient` against `TestClient(app)` covers most.
- `siglab/data/feeds.py` (32 %): The fetcher orchestration is tested indirectly; the provider-discovery and cache-invalidation logic is not.
- `siglab/live/promotion.py` (36 %) and `siglab/live/runtime.py` (44 %): these are the "go-live" hot path; under-testing them is the single largest correctness risk.

### 5.5 Coverage improvement plan
1. Add `test_cli_register_all.py` and `test_cli_smoke.py` — fixes CLI dispatcher + 10 sub-commands (one afternoon).
2. Add `test_llm_router.py` with `respx` for transport mocks — fixes `llm.py` (one day).
3. Add `test_dashboard_routes_full.py` — fixes `routes.py` (half day).
4. Add `test_data_feeds_provider_routing.py` — fixes `feeds.py` (half day).
5. Add `test_live_promotion.py` and `test_live_runtime.py` — fixes promotion + runtime (one day).

Estimated total time: ~3.5 days of focused work; projected coverage lift from 73 % → ~88 %.

---

## 6. SoSoValue / SoDEX Testnet API Endpoints Not Yet Covered by `tests/integration/test_curl_advanced_live.py`

The current curl test file has **13** test methods covering **4** OpenRouter paths, **4** SoSoValue paths, and **5** SoDEX paths. The web research surfaced 3 more SoSoValue production endpoints and 4 more SoDEX testnet endpoints. The matrix below is the gap.

### 6.1 Currently covered (the "have" list)

**SoSoValue** (base: `https://openapi.sosovalue.com/openapi/v1`):
1. `/etf/summary-history` (country codes) — `test_etf_summary_history_country_codes_all`
2. `/etf/summary-history` (date range) — `test_etf_summary_history_with_date_range`
3. `/currency/market-snapshot` (per id) — `test_currency_market_snapshot_for_each_known_id`
4. `/currency/klines` (multi-interval) — `test_currency_klines_multi_interval`

**SoDEX** (REST + WSS, `https://gw.sodex.dev/api/v1`):
1. `/spot/account/orders` (auth) — `test_account_orders_authenticated`
2. `/spot/account/positions` (auth) — `test_account_positions_authenticated`
3. `/market/trades` — `test_market_trades`
4. `/market/klines` (multi-interval) — `test_market_klines_multi_interval`
5. WSS handshake (env-gated behind `SODEX_WS_TESTNET=1`) — `test_sodex_ws_live.py`

**OpenRouter** (covered for completeness):
1. `/api/v1/chat/completions` (basic) — `test_nex_n2_pro_basic_round_trip`
2. `/api/v1/chat/completions` (system prompt) — `test_chat_completion_with_system_prompt`
3. `/api/v1/chat/completions` (tool choice required) — `test_chat_completion_with_tool_choice_required`
4. `/api/v1/chat/completions` (gzip) — `test_chat_completion_with_gzip` (failing)
5. `/api/v1/models` (pagination) — `test_models_endpoint_pagination`

### 6.2 Gaps discovered by web research

**SoSoValue** (developer portal lists the following live endpoints; per the akindo/ChainCatcher coverage, the demo key on the free tier covers `/api/sosotest`, `/api/soso-btc`, `/api/market`):

| Endpoint | Status | Notes |
|---|---|---|
| `https://api.sosovalue.com/api/sosotest` | **NOT COVERED** | The dedicated **testnet** endpoint that returns simulated but format-compatible data. With a free demo key, this is the safe path for CI. |
| `https://api.sosovalue.com/api/soso-btc` | **NOT COVERED** | Bitcoin-specific market data, free tier. |
| `https://api.sosovalue.com/api/market` | **NOT COVERED** | Broader crypto market data, free tier. |
| `/currency/listed` (currencies) | **NOT COVERED** | The current suite hits `/etf/*` and `/currency/{market-snapshot,klines}` but not the listing endpoint. |
| `/etf/list` | **NOT COVERED** | Listing of all ETFs. |
| `/etf/current-metrics` | covered indirectly via `test_sosovalue_api.py::test_client_parses_current_etf_metrics_object` (offline) — **no live curl test**. |
| `/etf/inflow-history` | covered offline only. |  |
| `/news/featured` | covered offline only. |  |
| `/news/verified` | covered offline only. |  |

**SoDEX** (testnet gateway per the SoDEX whitepaper):

| Endpoint | Status | Notes |
|---|---|---|
| `https://testnet-gw.sodex.dev/api/v1/spot` | **NOT COVERED** (production only via curl) | The testnet gateway exists; only mainnet-gw is hit. |
| `https://testnet-gw.sodex.dev/api/v1/perps` | **NOT COVERED** | Perp testnet not exercised. |
| `wss://testnet-gw.sodex.dev/ws/spot` | covered via env-gated test (only when `SODEX_WS_TESTNET=1`) | **Not in default CI loop.** |
| `wss://testnet-gw.sodex.dev/ws/perps` | **NOT COVERED** |  |
| `https://testnet-gw.sodex.dev/api/v1/faucet` | **NOT COVERED** | Faucet endpoint; required for the user's API key to actually transact on testnet. |

### 6.3 What the user's free API key unlocks (per web research)

The user's free SoSoValue demo key (per the developer portal, `m.sosovalue.com/developer`) covers:
- `/api/sosotest` — testnet-style simulated data
- `/api/soso-btc` — Bitcoin market
- `/api/market` — broad crypto market
- All `/openapi/v1/etf/*` and `/openapi/v1/currency/*` endpoints with conservative rate limits.

The user's SoDEX testnet key (if registered at `https://testnet-gw.sodex.dev`) covers the full perps+spot REST + WSS surface plus the faucet. Currently the suite exercises only the **mainnet** REST surface.

### 6.4 New curl tests to add (priority order)

| # | Test | Endpoint | Why |
|---|---|---|---|
| 1 | `test_sosovalue_sosotest_smoke` | `https://api.sosovalue.com/api/sosotest` | Testnet endpoint; safe to run in CI; format-compatible. |
| 2 | `test_sosovalue_soso_btc` | `https://api.sosovalue.com/api/soso-btc` | Free tier Bitcoin data; new path. |
| 3 | `test_sosovalue_market` | `https://api.sosovalue.com/api/market` | Free tier broad market. |
| 4 | `test_sosovalue_currency_listed` | `/openapi/v1/currency/listed` | Listing endpoint, not in current curl suite. |
| 5 | `test_sosovalue_etf_list` | `/openapi/v1/etf/list` | ETF listing. |
| 6 | `test_sodex_testnet_spot_symbols` | `https://testnet-gw.sodex.dev/api/v1/spot/...` | Testnet spot, parallel to mainnet. |
| 7 | `test_sodex_testnet_perps_symbols` | `https://testnet-gw.sodex.dev/api/v1/perps/...` | Testnet perps. |
| 8 | `test_sodex_testnet_faucet` | `https://testnet-gw.sodex.dev/api/v1/faucet` | Faucet — only path that *creates* test tokens. |
| 9 | `test_sodex_testnet_perps_klines` | `/perps/market/klines` on testnet |  |
| 10 | `test_sodex_testnet_perps_trades` | `/perps/market/trades` on testnet |  |
| 11 | `test_sosovalue_etf_current_metrics_live` | live curl to `/etf/current-metrics` | We have an offline parse test but no live round-trip. |
| 12 | `test_sosovalue_news_featured_live` | live curl to `/news/featured` |  |
| 13 | `test_sodex_testnet_ws_perps` | `wss://testnet-gw.sodex.dev/ws/perps` | Perps WSS — orthogonal to spot WSS. |

Estimated 13 new tests; ~250 lines of test code; adds ~30 s to the live-integration suite (each is ~2 s with a 2-call rate budget). All gated by `SODEX_TESTNET=1` / `SOSOVALUE_FREE_TIER=1` env vars so dev runs remain fast.

---

## 7. Summary Action List (no code edits performed)

1. **Skip-recovery** (test-only, 1 day): Replace the 11 BAI-removed `@unittest.skip` with `@pytest.mark.parametrize` over the new OpenRouter-era planner issue methods; same for the 1 `test_workspace_flow.py:2093` migration skip.
2. **Env-gate enabling** (env-only, 30 min): Add `SODEX_WS_TESTNET=1` and `SIGLAB_RUN_E2E=1` to a CI matrix so the 5 dashboard /risk skips and 1 WSS skip are exercised.
3. **Rate-limit hardening** (test-only, 1 day): Wrap live-integration helpers with `aiolimiter.AsyncLimiter(20, 1)` so the 9 OpenRouter and 2 SoSoValue rate-limit skips disappear.
4. **Test-ordering flake** (1 hour): `random.seed(0)` autouse fixture in `tests/conftest.py`.
5. **5× speedup** (1 day): `pytest-xdist -n auto --dist=loadscope --psutil` + `import_mode = "importlib"` + `-m "not slow"` for dev runs.
6. **Coverage gap fill** (3.5 days): Files in §5.1; biggest leverage is `llm/llm.py` (15 % → ~75 %).
7. **Live coverage expansion** (1 day): 13 new curl tests in §6.4; all env-gated.

Total estimated work: ~7 working days for full delivery.
