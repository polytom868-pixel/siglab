# SigLab

Research-to-action prototype for a one-person on-chain finance operator. Ingests SoSoValue evidence, links it with SoDEX market context, runs bounded strategy research loops, and emits operator-facing reports.

## Quick Start

```bash
pip install -e .
cp .env.example .env
```

Configure secrets locally — do not commit them:
- **SoSoValue API key**: `config.json` or `SOSOVALUE_API_KEY`
- **B.AI provider config**: `.siglab-provider.env`
- **SoDEX signer config**: only for signed-path validation

## How It Works

1. **Evidence** — `evidence-build` fetches verified SoSoValue API data (news, ETF flows, currency listings)
2. **Market context** — `sodex-ws-probe` pulls public SoDEX market data (klines, orderbook, tickers)
3. **Research loop** — `run` triggers planner/writer/reflector stages to generate and evaluate signal specs
4. **Report** — `market-report` merges evidence with market context into operator-facing reports
5. **Act** — paper trading simulates positions; risk guardian monitors portfolio exposure

## Core Commands

```bash
# Build SoSoValue evidence
python3 -m siglab.cli evidence-build --currency BTC --json

# Research loop (deterministic, no LLM)
python3 -m siglab.cli run --track trend_signals --skip-llm --iterations 1

# B.AI-backed loop with credit limits
python3 -m siglab.cli run --track trend_signals --iterations 1 \
  --max-total-credits 6000 --agent-label demo

# Operator market report
python3 -m siglab.cli market-report --entity BTC --json

# Validate boundaries
python3 -m siglab.cli sodex-preflight --json
python3 -m siglab.cli valuechain-preflight --json
```

## What Is Real Now

- **SoSoValue API**: currencies, news, ETF data — real endpoints with retry/error classification
- **SoDEX public context**: REST perp data + WebSocket feeds
- **Paper trading engine**: MARKET/LIMIT order lifecycle, funding simulation, session persistence
- **Risk Guardian**: composite scoring, drawdown, correlation, concentration alerts
- **Research loop**: planner/writer/reflector orchestration with lineage tracking
- **TUI**: Textual terminal app with 6 screens (market, paper, risk, strategy, telemetry, evidence)
- **CLI**: modular commands with Rich formatting
- **2200+ tests** across unit, integration, and golden-file regression

## What Is Not Real Yet

- Full SoSoValue coverage (Index, Macro, Crypto Stocks, Fundraising, Analysis Charts)
- Live signed SoDEX execution
- Private/account SoDEX WebSocket streams
- SSI/Index on-chain integration
- USD cost enforcement for B.AI (credits tracked, not dollars)

## Paper Trading

```bash
python3 -m siglab.cli paper-start --session my-first-paper
python3 -m siglab.cli paper-status --session <id>
python3 -m siglab.cli paper-promote --session <id>
```

Promotion engine gates eligibility via composite score (Sharpe, win rate, consistency). Reconciliation validates backtest vs paper PnL alignment.

## Terminal UI (TUI)

Textual+Rich terminal app at `siglab/tui/`:

| Screen | Features |
|---|---|
| Market Overview | Symbol search, sparkline klines, ticker table, order book depth |
| Paper Trading | Positions, order form (MARKET/LIMIT), order history, PnL chart |
| Risk Monitoring | Composite score gauge, drawdown, correlation heatmap, alerts |
| Strategy Research | Strategy list, results table, multi-select comparison |
| Telemetry Browser | Run list with filters, provider metrics, tool usage |
| Evidence & Demo | Evidence graph, 8-step interactive demo walkthrough |

```bash
python3 -m siglab.cli tui
```

## Validation

```bash
python3 -m pytest -q
python3 -m siglab.cli profile --strict --json
```

Current: 2200+ tests passing, strict profile has zero findings. Signed SoDEX validation requires credentials.

## License

MIT
