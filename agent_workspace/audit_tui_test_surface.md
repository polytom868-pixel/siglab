# SigLab TUI Test Surface — Surgical Audit

**Scope:** 19 owned test files. **Status:** read-only audit. No tests run.

**Files in the owned list that do not exist on disk:**
- `tests/test_tui_app.py` — not present
- `tests/test_tui_screens.py` — not present
- `tests/test_tui_widgets.py` — not present
- `tests/test_tui_loading.py` — not present

**Files audited (15 existing files):**
`test_dashboard_risk_integration.py`, `test_dashboard_runs.py`, `test_tui_api_client.py`, `test_tui_data_views.py`, `test_tui_evidence.py`, `test_tui_formatting.py`, `test_tui_foundation.py`, `test_tui_group_c_validation.py`, `test_tui_market.py`, `test_tui_paper_trading.py`, `test_tui_risk_screen.py`, `test_tui_strategy.py`, `test_tui_telemetry.py`, `test_tui_validation_contract.py`, `test_validation_tui_group_b.py`.

---

## 1. TEST INVENTORY

### 1.1 tests/test_dashboard_risk_integration.py
- 3 pytest classes; 14 test functions; no `pytest.mark.*` markers.
- Imports: `tempfile`, `numpy as np`, `fastapi.testclient.TestClient`, `siglab.config.SiglabConfig`, `siglab.dashboard.app.{DashboardState, WebSocketManager, create_app}`, `siglab.dashboard.routes._compute_risk_metrics` (test_dashboard_risk_integration.py:14-22).
- `TestRiskEndpoint` (test_dashboard_risk_integration.py:45) — exercises `GET /risk` via FastAPI TestClient with `tempfile.TemporaryDirectory` containing fake `.npy` paper session files. 7 tests cover null/no-config, with sessions, single session no correlation, with correlation, and direct call to `_compute_risk_metrics`.
- `TestRiskWebSocket` (test_dashboard_risk_integration.py:241) — exercises `WS /ws` with `client.websocket_connect`, sends `subscribe risk_score` and `get_risk` actions, asserts message fields. 6 tests.
- `TestRiskFullFlow` (test_dashboard_risk_integration.py:432) — single test that asserts expected JSON structure and ranges. 1 test.

### 1.2 tests/test_dashboard_runs.py
- 1 `unittest.TestCase` class; 7 test methods; no markers (test_dashboard_runs.py:35).
- Imports: `unittest`, `types.SimpleNamespace`, `siglab.dashboard.server.DashboardApp`, `siglab.schemas.SignalSpec`, `siglab.search.lineage.LineageStore` (test_dashboard_runs.py:1-11).
- Tests construct `LineageStore` in a temp dir, record evaluations, build `DashboardApp`, call `.experiments_payload()` and `.runs_payload()` directly, and assert dict fields (test_dashboard_runs.py:36-575). No HTTP, no WebSocket, no running server.

### 1.3 tests/test_tui_api_client.py
- 2 pytest classes; 9 test functions; all async with `@pytest.mark.asyncio`.
- Imports: `asyncio`, `unittest.mock.{AsyncMock, MagicMock, patch}`, `httpx`, `siglab.tui.api_client.TuiApiClient` (test_tui_api_client.py:5-11).
- `TestRequestWithRetry` (test_tui_api_client.py:30) — patches `httpx.AsyncClient.get` and `asyncio.sleep` to exercise `_request_with_retry`. 7 tests cover success, connect error retry, 5xx retry, 4xx no-retry, timeout retry.
- `TestGetPostHelpers` (test_tui_api_client.py:158) — exercises `_get` and `_post` via `httpx.AsyncClient.{get,post}` patches. 3 tests.

### 1.4 tests/test_tui_data_views.py
- 12 pytest classes; 20 test functions; no markers. `pytest` imported but only used for `pytest.raises` (test_tui_data_views.py:1-20).
- Imports: `siglab.tui.data_views.{KlineView, TickerView, OrderView, PositionView, PnlSnapshot, SymbolEntry, OrderBookView, GraphNode, GraphEdge, StrategyEntry, RiskSnapshot, closes_from_klines}` (test_tui_data_views.py:7-20).
- Each class: 1–3 tests of `from_dict`, defaults, immutability (`test_frozen` uses `pytest.raises((AttributeError, TypeError, Exception))` at test_tui_data_views.py:63). No mocking.

### 1.5 tests/test_tui_evidence.py
- 7 pytest classes; 48 test functions; some `@pytest.mark.asyncio`.
- Imports: `unittest.mock.{AsyncMock, MagicMock, patch}`, `siglab.tui.formatting.format_confidence`, `siglab.tui.screens.evidence.{DEMO_STEPS, DemoFlowWidget, EdgeDetailWidget, EvidenceGraphWidget, EvidenceScreen, _kind_icon, _kind_style}` (test_tui_evidence.py:8-22).
- `TestDemoSteps` (test_tui_evidence.py:28) — 4 tests on `DEMO_STEPS` constants.
- `TestHelpers` (test_tui_evidence.py:55) — 9 tests on `_kind_icon`, `_kind_style`, `format_confidence`.
- `TestEvidenceGraphWidget` (test_tui_evidence.py:93) — 8 tests: init, focus, `update_graph`, filter, render, group-by-kind.
- `TestEdgeDetailWidget` (test_tui_evidence.py:188) — 5 tests.
- `TestDemoFlowWidget` (test_tui_evidence.py:247) — 14 tests: navigation, set_step_result, set_running, render with result, fail, run.
- `TestEvidenceScreenRegistration` (test_tui_evidence.py:349) — 4 tests: import, registry, bindings, CSS.
- `TestEvidenceApiIntegration` (test_tui_evidence.py:381) — 1 test: `hasattr(client, "get_evidence_graph")`.

### 1.6 tests/test_tui_formatting.py
- 16 pytest classes; 59 test functions; no markers.
- Imports: `math`, `pytest`, `rich.text.Text`, and 19 names from `siglab.tui.formatting` (test_tui_formatting.py:4-32). `truncate` is imported twice (line 24 and line 31).
- One class per helper: `format_price`, `format_pnl`, `format_return`, `safe_float`, `truncate`, `format_change`, `format_volume`, `format_score`, `format_count`, `format_date`, `gauge_color`, `bar_gauge`, `compact_qty`, `sanitize_status_text`, `side_style`, `severity_color`.

### 1.7 tests/test_tui_foundation.py
- 13 pytest classes; 82 test functions; some `@pytest.mark.asyncio`.
- Imports: `httpx`, `unittest.mock.{AsyncMock, MagicMock, patch}`, `siglab.tui.api_client.TuiApiClient`, `siglab.tui.app.{NAV_ITEMS, SCREEN_IDS, SCREEN_NAMES, HelpScreen, NavSidebar, PlaceholderScreen, SigLabTUI}`, `siglab.tui.cli_bridge.{CliResult, run_cli}`, `siglab.tui.widgets.status_bar.SigLabStatusBar` (test_tui_foundation.py:8-25).
- `TestNavConstants` (test_tui_foundation.py:31) — 5 tests.
- `TestSigLabTUIApp` (test_tui_foundation.py:61) — 7 tests on TITLE, SUB_TITLE, CSS_PATH, SCREENS, BINDINGS, instantiation.
- `TestPlaceholderScreen` (test_tui_foundation.py:101) — 2 tests.
- `TestHelpScreen` (test_tui_foundation.py:117) — 5 tests.
- `TestTuiApiClient` (test_tui_foundation.py:153) — 8 tests including `test_run_cli_with_args` (test_tui_foundation.py:311) which spawns a real CLI subprocess via `run_cli("--help")` — see Section 4.
- `TestCliBridge` (test_tui_foundation.py:294) — 3 tests.
- `TestSigLabStatusBar` (test_tui_foundation.py:321) — 3 tests.
- `TestAppCompose` (test_tui_foundation.py:347) — 5 tests on lifecycle and screen switch actions; `test_all_screens_have_ctrl_c_binding` (test_tui_foundation.py:376) imports all 6 screens and asserts bindings.
- `TestNavSidebar` (test_tui_foundation.py:406) — 4 tests.
- `TestThemeSystem` (test_tui_foundation.py:428) — 6 tests reading `app.tcss` and `theme.tcss`.
- `TestModuleStructure` (test_tui_foundation.py:487) — 5 tests on `__init__.py` files and exports.
- `TestFormatting` (test_tui_foundation.py:522) — 15 tests re-importing `siglab.tui.formatting` and asserting color constants and outputs.
- `TestLoadingIndicator` (test_tui_foundation.py:616) — 3 tests.

### 1.8 tests/test_tui_group_c_validation.py
- 5 pytest classes; 148 test functions; some `@pytest.mark.asyncio`.
- Imports: `colorsys`, `re`, `unittest.mock.{AsyncMock, MagicMock}`, `httpx`, `pytest`, `siglab.tui.formatting` (10 names), `siglab.tui.loading.LoadingIndicator`, all 6 screen modules, `siglab.tui.widgets.sparkline.sparkline_text` (test_tui_group_c_validation.py:1-79).
- Helpers `_hex_to_rgb`, `_relative_luminance`, `_contrast_ratio` (lines 85-110) implement WCAG 2.1 luminance math used by the design-polish tests.
- `TestVAL_TUI_005_RiskMetrics` (test_tui_group_c_validation.py:254) — ~32 tests on `RiskGaugeWidget`, `DrawdownSparklineWidget`, `CorrelationHeatmapWidget`, `AlertStreamWidget`, `RiskScreen`, `TuiApiClient.get_risk`.
- `TestVAL_TUI_006_StrategyResearch` (test_tui_group_c_validation.py:522) — ~23 tests on list/filter/eval/results/comparison; uses `open(strat_mod.__file__).read()` (test_tui_group_c_validation.py:679) to grep for `"run_cli"`, `"ancestry"`, `"benchmark-eval"` strings — these are source-grep tests, not behavior tests.
- `TestVAL_TUI_007_TelemetryBrowser` (test_tui_group_c_validation.py:701) — ~21 tests on `TelemetryRunListWidget`, `ProviderMetricsWidget`, `ToolUsageWidget`, `RunDetailWidget`, `RunComparisonWidget`, `ServiceHealthWidget`, `TelemetryScreen`; also source-greps `"run_cli"`, `"telemetry-report"`, `"ancestry"`.
- `TestVAL_TUI_008_EvidenceGraphDemo` (test_tui_group_c_validation.py:912) — ~32 tests covering graph, edge detail, demo flow, screen bindings.
- `TestVAL_TUI_010_DesignPolish` (test_tui_group_c_validation.py:1162) — ~40 tests on color constants, semantic aliases, WCAG contrast ratios ≥ 4.5/3.0, keyboard navigation, loading indicator braille spinner, CSS file existence.

### 1.9 tests/test_tui_market.py
- 9 pytest classes; 69 test functions; 6 marked `@pytest.mark.asyncio`.
- Imports: `unittest.mock.{AsyncMock, MagicMock, patch}`, `httpx`, `pytest`, `TuiApiClient`, `format_change`, `format_price`, `format_volume`, market screen widgets, `SparklineWidget`, `ohlc_summary`, `sparkline_text` (test_tui_market.py:7-23).
- Classes: `TestFormatHelpers`, `TestSparkline`, `TestSymbolListWidget`, `TestKlinesChartWidget`, `TestTickerTableWidget`, `TestOrderBookWidget`, `TestMarketScreen`, `TestApiClientMarketMethods`, `TestModuleStructure`.

### 1.10 tests/test_tui_paper_trading.py
- 11 pytest classes; 63 test functions; 4 marked `@pytest.mark.asyncio`.
- Imports: `time`, `unittest.mock.{AsyncMock, MagicMock, patch}`, `pytest`, `format_pnl`, `format_price`, paper screen widgets, `_TextInputScreen` (test_tui_paper_trading.py:9-24).
- `TestFormattingHelpers` (test_tui_paper_trading.py:30) — 9 tests.
- `TestPositionsTableWidget` (test_tui_paper_trading.py:71) — 4 tests.
- `TestAccountSummaryWidget` (test_tui_paper_trading.py:120) — 3 tests.
- `TestPnlChartWidget` (test_tui_paper_trading.py:155) — 3 tests.
- `TestOrderFormWidget` (test_tui_paper_trading.py:180) — 19 tests on init, setters, toggles, validation (`get_order_params` returns None on bad input).
- `TestOrderHistoryWidget` (test_tui_paper_trading.py:332) — 5 tests.
- `TestPaperScreen` (test_tui_paper_trading.py:431) — 10 structural tests using `hasattr`.
- `TestTextInputScreen` (test_tui_paper_trading.py:475) — 2 tests.
- `TestPaperScreenIntegration` (test_tui_paper_trading.py:491) — 4 async tests, all patching `siglab.tui.screens.paper.run_cli` to return `MagicMock(returncode=0, stdout=...)`; `test_refresh_all_no_session` (test_tui_paper_trading.py:530) calls `await screen._refresh_all()` and asserts "Should not raise" — see Section 2.
- `TestPaperScreenCSS` (test_tui_paper_trading.py:575) — 2 tests reading `app.tcss`.
- `TestPaperModuleExports` (test_tui_paper_trading.py:607) — 2 tests.

### 1.11 tests/test_tui_risk_screen.py
- 9 pytest classes; 46 test functions; 2 marked `@pytest.mark.asyncio`.
- Imports: `pytest`, risk screen widgets, `_correlation_block`, `_correlation_color`, `gauge_color`, `severity_color` (test_tui_risk_screen.py:13-26).
- `TestGaugeColor` (test_tui_risk_screen.py:32) — 8 tests including NaN and boundary cases.
- `TestSeverityColor` (test_tui_risk_screen.py:60) — 5 tests.
- `TestCorrelationColor` (test_tui_risk_screen.py:80) — 6 tests.
- `TestCorrelationBlock` (test_tui_risk_screen.py:103) — 6 tests.
- `TestRiskGaugeWidget` (test_tui_risk_screen.py:128) — 4 tests.
- `TestDrawdownSparklineWidget` (test_tui_risk_screen.py:164) — 3 tests.
- `TestCorrelationHeatmapWidget` (test_tui_risk_screen.py:198) — 4 tests.
- `TestAlertStreamWidget` (test_tui_risk_screen.py:241) — 3 tests.
- `TestRiskScreen` (test_tui_risk_screen.py:293) — 5 structural tests; `test_alert_filter_cycle` (test_tui_risk_screen.py:322) does NOT call `action_filter_alerts` — it manually computes `cycle[(current + 1) % len(cycle)]` and asserts — see Section 2.
- Two module-level `@pytest.mark.asyncio` tests (test_tui_risk_screen.py:343, 373) that mount the screen in a real `App.run_test()` pilot.

### 1.12 tests/test_tui_strategy.py
- 7 pytest classes; 71 test functions; 4 marked `@pytest.mark.asyncio`.
- Imports: `unittest.mock.{AsyncMock, MagicMock}`, `pytest`, `TuiApiClient`, 5 formatting helpers, strategy screen widgets (test_tui_strategy.py:8-27).
- `TestFormatHelpers` (test_tui_strategy.py:81) — 21 tests on score/return/sharpe/drawdown/status/truncate.
- `TestStrategyListWidget` (test_tui_strategy.py:192) — 16 tests including set/get/filter/nav/select/render.
- `TestResultsTableWidget` (test_tui_strategy.py:335) — 7 tests.
- `TestComparisonPanelWidget` (test_tui_strategy.py:413) — 6 tests.
- `TestStrategyScreen` (test_tui_strategy.py:466) — 8 structural tests.
- `TestTuiApiClientStrategies` (test_tui_strategy.py:521) — 4 async tests patching `client._client` with AsyncMock.
- `TestStrategyRegistration` (test_tui_strategy.py:591) — 5 tests.

### 1.13 tests/test_tui_telemetry.py
- 10 pytest classes; 96 test functions; 3 marked `@pytest.mark.asyncio`.
- Imports: `unittest.mock.{AsyncMock, MagicMock}`, `pytest`, `TuiApiClient`, 8 formatting helpers, telemetry screen widgets (test_tui_telemetry.py:8-32).
- `TestFormatHelpers` (test_tui_telemetry.py:127) — 27 tests.
- `TestTelemetryRunListWidget` (test_tui_telemetry.py:261) — 16 tests.
- `TestProviderMetricsWidget` (test_tui_telemetry.py:412) — 6 tests.
- `TestToolUsageWidget` (test_tui_telemetry.py:472) — 6 tests.
- `TestRunDetailWidget` (test_tui_telemetry.py:524) — 4 tests.
- `TestRunComparisonWidget` (test_tui_telemetry.py:565) — 6 tests.
- `TestServiceHealthWidget` (test_tui_telemetry.py:614) — 5 tests.
- `TestTelemetryScreen` (test_tui_telemetry.py:662) — 8 tests on bindings/reactive state/filter sets.
- `TestTuiApiClientTelemetry` (test_tui_telemetry.py:730) — 3 async tests with `client._client = mock_http`.
- `TestTelemetryRegistration` (test_tui_telemetry.py:797) — 6 tests.

### 1.14 tests/test_tui_validation_contract.py
- 3 pytest classes; 65 test functions; some `@pytest.mark.asyncio`.
- Imports: `asyncio`, `json`, `os`, `subprocess`, `sys`, `unittest.mock.{AsyncMock, MagicMock, patch}`, `httpx`, `pytest`, `NAV_ITEMS`, `SCREEN_IDS`, `SigLabTUI`, `friendly_error`, `LoadingIndicator`, `SigLabStatusBar` (test_tui_validation_contract.py:14-28).
- `TestVAL_TUI_001_ScaffoldLaunchesAndNavigates` (test_tui_validation_contract.py:34) — 18 tests: 13 module-level (NAV_ITEMS, screen IDs, compose, CSS_PATH) and 5 `@pytest.mark.asyncio` pilot tests using `async with SigLabTUI().run_test() as pilot` and `await pilot.press(key)`.
- `TestVAL_TUI_002_CLICommandsRenderRich` (test_tui_validation_contract.py:169) — 10 tests using `_run_cli` (test_tui_validation_contract.py:172) which calls `subprocess.run(["poetry", "run", "python3", "-m", "siglab.cli"] + args, ...)` with `cwd="/home/eya/soso/siglab"` and `timeout=30` — see Section 4.
- `TestVAL_TUI_009_TUIHardening` (test_tui_validation_contract.py:264) — 30+ tests: keyboard bindings, `friendly_error` mapping (ConnectError → "connect" or "server"; TimeoutException → "timeout"; 500 → "500" or "server"; 401 → "auth"; 429 → "rate"; ValueError → "unexpected" and not "ValueError"), LoadingIndicator, StatusBar, CSS responsive, API connection reactive, pilot tests for help, F1, resize.

### 1.15 tests/test_validation_tui_group_b.py
- 12 pytest classes; 63 test functions; some `@pytest.mark.asyncio`.
- Imports: `json`, `time`, `unittest.mock.{AsyncMock, MagicMock, patch}`, `pytest`, `TuiApiClient`, market and paper screen widgets (test_validation_tui_group_b.py:11-33).
- Two validation blocks: `VAL-TUI-003` (market) and `VAL-TUI-004` (paper trading). 6 classes for market (SymbolList/KlinesChart/TickerTable/OrderBook/AutoRefresh/Integration), 6 for paper (Positions/OrderForm/OrderPlacement/OrderHistory/PnL/Integration).
- `TestVAL_TUI_003_Integration` (test_validation_tui_group_b.py:389) — 3 async tests using `asyncio.new_event_loop()` to run `loop.run_until_complete(screen._fetch_tickers())` (test_validation_tui_group_b.py:146-150) and `await screen._refresh_all()`. `screen.query_one` is replaced with a `mock_query` dict-based function.
- `TestVAL_TUI_004_OrderPlacementFlow` (test_validation_tui_group_b.py:658) — 3 async tests; `test_place_order_success` (test_validation_tui_group_b.py:661) patches `asyncio.create_subprocess_exec` and `asyncio.wait_for`; `test_place_order_failure_shows_error` (test_validation_tui_group_b.py:721) also patches these — see Section 4.
- `TestVAL_TUI_004_PnlUpdates.test_pnl_history_accumulates_on_multiple_refreshes` (test_validation_tui_group_b.py:935) calls `await screen._refresh_all()` twice and asserts `len(screen._pnl_history) == 2` and `screen._pnl_history[0] == 100.0`, `screen._pnl_history[1] == 200.0`.
- `TestVAL_TUI_004_Integration.test_init_session_and_refresh` (test_validation_tui_group_b.py:987) — patches `siglab.tui.screens.paper.run_cli` with a counter-based side effect that returns different `MagicMock` results on first vs subsequent calls.
- `TestVAL_TUI_004_Integration.test_screen_keyboard_bindings_complete` (test_validation_tui_group_b.py:1044) asserts `["escape", "r", "s", "b", "t", "enter", "n"]` ⊂ binding keys.
- `TestVAL_TUI_004_Integration.test_screen_has_all_compose_widgets` (test_validation_tui_group_b.py:1051) uses `inspect.getsource(PaperScreen.compose)` and asserts the source string contains `OrderFormWidget`, `AccountSummaryWidget`, `PnlChartWidget`, `PositionsTableWidget`, `OrderHistoryWidget`, `LoadingIndicator` — source-grep test, not behavior test.

### 1.16 Cross-file usage map
- `TuiApiClient` instantiated/imported in: test_tui_api_client.py:11, test_tui_evidence.py:386, test_tui_foundation.py:14, test_tui_group_c_validation.py:500,1136, test_tui_market.py:14,433, test_tui_strategy.py:13, test_tui_telemetry.py:13, test_validation_tui_group_b.py:18.
- `siglab.tui.app.{NAV_ITEMS, SCREEN_IDS, SigLabTUI}` imported in test_tui_foundation.py:15-22, test_tui_group_c_validation.py:1333,1360,1467, test_tui_strategy.py:594, test_tui_telemetry.py:801,805,810,826, test_tui_validation_contract.py:25, test_tui_evidence.py:356, test_tui_paper_trading.py:616.
- `app.tcss` read in test_tui_evidence.py:372, test_tui_foundation.py:434, test_tui_group_c_validation.py:1452, test_tui_paper_trading.py:580,597, test_tui_strategy.py:609, test_tui_telemetry.py:815.
- `format_confidence` imported only in test_tui_evidence.py:13.
- `run_cli` (cli_bridge) imported and patched in test_tui_paper_trading.py:503,521,567, test_validation_tui_group_b.py:926,972,975,1038.

---

## 2. ASSERTION QUALITY

**Tautology / "does not raise" only (no positive value asserted):**
- test_tui_paper_trading.py:530-534 `test_refresh_all_no_session` — calls `await screen._refresh_all()` with no `assert` at all; the only assertion is in the docstring "Should not raise". No positive behavior is verified.
- test_tui_validation_contract.py:140-146 `test_pilot_screen_switching_via_number_keys` — loops `for key in ["1", "2", "3", "4", "5", "6"]: await pilot.press(key); await pilot.pause()` with no `assert`. The comment "If we got here without error, all switches worked" is the only signal.
- test_tui_validation_contract.py:148-156 `test_pilot_screen_switching_via_nav_keys` — same pattern, no assert.
- test_tui_validation_contract.py:520-525 `test_pilot_number_key_screen_navigation` — same pattern, no assert.
- test_tui_risk_screen.py:322-331 `test_alert_filter_cycle` — does NOT call `screen.action_filter_alerts()`; it inlines `cycle.index(screen._filter_severity)` and `cycle[(current + 1) % len(cycle)]` and asserts `next_sev == "critical"`. This tests a Python list comprehension, not `action_filter_alerts` behavior.
- test_tui_market.py:349-356 `test_render_max_20_tickers` — passes 50 tickers and renders; the only "assertion" is the implicit `# Should not crash` comment. No actual property is checked.
- test_tui_group_c_validation.py:1066-1071 `test_demo_flow_navigation_backward` — sets `widget._current_step = 3`, calls `widget.retreat_step()`, asserts `widget.current_step == 2`. This is consistent with the SUT — but the test does not exercise the boundary `widget._current_step == 1`.
- test_tui_market.py:432-436 `test_screen_init_with_custom_api` and test_tui_market.py:438-441 `test_screen_init_default_api` — only verify the constructor wires `_api` and `_owns_api`. No fetch or render.

**Mocking the SUT or its collaborators wholesale:**
- test_tui_paper_trading.py:503-510 `_init_session_success`: patches `run_cli` to a fixed `MagicMock(returncode=0, stdout='{"session_id":...}')` and `screen.query_one` to `MagicMock(side_effect=Exception("not mounted"))`. The SUT is `PaperScreen._init_session`, and every dependency is mocked. The test verifies the field assignment path, not the actual `run_cli` invocation shape.
- test_validation_tui_group_b.py:126-156 `test_market_screen_fetch_tickers_populates_symbols`: replaces `screen.query_one` and patches `screen._api.get_market_tickers`. The SUT is `MarketScreen._fetch_tickers`; both collaborators are mocked.
- test_validation_tui_group_b.py:661-698 `test_place_order_success`: patches `asyncio.create_subprocess_exec`, `asyncio.wait_for`, and `screen._refresh_all` — the SUT is `PaperScreen._place_order` and all three real code paths are stubbed.
- test_tui_evidence.py:107-115 `test_widget_update_graph`: directly calls `widget.update_graph(nodes, edges)` and asserts the internal state. No mocked SUT, but the test asserts an internal tuple length, not a rendered output.

**Tautology / structural-attribute assertions that don't test behavior:**
- test_tui_market.py:416-417 `test_screen_class_exists`: `assert MarketScreen is not None` — passes if the import succeeded.
- test_tui_market.py:419-426 `test_screen_has_bindings`: asserts `["escape", "/", "j", "k", "enter", "r"]` in binding keys. Tests class definition, not runtime.
- test_tui_market.py:443-461 `test_screen_has_*` — 4 tests, each `assert hasattr(...)` with no functional exercise.
- test_tui_paper_trading.py:434-470 `TestPaperScreen` — 9 of 10 tests are pure `hasattr` checks.
- test_tui_strategy.py:469-513 `TestStrategyScreen` — 7 of 8 tests are `hasattr`/`BINDINGS` checks.
- test_tui_telemetry.py:665-723 `TestTelemetryScreen` — 7 of 8 tests are structural.
- test_tui_evidence.py:355-376 `TestEvidenceScreenRegistration` — all 4 are registry/binding/CSS existence checks.
- test_tui_foundation.py:350-400 `TestAppCompose` — 7 of 10 tests are `hasattr`; only `test_all_screens_have_ctrl_c_binding` actually walks the 6 screen classes.
- test_tui_strategy.py:594-617 `TestStrategyRegistration` — 5 structural tests.

**Source-grep "tests" (do not test behavior, test that a string is in a source file):**
- test_tui_group_c_validation.py:676-687 `test_strategy_screen_uses_cli_bridge` and `test_strategy_screen_runs_ancestry_command` and `test_strategy_screen_runs_benchmark_eval` — `open(strat_mod.__file__).read()` and `assert "run_cli" in source`, `assert "ancestry" in source`, `assert "benchmark-eval" in source`. These pass for any docstring comment.
- test_tui_group_c_validation.py:878-884 `test_telemetry_screen_uses_cli_bridge` — same pattern with `"telemetry-report"`, `"ancestry"`.
- test_validation_tui_group_b.py:1051-1060 `test_screen_has_all_compose_widgets` — `inspect.getsource(PaperScreen.compose)` and `assert "OrderFormWidget" in source`. A class imported anywhere in the file passes.

**Tests that test behavior well (positive control group):**
- test_tui_evidence.py:287-290 `test_widget_set_step_result` — exercises the setter and asserts the dict state.
- test_tui_risk_screen.py:138-148 `test_score_renders_gauge_bar` — sets `composite_score=0.72`, `sub_scores={"sharpe":0.85,"drawdown":0.72}`, `strategy_count=3`, asserts `"72/100"`, `"COMPOSITE RISK SCORE"`, `"Strategies: 3"`.
- test_tui_paper_trading.py:243-271 — 3 distinct `get_order_params` validation tests (no symbol, no quantity, negative quantity) each asserting a specific error substring.
- test_tui_foundation.py:156-167 — `test_init_default_url`, `test_init_custom_url` (verifies `rstrip("/")`), `test_init_custom_timeout` — these test constructor invariants.
- test_tui_data_views.py:61-65 `test_frozen` — uses `pytest.raises((AttributeError, TypeError, Exception))` to assert mutation raises. The catch is broad (any of three exception types) which is acceptable.
- test_tui_group_c_validation.py:1294-1327 — 7 WCAG contrast tests with specific numeric thresholds (≥ 4.5, ≥ 3.0) and assertion message printing the actual ratio.

**Mocking the SUT (borderline — the SUT is the method under test, but mocks of collaborators are legitimate):**
- test_tui_api_client.py:34-52 `test_success_first_try_no_retry` — patches `httpx.AsyncClient.get` to count calls. The SUT is `TuiApiClient._request_with_retry`; the dependency is `httpx`. This is legitimate.
- test_tui_market.py:539-562 `test_get_market_tickers_http_error` and `test_get_market_klines_connection_error` — same pattern. Legitimate.
- test_tui_strategy.py:524-538 `test_get_strategies` — replaces `client._client` with `AsyncMock()`. Reaches into private state. The SUT is `client.get_strategies`; the dep is the underlying HTTP. Legitimate.

---

## 3. COVERAGE GAPS

### 3.1 Screen data-loading
- **Market screen `_fetch_*` methods**: covered by test_tui_market.py:478-563 (`TestApiClientMarketMethods` — 6 tests on the API client methods themselves) and test_validation_tui_group_b.py:126-477 (`TestVAL_TUI_003_Integration` — 3 async tests calling `screen._refresh_all` and asserting widget attributes). However, the SUT is heavily mocked via `screen.query_one = mock_query` and `patch.object(screen._api, ...)`. Real subprocess / fetch path is not exercised.
- **Paper screen `_init_session`**: covered by test_tui_paper_trading.py:495-528 (2 tests) and test_validation_tui_group_b.py:987-1042 (1 test). The real `run_cli` is patched in all 3.
- **Paper screen `_place_order`**: covered by test_validation_tui_group_b.py:661-744 (2 tests). Both patch `asyncio.create_subprocess_exec`. The real `subprocess` path is not exercised.
- **Risk screen `_fetch_risk_data`, `_ws_risk_loop`, `_on_ws_risk_update`**: only the `TuiApiClient.get_risk` method is tested in test_tui_group_c_validation.py:497-514. The screen's actual fetch/WS handler methods are not exercised; only `assert hasattr(RiskScreen, "_ws_risk_loop")` at test_tui_group_c_validation.py:445.
- **Strategy screen `_refresh_all`, `_run_eval`, `_init_deck`**: only `hasattr` checks (test_tui_strategy.py:496-503). No test calls these methods with a real or mocked state.
- **Telemetry screen `_fetch_ops_board`**: only `hasattr(TelemetryScreen, "_fetch_ops_board")` at test_tui_group_c_validation.py:888. Not exercised.
- **Evidence screen `_refresh_graph`**: only `hasattr(EvidenceScreen, "_refresh_graph")` at test_tui_group_c_validation.py:1131. Not exercised.
- **Evidence screen `get_evidence_graph`**: only `assert "nodes" in result, "edges" in result` with fully mocked client (test_tui_group_c_validation.py:1133-1154, test_tui_evidence.py:384-389, test_tui_foundation.py:253-264).
- **Gap**: No integration test invokes a real `MarketScreen._fetch_tickers()` end-to-end against a fake httpx server. The `ticker_table.tickers` is set directly via `widget.tickers = [...]` in widget-level tests, bypassing the screen's data path.

### 3.2 Widget rendering
- All four widget rendering paths for market (SymbolList, Klines, Ticker, OrderBook) are covered in test_tui_market.py:194-407. Each widget has init/empty/data tests.
- All paper widgets (Positions, OrderForm, OrderHistory, PnlChart, AccountSummary) are covered in test_tui_paper_trading.py:71-425.
- Risk widgets (Gauge, Drawdown, Correlation, Alert) are covered in test_tui_risk_screen.py:128-287.
- Strategy widgets (List, Results, Comparison) are covered in test_tui_strategy.py:192-457.
- Telemetry widgets (RunList, ProviderMetrics, ToolUsage, RunDetail, RunComparison, ServiceHealth) are covered in test_tui_telemetry.py:261-653.
- Evidence widgets (Graph, EdgeDetail, DemoFlow) are covered in test_tui_evidence.py:93-343 and test_tui_group_c_validation.py:917-1102.
- **Coverage of `SparklineWidget`**: only init and `set_values` (test_tui_market.py:181-188). The `render()` method is not tested directly for the widget — only `sparkline_text` (the underlying function) is tested in test_tui_market.py:143-179.
- **Coverage of `LoadingIndicator.render()`**: 2 tests in test_tui_validation_contract.py:441-452 (empty → empty plain text, with status text → "Ready" in plain). 1 test in test_tui_group_c_validation.py:1423-1436 (loading=True → "Loading"; loading=False + status → "Live · refreshed"). The idle rendering path is not covered by test_tui_foundation.py:619-631.

### 3.3 Reactive state transitions
- `MarketScreen.current_symbol` change is tested in test_tui_market.py:462-472 — `_select_symbol("ETH-USD")` then asserts `current_symbol == "ETH-USD"`. `test_select_same_symbol_no_change` (test_tui_market.py:467) sets `current_symbol = "BTC-USD"` and calls `_select_symbol("BTC-USD")` — but the assertion is only `current_symbol == "BTC-USD"`, which it already was. The "no refresh triggered" behavior is not verified.
- `RiskScreen._filter_severity` cycle: test_tui_risk_screen.py:322-331 — does NOT call the action. The actual reactive transition is untested.
- `TelemetryScreen._date_range` cycle, `action_cycle_sort`, `action_cycle_status_filter`, `action_cycle_track_filter`: test_tui_telemetry.py:711-716 only asserts that filter sets contain `ALL`, `7d`, `30d`, `TODAY`. The cycle behavior is not exercised.
- `StrategyScreen.is_evaluating` reactive: test_tui_strategy.py:506-510 only checks `hasattr`; no transition tested. test_tui_group_c_validation.py:605-608 asserts default `is_evaluating is False`.
- `DemoFlowWidget._current_step` boundary: test_tui_evidence.py:265-269 tests advance at end (does not advance past `len(DEMO_STEPS)`). test_tui_evidence.py:277-281 tests retreat at start. Both verified.
- `OrderFormWidget._order_type` toggle: test_tui_paper_trading.py:212-218 (full cycle tested).
- `Widget.can_focus`: test_tui_evidence.py:103-105, 195-197, 283-285 — three tests confirm `can_focus is True` on the three evidence widgets.
- **Gap**: `TuiApiClient` lazy init reactive: only the initial state `_client is None` is tested (test_tui_foundation.py:169-171). The trigger that flips it (`_ensure_client`) is tested in isolation but not in the context of a method that calls it.

### 3.4 Keybinding handlers
- All 6 screens have `escape`, `ctrl+c`, `?`/`question_mark` asserted in test_tui_foundation.py:376-400, test_tui_validation_contract.py:305-340, test_tui_group_c_validation.py:1331-1394.
- `PaperScreen` "enter" binding for submit: test_validation_tui_group_b.py:750-753, test_tui_paper_trading.py:441.
- `EvidenceScreen` "tab", "enter", "n", "p", "a": test_tui_evidence.py:359-367, test_tui_group_c_validation.py:1106-1115.
- **Gap**: action method bodies are not exercised through bindings. For example, `MarketScreen.action_refresh_now` is asserted to exist (test_tui_market.py:460) but never invoked. `RiskScreen.action_filter_alerts` exists (test_tui_risk_screen.py:336) but is bypassed in the cycle test (Section 2).
- **Gap**: pilot tests for key-driven actions are limited to navigation. test_tui_validation_contract.py:148-156 `test_pilot_screen_switching_via_nav_keys` presses `j` and `k` but does not assert the resulting screen change.

### 3.5 Error fallback when API is down
- `friendly_error` mapping: test_tui_validation_contract.py:372-419 (6 tests) and test_tui_foundation.py:525-553 (4 tests). Both test `ConnectError`, `TimeoutException`, `HTTPStatusError` 500/401/429, and a generic `ValueError`.
- `TuiApiClient` retry on connect error: test_tui_api_client.py:54-72 (retry once then raise). 4xx no-retry: test_tui_api_client.py:118-134. 5xx retry: test_tui_api_client.py:75-115. Timeout retry: test_tui_api_client.py:137-154.
- `TuiApiClient` retry via `_post`: test_tui_api_client.py:189-208.
- `_init_session` graceful failure: test_tui_paper_trading.py:512-527 — sets `MagicMock(returncode=1, stderr="Session creation failed")` and asserts `screen.session_id == ""` and "error" or "failed" in `status_text`.
- `_place_order` failure path: test_validation_tui_group_b.py:721-744 — patches `subprocess` to returncode 1 and asserts `mock_form.show_error.assert_called_once()`.
- `MarketScreen._refresh_all_handles_partial_failure`: test_validation_tui_group_b.py:433-477 — klines/orderbook raise, tickers succeed, asserts `mock_symbol_list.set_symbols.assert_called_once()` and `is_loading is False`.
- `MarketScreen._refresh_all_handles_total_failure`: test_validation_tui_group_b.py:478-496 — all three raise, asserts `is_loading is False`.
- `RiskScreen.test_ws_risk_score_no_sessions`: test_dashboard_risk_integration.py:341-362 — asserts `composite_score is None`, `max_drawdown is None`, `note is not None`.
- `get_health` HTTP 503: test_tui_foundation.py:213-227 — asserts `pytest.raises(httpx.HTTPStatusError)`.
- **Gap**: the paper screen's PnL chart does not test what happens when `run_cli` returns malformed JSON. The dashboard runs test (test_dashboard_runs.py:431-450) tests the `ops_payload` malformed path but for a different surface.

### 3.6 Refresh cadence
- `MarketScreen._refresh_interval` constant: test_validation_tui_group_b.py:368-372 asserts `0 < interval <= 60`.
- `MarketScreen.on_mount` timer setup: test_validation_tui_group_b.py:354-356 — `assert hasattr(screen, "on_mount")`. The actual `set_interval` call inside `on_mount` is not exercised.
- `RiskScreen._refresh_seconds`: test_tui_group_c_validation.py:491-497 — `assert RiskScreen._refresh_seconds` (existence only, no numeric range test).
- **Gap**: no test verifies the actual timer fires and triggers a refresh. The auto-refresh mechanism is structurally asserted but not behaviorally.

### 3.7 Risk screen guard indicators
- `RiskGaugeWidget.render()` empty state: test_tui_risk_screen.py:131-136 asserts `"No risk data available"` when `composite_score is None`.
- Score rendering: test_tui_risk_screen.py:138-148 asserts `"72/100"`, `"COMPOSITE RISK SCORE"`, `"Strategies: 3"` with score 0.72.
- Boundary scores: test_tui_risk_screen.py:149-161 tests `0/100` and `100/100`.
- `gauge_color`: test_tui_risk_screen.py:32-57 tests red (0.2), yellow (0.5), green (0.8), boundary at 0.4/0.7, 0.0, 1.0, NaN (returns muted `#7d9483`).
- `DrawdownSparklineWidget`: test_tui_risk_screen.py:167-195 tests empty (`"Collecting equity data"`), with history (`"DRAWDOWN"`, `"-5.0%"`, `"-2.0%"`, `"in progress"`), with recovery (`"5 periods"`).
- `AlertStreamWidget`: test_tui_risk_screen.py:244-287 tests empty (`"No alerts"`), with entries (`"ALERT STREAM"`, severity labels), critical (`"CRIT"`).
- **Gap**: the risk screen source (siglab/tui/screens/risk.py) has `max_drawdown`, `current_drawdown`, `recovery_periods` reactives (siglab/tui/screens/risk.py:156-158), but no test verifies a guard threshold (e.g., when `composite_score < 0.4`, a "high risk" badge appears). The `_filter_severity` cycle is mocked-around (Section 2).
- **Gap**: no test verifies the `RiskScreen._on_ws_risk_update` message handler; only `assert hasattr(screen, "_on_ws_risk_update")` (test_tui_risk_screen.py:337).

---

## 4. SMELLS / RISKS

**subprocess / shell=True:**
- test_tui_validation_contract.py:172-185 `_run_cli` calls `subprocess.run(["poetry", "run", "python3", "-m", "siglab.cli"] + args, capture_output=True, text=True, cwd="/home/eya/soso/siglab", env=env, timeout=30)`. No `shell=True`. 10 tests use it. cwd is hardcoded to `/home/eya/soso/siglab`, which makes the suite non-portable to a different clone path.
- test_tui_foundation.py:311-315 `test_run_cli_with_args` calls `await run_cli("--help")` which transitively uses `asyncio.create_subprocess_exec`. Spawns a real Python process to invoke the real CLI. No `shell=True`.

**Mocking the SUT (or reaching into private state):**
- test_tui_strategy.py:533, 549, 564, 579 `client._client = mock_http` — sets private attribute `client._client` to an `AsyncMock`. Reaches past the constructor API.
- test_tui_telemetry.py:746, 763, 785 `client._client = mock_http` — same pattern.
- test_tui_group_c_validation.py:508, 1149 `client._client = mock_http` — same pattern.
- test_validation_tui_group_b.py:142 `screen.query_one = mock_query` — replaces Textual DOM query with a dict-based mock.
- test_tui_paper_trading.py:507 `screen.query_one = MagicMock(side_effect=Exception("not mounted"))` — every `query_one` call raises; the SUT's catch-and-log path is the only path that passes.
- test_validation_tui_group_b.py:1056 `screen._find_existing_session = AsyncMock(return_value=None)` — replaces internal method.

**Network dependencies:**
- test_tui_validation_contract.py:172-185 `_run_cli` (subprocess) — depends on `poetry` being installed and the project being runnable.
- test_tui_foundation.py:311-315 `test_run_cli_with_args` — depends on `siglab.cli` importable and `--help` working.
- No test directly opens a network socket. All HTTP-bound tests patch `httpx.AsyncClient.get` or `httpx.AsyncClient.post`.

**Flaky timing:**
- test_tui_paper_trading.py:532 `screen._refresh_all` after no `await pilot.pause()` — relies on the async loop completing without real time pressure, but the `run_cli` patch is `AsyncMock(return_value=mock_result)`, so it returns instantly. Not flaky in practice.
- test_validation_tui_group_b.py:933-980 `test_pnl_history_accumulates_on_multiple_refreshes` — calls `_refresh_all` twice sequentially. No `asyncio.sleep` between, but relies on `AsyncMock` returning immediately. Not flaky in practice.

**Hardcoded sleeps:**
- test_tui_api_client.py:67, 91, 111, 150, 204 `with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep` — patches `asyncio.sleep` so the SUT's `await asyncio.sleep(0.5)` (siglab/tui/api_client.py:76) is replaced with a mock. The real sleep is not invoked. test_tui_api_client.py:70 asserts `mock_sleep.assert_awaited_once_with(0.5)`.
- No test in the 15 owned files uses `time.sleep` or unpatched `asyncio.sleep`.

**Secrets in test fixtures:**
- test_dashboard_risk_integration.py:41 `sosovalue_api_key_override=None` — passes `None` explicitly; no secret in the fixture.
- test_tui_paper_trading.py:665 `screen.session_id = "test-session-123"` — placeholder string, not a secret.
- No test references real API keys, tokens, or credentials.

**Global state mutation:**
- test_tui_paper_trading.py:507, 524, 554, 565 `screen.query_one = MagicMock(...)` — mutates instance state without restoration. Each test creates a new `PaperScreen()`, so cross-test leakage is contained, but within a test the mutation persists.
- test_validation_tui_group_b.py:142, 202, 269, 335, 418, 488, 924, 1027 same pattern on `MarketScreen`, `PaperScreen`.
- test_tui_evidence.py:289 `widget.set_step_result(1, ...)` mutates the widget's `_step_results` dict.
- `os.environ` is not mutated in any test in the 15 owned files (test_tui_validation_contract.py only reads `os.environ.copy()` at line 174).

**Tests that mutate Textual pilot state without restoring:**
- test_tui_validation_contract.py:118-122 `test_pilot_app_launches` — opens `async with SigLabTUI().run_test() as pilot`, asserts `pilot.app.title == "SigLab"` and `pilot.app.is_mounted`. The `async with` restores the app on exit.
- test_tui_validation_contract.py:140-146 `test_pilot_screen_switching_via_number_keys` — presses keys 1-6. The pilot's screen stack changes are not asserted. The `async with` block restores.
- test_tui_validation_contract.py:528-537 `test_pilot_escape_returns_to_main` — pushes help screen and asserts `len(pilot.app.screen_stack) == 1` after escape. The `async with` block restores.
- test_tui_risk_screen.py:343-370 `test_risk_screen_mounts_without_error` — mounts RiskScreen in a TestApp and asserts `app.is_running` and `app.screen is not None`. The `async with app.run_test()` restores.
- No test relies on persistent Textual state between tests; each test opens and closes its own pilot.

**tmux/PTY requirements:**
- None. No test in the 15 owned files invokes tmux, pty, or a real terminal. The file `tests/test_tui_tmux_hardening.py` exists (29 KiB, last modified Sat Jun 6) but is not in the owned list.

**Other deterministic risks:**
- test_tui_validation_contract.py:181 `cwd="/home/eya/soso/siglab"` — absolute hardcoded path. Suite fails on a different machine.
- test_tui_validation_contract.py:178 `["poetry", "run", "python3", "-m", "siglab.cli"]` — depends on `poetry` and `python3` being on PATH with the right version.
- test_tui_evidence.py:96-101 `test_widget_init` — asserts `widget._graph_nodes == ()`, `widget._edges == ()`. Reaches into private attributes.

---

## 5. TUI ↔ API CONTRACT (test_tui_api_client.py)

The contract test in `test_tui_api_client.py` does NOT cover HTTP endpoints. It covers the `_request_with_retry` method (test_tui_api_client.py:30-156, 7 tests) and the `_get` / `_post` wrappers (test_tui_api_client.py:158-209, 3 tests). The actual endpoint methods on `TuiApiClient` (e.g. `get_market_tickers`, `get_evidence_graph`, `get_risk`, `get_strategies`) are tested in other files with mocked HTTP.

**`TuiApiClient` endpoint coverage in other owned files (each is smoke-tested with `assert "key" in result` or `isinstance(result, dict)`):**

| File | Endpoint method | Test line | Assertion style |
|---|---|---|---|
| test_tui_foundation.py:195-211 | `get_health` | L195 | `result["status"] == "ok"`, `"version" in result`, `"uptime_seconds" in result` — moderate |
| test_tui_foundation.py:230-239 | `get_config` | L230 | `isinstance(result, dict)` — smoke only |
| test_tui_foundation.py:242-251 | `get_ops_board` | L242 | `isinstance(result, dict)` — smoke only |
| test_tui_foundation.py:254-264 | `get_evidence_graph` | L254 | `"nodes" in result, "edges" in result` — smoke only |
| test_tui_foundation.py:267-276 | `get_skill_report` | L267 | `isinstance(result, dict)` — smoke only |
| test_tui_foundation.py:279-288 | `get_risk` | L279 | `"composite_score" in result` — smoke only |
| test_tui_market.py:482-492 | `get_market_symbols` | L482 | `"symbols" in result, result["count"] == 5` — moderate |
| test_tui_market.py:495-505 | `get_market_tickers` | L495 | `"tickers" in result, len(result["tickers"]) == 5` — moderate |
| test_tui_market.py:508-523 | `get_market_klines` | L508 | `result["symbol"] == "BTC-USD", result["count"] == 20` — moderate |
| test_tui_market.py:526-536 | `get_market_orderbook` | L526 | `"bids" in result, "asks" in result` — smoke only |
| test_tui_market.py:539-551 | `get_market_tickers` HTTP 500 | L539 | `pytest.raises(httpx.HTTPStatusError)` — error path |
| test_tui_market.py:554-563 | `get_market_klines` ConnectError | L554 | `pytest.raises(httpx.ConnectError)` — error path |
| test_tui_strategy.py:525-538 | `get_strategies` | L525 | `isinstance(result, dict), result["count"] == 0` — smoke |
| test_tui_strategy.py:541-553 | `get_strategy_detail` | L541 | `result["spec_hash"] == "abc123"` — moderate |
| test_tui_strategy.py:556-568 | `get_benchmark_status` | L556 | `isinstance(result, dict)` — smoke only |
| test_tui_strategy.py:571-583 | `get_benchmark_results` | L571 | `isinstance(result, dict)` — smoke only |
| test_tui_telemetry.py:734-750 | `get_ops_board` | L734 | `isinstance(result, dict)` — smoke only |
| test_tui_telemetry.py:753-770 | `get_skill_report` | L753 | `isinstance(result, dict), result["total_skills"] == 0` — smoke |
| test_tui_telemetry.py:773-789 | `get_telemetry_report` | L773 | `isinstance(result, dict)` — smoke only |
| test_tui_evidence.py:385-389 | `get_evidence_graph` | L385 | `assert hasattr(client, "get_evidence_graph")` — NOT a behavior test, only existence |
| test_tui_group_c_validation.py:498-514 | `get_risk` | L498 | `"composite_score" in result, "max_drawdown" in result, "correlation_matrix" in result` — moderate |
| test_tui_group_c_validation.py:1134-1154 | `get_evidence_graph` | L1134 | `"nodes" in result, "edges" in result` — smoke |

**Endpoints that are ONLY smoke-tested (`assert "key" in result` or `isinstance(result, dict)`):**
- `get_config` — test_tui_foundation.py:230
- `get_ops_board` (in foundation) — test_tui_foundation.py:242
- `get_skill_report` (in foundation) — test_tui_foundation.py:267
- `get_risk` (in foundation) — test_tui_foundation.py:279 — only asserts `"composite_score" in result`, no value check
- `get_market_orderbook` — test_tui_market.py:526 — only asserts `"bids" in result, "asks" in result`
- `get_benchmark_status` — test_tui_strategy.py:556 — `isinstance(result, dict)`
- `get_benchmark_results` — test_tui_strategy.py:571 — `isinstance(result, dict)`
- `get_ops_board` (in telemetry) — test_tui_telemetry.py:734 — `isinstance(result, dict)`
- `get_telemetry_report` — test_tui_telemetry.py:773 — `isinstance(result, dict)`
- `get_evidence_graph` (in evidence test) — test_tui_evidence.py:385 — `assert hasattr(...)` (not even a result inspection)
- `get_evidence_graph` (in group c) — test_tui_group_c_validation.py:1134 — `"nodes" in result, "edges" in result`

**HTTP method/URL path assertions (proves the SUT called the right endpoint):**
- test_tui_api_client.py:178-184 `test_post_delegates_to_request_with_retry` — `async def mock_post(self_client, path, **kwargs): assert kwargs.get("json") == {"name": "test"}`. Tests that the JSON body is forwarded. Does not assert the path.
- test_tui_api_client.py:168 `await client._get("/items", params={"q": "test"})` — `result == {"items": [1]}`. The mock returns the canned response without inspecting the path or params.

**Retry behavior contract (the core of test_tui_api_client.py):**
- Success: 1 call, no retry (test_tui_api_client.py:34-52).
- ConnectError → 1 retry → raises (test_tui_api_client.py:54-72). `asyncio.sleep` is awaited with `(0.5,)` (test_tui_api_client.py:70).
- 5xx → 1 retry → 200 success (test_tui_api_client.py:75-96). 2 calls.
- 5xx → 1 retry → 5xx again → raises (test_tui_api_client.py:98-115). 2 calls.
- 4xx → no retry → raises (test_tui_api_client.py:118-135). 1 call.
- Timeout → 1 retry → raises (test_tui_api_client.py:137-155). 2 calls.
- `_get` and `_post` delegate to `_request_with_retry` (test_tui_api_client.py:161-187).
- `_post` retries on ConnectError (test_tui_api_client.py:189-208).

**Missing endpoint tests (no test exercises the method at all):**
- `TuiApiClient.subscribe_risk_ws` — exists per test_tui_group_c_validation.py:453 `test_api_client_has_ws_subscribe_risk` which is `assert hasattr(client, "subscribe_risk_ws")` — existence only.
- `TuiApiClient.close` — tested via `await client.close()` at the end of every async test, but no dedicated test verifies connection cleanup behavior beyond `client._client is None` in test_tui_foundation.py:181-187.
- `TuiApiClient._ensure_client` lazy init — tested in test_tui_foundation.py:174-178.

---

## 6. EVIDENCE INDEX (line-by-line)

- Subprocess: test_tui_validation_contract.py:177-184 (subprocess.run with hardcoded cwd).
- Subprocess (transitive): test_tui_foundation.py:311-315 (run_cli spawns Python).
- asyncio.sleep mock: test_tui_api_client.py:67, 91, 111, 150, 204.
- Hardcoded path: test_tui_validation_contract.py:181.
- Source-grep tests: test_tui_group_c_validation.py:679, 685, 692, 881; test_validation_tui_group_b.py:1054-1060.
- Tautology / no-assert: test_tui_paper_trading.py:530-534; test_tui_validation_contract.py:140-146, 148-156, 520-525; test_tui_market.py:349-356.
- Bypassed real method: test_tui_risk_screen.py:322-331 (action_filter_alerts not called).
- Hasattr-only tests: test_tui_market.py:443-461; test_tui_paper_trading.py:434-470; test_tui_strategy.py:469-513; test_tui_telemetry.py:665-723; test_tui_evidence.py:355-376; test_tui_foundation.py:350-400.
- Private attribute set: test_tui_strategy.py:533, 549, 564, 579; test_tui_telemetry.py:746, 763, 785; test_tui_group_c_validation.py:508, 1149.
- query_one replaced: test_validation_tui_group_b.py:142, 202, 269, 335, 418, 488, 924, 1027; test_tui_paper_trading.py:507, 524, 554, 565.
- run_cli patched: test_tui_paper_trading.py:503, 521, 567; test_validation_tui_group_b.py:926, 972, 975, 1038.
- create_subprocess_exec patched: test_validation_tui_group_b.py:681, 730.
- WCAG contrast tested: test_tui_group_c_validation.py:1294-1327 (7 thresholds).
- Risk gauge boundary: test_tui_risk_screen.py:32-57.
- 4xx no-retry: test_tui_api_client.py:118-135.
- 5xx retry success: test_tui_api_client.py:75-96.
- ConnectError retry: test_tui_api_client.py:54-72.
- Timeout retry: test_tui_api_client.py:137-155.
- friendly_error mapping: test_tui_validation_contract.py:372-419; test_tui_foundation.py:525-553.
- All 6 screens escape/ctrl+c/question_mark: test_tui_foundation.py:376-400, test_tui_group_c_validation.py:1331-1356, test_tui_validation_contract.py:305-340.
- Tabs of evidence screen: test_tui_evidence.py:359-367; test_tui_group_c_validation.py:1106-1115.
- Tabs of risk/strategy/telemetry: test_tui_group_c_validation.py:1371-1389.
- Demo flow step boundaries: test_tui_evidence.py:265-281.
- OrderForm validation: test_tui_paper_trading.py:243-291.
- Risk composite score render: test_tui_risk_screen.py:138-148.
- `get_risk` smoke: test_tui_foundation.py:279-288.
- `get_evidence_graph` smoke: test_tui_foundation.py:253-264; test_tui_evidence.py:385-389; test_tui_group_c_validation.py:1134-1154.
- `get_market_tickers` ConnectError: test_tui_market.py:554-563.
- `_compute_risk_metrics` direct: test_dashboard_risk_integration.py:216-238.
- `_compute_risk_metrics` two strategies correlation: test_dashboard_risk_integration.py:178-214.
- WS risk_score subscribe: test_dashboard_risk_integration.py:244-274.
- WS get_risk action: test_dashboard_risk_integration.py:365-385.
- WS vs REST field consistency: test_dashboard_risk_integration.py:387-429.
- DashboardApp experiments_payload grouped runs: test_dashboard_runs.py:36-185.
- DashboardApp runs_payload with workspace trace: test_dashboard_runs.py:186-304.
- Skill value NOISY: test_dashboard_runs.py:306-324.
- ops_payload artifact status: test_dashboard_runs.py:326-429.
- ops_payload malformed: test_dashboard_runs.py:431-450.
- Active workspace run without ancestry: test_dashboard_runs.py:452-493.
- Repeat burn-in: test_dashboard_runs.py:495-575.
- PnL accumulation across refreshes: test_validation_tui_group_b.py:933-980.
- Market refresh all partial failure: test_validation_tui_group_b.py:433-477.
- Market refresh all total failure: test_validation_tui_group_b.py:478-496.
- Paper _init_session: test_tui_paper_trading.py:495-528; test_validation_tui_group_b.py:987-1042.
- Paper _place_order success: test_validation_tui_group_b.py:661-698.
- Paper _place_order failure: test_validation_tui_group_b.py:721-744.
- 0.5s backoff value: test_tui_api_client.py:70.
- `close()` sets `_client = None`: test_tui_foundation.py:181-187.
- Lazy init: test_tui_foundation.py:169-171.
- Repr/equality of CliResult: test_tui_foundation.py:297-309.
- nav-sidebar ids in order: test_tui_foundation.py:419-422, 97.
- App.tcss has paper styles: test_tui_paper_trading.py:578-601.
- App.tcss has strategy styles: test_tui_strategy.py:607-613.
- App.tcss has evidence styles: test_tui_evidence.py:369-376, test_tui_group_c_validation.py:1449-1466.
- theme.tcss color variables: test_tui_foundation.py:453-472, test_tui_group_c_validation.py:1191-1225.
- Background not pure black: test_tui_foundation.py:474-481.
- `truncate` ellipsis: test_tui_paper_trading.py:178-184; test_tui_strategy.py:175-184; test_tui_formatting.py:111-126.
- Sparkline block chars: test_tui_market.py:186-187.
- OHLC summary: test_tui_market.py:167-179.
- Sparkline downsampling: test_tui_market.py:157-160.
- _correlation_block threshold chars: test_tui_risk_screen.py:103-122.
- _correlation_color boundary: test_tui_risk_screen.py:80-100.
- gauge_color NaN: test_tui_risk_screen.py:56-57.
- severity_color case insensitive: test_tui_risk_screen.py:75-77.
- format_latency ms threshold: test_tui_strategy.py (no test for format_latency here); test_tui_telemetry.py:153-170 (via format_latency).
- PnLChart Low/High/Now: test_tui_paper_trading.py:167-174.
- RunComparisonWidget: test_tui_telemetry.py:565-606.
- ToolUsageWidget sorted by count: test_tui_telemetry.py:509-516 (`plain.index("open_file") < plain.index("think")`).
- SymbolEntry use in SymbolList: test_validation_tui_group_b.py:155-156.
- MAX_COMPARE cap: test_tui_strategy.py:289-297.
- DEMO_STEPS sequential 1..N: test_tui_evidence.py:42-44, test_tui_group_c_validation.py:1029-1032.
- _step_results: test_tui_evidence.py:287-290.
- update_graph tuple conversion: test_tui_evidence.py:107-115.
- set_filter kind + text: test_tui_evidence.py:118-140.
- _filtered_nodes: test_tui_evidence.py:126-140.
- 2/2 nodes 1 edges summary: test_tui_group_c_validation.py:986-990.
- records: 620 from JSON: test_tui_evidence.py:334-343, test_tui_group_c_validation.py:1086-1095.
- Risk screen LoadingIndicator id: test_tui_risk_screen.py (no explicit test, but `assert hasattr(RiskScreen, "compose")` at test_tui_risk_screen.py:300).
- Pilot help screen opens: test_tui_validation_contract.py:508-517.
- Pilot F1 opens help: test_tui_validation_contract.py:539-547.
- Pilot resize: test_tui_validation_contract.py:549-558.

---

**Files referenced but not in owned list (read-only context, not audited):** `tests/test_tui_tmux_hardening.py`, `tests/golden/*`, `tests/test_paper_client.py`, `siglab/tui/screens/risk.py`, `siglab/tui/formatting.py`, `siglab/tui/api_client.py`, `siglab/dashboard/routes.py`, `siglab/dashboard/server.py`.
