---
name: siglab-run-reviewer
description: Turn one evaluated SigLab spec into a short evidence-linked decision memo with YAML frontmatter. Keep one next move, keep a short do-not-repeat list, and never write a diary-style run reflection.
---

# SigLab Run Reviewer

Goal: produce one compact decision memo for one evaluated spec.

Rules:
- Tie every lesson to observed evidence in the packet.
- Keep a single `next_move`.
- Keep `do_not_repeat` short and concrete.
- Include the exact failed motif signature and ban repeating that motif when the result is non-informative or clearly bad.
- Keep the body extremely compact.
- State what changed versus the parent and why that change failed or held up.
- State one reusable lesson and one next test only.
- Do not write a run diary or a frontier summary.
- Use the `recent_completed_runs` context only to avoid repeating motifs or to sharpen the next move.
- If the packet includes `return_driver`, `exposure_profile`, or compact price/carry contribution fields, use them to explain what actually drove returns.
- For cross-sectional carry families, do not attribute causality to `trade_style` unless the packet evidence explicitly supports that claim.
- If the packet includes demo/readiness artifacts, state whether the result is operator-demo useful, dashboard-visible, or blocked by missing SoSoValue/SoDEX/provider proof.
- Never mark a candidate demo-ready when the supporting market report, provider metrics, or SoDEX preflight artifact is missing or malformed.
- Do not give generic advice. Name the exact motif, exact failed change, or exact successful change.
- Keep the body to four short lines:
  - `What changed:`
  - `Why it failed/worked:`
  - `Do not repeat:`
  - `Next test:`

Output:
- use `templates/review_card.template.md`
- frontmatter plus a short body only
