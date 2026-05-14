# SigLab

SigLab is a SoSoValue-backed research-to-action prototype for a one-person on-chain finance operator.

It ingests verified SoSoValue API data, normalizes evidence, links it with SoDEX public market context, runs bounded strategy research loops, and emits operator-facing reports with explicit risk and live-execution refusal boundaries.

It is not full SoSoValue API coverage. It is not live signed SoDEX execution. Those claims are forbidden until the missing official endpoints and credentials exist.

## What Is Real Now

- SoSoValue API integration for verified callable surfaces:
  - listed currencies
  - featured news
  - featured news by currency
  - ETF historical inflow chart
  - current ETF data metrics
- SoDEX public REST and public WebSocket market context.
- SoDEX signed-write scaffolding, dry-run signing inputs, nonce/signature validation, and preflight refusal.
- B.AI-backed planner/writer/reflector routing with run-level telemetry artifacts.
- Evidence graph, market report, demo report, demo manifest, strict profile, and full local tests.

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

Current expected local state after this hardening pass:

- full suite passes
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
