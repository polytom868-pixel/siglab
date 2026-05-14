# SigLab vs Wayfinder Gap Map

Generated: 2026-05-14

This comparison uses the local `wayfinder-autolab-backup-20260512-160007` clone as concrete reference material and public Wayfinder package/docs only for surrounding context. It does not assume missing files exist.

## Concrete Structures Found

| Area | Wayfinder Autolab | SigLab | Status |
| --- | --- | --- | --- |
| Repo-local skills | `.agents/skills/autolab-research-planner`, `autolab-candidate-writer`, `autolab-post-run-reflector` | `.agents/skills/siglab-signal-scout`, `siglab-spec-writer`, `siglab-run-reviewer` | MATCHED_AND_ADAPTED |
| Planner runner | `wayfinder_autolab/orchestration/planner_runner.py` | `siglab/orchestration/planner_runner.py` | MATCHED_WITH_SOSOVALUE_EXTENSIONS |
| Writer runner | `wayfinder_autolab/orchestration/writer_runner.py` | `siglab/orchestration/writer_runner.py` | MATCHED_WITH_CONTRACT_HARDENING |
| Reflector runner | `wayfinder_autolab/orchestration/reflector_runner.py` | `siglab/orchestration/reflector_runner.py` | MATCHED |
| Workspace manifests | `wayfinder_autolab/workspace/manifests.py` | `siglab/workspace/manifests.py` | MATCHED_WITH_SIGLAB_TERMS |
| CLI loop | `autolab run --iterations 0`, resume, labels | `siglab run --iterations 0`, resume, labels, budget/credit guards | EXCEEDED_ON_GOVERNANCE |
| Dashboard | local dashboard views | local dashboard views plus demo/report artifacts | PARTIAL_PRODUCT_UX |
| Promotion/export | Wayfinder generated strategy packages | SigLab guarded deployment/export path and SoDEX preview/preflight | PARTIAL_EXECUTION_GAP |
| Provider routing | Kimi/DeepSeek/OpenRouter env routes | B.AI/OpenRouter/Anthropic-compatible routing with Credits telemetry | EXCEEDED_ON_COST_PRESSURE |
| MCP project config | Not present in local clone; public Wayfinder SDK ecosystem references MCP server support | `.mcp.json` exists as empty explicit integration point | PARTIAL |
| Agent instructions | Not present in local clone | `AGENTS.md` added | EXCEEDED_LOCAL_GUIDANCE |

## What SigLab Already Has

- Real SoSoValue API client for verified callable endpoints.
- SoDEX public REST/WebSocket evidence path.
- Signed SoDEX dry-run/preflight scaffolding.
- B.AI provider metrics, credit-pressure, and context-pressure telemetry.
- `demo-report`, `market-report`, `demo-manifest`, `telemetry-report`.
- Strict profile and full suite.

## What Wayfinder Has That SigLab Still Lacks

- A more established product narrative around strategy promotion into Wayfinder runnable packages.
- A cleaner quickstart story in the original README.
- A focused domain: Wayfinder Autolab optimizes around Wayfinder strategy execution; SigLab spans SoSoValue, SoDEX, ValueChain, and SSI, so unresolved product breadth is higher.

## What Should Be Adopted Or Adapted

- Keep the skill-based planner/writer/reflector split. SigLab already adapted it correctly.
- Keep local dashboard/run-detail/experiment-detail style, but add a buildathon operator panel around demo artifacts.
- Keep explicit live-export caution. SigLab should remain stricter than Wayfinder on signed execution claims.
- Keep agent-compatible repo guidance. SigLab now has `AGENTS.md`; future MCP servers should be added to `.mcp.json` only when real.

## What Should Be Rejected

- Do not copy Wayfinder-specific generated strategy promotion claims into SigLab unless SoDEX/SSI execution is live-proven.
- Do not treat public SoDEX market streams as private/account stream readiness.
- Do not infer SoSoValue module coverage from product pages.

## Current Hard Gaps

1. Product UX is still artifact/CLI-heavy.
2. Signed SoDEX execution is blocked by credentials and live validation.
3. Private/account SoDEX WebSocket validation is blocked by account details.
4. SoSoValue full ecosystem coverage is blocked by missing official callable endpoints.
5. SSI/Index integration is product-doc-only until official contracts/data feeds are pinned.

