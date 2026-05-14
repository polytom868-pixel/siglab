# SigLab Production Hardening Report

## Wayfinder Parity Matrix

| WAYFINDER_PATH | SIGLAB_PATH | STATUS |
| --- | --- | --- |
| `wayfinder_autolab/data/lake.py` | `siglab/data/store.py` | RENAMED_BUT_INCOMPLETE |
| `wayfinder_autolab/data/providers.py` | `siglab/data/feeds.py` | PARTIAL |
| `wayfinder_autolab/live/runtime.py` | `siglab/live/runtime.py` | PARTIAL |
| `wayfinder_autolab/live/exporter.py` | `siglab/live/exporter.py` | PARTIAL |
| `wayfinder_autolab/live/generated_strategies/__init__.py` | `siglab/live/deployed_agents/__init__.py` | RENAMED_BUT_INCOMPLETE |
| `wayfinder_autolab/llm/kimi.py` | none | REGRESSED |
| `wayfinder_autolab/models.py` | `siglab/schemas.py` | RENAMED_BUT_INCOMPLETE |
| `wayfinder_autolab/settings.py` | `siglab/config.py` | RENAMED_BUT_INCOMPLETE |
| `wayfinder_autolab/orchestration/*` | `siglab/orchestration/*` | MATCHED |
| `wayfinder_autolab/research/*` | `siglab/research/*` | MATCHED |
| `wayfinder_autolab/search/*` | `siglab/search/*` | MATCHED |
| `wayfinder_autolab/workspace/*` | `siglab/workspace/*` | MATCHED |
| `wayfinder_autolab/tools/*` | `siglab/tools/*` | MATCHED |
| `wayfinder_autolab/cli.py` | `siglab/cli.py` | PARTIAL |
| `wayfinder_autolab/benchmark.py` | `siglab/benchmark.py` | MATCHED |
| `wayfinder_autolab/run_config.py` | `siglab/run_config.py` | MATCHED |
| `wayfinder_autolab/families.py` | `siglab/families.py` | PARTIAL |
| `wayfinder_autolab/feature_dsl.py` | `siglab/feature_dsl.py` | MATCHED |
| `wayfinder_autolab/strategy_semantics.py` | `siglab/strategy_semantics.py` | MATCHED |

## SoSoValue Capability Matrix

| DOC MODULE | ENDPOINT | SIGLAB WRAPPER | TESTED | CACHED | RETRIED | RATE-LIMITED | USED BY STRATEGY | STATUS |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Currency & Pairs | `POST /openapi/v1/data/default/coin/list` | `SoSoValueClient.listed_currencies` | yes | yes | yes | yes | no | IMPLEMENTED |
| Feeds | `GET /api/v1/news/featured` | `SoSoValueClient.featured_news` | yes | yes | yes | yes | no | IMPLEMENTED |
| Feeds | `GET /api/v1/news/featured/currency` | `SoSoValueClient.featured_news_by_currency` | yes | yes | yes | yes | yes | IMPLEMENTED |
| ETF | `POST /openapi/v2/etf/historicalInflowChart` | `SoSoValueClient.etf_historical_inflow` | yes | yes | yes | yes | yes | IMPLEMENTED |
| ETF | `POST /openapi/v2/etf/currentEtfDataMetrics` | `SoSoValueClient.etf_current_metrics` | yes | yes | yes | yes | no | IMPLEMENTED |
| Market/reference layer | currency info / market snapshot / trading pairs / historical klines / supply / sector spotlight | none | no | no | no | no | no | BLOCKED |
| SoSoValue Index | index data | none | no | no | no | no | no | BLOCKED |
| Crypto Stocks | crypto stocks data | none | no | no | no | no | no | BLOCKED |
| BTC Treasuries | BTC treasury data | none | no | no | no | no | no | BLOCKED |
| Fundraising | fundraising data | none | no | no | no | no | no | BLOCKED |
| Macro | macro events / event history | none | no | no | no | no | no | BLOCKED |
| Analysis Charts | analysis chart data | none | no | no | no | no | no | BLOCKED |

Blocked means the official callable endpoint page was not verified from the accessible SoSoValue GitBook API navigation during this sweep. It is not treated as implemented.

## Evidence Engine

- `siglab evidence-build` builds JSONL evidence from implemented, verified SoSoValue surfaces only.
- Current normalized modules: `ETF`, `Feeds`.
- Current normalized relations: `total_net_inflow`, `total_net_assets`, `news_mention`.
- Current graph relation: `feed_event_near_etf_flow`.
- The graph relation is explicitly temporal/categorical and carries a `not causal` warning. It is not treated as predictive proof.
- `siglab evidence-build` also writes a compact `.summary.json` sidecar for loop prompts and operator inspection, so consumers do not have to stuff raw JSONL into LLM context.
- Evidence writes are idempotent by deterministic `evidence_id`; immediate live repeat skipped 604 duplicates and appended 0 rows.
- SoSoValue news pagination is supported for the verified featured-news endpoints, with the documented `pageSize <= 100` enforced before upstream calls.
- SoSoValue client now enforces a conservative process-local rolling `20 calls/min` budget from the public developer page. This is not distributed; multi-process deployments still need a shared limiter.
- SoSoValue ETF current metrics are live-smoked and endpoint-validated for aggregate metric objects plus ETF row fields. Live v2 uses `totalTokenHoldings`; legacy V1 docs refer to BTC-specific holdings.
- Live validation on 2026-05-13 built 608 evidence records and 8 cross-module links from ETF inflow plus two pages of featured-news surfaces.
- Vector retrieval is not added yet because the deterministic evidence schema and relation quality must prove useful first.

## Autorun Reality

- `inspect`: live SoSoValue-backed ETF proxy passed.
- `run`: one iteration passed with `--skip-llm`.
- `resume`: resumed a saved run at iteration 2 and completed one more iteration.
- `loop_forever`: CLI supports `--iterations 0`; it was not executed indefinitely.
- `export`: dry export produced a deployable package. Scheduling still requires a real runner client.
- `live_dry_split`: dry LLM/run/export behavior works. Real LLM mode is blocked by absent provider API keys.

## Remaining Unsafe Areas

- SigLab still lacks verified Kimi parity after the refactor removed `llm/kimi.py`.
- OpenRouter, Kimi, and Claude live smoke calls are blocked by missing keys.
- SoDEX execution is fail-loud by design when no real client is injected; that is safer than fake fills but not complete live execution.
- Current market features are ETF-proxy-derived and sparse for perp-specific funding/return metrics.

## SoDEX Operator Boundary

- `siglab sodex-preflight --json` reports public-read readiness, signed-path prerequisites, schema pinning, documented endpoint weights, and exact missing live-write inputs without printing secrets.
- `siglab sodex-preview` is dry-run only: it builds canonical body, canonical signing payload, signature input, nonce, domain, and documented request weight without submitting or fabricating a signature.
- Dry-run previews accept official SDK enum names such as `BUY`, `SELL`, `LIMIT`, `MARKET`, `GTC`, `FOK`, `IOC`, `GTX`, `BOTH`, `LONG`, `SHORT`, `NORMAL`, `STOP`, `BRACKET`, `ATTACHED_STOP`, `ISOLATED`, and `CROSS`, then convert to numeric canonical payload fields.
- `siglab sodex-ws-probe` performs bounded public WebSocket subscription probes without signing or submitting orders; latest mainnet perps `allBookTicker` probe produced 76 evidence records.
- Real signed-write validation remains blocked until the operator supplies actual SoDEX signed-path credentials and account setup.
