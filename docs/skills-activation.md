# SigLab Skills Activation Report

| skill/helper/module | status | called from real run | effect | dead code |
| --- | --- | --- | --- | --- |
| `.agents/skills/siglab-research-planner/SKILL.md` | ACTIVE | `ResearchPlannerRunner.run` | System prompt for planner; live B.AI trace showed planner tool rounds. | no |
| `.agents/skills/siglab-post-run-reflector/SKILL.md` | ACTIVE | `ReflectionRunner.run` | System prompt for post-run lesson card; live B.AI trace captured reflector provider/model. | no |
| `.agents/skills/siglab-spec-writer/SKILL.md` | ACTIVE | `SpecWriterRunner.run` | System prompt for JSON spec writer and repair loop. | no |
| `siglab.tools.workspace_search.search_workspace` | ACTIVE | planner tool registry | Lets planner search workspace files. | no |
| `siglab.tools.workspace_search.search_workspace_text` | ACTIVE | planner tool registry | Lets planner grep workspace text. | no |
| `siglab.tools.workspace_open.open_workspace_file` | ACTIVE | planner and mutator tool registries | Opens bounded workspace context. | no |
| `siglab.tools.feature_lookup.search_features` | ACTIVE | planner and mutator tool registries | Feature discovery and alias lookup. | no |
| `siglab.tools.feature_lookup.inspect_feature` | ACTIVE | planner and mutator tool registries | Feature-level metadata and validity. | no |
| `siglab.tools.feature_lookup.suggest_feature_set` | ACTIVE | planner and mutator tool registries | Candidate feature grouping. | no |
| `HypothesisSandbox.probe_feature_forward_stats` | ACTIVE | mutator/planner tools | Train-only feature predictiveness probe. | no |
| `HypothesisSandbox.inspect_pre_audit_spec` | ACTIVE | mutator/planner tools | Diagnoses prior spec behavior before mutation. | no |
| `HypothesisSandbox.probe_spec_gate_impact` | ACTIVE | mutator/planner tools | Validates gate behavior before trusting proposed gates. | no |
| `HypothesisSandbox.compare_intended_vs_frozen_spec` | ACTIVE | mutator/planner tools | Detects sweep drift between intended and frozen spec. | no |
| `HypothesisSandbox.summarize_experiment_frontier` | ACTIVE | mutator/planner tools | Guides family/feature frontier decisions. | no |
| `WebResearcher` | PARTIAL | planner setup and run context | Available to planner flow; disabled when external research is not configured. | no |
| `benchmark-init/eval/status` helpers | ACTIVE | CLI benchmark commands | External agent benchmark sessions and comparisons. | no |

Live trace evidence:

- `runs/trend_signals/workspaces/20260512T213937Z/iterations/0001_2da563b7063ee0a4/planner_trace.json`: provider `bai`, model `kimi-k2.5`, tool rounds `2`.
- `runs/trend_signals/workspaces/20260512T213937Z/iterations/0001_2da563b7063ee0a4/writer_trace.json`: provider `bai`, model `kimi-k2.5`.
- `runs/trend_signals/workspaces/20260512T213937Z/iterations/0001_2da563b7063ee0a4/reflector_trace.json`: provider `bai`, model `kimi-k2.5`.

Hard gap:

- No dead `.agents/skills/siglab-*` skill remained after trace inspection.
- Skill value is now exposed through dashboard experiment payloads as `skill_value_report`.
- Planner tool usage is mandatory for live providers, but cost is bounded:
  - max planner tool rounds: `8`
  - max total planner tool calls per attempt: `24`
  - max probe calls total per attempt: `8`
  - max calls to one probe tool per attempt: `6`
- Probe budgets reset per repair attempt. Exhausted budgets become planner semantic failures instead of silent degraded success.
- The remaining weakness is counterfactual value attribution: `skill_value_report` classifies value by observable tool type and invocation count, not by measured keep-rate lift.
