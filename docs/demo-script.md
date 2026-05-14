# SigLab Demo Script

This script is strict: it proves live data ingestion and operator reporting, but it does not claim signed SoDEX execution unless credentials pass preflight.

## Preconditions

- Repo-local SoSoValue config exists at `config.json` or `SOSOVALUE_CONFIG_PATH`.
- Repo-local B.AI config exists at `.siglab-provider.env` if LLM loop demo is needed.
- No SoDEX signed-write claim is allowed unless `siglab sodex-preflight --json` reports `live_write_allowed: true`.

## 1. Build SoSoValue Evidence

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

Expected proof:

- `record_count` greater than zero.
- ETF records from `sosovalue.etf_historical_inflow`.
- Feed records from `sosovalue.featured_news` or `sosovalue.featured_news_by_currency`.
- Warnings remain non-causal.

## 2. Probe SoDEX Public WebSocket

```bash
python3 -m siglab.cli sodex-ws-probe \
  --channel allBookTicker \
  --timeout-seconds 12 \
  --evidence-output runs/evidence/sodex_ws_evidence.jsonl \
  --json
```

Expected proof:

- `ready: true`.
- `signed: false`.
- `live_write: false`.
- `evidence_records_appended` greater than zero.
- BTC quote evidence includes bid/ask aliases when present.

## 3. Render Evidence Graph

```bash
python3 -m siglab.cli evidence-map \
  --evidence runs/evidence/live_sosovalue_probe_btc_pages.jsonl \
  --output runs/evidence/evidence_graph.html \
  --json
```

Expected proof:

- HTML file exists.
- Graph says links are not causal claims.

## 4. Generate Market Report

```bash
python3 -m siglab.cli market-report \
  --entity BTC \
  --sosovalue-evidence runs/evidence/live_sosovalue_probe_btc_pages.jsonl \
  --sodex-evidence runs/evidence/sodex_ws_evidence.jsonl \
  --output runs/market_report_latest.json \
  --html-output runs/market_report_latest.html \
  --json
```

Expected proof:

- Status is `READY_FOR_OPERATOR_REVIEW` when ETF, feed, and quote evidence exist.
- Report includes ETF flow direction, SoDEX bid/ask, recent news titles, and live-write refusal.
- Report explicitly states causality is not claimed.
- Report selection semantics say it uses parsed timestamps and skips invalid required values, so stale or malformed duplicate rows should not win.
- Report includes `decision_support.stance`, required confirmations, invalidation checks, next actions, and risk controls.

## 5. Capture Provider Telemetry

```bash
python3 -m siglab.cli telemetry-report \
  --track trend_signals \
  --json > runs/latest_telemetry_report.json
```

Expected proof:

- `provider_metrics_status` is `present` after a run that wrote provider metrics.
- Provider metrics include latency, usage tokens when returned by upstream, B.AI Credits estimate when priced tokens exist, and context/credit pressure event counts.
- `cost_usd` remains `null`; the demo must say Credits are not USD.

## 6. Verify Live Boundary

```bash
python3 -m siglab.cli sodex-preflight --json
```

Expected proof:

- If signed credentials are missing, live write is refused with exact missing prerequisites.
- No deployment/demo language should imply real trading readiness while this fails.

## 7. Optional B.AI Loop With Budget Guard

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

Expected proof:

- Provider/model is recorded in traces.
- Credits telemetry is recorded in `runs/provider_metrics/*.jsonl` when B.AI returns usage.
- If the estimated one-call budget is too low, the run refuses before provider HTTP.

## Brutal Red Flags To Say Out Loud

- SoSoValue coverage is not full ecosystem coverage. It is currently verified ETF/news/currency plus blocked maps for unverified modules.
- SoDEX public read/WebSocket proof is not signed execution proof.
- Market reports are evidence-linked explanations, not predictions and not causal claims.
- USD spend is not enforced. B.AI Credits are enforced as Credits, not dollars.
- A strategy run can honestly return no passing candidate. Do not cherry-pick or relabel a rejected candidate as an opportunity.

## 8. Build Demo Manifest

```bash
python3 -m siglab.cli demo-manifest \
  --output runs/demo_manifest_latest.json \
  --html-output runs/demo_manifest_latest.html \
  --json
```

Expected proof:

- Manifest indexes market report, evidence graph, telemetry report, provider metrics, API surface docs, and readiness audit.
- HTML panel exists at `runs/demo_manifest_latest.html`.
- `sodex_live_write_allowed` remains false unless SoDEX preflight passes.
- `causality_claimed` and `usd_cost_claimed` remain false.

## 9. Open Operator Ops Board

```bash
python3 -m siglab.cli dashboard --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765/ops`.

Expected proof:

- `/ops` reads `runs/demo_manifest_latest.json`, `runs/latest_telemetry_report.json`, `runs/market_report_latest.json`, and `runs/sodex_preflight_latest.json`.
- Missing or malformed artifacts are shown as missing/malformed, not silently treated as ready.
- The board shows SoSoValue flow status, SoDEX public evidence, live-write refusal state, provider telemetry, red flags, and latest market-report stance.
- The board is read-only. It is a demo/operator monitor, not a live trading surface.
