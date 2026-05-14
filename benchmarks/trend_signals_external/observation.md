# Benchmark Observation: trend_signals_external

This committed deck gives judges and external agents a concrete benchmark entrypoint without requiring `benchmark-init` before the demo.

## Objective

- Improve `benchmarks/trend_signals_external/spec.yaml`.
- A spec is `keep` only if it passes normal SigLab gating and beats the incumbent.
- Do not claim SoDEX signed execution. This deck is strategy research only.

## Current Incumbent

- hash: `9d52d77fe7796118`
- source: `committed_demo_fixture`
- family: `perp_multi_asset_carry`
- score: not evaluated in this checkout

## Allowed Direction

- Prefer evidence-linked changes using known trend/carry features.
- Preserve market-neutral risk unless the family manifest explicitly supports a directional change.
- Treat missing SoSoValue/SoDEX evidence as a blocker, not a reason to invent signal quality.
