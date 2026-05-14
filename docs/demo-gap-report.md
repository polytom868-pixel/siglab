# Demo Gap Report

Generated: 2026-05-14

## Current Demo Flow

SigLab can now demonstrate:

1. Source-backed SoSoValue evidence ingestion through `siglab evidence-build`.
2. Source-backed SoDEX public market stream ingestion through `siglab sodex-ws-probe --evidence-output ...`.
3. ValueChain RPC readiness through `siglab valuechain-preflight`.
4. Honest SoDEX live-write refusal through `siglab sodex-preflight`.
5. Evidence visualization through `siglab evidence-map`.
6. Operator market explanation through `siglab market-report`, joining SoSoValue ETF/feed evidence with the latest valid SoDEX quote evidence and live-write preflight refusal.
7. Provider telemetry through `siglab telemetry-report`, including run-level provider metrics artifacts when present.

## Still Missing

| Gap | Severity | Why it matters | Fix |
| --- | --- | --- | --- |
| No single guided demo command | LOW | `demo-report`, `market-report`, `telemetry-report`, and `demo-manifest` now exist; `demo-manifest` writes a static HTML panel. | Package the panel with screenshots and a one-command demo runner. |
| No signed SoDEX validation | HIGH | Insight-to-action remains dry-run/refusal. | Requires credentials; until then keep refusal explicit. |
| SoSoValue non-ETF/non-feed modules missing | HIGH | “Full ecosystem” claim would be false. | Continue official endpoint discovery. |
| No WebSocket daemon | MEDIUM | One-shot stream proof is not production stream processing. | Add supervised stream runner later. |
| No centralized observability | MEDIUM | Provider metrics are artifact-backed and indexed by the demo manifest, but process-local. Multi-process usage, rate-limit pressure, and crash recovery telemetry still require manual artifact inspection. | Add retention/rotation and a live dashboard view for traces, provider metrics, evidence, and preflight state. |
| No Index/SSI contract integration | MEDIUM | SSI protocol story is conceptual only. | Wait for official contract/data source pins. |

## Demo Red Flags To Say Out Loud

- This is not a live trading bot yet.
- It is not full SoSoValue API coverage.
- It is not a causal market predictor; `market-report` states temporal/contextual evidence only.
- It is a research-to-action prototype with hard refusal at unsafe live boundaries.
- Provider telemetry is real when the provider returns usage, but USD cost is still unverified and must not be claimed.
- SoDEX public market/account reads and public WebSocket are real; signed writes are not live-proven.
