# SigLab TUI Application

## Purpose

The SigLab TUI is a Textual-based terminal dashboard that provides operators with a keyboard-driven interface to all SigLab subsystems — market data, paper trading, risk monitoring, strategy research, telemetry, and evidence graph browsing. It serves researchers and operators who need real-time visibility into SoDEX markets, portfolio risk, experiment runs, and the evidence chain without leaving the terminal.

## Architecture

### High-Level Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│                        SigLabTUI App                         │
│  ┌──────────┐  ┌──────────────────────────────────────────┐ │
│  │   Nav    │  │              Content Area                 │ │
│  │ Sidebar  │  │  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐    │ │
│  │          │  │  │Market│ │Paper │ │Risk  │ │Strat │    │ │
│  │ 1.MARKET │  │  │Screen│ │Screen│ │Screen│ │egy   │    │ │
│  │ 2.PAPER  │  │  └──┬───┘ └──┬───┘ └──┬───┘ └──┬───┘    │ │
│  │ 3.RISK   │  │     │        │        │        │        │ │
│  │ 4.STRATEGY│  │  ┌──┴───┐ ┌──┴───┐ ┌──┴───┐ ┌──┴───┐    │ │
│  │ 5.TELEMET│  │  │Telem.│ │Evid. │ │      │ │      │    │ │
│  │ 6.EVIDENC│  │  │Screen│ │Screen│ │      │ │      │    │ │
│  └──────────┘  │  └──────┘ └──────┘ └──────┘ └──────┘    │ │
│               └──────────────────────────────────────────┘ │
│  ┌─────────────────────────────────────────────────────────┐│
│  │                    Status Bar (v0.1.0, connection, time) ││
│  └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
                         │              │
                         ▼              ▼
              ┌──────────────┐  ┌───────────────┐
              │ TuiApiClient │  │  CLI Bridge    │
              │ (FastAPI HTTP│  │ (subprocess    │
              │  + WebSocket)│  │  python -m     │
              └──────┬───────┘  │  siglab.cli)   │
                     │          └───────┬───────┘
                     ▼                  ▼
              ┌──────────────┐  ┌───────────────┐
              │  FastAPI     │  │  SigLab CLI   │
              │  Dashboard   │  │  (direct DB/   │
              │  (port 3100) │  │   config)      │
              └──────────────┘  └───────────────┘
```

### Module Layout

```
siglab/tui/
├── __init__.py              # Exposes SigLabTUI via lazy import
├── app.py                   # Main app class, navigation shell, help overlay
├── api_client.py            # TuiApiClient — async HTTP + WebSocket client
├── cli_bridge.py            # run_cli() — subprocess bridge to siglab.cli
├── data_views.py            # Zero-copy frozen dataclass views (TickerView, SymbolEntry, etc.)
├── formatting.py            # Color constants, format helpers, safe_query(), CSS snippets
├── loading.py               # LoadingIndicator spinner widget
├── screens/
│   ├── base.py              # BaseScreen — lifecycle, bindings, auto-refresh, search contract
│   ├── market.py            # MarketScreen — symbols, klines, tickers, order book
│   ├── paper.py             # PaperScreen — positions, order form, history, PnL chart
│   ├── risk.py              # RiskScreen — gauge, drawdown, correlation, alerts
│   ├── strategy.py          # StrategyScreen — list, results, comparison, evaluation
│   ├── telemetry.py         # TelemetryScreen — runs, provider metrics, tool usage
│   └── evidence.py          # EvidenceScreen — evidence graph, demo flow walkthrough
├── styles/
│   └── app.tcss             # Consolidated Textual CSS (theme variables + all screen layouts)
└── widgets/
    ├── __init__.py
    ├── base.py              # FilterableListWidget, ComparisonWidget base classes
    ├── sparkline.py         # sparkline_text(), ohlc_summary(), SparklineWidget
    └── status_bar.py        # SigLabStatusBar — version, connection, time
```

### Design Principles

- **Zero-copy data flow**: API response dicts are stored by reference, never copied. Immutable tuples and frozen dataclasses (`data_views.py`) wrap raw data without allocation.
- **Declarative search contract**: Screens set `_search_input_id` + `_search_list_id`; the base class wires search automatically.
- **Single CSS file**: Textual parses CSS files independently, so theme variables must be in the same file that uses them. All styles are consolidated in `app.tcss`.
- **Dual data sources**: `TuiApiClient` (HTTP/WS to FastAPI) for market/risk/evidence data; `run_cli()` (subprocess) for paper trading, strategy, and telemetry data.

## Screens

### Market Screen (`MarketScreen`)

**Purpose**: Real-time market overview of SoDEX perpetual futures.

| Widget | Description | Data Source |
|--------|-------------|-------------|
| `SymbolListWidget` | Filterable list of perp symbols with selection | `TuiApiClient.get_market_tickers()` |
| `KlinesChartWidget` | ASCII sparkline of kline close prices + OHLC summary | `TuiApiClient.get_market_klines()` |
| `TickerTableWidget` | Top-20 ticker table (symbol, price, 24h change, volume) | `TuiApiClient.get_market_tickers()` |
| `OrderBookWidget` | Bid/ask depth with volume bars and spread display | `TuiApiClient.get_market_orderbook()` |

**Key bindings**: `j/k` navigate symbol list, `/` search symbols, `Enter` select symbol (loads klines + order book), `r` refresh.

**Auto-refresh**: 30 seconds.

### Paper Trading Screen (`PaperScreen`)

**Purpose**: Paper trading with order entry, position tracking, and PnL monitoring.

| Widget | Description | Data Source |
|--------|-------------|-------------|
| `OrderFormWidget` | Order entry (symbol, side, type, qty, price) | User input |
| `AccountSummaryWidget` | Session name, realized/unrealized/total PnL, funding | `run_cli("paper-status")` |
| `PnlChartWidget` | PnL sparkline with min/high/current | Accumulated from paper-status |
| `PositionsTableWidget` | Open positions with mark prices and unrealized PnL | `run_cli("paper-status")` |
| `OrderHistoryWidget` | Full order log (time, side, type, symbol, qty, price, status) | `run_cli("paper-status")` |

**Key bindings**: `s` set symbol, `b` toggle buy/sell, `t` toggle market/limit, `Q` set quantity, `p` set price, `Enter` submit order, `n` new session, `c` cancel order.

**Auto-refresh**: 15 seconds. Session management reuses existing `tui-session` or creates a new one.

### Risk Monitoring Screen (`RiskScreen`)

**Purpose**: Portfolio risk metrics with real-time WebSocket updates.

| Widget | Description | Data Source |
|--------|-------------|-------------|
| `RiskGaugeWidget` | Composite risk score (0-100) with sub-score bars | `TuiApiClient.get_risk()` + WS |
| `DrawdownSparklineWidget` | Historical drawdown chart with max/current/recovery | `TuiApiClient.get_risk()` |
| `CorrelationHeatmapWidget` | Cross-strategy correlation matrix (ASCII block chars) | `TuiApiClient.get_risk()` + WS |
| `AlertStreamWidget` | Severity-colored alert log (newest first, max 50) | `TuiApiClient.get_risk()` |

**Key bindings**: `r` refresh, `j/k` scroll alerts, `f` cycle alert filter (all → critical → warning → info).

**Auto-refresh**: 15 seconds. Subscribes to `risk_score` WebSocket for live gauge/drawdown/correlation updates with exponential backoff on connection failure.

### Strategy Research Screen (`StrategyScreen`)

**Purpose**: Browse, evaluate, and compare strategy experiments.

| Widget | Description | Data Source |
|--------|-------------|-------------|
| `StrategyListWidget` | Multi-select strategy list (hash, family, score, status) | `run_cli("ancestry")` |
| `ResultsTableWidget` | Sortable results table (score, PnL%, Sharpe, MaxDD, sparkline) | `run_cli("ancestry")` |
| `ComparisonPanelWidget` | Side-by-side comparison (2-4 strategies) with equity overlay | Derived from selection |

**Key bindings**: `j/k` navigate, `/` search, `Space` toggle multi-select, `c` toggle comparison view, `e` run evaluation, `i` initialize deck, `s` cycle sort column.

**Auto-refresh**: 30 seconds. Supports `benchmark-eval` and `benchmark-init` CLI commands.

### Telemetry Screen (`TelemetryScreen`)

**Purpose**: Experiment run browser with provider metrics and tool usage.

| Widget | Description | Data Source |
|--------|-------------|-------------|
| `TelemetryRunListWidget` | Multi-select run list with date/status/track filters | `run_cli("ancestry")` |
| `RunDetailWidget` | Selected run metadata (hash, track, family, score, status) | Derived from selection |
| `ProviderMetricsWidget` | Token usage, model distribution, credit/context pressure | `run_cli("telemetry-report")` |
| `ToolUsageWidget` | Tool invocation counts, latency p50/p95, error rate | `run_cli("telemetry-report")` |
| `ServiceHealthWidget` | Service status indicators + artifact freshness | `TuiApiClient.get_ops_board()` |
| `RunComparisonWidget` | Side-by-side run comparison | Derived from multi-select |

**Key bindings**: `j/k` navigate, `/` search, `Space` select, `c` toggle compare, `d` cycle date range (ALL/7d/30d/TODAY), `f` cycle status filter (ALL/PASSED/FAILED/RUNNING/PENDING), `t` cycle track filter, `v` toggle view (telemetry ↔ service health).

**Auto-refresh**: 30 seconds.

### Evidence Screen (`EvidenceScreen`)

**Purpose**: Evidence graph browser and interactive buildathon demo flow.

| Widget | Description | Data Source |
|--------|-------------|-------------|
| `EvidenceGraphWidget` | ASCII tree of evidence nodes (source/entity/module) with edge counts | `TuiApiClient.get_evidence_graph()` |
| `EdgeDetailWidget` | Connection details for evidence graph edges | Shared from graph widget |
| `DemoFlowWidget` | 8-step interactive demo walkthrough with execution results | `run_cli()` per step |

**Key bindings**: `/` filter evidence, `Tab` switch pane (graph ↔ demo), `Enter` run current demo step, `n/p` next/prev step, `a` run all steps, `f` filter by source.

**Auto-refresh**: 30 seconds. Demo steps are defined in `DEMO_STEPS` and execute CLI commands sequentially.

## Widgets

### Base Classes (`widgets/base.py`)

#### `FilterableListWidget`

Reusable base for all filterable, navigable list widgets.

- **Inherited by**: `SymbolListWidget`, `StrategyListWidget`, `TelemetryRunListWidget`
- **Features**: Text search filtering, single/multi-select, `j/k` navigation, reactive `selected_index`
- **Contract**: Subclasses implement `_matches(item)`, `_render_item(item, index, is_selected)`, `_get_item_key(item)`
- **Data flow**: `set_data(items)` stores as immutable tuple; `set_filter(text)` triggers single-pass `_apply_filters()`

#### `ComparisonWidget`

Base for side-by-side comparison of 2+ items.

- **Inherited by**: `ComparisonPanelWidget` (strategy), `RunComparisonWidget` (telemetry)
- **Features**: Configurable `_metrics` list of `(label, key, format_str)` tuples, delta column, color-coded columns
- **Extensible**: `_render_extra()` hook for additional content (e.g., equity curve overlay)

### Sparkline (`widgets/sparkline.py`)

- `sparkline_text(values, width, bullish_color, bearish_color)` — renders Unicode block sparkline (`▁▂▃▄▅▆▇█`) as Rich `Text`
- `ohlc_summary(candles)` — compact OHLC line from candle dicts
- `SparklineWidget` — standalone sparkline widget with `set_values()`

### Status Bar (`widgets/status_bar.py`)

`SigLabStatusBar` — persistent bottom bar showing:
- Left: `SigLab v{version} [●/○] {api_url}`
- Center: (available for screen-specific hints)
- Right: UTC timestamp (updates every second)

### Loading Indicator (`loading.py`)

`LoadingIndicator` — animated braille spinner (10 frames at 100ms) during data fetches; shows static status text when idle.

## Data Flow

### API Client (`TuiApiClient`)

Async HTTP client connecting to the FastAPI dashboard at `http://localhost:3100`.

| Method | Endpoint | Used By |
|--------|----------|---------|
| `get_health()` | `GET /health` | App startup health check |
| `get_market_tickers()` | `GET /market/tickers` | Market screen |
| `get_market_klines()` | `GET /market/klines/{symbol}` | Market screen |
| `get_market_orderbook()` | `GET /market/orderbook/{symbol}` | Market screen |
| `get_risk()` | `GET /risk` | Risk screen |
| `get_evidence_graph()` | `GET /evidence-graph` | Evidence screen |
| `get_ops_board()` | `GET /ops-board` | Telemetry screen (service health) |
| `get_strategies()` | `GET /strategies` | Strategy screen |
| `get_strategy_detail()` | `GET /strategies/{hash}` | Strategy screen |
| `get_benchmark_status()` | `GET /benchmark/status` | Strategy screen |
| `get_benchmark_results()` | `GET /benchmark/results` | Strategy screen |
| `get_skill_report()` | `GET /skill-report` | Telemetry screen |
| `get_config()` | `GET /config` | Config display |
| `ws_subscribe_risk()` | `WS /ws` (subscribe: risk_score) | Risk screen (real-time) |
| `get_market_symbols()` | `GET /market/symbols` | Market screen symbol list |
| `list_paper_sessions()` | `GET /paper/sessions` | Paper screen session listing |
| `create_paper_session()` | `POST /paper/sessions` | Paper screen session creation |
| `get_paper_session()` | `GET /paper/sessions/{id}` | Paper screen session status |
| `get_paper_positions()` | `GET /paper/sessions/{id}/positions` | Paper screen positions |
| `get_paper_orders()` | `GET /paper/sessions/{id}/orders` | Paper screen order history |
| `get_paper_pnl()` | `GET /paper/sessions/{id}/pnl` | Paper screen PnL summary |
| `place_paper_order()` | `POST /paper/sessions/{id}/orders` | Paper screen order placement |
| `cancel_paper_order()` | `DELETE /paper/sessions/{id}/orders/{oid}` | Paper screen order cancel |
| `ws_connect()` | `WS /ws` | Low-level WS connection |
| `close()` | — | Close HTTP client session |

### CLI Bridge (`cli_bridge.py`)

Runs `python -m siglab.cli <args>` as an async subprocess. Returns `CliResult(returncode, stdout, stderr, command)`.

| CLI Command | Used By |
|-------------|---------|
| `paper-start --session <name>` | Paper screen (session creation) |
| `paper-status --session <id>` | Paper screen (positions/orders/PnL) |
| `ancestry --json` | Strategy + Telemetry screens (experiment runs) |
| `benchmark-eval --deck <name> --json` | Strategy screen (evaluation) |
| `benchmark-init --deck <name> --json --force` | Strategy screen (deck initialization) |
| `telemetry-report --json` | Telemetry screen (provider metrics) |
| `evidence-build`, `sodex-ws-probe`, `evidence-map`, `market-report`, `sodex-preflight`, `demo-manifest`, `wave-status` | Evidence screen (demo flow steps) |

### Direct Imports

Paper trading order placement and cancellation use direct Python subprocess calls to `SoDEXPaperPerpsClient` via `asyncio.create_subprocess_exec` with JSON stdin, bypassing the CLI bridge for tighter error handling.

### Zero-Copy Data Views (`data_views.py`)

Frozen dataclasses that wrap raw API response dicts without copying:

| View | Fields | Used By |
|------|--------|---------|
| `TickerView` | symbol, last_price, price_change_pct, volume | Market screen |
| `SymbolEntry` | name, symbol, price, change_pct, volume | Symbol list widget |
| `KlineView` | open, high, low, close, volume | Klines chart |
| `OrderBookView` | bids, asks, symbol | Order book widget |
| `PositionView` | symbol, quantity, entry_price, unrealized_pnl | Paper positions |
| `OrderView` | order_id, symbol, side, order_type, quantity, price, fill_price, status | Paper orders |
| `PnlSnapshot` | realized, unrealized, total, funding, open_count | Paper account |
| `RiskSnapshot` | composite_score, sub_scores, drawdown_history, etc. | Risk screen |
| `GraphNode` | id, label, kind, count | Evidence graph |
| `GraphEdge` | source, target, label, confidence, warning | Evidence graph |
| `StrategyEntry` | spec_hash, family, track, hypothesis, passed, score, etc. | Strategy screen |

## Styling

### CSS Architecture

All Textual CSS is in a single file (`styles/app.tcss`) because Textual parses CSS files independently — variables from one file are not available in another.

```
styles/app.tcss
├── 1. Theme Variables & Semantic Tokens
│   ├── Primitive palette ($bg, $surface, $accent-green, etc.)
│   └── Semantic aliases ($success, $warning, $panel-bg, etc.)
├── 2. App Layout (sidebar, content area, status bar)
├── 3. Market Screen
├── 4. Paper Trading Screen
├── 5. Risk Monitor Screen
├── 6. Strategy Research Screen
├── 7. Telemetry Browser Screen
└── 8. Evidence Graph & Demo Flow Screen
```

### Color Palette

| Token | Hex | Usage |
|-------|-----|-------|
| `$bg` | `#0a0a0a` | App background |
| `$surface` | `#0d1210` | Panel/widget backgrounds |
| `$surface-raised` | `#162019` | Hover states, active nav |
| `$text-primary` | `#e2ebe5` | Primary text |
| `$text-secondary` | `#a3b5a8` | Secondary text |
| `$text-muted` | `#7d9483` | Muted text, dividers |
| `$accent-green` | `#4ade80` | Gains, success, active focus |
| `$warning-yellow` | `#f0b456` | Warnings, caution |
| `$error-red` | `#f87171` | Errors, losses, high risk |
| `$info-blue` | `#60a5fa` | Info, links, multi-select |
| `$accent-purple` | `#a78bfa` | Comparison column 4 |
| `$border-dim` | `#2a3a30` | Borders, dividers |
| `$border-focus` | `#4ade80` | Focus borders |
| `$input-bg` | `#1a2a1f` | Input field backgrounds |

### Shared CSS Snippets (`formatting.py`)

Python-level CSS fragments interpolated into widget `DEFAULT_CSS`:

- `PANEL_CSS` — padding + surface background
- `SCROLLABLE_CSS` — panel + overflow-y auto
- `COMPACT_CSS` — auto height, min 5, panel
- `EXPANDABLE_CSS` — 1fr height, min 6, scrollable

### Responsive Behavior

Widgets adapt to available width:

- **Positions table**: Hides MARK column below 70 chars
- **Order history**: Hides PRICE column below 72 chars
- **Results table**: Progressively hides SPARKLINE → MAXDD → PnL% as width decreases (92 → 76 → 66 → 56 breakpoints)
- **Correlation matrix**: Limits displayed strategies and truncates names based on available width
- **Drawdown sparkline**: Caps chart width to widget width minus padding

## Key Bindings

### Global (All Screens)

| Key | Action |
|-----|--------|
| `1` | Switch to Market |
| `2` | Switch to Paper |
| `3` | Switch to Risk |
| `4` | Switch to Strategy |
| `5` | Switch to Telemetry |
| `6` | Switch to Evidence |
| `q` / `Ctrl+Q` / `Ctrl+C` | Quit |
| `?` / `F1` | Show help overlay |
| `Escape` | Go back / dismiss modal |
| `r` | Refresh current screen |
| `/` | Focus search/filter |
| `j` | Move down in list |
| `k` | Move up in list |

### Per-Screen Bindings

See the [Screens](#screens) section above for each screen's additional bindings.

## How to Launch

```bash
# Start the TUI directly
python -m siglab.tui.app

# Or via the module entry point
python -m siglab.tui

# Ensure the FastAPI dashboard is running for API-dependent screens:
python -m siglab.dashboard.server
```

The TUI connects to `http://localhost:3100` by default. The status bar shows connection state (● green = connected, ○ red = disconnected).

## Cross-Module Relationships

- **TUI → Dashboard (HTTP/WS)**: `TuiApiClient` connects to FastAPI at `http://localhost:3100` for market data, risk, evidence, ops-board, strategies, benchmarks, skill reports, and paper trading. WebSocket provides real-time risk score updates (15s periodic push).
- **TUI → CLI (subprocess bridge)**: `cli_bridge.run_cli()` invokes `python -m siglab.cli <command>` as async subprocess for paper trading, strategy ancestry, telemetry reporting, and evidence demo flow.
- **TUI → Evaluation results**: Strategy screen displays benchmark evaluation results. Telemetry screen shows provider metrics and tool usage from `telemetry-report`.

## Testing

```bash
# Run all TUI tests
python -m pytest tests/test_tui_*.py -q

# Run specific test modules
python -m pytest tests/test_tui_foundation.py -q          # Base classes, formatting, data views
python -m pytest tests/test_tui_market.py -q               # Market screen
python -m pytest tests/test_tui_paper_trading.py -q        # Paper trading screen
python -m pytest tests/test_tui_risk_screen.py -q          # Risk screen
python -m pytest tests/test_tui_strategy.py -q             # Strategy screen
python -m pytest tests/test_tui_telemetry.py -q            # Telemetry screen
python -m pytest tests/test_tui_evidence.py -q             # Evidence screen
python -m pytest tests/test_tui_validation_contract.py -q  # Validation contract tests
python -m pytest tests/test_tui_group_c_validation.py -q   # Group C validation
python -m pytest tests/test_validation_tui_group_b.py -q   # Group B validation
python -m pytest tests/test_tui_tmux_hardening.py -q       # Tmux hardening tests
```
