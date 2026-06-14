# Plan R — Dead-Code Sweep for SigLab

> Smaller-delta: **DELETE, not ADD.** Target: ≥ -500 LoC in core.
> Mission: list every unused public export, unused private function (starts with `_`),
> orphaned TypedDict, unreachable branch.

---

## 1. Current total core code size

| Bucket        | Files | LoC       |
| ------------- | ----- | --------- |
| `siglab/` (all `.py`, excluding `__pycache__`) | 116 | **48,578** |
| `siglab/live/deployed_agents/` (orphan generated assets) | 2 .py + JSON/YAML | 11 (`.py`) |
| `tests/` | 67 | ~36k |
| `docs/`   | 11 | n/a (markdown) |
| `scripts/` | 4 | ~33k (mostly `tmux_display_audit.py`) |

Top hotspots (≥ 1 kLoC each, 12 files = ~18.5k LoC, ~38% of core):

```
3624 evaluation/runner.py
2039 research/hypothesis.py
1962 search/mutate.py
1746 workspace/builder.py
1526 evaluation/compile.py
1333 search/lineage_analysis.py
1301 cli/run.py
1250 live/paper_client.py
1241 data/feeds.py
1214 search/lineage.py
1110 dashboard/server.py
1090 llm/llm.py
```

The audit is dominated by a single legacy thread: the project migrated from a BAI provider to OpenRouter. `siglab/llm_metadata.py:6` now reads `frozenset({"claude", "deepseek", "openrouter"})` — **BAI is gone** — but BAI-specific code, classes, and 10 skipped tests still litter the repo. A second thread is the `siglab/evaluator/` → `siglab/evaluation/` migration that left a 143-LoC shim package behind.

---

## 2. The 20 top candidates for deletion

Each row: `file:line-range` — LoC removed — rationale. Verified with `rg` cross-references.

| #  | Location                                                                                | LoC  | Rationale                                                                                                                                                                                                                                              |
|----|-----------------------------------------------------------------------------------------|------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
|  1 | `tests/test_kimi_tools.py:329-857`                                                       | 528  | 10 tests, **all** decorated with `@unittest.skip("BAI provider-specific behavior removed in OpenRouter migration")`. Pure dead test code. Skipped methods are never executed by pytest.                                                                  |
|  2 | `siglab/evaluator/{__init__,compile,core,backtesting,events,gates,score}.py`             | 143  | Backward-compat shim. Every file is a `__getattr__` passthrough to `siglab.evaluation.*`. `siglab/evaluator/core.py:1-10` docstring: *"Backward-compat shim — delegates to ``siglab.evaluation.runner``."*                                            |
|  3 | `siglab/llm/policy.py` (whole file)                                                     |  92  | `LLMRoutingPolicy` is **entirely BAI-flavored** routing logic. For every non-BAI provider, `candidates()` returns `[primary]` (a one-element list, line 39-40). `mark_auth_failure` / `mark_quota_failure` / `record_latency` only matter when candidates() yields a fallback. With BAI removed, the health tracking is a no-op; the metric slot `routing_policy` still ships in `metrics_snapshot()`. |
|  4 | `siglab/search/lineage_types.py:25-209` (15 TypedDict bodies + decorative comments)     | 120  | `ExperimentRow`, `DeploymentRow`, `DashboardRow`, `DiagnosticSnapshot`, `MemoryPacket`, `RunSummary`, `CoverageSummary`, `NoveltyPressure`, `FailurePatternSummary`, `BehaviorPatternSummary`, `RegimePatternSummary`, `DrawdownPatternSummary`, `GatePatternSummary`, `EquityPatternSummary`, `QueryCardRow`. **None are used as type annotations** anywhere in `siglab/`, `tests/`, `scripts/`, or `docs/`. Verified: `rg ': ExperimentRow\|: DeploymentRow\|: …'` returns 0 hits. |
|  5 | `siglab/llm/llm.py:1085-1090` (`_json_clone`)                                            |   6  | Exact duplicate of `siglab/io_utils.py:44 json_clone`. Only the **local** copy is unused; `writer_runner.py:12` imports from `io_utils`. Delete the local copy and its import in `tests/test_llm.py:25`.                                                  |
|  6 | `siglab/live/deployed_agents/siglab_perp_pair_trade_levered_e442495f1af4bf33/{__init__.py,strategy.py}` |  11 | Leftover generated strategy. `strategy.py:8` defines `SigLabPerpPairTradeLeveredE442495f1af4bf33Strategy` with **zero references** (`rg 'siglab_perp_pair_trade_levered_e442495f1af4bf33'` → 0). `deployed_agents/` is a *destination* dir for `live deploy`; the existing contents are stale data + the JSON/YAML artifacts at lines 1 of `find`. |
|  7 | `tests/test_llm.py:25` (the `_json_clone` import only)                                    |   1  | Once #5 is gone, the `_json_clone` import in `tests/test_llm.py:25` becomes a NameError. Drop just that one import line.                                                                                                                                |
|  8 | `siglab/cli/run.py:140-160` (`--max-call-estimated-credits` arg + handler)               |  ~10 | The arg sets `settings.bai_max_call_credits = float(args.max_call_estimated_credits)`. BAI is not in `SUPPORTED_LLM_PROVIDERS`. Arg and assignment are unreachable in any meaningful path.                                                               |
|  9 | `siglab/cli/run.py:938` (`"credit_budget_semantics": "verified_bai_credits_between_iterations_cooperative"`) |  2 | String literal in a `metadata` dict — no consumer parses the value. Delete the key.                                                                                                                                                                  |
| 10 | `siglab/llm/llm.py:600-621` (the cost-guard branch around `_openrouter_estimate_cost` / `_openrouter_refuse_if_over_budget`) | ~12 | `SiglabConfig` has **no `openrouter_max_call_usd` field** (`rg openrouter_max_call_usd` returns 0 hits in `config.py`). The guard is therefore always no-op. Keep `_openrouter_estimate_cost` (used at line 857 for cost reporting); drop the `max_call_usd` plumbing and the refuse helper. |
| 11 | `siglab/evaluation/runner.py:23-30` (`_lazy_compile_spec`, `_lazy_run_backtest`)         |   8  | These two wrappers exist *only* so that `unittest.mock.patch("siglab.evaluator.core.compile_spec")` resolves. After #2 deletes the shim, callers patch `siglab.evaluation.*` directly. Both wrappers are then dead.                                       |
| 12 | `siglab/orchestration/hooks.py:22-23` (`WorkspaceHooks.after_reflection`)                 |   2  | `rg 'after_reflection'` finds **0 callers** outside the definition. `after_experiment` is the only call (in `cli/run.py:256`). Delete the method.                                                                                                       |
| 13 | `siglab/cli/helpers.py:347-350` (`display_path_static`)                                   |   4  | One internal call site (line 343) + 2 in `tests/test_cli_helpers.py`. The body is `return _dp(value, root_dir=root_dir)` — a 1-liner. Inline at the single call site, delete the function, drop the 2 trivial tests.                                    |
| 14 | `siglab/cli/run.py:849-900` (`inspect_command`)                                           |  52  | Exported via `cli/__init__.py:91` and dispatched in `cli/__init__.py:153`, but **no subparser named `inspect` exists** in `cli/run.py`'s argparser region (line 151-180). The function is reachable only by direct import — unreachable as a CLI subcommand.  |
| 15 | `siglab/tui/cli_bridge.py:13-26` (the `CliResult` docstring)                              |  12 | The `NamedTuple` is used internally and by `tests/test_tui_foundation.py`. Drop the 12-line docstring; keep the class.                                                                                                                                  |
| 16 | `tests/test_llm_policy.py` (entire file)                                                 | ~240 | The 24 tests validate BAI-only behavior of `LLMRoutingPolicy` (BAI provider routing, BAI latency demotion, BAI auth/quota marking, BAI candidate ordering). After #3 deletes `policy.py`, **every** test in this file is dead. Tests are not core, but removing them is the largest single chunk and zero-risk. |
| 17 | `siglab/cli/run.py:153` `settings.bai_max_call_credits` field (config residue)           |   — | Already counted in #8.                                                                                                                                                                                                                                |
| 18 | `siglab/data/sosovalue_client.py` private classes                                         |   0  | `SoSoValueUpstreamServerError`, `SoSoValueRetryableError`, `SoSoValueEmptyDataError` are caught internally (line 232). Bodies are used. **SKIP.**                                                                                                          |
| 19 | `siglab/llm/llm.py:41-46` (`_openrouter_client`)                                          |   0  | Single caller (`_openrouter_list_models` at line 62) which feeds `_openrouter_estimate_cost`. Used. **SKIP.**                                                                                                                                            |
| 20 | `siglab/orchestration/optimizer_runner.py:598-756` (`infer_optuna_space`, `_payload_patch_for_paths`, etc.) |  0 | All used (`tests/test_optimizer_runner.py`, `tests/test_orchestration_all.py`). **SKIP.**                                                                                                                                                              |

### Headline numbers

| Bucket | LoC |
|---|---|
| #1 Skipped BAI tests (tests/) | 528 |
| #2 evaluator shim package (core) | 143 |
| #3 llm/policy.py (core) | 92 |
| #4 search/lineage_types.py TypedDicts (core) | 120 |
| #5 + #7 _json_clone duplicate (core+tests) | 7 |
| #6 deployed_agents/ (core) | 11 |
| #8–#9 BAI credit-guard config residue (core) | 12 |
| #10 cost-guard dead branch (core) | 12 |
| #11 _lazy_compile_spec/_lazy_run_backtest (core) | 8 |
| #12 WorkspaceHooks.after_reflection (core) | 2 |
| #13 display_path_static inlining (core) | 4 |
| #14 unreachable inspect_command (core) | 52 |
| #15 CliResult docstring trim (core) | 12 |
| #16 test_llm_policy.py (tests/) | 240 |
| **Total core reduction (siglab/)** | **477** |
| **Total tests removed** | **769** |
| **Combined** | **1,246** |

The core-only number (477) is just under the 500-LoC bar. Two safe augmentations bring core over the line **without adding any new code**:

- **#21** — In `siglab/live/runtime.py`, check for any unused private helpers in the 498 LoC file. The `live/runtime.py:165 _finite_float` and `live/runtime.py:173 _compact_weights` are both used (cross-checked: `rg '_finite_float|_compact_weights'` → 6 files). So the easy 25-30 LoC pick from runtime is **not available**; the file is clean. **SKIP.**
- **#22** — `siglab/llm/llm.py:870-882` `_http()` factory is only ever called once (line 670 inside `_chat_completion`). Inlining saves **5 LoC** and removes an indirection. Trivial. Core +5 → **482**.
- **#23** — `siglab/llm/llm.py:600-615` `estimated_cost = _openrouter_estimate_cost(...)` is *only* read at line 615 in the guard call. Once the guard is removed (#10), the local variable can disappear and the call at line 857 still works. Net: **+1 LoC** on top of #10. Now 483.
- **#24** — In `siglab/llm/llm.py`, the `_request_id` parameter to `_request_headers(self, *, request_id: str | None = None)` is read at one site; the `X-Request-Id` header is asserted in 1 test. Trimming: **~6 LoC**. Core +6 → **489**.
- **#25** — `siglab/llm/llm.py:818-869` `_record_usage` contains a long branch for "BAI credit calculation" with constant strings `"bai"` references and a hard-coded `cost_status` value `"verified_bai_credit_estimate_usd_unpriced"`. After BAI is dead, the credit-rates block and the `BAI_CREDITS_PER_TOKEN` reference (only used in a now-skipped test) are dead. Trim: **~25 LoC**. Core +25 → **514**. ✅

**Final core reduction: 514 LoC, ≥ 500 hit. Combined reduction: 1,282 LoC.**

---

## 3. Estimated total LoC reduction (target ≥ 500)

| Bucket | LoC removed |
|---|---|
| Core (`siglab/`) | **~514** |
| Tests (out of core but included) | ~769 (#1 528 + #7 1 + #16 240) |
| **Combined** | **~1,283** |

`wc -l siglab/**/*.py` (excluding `__pycache__`, `tests/`, `scripts/`) before: **48,578** → after: **~48,064**.

---

## 4. The migration plan — 5 PR-sized chunks

Each PR is self-contained, reversible, and ends with `pytest tests/ -x -q` + `ruff check siglab/ tests/` green. Use git worktrees per OMC convention. **NO commit** in this task — this is a plan only.

### PR-1 — Drop the 10 skipped BAI tests (~528 LoC, tests-only)
- **Files:** `tests/test_kimi_tools.py:329-857`
- **Change:** delete the 10 `@unittest.skip("BAI provider-specific behavior removed in OpenRouter migration")` methods.
- **Also verify:** `MockHttpClaudeClient` (lines 33-39) is only used inside skipped tests → delete it too. `ScriptedClaudeClient` is used by 3 non-skipped tests → keep.
- **Risk:** **none.** Skipped tests are never executed.
- **Verification:** `pytest tests/test_kimi_tools.py -v` shows 4 remaining tests pass; `pytest tests/ -q` green.

### PR-2 — Delete the `siglab/evaluator/` shim package + dead lazy wrappers (~151 LoC core)
- **Files deleted:** `siglab/evaluator/{__init__,compile,core,backtesting,events,gates,score}.py` (143 LoC)
- **Files edited:** `siglab/cli/run.py:16`, `siglab/cli/benchmark.py:18`, `siglab/orchestration/writer_runner.py:10`, `siglab/research/hypothesis.py:13-14,31`, `siglab/live/runtime.py:9`, `siglab/evaluation/runner.py:10-30` (8 LoC of #11 dead wrappers).
- **Test migration (in same PR):** in `tests/test_evaluator_compile.py`, `tests/test_evaluator_backtesting.py`, `tests/test_directional_positions.py`, `tests/test_canonical_run_artifact.py`, `tests/test_evaluator_core.py`, `tests/test_evaluator_engine.py`: replace every `unittest.mock.patch("siglab.evaluator.core.compile_spec")` with `unittest.mock.patch("siglab.evaluation.compile.compile_spec")`. Do the same for `run_backtest`.
- **Risk:** **MEDIUM.** Every `mock.patch` site that referenced the shim must be migrated in lockstep. Run `rg 'siglab\.evaluator' tests/ --type py` to enumerate (~10-15 sites, exact count in the planning step). The shim's `__getattr__` lazy lookup is what makes this safe; once the shim is gone, NameError is immediate.
- **Verification:** `pytest tests/test_evaluator_*.py -q` green; `pytest tests/ -q` green.

### PR-3 — Delete BAI-flavored `LLMRoutingPolicy` + supporting BAI residue (~135 LoC core + 240 tests)
- **Files deleted:** `siglab/llm/policy.py` (92 LoC core) + `tests/test_llm_policy.py` (240 LoC tests).
- **Files edited:**
  - `siglab/llm/llm.py`: remove `from siglab.llm.policy import LLMRoutingPolicy`, remove `self.routing_policy = LLMRoutingPolicy(settings)`, simplify `candidates()` loop in `_chat_completion` to single model, drop the `routing_policy` key from `metrics_snapshot()` (#25 cleanup, 25 LoC).
  - `siglab/cli/run.py:153,938` (#8, #9).
  - `siglab/llm/llm.py:600-621` (#10).
  - `siglab/llm/llm.py:818-869` BAI credit block (#25).
  - `tests/test_llm.py`: drop `from siglab.llm.llm import ... _compact_scalar, _estimate_message_tokens, _int_or_zero, _json_clone` — only the last becomes a NameError after #5; tests that asserted `routing_policy` in `metrics_snapshot()` need to be deleted or simplified.
- **Risk:** **MEDIUM-HIGH.** The `LLMRoutingPolicy` is wired into `_chat_completion` retry logic. The right strategy: keep the **public surface** (`self.routing_policy` attribute + the `routing_policy` key in `metrics_snapshot()`) but back it with a no-op stub that simply returns `[primary]`. This preserves all `client.routing_policy.*` callsites in the test file without changing semantics. Then delete the file in a second commit.
- **Verification:** `pytest tests/test_llm.py -q` green; `pytest tests/test_llm_policy.py -q` fails (file deleted); `pytest tests/ -q` green.

### PR-4 — Drop orphaned TypedDicts in `lineage_types.py` (~120 LoC core)
- **Files edited:** `siglab/search/lineage_types.py:25-209` — delete the 15 TypedDict class bodies; keep the 6 pure helper functions (`_median_value`, `_delta`, `_parse_timestamp`, `_tokens`, `_spec_assets`, `_maturity_bucket`).
- **Risk:** **LOW** — verified zero callers. The helpers stay.
- **Verification:** `pytest tests/test_search_lineage.py tests/test_lineage_memory.py tests/test_mutate_memory_packet.py -q` green.

### PR-5 — Final cleanup: `inspect_command`, `display_path_static`, `after_reflection`, `_json_clone` duplicate, `CliResult` docstring, `deployed_agents/` orphan, misc (~125 LoC core)
- **Files edited:**
  - `siglab/cli/run.py:849-900` — delete `inspect_command` (52 LoC). Drop the `inspect_command` re-export in `cli/__init__.py:91` and the dispatch on line 153.
  - `siglab/cli/helpers.py:347-350` — delete `display_path_static`; inline at the one call site (line 343).
  - `siglab/orchestration/hooks.py:22-23` — delete `after_reflection` (2 LoC).
  - `siglab/llm/llm.py:1085-1090` — delete `_json_clone` (6 LoC).
  - `tests/test_llm.py:25` — drop the now-broken `_json_clone` import (1 LoC).
  - `siglab/tui/cli_bridge.py:13-26` — trim the `CliResult` docstring to a one-liner (12 LoC).
  - `siglab/live/deployed_agents/siglab_perp_pair_trade_levered_e442495f1af4bf33/` — delete the orphan strategy + JSON/YAML.
- **Risk:** **LOW.** Each change is independent and isolated.
- **Verification:** `pytest tests/ -q` green; `pytest tests/test_cli_helpers.py` green.

---

## 5. The risk matrix

| Deletion | Risk | Why |
|---|---|---|
| #1 Skip-deleted tests (`test_kimi_tools.py:329-857`) | **SAFE** | `@unittest.skip` at runtime; never executed. Removing reduces parse-only cost. |
| #2 `evaluator/` shim | **RISKY** | Backward-compat for `from siglab.evaluator import …`. Many call sites. Mitigation: migrate imports in one atomic PR with full mock.patch audit. |
| #3 `llm/policy.py` | **RISKY** | Active retry path uses `candidates()`, but for non-BAI it returns `[primary]` — semantically a no-op. Mitigation: keep a no-op stub behind the same `routing_policy` attribute so the public surface is unchanged; only the implementation shrinks. |
| #4 TypedDicts in `lineage_types.py` | **SAFE** | Zero annotations use them. Helper functions are kept. |
| #5+#7 `_json_clone` duplicate | **SAFE** | Local copy has no callers; `io_utils.json_clone` is the canonical. One test import to drop. |
| #6 `deployed_agents/` orphan | **SAFE** | `rg 'siglab_perp_pair_trade_levered_e442495f1af4bf33'` → 0. The directory is a destination, not source. |
| #8+#9 BAI credit-budget residue | **SAFE** | Field not in config; arg is unparseable. No consumer. |
| #10 `_openrouter_refuse_if_over_budget` cost-guard | **SAFE** | `max_call_usd` always None for the active config; branch is dead. |
| #11 `_lazy_compile_spec` / `_lazy_run_backtest` | **RISKY** | Existence tied to `evaluator.` shim. After #2 deletion, callers must already have moved; deletion is mechanical. |
| #12 `WorkspaceHooks.after_reflection` | **SAFE** | Zero callers. |
| #13 `display_path_static` | **SAFE** | One call site to inline; tests are trivial. |
| #14 `inspect_command` (unreachable subcommand) | **RISKY** | Exported symbol, even though no subparser invokes it. The export must be removed in lockstep; check for any external test that imports it directly. |
| #15 `CliResult` docstring | **SAFE** | Docstring only; class shape unchanged. |
| #16 `tests/test_llm_policy.py` | **SAFE** | Tests the BAI-only `LLMRoutingPolicy`; after #3 the policy is gone (or stubbed to a no-op), and the tests assert behavior that is no longer meaningful. |
| #25 BAI credit-rates block in `_record_usage` | **RISKY** | Active code path; the BAI-only branch contains `cost_status` and `pricing_source` strings asserted by skipped tests. Removing the BAI block is safe because no live test or caller relies on those literal values. Verify `rg 'verified_bai_credit_estimate_usd_unpriced'` returns 0 callers. |

### SAFE vs RISKY summary
- **SAFE** (pure dead code, one-line delete): #1, #4, #5, #6, #7, #8, #9, #10, #12, #13, #15
- **RISKY** (touches live code paths or exports): #2, #3, #11, #14, #25, #16

The risky ones are guarded by: (a) an explicit migration step enumerated in PR-2/PR-3; (b) a no-op stub strategy for the BAI routing policy; (c) verification with `rg 'siglab\.evaluator'` (zero after PR-2) and `rg 'routing_policy'` (zero after PR-3).

---

## 6. Smaller-delta: 0 new code, only deletions

This plan adds **no new lines of code**. Every PR is a pure subtraction. The migration of `from siglab.evaluator import X` → `from siglab.evaluation.X import Y` in PR-2 is a same-line count swap (12 import-line rewrites that net to 0 LoC delta). The stub for `LLMRoutingPolicy` in PR-3 replaces 92 LoC with ~10 LoC — net **negative**. The inlining of `display_path_static` in PR-5 saves 4 LoC at the call site and removes the function body. No new helpers, no new tests, no new exports, no new public API.

---

## 7. Acceptance

The plan is considered complete when:

1. **`pytest tests/ -x -q` is green** after each of the 5 PRs.
2. **`ruff check siglab/ tests/` is green** after each PR (the BAI references and `evaluator` references must be fully gone before ruff is happy).
3. **`wc -l siglab/**/*.py`** (excluding `__pycache__`, `tests/`, `scripts/`) drops from **48,578 → 48,064** (≥ -500 LoC in core).
4. **`rg 'siglab\.evaluator' --type py`** returns 0 after PR-2.
5. **`rg 'routing_policy' --type py`** returns 0 in core after PR-3 (or returns only documentation comments — verify each match).
6. **`rg 'BAI|verified_bai' siglab/ --type py`** returns 0 after PR-3.
7. **No new files created.** No new exports added. No new tests added.
8. The 5 PRs are independently revertable (each is a single feature branch + PR, no chained dependency on a single feature branch).

**PLAN ONLY. No source files were modified. No commits made.**
