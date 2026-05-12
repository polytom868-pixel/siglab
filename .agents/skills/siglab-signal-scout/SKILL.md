---
name: siglab-signal-scout
description: Plan the most informative next SigLab experiment from a small workspace brief. Use workspace search/open and a minimal evidence tool set when the default files are insufficient. For external API-state checks, treat the SoSoValue developer pages as the source of truth. Emit a mostly free-form signal note. Never emit spec JSON.
---

# SigLab Signal Scout

Goal: decide the most informative next experiment, not the easiest local tweak.

Default context:
- `RUNBOOK.md`
- `TASK.md`
- `WORKSPACE_INDEX.md`
- `current/SESSION_STATE.json`
- `current/frontier_brief.md`
- `current/market_brief.md`
- `current/parent_card.md`

Workflow:
1. Read the default files first.
2. If the answer is still unclear, browse the workspace with `search_workspace` and `open_file`.
3. If you do not fully understand a feature or need a semantically diverse alternative set, use `search_features`, `inspect_feature`, or `suggest_feature_set`.
4. Run probes only when they would discriminate between competing explanations.
5. Write one signal note in normal markdown.

Requirements:
- Prefer discriminating experiments over threshold tweaks.
- If evidence is contradictory, investigate before deciding.
- Cite only relative workspace `evidence_paths`.
- Keep `tools_used` truthful.
- Never emit spec JSON.
- You may propose novel feature formulas; aliases are not the full limit.
- Only compose new features from the family manifest's listed aliases, raw series, and formula operators.
- The family manifest includes alias definitions; prefer existing aliases when they already express the intended signal.
- The note should make the intended family, named features, gate dimensions, and next test obvious enough for a deterministic extractor and the writer to preserve them.
- Make `must_answer` effectively a concrete falsification question with a yes/no structure and a success criterion tied to pre-audit return, validation, or drawdown, even if you state it in prose.
- If you name a specific feature or gate dimension, say it explicitly in the note.
- If the workspace state says a non-regime axis of variation is required, do not answer with another regime-only carry variant.
- For `multi_asset_yield`, remember the family is a cross-sectional ranked long/short execution family. Yield features may rank the book, but realized returns can still be mostly price-led.
- For `multi_asset_yield`, treat feature mix, book structure, long/short counts, concentration, and regime suppression as the primary structural levers.
- Do not treat cross-sectional `trade_style` as a primary evaluator-side lever for `multi_asset_yield` unless the evidence packet explicitly shows it mattered.
- When switching families or trade styles, say so explicitly in the body.
- If the next experiment depends on a regime gate, include a `## Suggested gate spec` section with validator-legal shapes only:
  - string expression, e.g. `ge(pair_corr_72h,0.9)`
  - dict with `expression` and optional `min` / `max`, e.g. `{"expression":"market_volatility_168h","max":0.0085}`
  - do not invent keys like `op`, `condition`, `threshold`, or `active`
- If you include tiny thresholds, prefer plain decimals like `0.000015` instead of scientific notation.

Output shape:
- Use the template in `templates/signal_note.template.md`.
- The body should usually include:
  - `## Diagnosis`
  - `## Evidence`
  - `## Competing explanations`
  - `## Proposed next experiment`
  - `## Suggested gate spec` when gates are central to the idea
  - `## Risks`

Read `references/workspace_guide.md` only if you need a quick reminder of the workspace contract.

For SoSoValue API-state or endpoint questions, use the official developer surfaces first:
- https://m.sosovalue.com/developer
- https://m.sosovalue.com/developer/dashboard
