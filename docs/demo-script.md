# SigLab Demo Script

This script is strict: it proves live data ingestion and operator reporting, but it does not claim signed SoDEX execution unless credentials pass preflight.

## Preconditions

- Repo-local SoSoValue config exists at `config.json` or `SOSOVALUE_CONFIG_PATH`.
- Repo-local B.AI config exists at `.siglab-provider.env` if LLM loop demo is needed.
- No SoDEX signed-write claim is allowed unless `siglab sodex-preflight --json` reports `live_write_allowed: true`.

## 1. One-Shot Demo Run

The single command that automatically collects all evidence and produces the full report:

```bash
python3 -m siglab.cli demo run --json
```

Expected proof:

- `preflight` reports signed execution state honestly.
- `evidence` section shows non-zero files (SoSoValue + SoDEX).
- `market_report` shows status `PARTIAL` or `READY_FOR_OPERATOR_REVIEW` based on evidence completeness.
- Market report includes `sosovalue_rows_read > 0` and `sodex_rows_read > 0` when live data is available.
- `telemetry_report` provides tool/providers metrics.

The demo run writes `runs/demo_run_latest.json` containing:
- `sodex_preflight` — live-boundary readiness
- `demo_manifest` — artifact inventory
- `market_report` — entity, status, warnings
- `evidence` — evidence file paths and generation errors
- `telemetry_report` — trace/providers/evidence telemetry

Evidence files are written to `runs/evidence/`:
- `runs/evidence/sosovalue_evidence_*.jsonl` — SoSoValue ETF inflow + news records
- `runs/evidence/sodex_rest_evidence_*.jsonl` — SoDEX REST perps ticker + book ticker records

## Fast Refresh Existing Proof Artifacts

If evidence artifacts already exist, refresh the operator board inputs without live trading:

```bash
python3 -m siglab.cli demo manifest --json
```

Expected proof:

- Manifest indexes market report, evidence files, telemetry report, provider metrics, and readiness audit.
- Signed SoDEX live execution remains refused unless preflight prerequisites really pass.
- Missing evidence yields a partial market report instead of a fake opportunity.
- Evidence telemetry in the manifest shows evidence_count and evidence_sources.

## 2. Market Report (standalone)

```bash
python3 -m siglab.cli market-report \
  --entity BTC \
  --output runs/market_report_latest.json \
  --html-output runs/market_report_latest.html \
  --json
```

By default reads the latest evidence from `runs/evidence/`.

Expected proof:

- Status is `READY_FOR_OPERATOR_REVIEW` when ETF, feed, and quote evidence exist.
- Report includes ETF flow direction, SoDEX bid/ask, recent news titles, and live-write refusal.
- Report explicitly states causality is not claimed.
- Report includes `decision_support.stance`, required confirmations, invalidation checks, next actions, and risk controls.

## 3. Capture Provider Telemetry

```bash
python3 -m siglab.cli telemetry-report \
  --track trend_signals \
  --json
```

Expected proof:

- `provider_metrics_status` is `present` after a run that wrote provider metrics.
- Provider metrics include latency, usage tokens when returned by upstream, B.AI Credits estimate when priced tokens exist, and context/credit pressure event counts.
- `cost_usd` remains `null`; the demo must say Credits are not USD.
- When evidence files exist, telemetry includes `evidence.evidence_count` and `evidence.evidence_sources`.

## 4. Verify Live Boundary

```bash
python3 -m siglab.cli sodex-preflight --json
```

Expected proof:

- If signed credentials are missing, live write is refused with exact missing prerequisites.
- No deployment/demo language should imply real trading readiness while this fails.

## 5. Optional B.AI Loop With Budget Guard

```bash
set -a && . ./.siglab-provider.env && set +a
python3 -m siglab.cli operator \
  --session demo-session-1
```

Expected proof:

- Provider/model is recorded in traces.
- Credits telemetry is recorded in `runs/provider_metrics/*.jsonl` when the provider returns usage.

## Brutal Red Flags To Say Out Loud

- SoSoValue coverage is not full ecosystem coverage. It is currently verified ETF/news/currency plus blocked maps for unverified modules.
- SoDEX public read/WebSocket proof is not signed execution proof.
- Market reports are evidence-linked explanations, not predictions and not causal claims.
- USD spend is not enforced. B.AI Credits are enforced as Credits, not dollars.
- A strategy run can honestly return no passing candidate. Do not cherry-pick or relabel a rejected candidate as an opportunity.

## Available CLI Commands

```bash
# Demo / evidence pipeline
python3 -m siglab.cli demo run --json          # full collection → manifest → market → telemetry
python3 -m siglab.cli demo manifest --json      # index existing artifacts

# Market report
python3 -m siglab.cli market-report --entity BTC --json

# Telemetry
python3 -m siglab.cli telemetry-report --track trend_signals --json

# SoDEX boundary
python3 -m siglab.cli sodex-preflight --json

# Operator pipeline
python3 -m siglab.cli operator --session demo --json

# Dashboard
python3 -m siglab.cli dashboard --port 8080
python3 -m siglab.cli dashboard-start --port 8080
```
