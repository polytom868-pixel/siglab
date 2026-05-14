# Product Flow Validation

Generated: 2026-05-14

This validates the current buildathon product flow without pretending live trading exists.

## Flow Tested

1. SoSoValue evidence is available through `evidence-build`.
2. SoDEX public quote evidence is available through `sodex-ws-probe`.
3. `market-report` turns evidence into non-causal operator decision support.
4. `run --skip-llm --iterations 1 --symbols BTC,ETH,SOL` runs bounded strategy evaluation without live execution.
5. `sodex-preflight` refuses signed writes when credentials are missing.
6. `demo-manifest` indexes the artifact set.

## Latest Strategy Evaluation Proof

Command:

```bash
python3 -m siglab.cli run \
  --track trend_signals \
  --skip-llm \
  --iterations 1 \
  --symbols BTC,ETH,SOL \
  --run-label product-flow-validation \
  --agent-label siglab-product-flow \
  --max-runtime-seconds 60
```

Observed latest workspace:

- `runs/trend_signals/workspaces/20260514T054437Z`
- symbols: `BTC`, `ETH`, `SOL`
- parent family: `perp_multi_asset_decision`
- outcome: no passing spec in iteration 1

This is acceptable product behavior: the system evaluated candidates and refused weak results instead of fabricating an opportunity.

## Current Operator Value

PASS:

- A user can ingest real SoSoValue evidence.
- A user can attach live public SoDEX quote context.
- A user can get an evidence-linked market report with risk controls and next actions.
- A user can run bounded strategy evaluation.
- A user can see signed execution is blocked before any live write.

PARTIAL:

- The decision support is not yet a guided app.
- The strategy loop can evaluate but may return no passing candidate.
- The SoSoValue context is narrow until more official callable endpoints exist.

FAIL / BLOCKED:

- No live signed SoDEX execution.
- No private/account stream validation.
- No SSI/index contract integration.

## Red Flags For Judges

- This is decision support, not an autonomous trading product.
- The strongest current product proof is static HTML/JSON artifacts, not a polished interactive panel.
- The system is honest about no-trade outcomes; demo scripts must not cherry-pick a passing candidate.

