# SigLab

SigLab is a SoSoValue-backed research-to-action prototype for a one-person on-chain finance operator.

It ingests verified SoSoValue API data, normalizes evidence, links it with SoDEX public market context, runs bounded strategy research loops, and emits operator-facing reports with explicit risk and live-execution refusal boundaries.

It is not full SoSoValue API coverage. It is not live signed SoDEX execution. Those claims are forbidden until the missing official endpoints and credentials exist.

## What Is Real Now

- **SoSoValue API integration** for verified callable surfaces:
  - listed currencies
  - featured news
  - featured news by currency
  - ETF historical inflow chart
  - current ETF data metrics
  - real endpoints with retry/error classification
- **SoDEX public market context**:
  - REST perp data (klines, symbols, tickers, order book)
  - public WebSocket feeds
- **Paper trading engine**: `SoDEXPaperPerpsClient` with `.npy` session persistence, MARKET/LIMIT order lifecycle, funding simulation, CLI commands for session start/status/promote.
- **Promotion + Reconciliation**: composite score engine, promotion eligibility gates, backtest vs paper PnL reconciliation engine.
- **Dashboard**: FastAPI+WebSocket dashboard (port 3100) with `/health`, `/config`, `/ops-board`, `/evidence-graph`, `/skill-report`, `/risk` REST endpoints and WS streaming. Modular static UI served from `siglab/dashboard/static/`.
- **Risk Guardian**: composite risk scoring, max drawdown, correlation matrix, concentration breach detection, alert thresholds, position sizing, historical drawdown tracking. 73 risk tests + 39 e2e integration tests.
- **Architecture**: evaluator refactored from a 3635-line `core.py` into 6 focused submodules (`runner`, `gates`, `backtest`, `compile`, `feature_dsl`, `strategy_semantics`) under `siglab/evaluation/`, plus `score` and `events` modules. TypedDict orchestration contracts throughout.
- **CLI**: modular CLI with 14 subcommand modules and 30 commands covering evidence, market reports, demo artifacts, benchmarking, config validation, paper trading, dashboard lifecycle, deployment, SoDEX operations, research run loops, and ancestry management. All 30 commands use Rich formatting (tables, panels, semantic colors) with `--no-color` flag and `NO_COLOR` env var support.
- **Terminal UI (TUI)**: Textual+Rich terminal application at `siglab/tui/` with 6 screens (Market, Paper Trading, Risk Monitoring, Strategy Research, Telemetry Browser, Evidence Graph & Demo Flow), navigation sidebar, status bar, FastAPI httpx API client to dashboard on `:3100`, async CLI bridge, theme system with centralized color constants, consistent keyboard shortcuts, WCAG AA contrast, and WebSocket reconnection with backoff. 595 TUI-specific tests.
- **SoDEX** signed-write scaffolding, dry-run signing inputs, nonce/signature validation, and preflight refusal.
- **B.AI-backed** planner/writer/reflector routing with run-level telemetry artifacts.
- Evidence graph, market report, demo report, demo manifest, strict profile, and full local tests.
- **2200+ tests** covering foundation fixes (8 critical bugs fixed: NameError, duplicate yield_flows, config defaults, hardcoded leak_checks_passed, liquidation timestamp, annualization 365.25, percentile interpolation, dead PT/lending code), golden-file regression tests, and comprehensive e2e integration tests.

## What Is Not Real Yet

- Full SoSoValue coverage for Index, Macro, Crypto Stocks, BTC Treasuries, Fundraising, or Analysis Charts.
- Live signed SoDEX writes.
- Private/account SoDEX WebSocket validation.
- SSI/Index on-chain integration.
- USD cost enforcement for B.AI usage. SigLab tracks B.AI Credits where available, not dollars.

## Quick Start

Use Python 3.12.

```bash
pip install -e .
cp .env.example .env
cp config.example.json config.json
```

Configure secrets locally. Do not commit them.

- SoSoValue API key: `config.json` or `SOSOVALUE_API_KEY`
- B.AI provider config: `.siglab-provider.env`
- SoDEX signer/account config: only when doing signed-path validation

## Buildathon Demo Flow

Build live SoSoValue evidence:

```bash
python3 -m siglab.cli evidence-build \
  --currency BTC \
  --etf-type us-btc-spot \
  --news-page-size 20 \
  --news-pages 2 \
  --output runs/evidence/live_sosovalue_probe_btc_pages.jsonl \
  --summary-output runs/evidence/live_sosovalue_probe_btc_pages.summary.json \
  --json
```

Probe public SoDEX WebSocket market evidence:

```bash
python3 -m siglab.cli sodex-ws-probe \
  --channel allBookTicker \
  --timeout-seconds 12 \
  --evidence-output runs/evidence/sodex_ws_evidence.jsonl \
  --json
```

Generate an operator market report:

```bash
python3 -m siglab.cli market-report \
  --entity BTC \
  --sosovalue-evidence runs/evidence/live_sosovalue_probe_btc_pages.jsonl \
  --sodex-evidence runs/evidence/sodex_ws_evidence.jsonl \
  --output runs/market_report_latest.json \
  --html-output runs/market_report_latest.html \
  --json
```

Start a paper trading session:

```bash
python3 -m siglab.cli paper-start --session my-first-paper
python3 -m siglab.cli paper-status --session <session-id>
```

Promote a paper session if eligible:

```bash
python3 -m siglab.cli paper-promote --session <session-id>
```

Launch the dashboard:

```bash
python3 -m siglab.cli dashboard-start --port 3100
```

Visit `http://localhost:3100` for the ops board, experiment browser, risk panel, and evidence graph.

Run the benchmark suite:

```bash
python3 -m siglab.cli benchmark-init
python3 -m siglab.cli benchmark-eval
python3 -m siglab.cli benchmark-status
```

Index demo artifacts for judges/operators:

```bash
python3 -m siglab.cli demo-manifest \
  --output runs/demo_manifest_latest.json \
  --html-output runs/demo_manifest_latest.html \
  --json
```

Check live boundaries:

```bash
python3 -m siglab.cli sodex-preflight --json
python3 -m siglab.cli valuechain-preflight --json
```

## Research Loop

One deterministic iteration:

```bash
python3 -m siglab.cli run --track trend_signals --skip-llm --iterations 1
```

B.AI-backed bounded loop:

```bash
set -a && . ./.siglab-provider.env && set +a
python3 -m siglab.cli run \
  --track trend_signals \
  --iterations 1 \
  --max-call-estimated-credits 3000 \
  --max-total-credits 6000 \
  --max-provider-errors 1 \
  --agent-label demo-deepseek-v4-flash \
  --run-label demo-deepseek-v4-flash
```

Telemetry:

```bash
python3 -m siglab.cli telemetry-report --track trend_signals --json
```

## Dashboard

SigLab includes a FastAPI+WebSocket dashboard (port 3100) for real-time visibility into the system.

**REST endpoints**:
| Endpoint | Description |
|---|---|
| `/health` | Service health, version, uptime |
| `/config` | Full SigLab configuration dump |
| `/ops-board` | Operational artifacts with staleness checks |
| `/evidence-graph` | Evidence lineage graph |
| `/skill-report` | Agent skill execution telemetry |
| `/risk` | Portfolio risk metrics (composite score, drawdown, alerts, concentration) |

**WebSocket streaming**: subscribe to real-time updates on selected endpoints.

**CLI commands**:

```bash
python3 -m siglab.cli dashboard          # show dashboard status
python3 -m siglab.cli dashboard-start    # start dashboard server
python3 -m siglab.cli dashboard-stop     # stop dashboard server
```

The dashboard serves static UI from `siglab/dashboard/static/`. Use the `/risk` endpoint to view composite risk scores, max drawdown, correlation matrices, concentration breach reports, and historical drawdown events.

## Terminal UI (TUI)

A full Textual+Rich terminal application at `siglab/tui/` for navigating SigLab from the terminal.

**Architecture**:
| Component | Location |
|---|---|
| App scaffold + navigation | `siglab/tui/app.py` |
| Screens (6) | `siglab/tui/screens/` — Market, Paper Trading, Risk, Strategy, Telemetry, Evidence |
| Shared widgets | `siglab/tui/widgets/` |
| API client | `siglab/tui/api_client.py` — `httpx.AsyncClient` to dashboard on `:3100` |
| CLI bridge | `siglab/tui/cli_bridge.py` — async subprocess invocations of `siglab.cli` commands |
| Theme / styles | `siglab/tui/styles/` — Textual CSS with centralized design tokens |

**Screens**:

| Screen | Key Features |
|---|---|
| **Market Overview** | Symbol list with search/filter, ASCII sparkline klines chart, ticker table with 24h change, order book depth. Auto-refresh every 30s. |
| **Paper Trading** | Positions table with PnL, order form (MARKET/LIMIT), order history, PnL sparkline chart. Orders via CLI bridge subprocess. |
| **Risk Monitoring** | Composite score gauge, drawdown sparkline, correlation matrix heatmap, alert stream. WebSocket real-time updates. |
| **Strategy Research** | Strategy list with search/filter, results table with score/PnL/Sharpe, multi-select comparison (2–4 strategies), evaluation via CLI bridge. |
| **Telemetry Browser** | Run list with filters (date/status/track), provider metrics, tool usage, run comparison, service health. |
| **Evidence Graph & Demo Flow** | Evidence graph tree view, edge details, 8-step interactive buildathon demo walkthrough. |

**Key design decisions**:
- Centralized color constants replace ~200 hardcoded hex values; single `app.tcss` stylesheet.
- WCAG AA contrast across all screens.
- Consistent keyboard shortcuts: `q` quit, `?` help overlay, `Escape` back, `1`–`6` screen jump, `j`/`k` navigate.
- Friendly error messages, loading indicators, toast notifications.
- WebSocket reconnection with exponential backoff.

**Run the TUI**:

```bash
python3 -m siglab.cli tui          # launch the terminal UI
```

Requires the dashboard to be running on `:3100` for API and WebSocket data.

## Paper Trading

`SoDEXPaperPerpsClient` simulates order execution on real SoDEX market data without submitting live trades.

- **Session persistence**: `.npy` files survive process restarts.
- **Order types**: MARKET and LIMIT orders with a full lifecycle (OPEN → FILLED / CANCELLED / EXPIRED).
- **Funding simulation**: 8-hour perp funding intervals.
- **CLI commands**:

```bash
python3 -m siglab.cli paper-start        # create a new paper session
python3 -m siglab.cli paper-status       # show session status
python3 -m siglab.cli paper-promote      # check eligibility and promote
```

**Promotion engine**: composite score based on Sharpe, win rate, and consistency. Gate checks (consecutive profitable days, minimum trading days, score threshold) determine real-money readiness.

**Reconciliation**: backtest vs paper PnL reconciliation engine validates strategy consistency across simulation modes.

## Risk Guardian

The `siglab.risk.guardian` module provides portfolio-level risk analysis.

**Capabilities**:

- **Composite risk score**: weighted blend of Sharpe, drawdown, concentration, and correlation risk.
- **Max drawdown**: trailing peak-to-trough analysis.
- **Correlation matrix**: cross-strategy pair correlations.
- **Concentration breach detection**: alerts when position concentration exceeds configurable limits.
- **Alert thresholds**: configurable severity levels (info, warning, critical) per metric.
- **Position sizing**: risk-limit-aware position size computation.
- **Historical drawdown tracking**: event detection with recovery time.

Accessible via the dashboard `/risk` REST endpoint and WebSocket stream.

## Agent And Skill Wiring

SigLab uses repo-local skills:

- `.agents/skills/siglab-signal-scout`
- `.agents/skills/siglab-spec-writer`
- `.agents/skills/siglab-run-reviewer`

The loop loads these through `ResearchPlannerRunner`, `SpecWriterRunner`, and `ReflectionRunner`. Workspace setup mirrors `.agents/skills` into `.claude/skills` for Claude-compatible tooling.

## Validation

```bash
python3 -m pytest -q
python3 -m siglab.cli profile --strict --json
```

Current expected local state:

- **2200+ tests** passing across all areas
- strict profile has zero findings
- signed SoDEX live validation remains blocked unless credentials are configured

## Source-Of-Truth Docs

- `docs/sosovalue-api-surface.yaml`
- `docs/sodex-api-surface.yaml`
- `docs/sosovalue-ecosystem-surface.yaml`
- `docs/buildathon-readiness-audit.md`
- `docs/demo-script.md`
- `docs/demo-gap-report.md`
- `docs/provider-routing.md`
- `docs/access-and-testnet-plan.md`
- `docs/product-flow-validation.md`

The generated `runs/demo_manifest_latest.html` is the closest current artifact to a buildathon demo panel. It is still static HTML, not a full app.
