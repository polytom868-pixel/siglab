---
name: siglab-spec-writer
description: Turn one SigLab signal note into exactly one valid spec JSON object. Use the supplied manifest, cookbook pages, schema, and probe outputs only. Do not browse, rediagnose, or emit unsupported keys.
---

# SigLab Spec Writer

Goal: convert the chosen signal note plus extracted planner contract into one valid spec JSON object.

Rules:
- Do not browse.
- Do not use tools.
- Do not rediagnose the problem.
- Emit exactly one spec object, not a wrapper.
- Use only supported top-level keys.
- Keep the thesis concise and consistent with the signal note.
- The extracted planner contract is binding. If the prose and the contract disagree, follow the contract.
- Preserve any `required_features`, `required_gate_dimensions`, and `required_variation_axis` from the planner contract exactly unless the repair packet says they were invalid.
- The family manifest and family contract are the source of truth for legal fields and valid formulas.
- Novel feature formulas are allowed when they use only aliases, raw series, and operators listed in the family manifest.
- Regime gates must use the validator contract exactly:
  - `regime_gates.entry` is `[]` or a list of string expressions / dicts
  - valid dict form is `{"expression": "...", "min": <optional>, "max": <optional>}`
  - valid string form is a boolean DSL expression like `ge(pair_corr_72h,0.9)`
  - do not emit `op`, `condition`, `threshold`, `active`, or `feature`-only gate objects
- If the extracted planner contract provides an explicit `planner_regime_gates` block, treat it as canonical.
- Copy explicit planner-provided gate specs literally, including numeric thresholds.
- Example: if the planner gives `{"expression":"funding_dispersion_72h","min":0.000001}`, emit that exact gate spec. Do not change it to `1.0` or a different threshold.
- Never rewrite small thresholds into scientific notation. Keep `0.000015` as `0.000015`, not `1.5e-05`.
- Some policy fields may be locally swept by the evaluator. Choose coherent starting values rather than brittle knife-edge thresholds.

Inputs:
- signal note
- extracted planner contract
- parent card
- one family manifest
- one family feature contract
- selected cookbook pages
- spec schema
- referenced probe outputs

Output:
- one JSON object matching `templates/spec_schema.json`

Before emitting:
- check `templates/spec_checklist.md`
