## Diagnosis

State the causal belief being tested and why it matters now.

## Evidence

- cite the most relevant workspace evidence
- mention only what materially changes the next test

## What To Test

Name the intended family, the core change, and how success or failure will be judged.

## Suggested Gate Spec

Optional. Include only if the next experiment depends on a gate. Use validator-legal shapes and plain decimal thresholds.

## Risks

Name what could make the test uninformative or redundant.

```yaml
# Optional hint block. Keep it small.
target_family: perp_multi_asset_carry
must_answer: Does adding `co_movement_72h` improve pre-audit return without making validation negative?
required_features:
  - relative_carry_z_72h
  - co_movement_72h
required_gate_dimensions:
  - co_movement_72h
forbidden_motifs:
  - second pure trend overlay
```
