# trend_signals_external

This benchmark deck is modeled after `autoresearch`.

## Setup

Read these files first:
- `README.md`
- `benchmarks/trend_signals_external/observation.md`
- `benchmarks/trend_signals_external/spec.yaml`
- `benchmarks/trend_signals_external/best_spec.yaml`

You are benchmarking an external-agent loop against the fixed `siglab` evaluator.

## What you can edit

- Edit only `benchmarks/trend_signals_external/spec.yaml`

## What you cannot edit

- Do not edit runtime code, evaluator code, mutator code, or the benchmark keep/discard logic.
- Do not change the evaluation harness.

## Benchmark loop

1. Read `observation.md`
2. Edit `spec.yaml`
3. Run:

```bash
poetry run siglab benchmark-eval --deck trend_signals_external
```

4. Check the returned status and `results.tsv`
5. If the result is `keep`, the benchmark command has advanced the incumbent.
6. If the result is `discard`, `invalid`, or `crash`, the benchmark command has restored `spec.yaml` back to the incumbent.
7. Repeat

## Goal

Beat the incumbent on `aggregate_score` while still passing normal gating.

Tie-breaks:
1. `validation_total_return`
2. `pre_audit_canonical_total_return`
