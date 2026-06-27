# Test Infrastructure

## Purpose

The SigLab test suite ensures quality and catches regressions across the entire codebase. It validates correctness of numerical computations (evaluator, risk, backtesting), data persistence (sessions, evidence, lineage), external integrations (SoSoValue, SoDEX), and cross-module workflows. Tests serve as executable specifications — each test doubles as documentation of expected behavior.

## Architecture

Tests are organized into five distinct categories:

| Category | Files | Purpose |
|----------|-------|---------|
| **Unit** | Most `test_*.py` files | Test individual modules in isolation with mocked dependencies |
| **Integration** | `test_e2e_integration.py`, `test_dashboard_*.py` | Cross-module flows spanning multiple subsystems |
| **Golden-file** | `test_golden_evaluator.py`, `golden/` | Byte-reproducible regression tests for the evaluator pipeline |
| (removed) | TUI tests have been removed |
|

All tests run under pytest with `pytest-asyncio` for async support and `pytest-timeout` for bounded execution.

## Test Files

### Core / Configuration

| File | Coverage |
|------|----------|
| `test_config.py` | SiglabConfig loading, validation, path resolution, env overrides |
| `test_repo_hygiene.py` | Repository hygiene checks (forbidden files, naming) |
| `test_gates.py` | Gate/condition evaluation |
| `test_hardening_profile.py` | Hardening profile application |
| `test_score.py` | Scoring utility |

### Evaluator / Research

| File | Coverage |
|------|----------|
| `test_evaluator_core.py` | Core evaluator logic — spec compilation, feature computation |
| `test_evaluator_compile.py` | Spec-to-strategy compilation pipeline |
| `test_evaluator_engine.py` | Evaluation engine orchestration |
| `test_evaluator_events.py` | Event-driven evaluator scenarios |
| `test_evaluator_backtesting.py` | Backtesting engine correctness |
| `test_golden_evaluator.py` | Golden-file regression (byte-identical hash) |
| `test_feature_dsl.py` | Feature DSL parsing and evaluation |
| `test_mutate_memory_packet.py` | Mutation and memory packet handling |
| `test_hypothesis_sandbox.py` | Hypothesis generation sandbox |
| `test_next_bar_bias.py` | Next-bar bias guard in backtester — ensures no look-ahead leakage |

### Data / Providers

| File | Coverage |
|------|----------|
| `test_data_store.py` | ParquetLake storage — write, read, schema evolution |
| `test_sosovalue_api.py` | SoSoValue API client |
| `test_sosovalue_capabilities.py` | SoSoValue capability discovery |
| `test_sodex_feeds.py` | SoDEX market data feeds (klines, funding, mark prices) |
| `test_sodex_client.py` | SoDEX REST client |
| `test_sodex_ws.py` | SoDEX WebSocket client |
| `test_sodex_rate_limit.py` | Rate limiting |
| `test_sodex_signing.py` | Request signing |
| `test_sodex_signed_client.py` | Signed client for private endpoints |
| `test_sodex_runtime_preflight.py` | Runtime preflight checks |
| `test_market_data_provider.py` | MarketDataProvider abstraction |
| `test_provider_utils.py` | Provider utility functions |
| `test_web_research.py` | Web research module |

### Live / Paper Trading

| File | Coverage |
|------|----------|
| `test_paper_client.py` | Paper trading client — orders, fills, persistence, cancellation |
| `test_promotion.py` | Strategy promotion eligibility and scoring |
| `test_reconciliation.py` | Paper-to-live reconciliation engine |
| `test_live_exporter.py` | Live data exporter |
| `test_cli_paper_promote.py` | CLI paper/promote commands |
| `test_pt_roll_forward.py` | Roll-forward logic |
| `test_directional_positions.py` | Directional position tracking |

### Risk

| File | Coverage |
|------|----------|
| `test_risk_guardian.py` | Risk guardian — composite scores, drawdown, correlation, concentration, alerts, position sizing |

### Dashboard

| File | Coverage |
|------|----------|
| `test_dashboard_runs.py` | Dashboard run display |
| `test_dashboard_risk_integration.py` | Risk metrics in dashboard |

### Orchestration / Workspace

| File | Coverage |
|------|----------|
| `test_orchestration_all.py` | Orchestration planner, writer, reflector runners |
| `test_workspace_flow.py` | Workspace lifecycle flows |
| `test_workspace_search.py` | Workspace search functionality |
| `test_lineage_memory.py` | Lineage tracking and memory |
| `test_evidence_store.py` | Evidence store operations |
| `test_canonical_run_artifact.py` | Canonical run artifact handling |
| `test_deterministic_archive.py` | Deterministic archive generation |

### LLM Integration

| File | Coverage |
|------|----------|
| `test_llm_claude.py` | Claude LLM integration |
| `test_llm_metadata.py` | LLM metadata handling |
| `test_llm_policy.py` | LLM policy enforcement |
| `test_kimi_tools.py` | Kimi tool integration |

### CLI

| File | Coverage |
|------|----------|
| `test_cli_agent_safety.py` | CLI agent safety guards |

### Telemetry

| File | Coverage |
|------|----------|
| `test_telemetry.py` | Telemetry recording and reporting |

### Benchmark

| File | Coverage |
|------|----------|
| `test_benchmark_deck.py` | Benchmark deck generation |
| `test_visualization.py` | Visualization output |

### E2E Integration

| File | Coverage |
|------|----------|
| `test_e2e_integration.py` | Cross-module flows: VAL-CROSS-001 through VAL-CROSS-008 |

### TUI (Mock)

| File | Coverage |
|------|----------|
| `test_tui_foundation.py` | TUI foundation — app launch, sidebar, navigation |
| `test_tui_market.py` | Market screen widgets |
| `test_tui_paper_trading.py` | Paper trading screen |
| `test_tui_risk_screen.py` | Risk screen |
| `test_tui_strategy.py` | Strategy screen |
| `test_tui_telemetry.py` | Telemetry screen |
| `test_tui_evidence.py` | Evidence screen |
| `test_tui_validation_contract.py` | TUI validation contracts |
| `test_tui_group_c_validation.py` | TUI Group C validation |
| `test_validation_tui_group_b.py` | TUI Group B validation |

### TUI (Tmux)

| File | Coverage |
|------|----------|
| `test_tui_tmux_hardening.py` | Deterministic tmux-based TUI tests |

## Fixtures

Shared fixtures live in `tests/conftest.py` and are imported by most test files.

### `sample_spec`

A minimal, deterministic `SignalSpec` with:
- Track: `trend_signals`
- Family: `perp_multi_asset_decision`
- Features: `price_return_24h`, `price_return_72h`, `ema_gap_12_26`, `funding_72h_mean`
- Universe: max 2 symbols, 21-day lookback, 1h interval
- Risk: max leverage 1.0

### `sample_spec_minimal`

An even simpler spec for path-level tests (single feature, no risk bounds).

### `mock_settings`

A `SiglabConfig`-style `MagicMock` with `root_dir` pointing to the real repository root. All config paths (SoSoValue config, strategy dir, data lake, artifacts, live dir, ancestry DB) are set to `tests/_data/` subdirectories.

### `deterministic_provider` (DeterministicMockProvider)

A `MarketDataProvider` stand-in that returns canned deterministic data:
- `discover_perp_symbols()` → `["BTC", "ETH"]`
- `fetch_perp_bundle()` → seeded random-walk price series (seed=42 for BTC, seed=99 for ETH) with constant funding rates
- All other methods raise `NotImplementedError` to fail fast on unexpected code paths

### `compute_evaluation_hash()`

A standalone helper function (not a pytest fixture) that computes a SHA-256 hash of `spec_hash` and `summary` fields. Defined in `conftest.py` under the "Helpers for golden-file hashing" section. Import and call directly; does not require fixture injection.

## Golden Files

Golden-file regression tests live in `tests/test_golden_evaluator.py` and store reference hashes in `tests/golden/`.

### How It Works

1. The `DeterministicMockProvider` feeds identical synthetic data every run.
2. `ResearchEvaluator.evaluate()` produces a result dict with `spec_hash` and `summary`.
3. `compute_evaluation_hash()` hashes these fields into a single SHA-256 hex digest.
4. The hash is compared against `tests/golden/evaluator_golden.txt`.
5. On first run (or after intentional changes), the test records the hash and skips. On subsequent runs, it asserts byte-identical match.

### Contract Assertions

- **VAL-EVAL-004**: Golden-file regression test passes (byte-identical hash across runs)
- **VAL-EVAL-008**: Backward compat after refactoring (same spec → same results)

### Updating Golden Files

Delete `tests/golden/evaluator_golden.txt` and re-run. The test records the new hash. Review the diff to confirm the change is intentional.

## TUI Tests

TUI screen and widget tests (in `test_tui_*.py`, excluding `test_tui_tmux_hardening.py`) use mock app contexts rather than actual terminal rendering.

### Mock Pattern

Tests create a `Textual` app instance in headless mode, push the relevant screen, and assert widget state programmatically. This allows fast execution without requiring a terminal or display server.

### Coverage

Each TUI screen has dedicated test files covering:
- Widget rendering and layout
- Input handling (search, form submission)
- Data display correctness
- Navigation between screens
- Error state display
- Validation contracts

## Tmux Hardening Tests

`test_tui_tmux_hardening.py` provides deterministic end-to-end TUI testing by driving the actual TUI process through `tmux`.

### Pattern

1. `TmuxTUI` context manager creates a tmux session at a fixed size (default 120×40).
2. `tmux send-keys` sends keystrokes to the TUI process.
3. `tmux capture-pane -p -J` captures rendered output with ANSI stripping.
4. All tests use keyword matching against known screen content.

### Key Classes

- **`TmuxTUI`**: Context manager wrapping tmux session lifecycle (create, interact, capture, kill)
- **`pop_to_base()`**: Pops all pushed screens via Escape to reach the sidebar layout
- **`switch_screen(screen)`**: Navigates to a numbered screen (1–6) from base layout

### Test Categories

| Class | What it tests |
|-------|---------------|
| `TestAppLaunch` | Market screen renders, search visible, symbol column, process alive, status bar |
| `TestBaseLayout` | Sidebar title after Escape, nav items visible (1–6) |
| `TestScreenSwitching` | Keys 1–6 switch to correct screens, cycling all 6, no crash |
| `TestHelpOverlay` | F1 opens help, Escape dismisses, accessible from multiple screens |
| `TestSearchInput` | Typed text appears in search, placeholder visible |
| `TestDataRefresh` | `r` key triggers refresh, rapid refreshes stable |
| `TestErrorStates` | Graceful handling when API unreachable, navigation still works |
| `TestResizeBehavior` | 80/120/160 columns, rapid resize, all screens at each width |
| `TestKeyboardNavigation` | Escape pops, push/pop cycle, j/k navigation |
| `TestDeterminism` | 3 consecutive runs produce identical output for market, help, paper, base layout |

### Timing Constants

| Constant | Value | Purpose |
|----------|-------|---------|
| `_SETTLE_SECS` | 4.0s | Initial TUI render wait |
| `_NAVIGATE_SECS` | 2.5s | Screen switch wait |
| `_RESIZE_SECS` | 2.0s | Resize re-render wait |
| `_OVERLAY_SECS` | 1.5s | Help overlay wait |

## E2E Integration Tests

`test_e2e_integration.py` validates cross-module flows spanning multiple subsystems. Each test is isolated and uses deterministic data.

### Validated Flows

| ID | Flow | Modules |
|----|------|---------|
| VAL-CROSS-001 | SoDEX klines → backtest → paper trade → promote → reconciliation | SoDEX, evaluator, paper, promotion, reconciliation |
| VAL-CROSS-002 | SoSoValue market data → evaluation → paper trading → dashboard | SoSoValue, evaluator, paper, dashboard |
| VAL-CROSS-003 | CLI paper commands → paper sessions → dashboard display | CLI, paper, dashboard |
| VAL-CROSS-006 | Paper trading → risk scoring → dashboard display | Paper, risk, dashboard |
| VAL-CROSS-007 | Research → evaluate → paper trade flow | Research, evaluator, paper |
| VAL-CROSS-008 | SoDEX API failure → graceful degradation | SoDEX, all consumers |

### Test Helpers

- `mock_feeds`: Mocked `SoDEXFeeds` with canned kline data
- `tmp_sessions_dir`: Temporary directory for paper session `.npy` files
- `_make_kline_data()`: Deterministic kline generator (seeded RNG)
- `_create_minimal_config()`: `SiglabConfig` pointing to temp directories
- `_create_dashboard_app_with_config()`: FastAPI `TestClient` for dashboard
- `_run_cli()`: Subprocess runner for CLI commands

## Coverage

The test suite contains **2,460 test functions** across **65 test files**.

### By Area

| Area | Test Count | Files |
|------|-----------|-------|
| Evaluator/Research | ~524 | 9 files |
| TUI (mock) | ~614 | 10 files |
| TUI (tmux) | 54 | 1 file |
| LLM Integration | ~300 | 4 files |
| Orchestration/Workspace | ~305 | 7 files |
| Paper Trading | ~120 | 5 files |
| Risk | 73 | 1 file |
| Data/Providers | ~270 | 13 files |
| E2E Integration | 38 | 1 file |
| CLI | 35 | 1 file |
| Config/Misc | ~76 | 8 files |

## Running Tests

### Full Suite

```bash
python3 -m pytest -q
```

### Specific File

```bash
python3 -m pytest tests/test_risk_guardian.py -v
```

### By Marker

```bash
# Integration tests (make real API calls or CLI subprocesses)
python3 -m pytest -m integration -v

# Tmux-based TUI tests (require tmux)
python3 -m pytest -m tmux -v

# Async tests
python3 -m pytest -m asyncio -v
```

### With Timeout

Tests use `pytest-timeout` to prevent hangs. The default timeout applies; specific tests may override it.

### Async Tests

All async tests use `pytest-asyncio` with `asyncio_mode = "auto"` (configured in `pyproject.toml`). Async test functions are marked with `@pytest.mark.asyncio` and run automatically.

### Parallel Execution

The suite supports parallel execution via `pytest-xdist` if installed:
```bash
python3 -m pytest -n auto -q
```

### Pytest Configuration (pyproject.toml)

```toml
[tool.pytest.ini_options]
markers = [
    "integration: marks tests that make real API calls or run CLI subprocesses",
    "asyncio: marks async test cases",
    "tmux: marks tmux-based TUI tests that spawn terminal sessions",
]
asyncio_mode = "auto"
```

## Known Issues

### Pre-existing Failures

- Some tmux tests may fail in environments without `tmux` installed or with display-server constraints
- Integration-marked tests require network access and valid API keys
- Golden-file tests will fail if `tests/golden/evaluator_golden.txt` is missing (first run records the hash)

### Flaky Tests

- **Tmux hardening tests**: Timing-dependent (uses `time.sleep` for settling). Increasing `_SETTLE_SECS` or `_NAVIGATE_SECS` may help in slow CI environments.
- **Network-dependent tests**: Tests hitting real SoSoValue or SoDEX APIs may fail due to rate limits or outages. Use the `integration` marker to run them selectively.

### Coverage Gaps

- Live signed SoDEX execution is not tested (by design — AGENTS.md prohibits claiming live integration without validation)
- B.AI Credits cost enforcement has no hard tests
- SoDEX private/account stream tests are limited to preflight checks

## Cross-Module Coverage

Tests span the full SigLab stack:

- **CLI** — `test_cli_agent_safety.py` validates commands, flags, JSON output, agent-safety guards
- **TUI** — `test_tui_*.py` (mock + tmux) cover all six screens, widgets, navigation, input handling
- **Dashboard** — `test_dashboard_runs.py`, `test_dashboard_risk_integration.py` cover FastAPI endpoints and risk display
- **Evaluation** — `test_evaluator_*.py`, `test_golden_evaluator.py` cover compilation, backtesting, regression
- **Paper Trading** — `test_paper_client.py`, `test_promotion.py`, `test_reconciliation.py` cover paper-to-live pipeline
- **Risk** — `test_risk_guardian.py` covers composite scoring, drawdown, correlation, alerts
- **E2E** — `test_e2e_integration.py` validates cross-module flows (VAL-CROSS-001 through 008)

## Adding Tests

### Follow Existing Patterns

1. **File naming**: `test_<module_name>.py` in the `tests/` directory
2. **Class grouping**: Group related tests into classes with descriptive names
3. **Async tests**: Mark with `@pytest.mark.asyncio` (or rely on `asyncio_mode = "auto"`)
4. **Use shared fixtures**: Import from `conftest.py` — `sample_spec`, `mock_settings`, `deterministic_provider`, `compute_evaluation_hash`
5. **Deterministic data**: Use seeded RNG (`np.random.default_rng(seed)`) for reproducible test data
6. **Contract IDs**: Reference VAL-* assertion IDs in docstrings (e.g., `VAL-PAPER-001`)

### Example

```python
"""
Tests for <module>.

Covers:
- VAL-<AREA>-<NNN>: <assertion description>
"""

import pytest
from conftest import REPO_ROOT, DeterministicMockProvider

class TestFeatureName:
    """VAL-<AREA>-<NNN>: Description."""

    def test_known_input_produces_expected_output(self) -> None:
        result = my_function(input_data)
        assert result == expected_value

    @pytest.mark.asyncio
    async def test_async_operation(self, mock_settings) -> None:
        provider = DeterministicMockProvider()
        result = await async_operation(provider)
        assert result["key"] == "value"
```

### Validation Checklist

Before adding tests, verify:

- [ ] Test file follows `test_<module>.py` naming
- [ ] Test class/docstring references a VAL-* assertion ID
- [ ] Shared fixtures from `conftest.py` are reused (no duplicate setup)
- [ ] Test data is deterministic (seeded RNG, no `time.time()`, no random UUIDs)
- [ ] Async tests use `@pytest.mark.asyncio`
- [ ] TUI mock tests use headless app contexts
- [ ] TUI tmux tests use the `TmuxTUI` helper and `@pytest.mark.tmux`
- [ ] Golden-file tests: if modifying evaluator output, update `tests/golden/evaluator_golden.txt`
