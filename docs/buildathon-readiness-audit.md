# Buildathon Readiness Audit

Generated: 2026-05-14

This audit is intentionally strict. Mock-only behavior does not count as real integration. Dry-run SoDEX signing does not count as validated live execution.

## Required

| Requirement | Status | Evidence | Red flags | Shortest score upgrade |
| --- | --- | --- | --- | --- |
| Must genuinely integrate SoSoValue API | PASS | Centralized `SoSoValueClient` uses `x-soso-api-key`, retries, caching, dedupe, metrics, and live evidence builds from ETF/news/currency surfaces. See `docs/sosovalue-api-surface.yaml` and `siglab/data/sosovalue_client.py`. | Surface is still narrow versus product ecosystem. Index, Macro, Crypto Stocks, BTC Treasuries, Fundraising, and Analysis Charts lack verified callable wrappers. | Keep crawling official docs and add only verified high-value endpoints with parser/tests/live smoke. |
| Must have a clear use case and real user value | PARTIAL | SigLab can run research loops, ingest SoSoValue ETF/news evidence, link temporal evidence, generate strategy candidates, export guarded live-adjacent artifacts, emit `siglab market-report`, and expose `/ops` as a read-only operator board. | Product story is better but still not a fully guided app; screenshots and a scripted demo recording are still needed. | Add screenshots/video and turn `/ops` into the first screen in the demo flow. |
| Must complete a basic flow from data input to output | PASS | `siglab evidence-build` fetches live SoSoValue data; `sodex-ws-probe` captures live SoDEX quote evidence; `market-report` joins ETF flow, feed context, latest valid quote, and live-write preflight into JSON/HTML decision support. The report rejects stale/malformed evidence rows in tests and includes confirmations, invalidation checks, and risk controls. | Final trade action still refuses without signed prerequisites, which is correct but weak for “execution” demos. | Package the generated artifacts and script into a single demo folder. |
| Must provide verifiable demo materials and documentation | PASS | Docs exist for provider routing, loop supervision, production hardening, API surfaces, and this audit. `docs/demo-script.md`, `runs/demo_report_latest.html`, `runs/market_report_latest.html`, and `runs/demo_manifest_latest.html` provide proof artifacts. | No live interactive app panel yet; current panel is generated static HTML. | Add screenshots and a one-command demo runner. |

## Bonus / Stronger Target

| Requirement | Status | Evidence | Red flags | Shortest score upgrade |
| --- | --- | --- | --- | --- |
| Integration with SoDEX API | PARTIAL | Public perps REST reads exist and are live-probed for symbols/coins/tickers/miniTickers/mark prices/book tickers/orderbook/klines/trades and account reads. Public WebSocket `allBookTicker` is live-probed and normalized into evidence. Signed dry-run scaffolding exists with nonce, canonical payload hash, EIP-712, headers, and preflight. | No live signed write validation; no private account stream validation. | Add signed credential validation and private stream checks when credentials are available. |
| AI-enhanced functionality | PASS | B.AI provider routing, planner/writer/reflector, live DeepSeek V4 Flash work cases, skill telemetry, evidence-summary prompt injection, run-level provider metrics artifacts, and committed `trend_signals_external` benchmark deck exist. `telemetry-report` now surfaces provider usage, estimated Credits, latency, context pressure, and malformed/missing metrics status where available. | DeepSeek planner can still over-probe on first attempt; model quality varies by stage; USD cost is not verified. | Keep reducing first-pass tool waste and use `--max-total-credits` plus `--max-call-estimated-credits` for honest Credits-budgeted loops. |
| Help users discover opportunities, generate signals, or explain markets | PASS | Research loops, evidence graph, and `market-report` can connect ETF flows, SoSoValue feeds, and SoDEX quote evidence into a non-causal operator explanation with decision-support next actions. | Cross-module evidence is still mostly ETF/news/currency plus SoDEX quote; no verified Macro/Index/Treasury/Fundraising wrappers. | Add missing official endpoints when verified and promote report output into dashboard/panel UX. |
| Risk control, confirmation mechanisms, and security awareness | PASS | Live SoDEX refuses without prerequisites; dry-run signing does not fake readiness; provider/secrets are redacted; strict profiler fails stubs. | Distributed rate limiting and live exchange confirmations are not implemented. | Add multi-process request-weight coordination and signed live confirmation checks. |
| Complete flow from insight to action | PARTIAL | Insight -> candidate -> backtest/evaluate -> export/preflight exists; SoDEX preview shows signed request inputs without submission. | Real action is blocked by credentials and unvalidated signed writes. | Add live credential setup docs and a testnet/mainnet validation checklist. |
| Better product experience: panels, bots, skills, workflows | PARTIAL | Dashboard skill reports, structured run artifacts, evidence graph, market report, static demo manifest panel, and `/ops` operator board exist; CLI has inspect/run/resume/evidence-build/preflight. | `/ops` is read-only and artifact-backed; no interactive guided workflow or screenshots yet. | Add screenshots and a single guided demo entry command that refreshes artifacts then opens `/ops`. |

## Top Red Flags

- SoSoValue API coverage remains incomplete beyond verified ETF, Feeds, and currency list surfaces.
- SoDEX private/account WebSocket streams are not credential-validated.
- SoDEX signed live validation is externally blocked and must stay marked blocked until credentials exist.
- ValueChain RPC chain-id preflight is present and live-proven, but no SSI/index contract integration exists.
- Predictive estimates are empirical but not calibrated; calibration error is still unknown.
- Product demo path is now reviewable as static HTML, but still not a live app experience.
- Telemetry is now artifact-backed, but it is still process-local and not a centralized observability backend.
- README was stale before this pass; it now documents the real demo commands and forbidden claims, but a polished non-engineer panel remains missing.
- `docs/product-flow-validation.md` records a real BTC/ETH/SOL bounded evaluation that produced no passing candidate. That is honest refusal, not a demo failure.

## Highest Impact Next Upgrades

1. Add signed SoDEX credential validation when credentials are available.
2. Improve the buildathon demo report into a polished panel or packaged demo folder: SoSoValue input -> evidence graph -> market report -> telemetry report -> SoDEX/ValueChain preflight.
3. Continue verified SoSoValue endpoint discovery and add high-value wrappers only when docs are source-backed.
4. Add centralized run index/manifest linking traces, provider metrics, evidence maps, market report, and preflight results for one-click judging.
5. Add screenshots/video of `/ops` and the market report for judging.
