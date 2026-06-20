# Architecture Blueprint — SigLab Cross-Module Dependency Map

**Track:** 1.4 (Wave 1 — "Foundation Clearance")
**Agent:** A4 (Architecture / Product-Domain Agent)
**Date:** 2026-06-20
**Audit Reference:** 0xmiharbi\_deep\_audit.md (§2.1–2.3, §3, §5)
**LOC:** 0 (pure analysis — no code changes)

---

## Baseline Metrics

| Metric | Value |
|--------|-------|
| Total Python files | 135 |
| Total LOC | 49,668 |
| Modules (subdirectories) | 14 |
| Root files | 15 |

---

## 1. Dependency Map — All Cross-Module Imports

### 1.1 Operator Modules That Import From Research Modules

These are the **critical extraction points** — operator modules that depend on research modules scheduled for deletion in Wave 2.

| Source (Operator) | Target (Research) | Import | Why It Exists | Extraction Needed |
|---|---|---|---|---|
| `live/runtime.py:9` | `evaluator/compile.py` | `from siglab.evaluator.compile import compile_spec` | `DirectionalPerpsSigLabStrategy._latest_target_snapshot()` calls `compile_spec()` to generate live target weights | **YES — Extraction Point #1** |
| `live/exporter.py:12` | `search/lineage.py` | `from siglab.search.lineage import LineageStore` | `LiveDeploymentManager` reads deployment records from LineageStore | **YES — Extraction Point #2** |
| `cli/deploy.py:12` | `search/lineage.py` | `from siglab.search.lineage import LineageStore` | `deploy` CLI reads deployment records | **YES — Extraction Point #2** |
| `cli/helpers.py:451` | `orchestration/contracts.py` | `from siglab.orchestration.contracts import motif_signature` | `_negative_streak()` helper uses motif matching | **YES — Extraction Point #3** |
| `cli/helpers.py:485` | `orchestration/trials.py` | `from siglab.orchestration.trials import summarize_generalization` | `summarize_generalization_from_lib()` wraps research-only function | **YES — Extraction Point #3** |
| `cli/run.py:16-44` | Multiple (orchestration, research, search, workspace, evaluator) | 10+ imports from research modules | Entire file is the research loop CLI entry point | **YES — Extraction Point #4** |
| `dashboard/server.py:24` | `search/lineage.py` | `from siglab.search.lineage import LineageStore` | Dashboard reads lineage data | Resolved: **dashboard/ deleted in Wave 1.4** |
| `dashboard/app.py:14` | `search/lineage.py` | `from siglab.search.lineage import LineageStore` | Dashboard app reads lineage data | Resolved: **dashboard/ deleted in Wave 1.4** |

### 1.2 Research Modules — Internal Import Graph

Research modules import heavily from each other. This graph shows why deletion must be atomic (cannot delete one without extracting operator dependencies first).

```
orchestration/
  ├── → evaluation/strategy_semantics  (contracts.py, planner_contract.py, writer_runner.py)
  ├── → evaluator/compile              (writer_runner.py)
  ├── → llm/                           (planner_runner.py, planner_tools.py, writer_runner.py)
  ├── → research/                      (planner_runner.py, planner_tools.py, writer_runner.py)
  ├── → search/mutate                  (writer_runner.py)
  ├── → workspace/builder              (planner_runner.py, planner_tools.py, writer_runner.py)
  ├── → workspace/cards                (writer_runner.py, planner_runner.py)
  └── → tools/                         (planner_tools.py)

search/
  ├── → evaluation/strategy_semantics  (lineage_analysis.py, mutate.py)
  ├── → evaluation/feature_dsl         (mutate.py)
  ├── → evaluation/analysis_utils      (lineage_analysis.py)
  ├── → evaluation/score               (select.py)
  ├── → llm/                           (mutate.py)
  └── → search/ (internal)             (lineage → lineage_analysis, lineage_types; select → mutate)

workspace/
  ├── → evaluation/strategy_semantics  (builder.py, manifests.py)
  ├── → evaluation/feature_dsl         (manifests.py)
  ├── → orchestration/trials           (builder.py, cards.py)
  ├── → orchestration/contracts        (manifests.py)
  ├── → search/lineage                 (builder.py)
  ├── → search/mutate                  (builder.py, manifests.py)
  └── → workspace/ (internal)          (builder → cards, indexes, manifests)

research/
  ├── → evaluation/                    (hypothesis.py)
  ├── → evaluator/                     (hypothesis.py)
  └── → search/                        (hypothesis.py)

cli/run.py
  ├── → orchestration/                 (runner, run_context, trials, contracts)
  ├── → research/                      (HypothesisSandbox, WebResearcher)
  ├── → search/                        (LineageStore, SpecMutator, pick_parent)
  ├── → workspace/                     (WorkspaceBuilder, WorkspaceSession)
  ├── → evaluator/                     (ResearchEvaluator)
  ├── → data/                          (MarketDataProvider)
  ├── → llm/                           (ClaudeClient)
  └── → cli/helpers                    (10+ helper functions)

cli/benchmark.py
  ├── → evaluator/                     (ResearchEvaluator)
  ├── → search/                        (LineageStore, SpecMutator)
  └── → data/                          (MarketDataProvider, ParquetLake)
```

### 1.3 Death Star Diagram — What Depends on What

```
                              ┌──────────────────────┐
                              │    orchestration/     │ ←── 4,991 LOC (14 files)
                              │    (AI research loop) │
                              └──────────┬───────────┘
                                         │ imports from
                    ┌────────────────────┼────────────────────┐
                    │                    │                    │
                    ▼                    ▼                    ▼
            ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐
            │ evaluation/  │   │   search/    │   │   workspace/     │
            │ 6,473 LOC    │   │ 5,294 LOC    │   │  2,975 LOC       │
            └──────────────┘   └──────────────┘   └──────────────────┘
                    │                  │                    │
                    │         ┌────────┘                    │
                    │         ▼                             │
                    │   ┌──────────────┐                    │
                    └──→│  research/   │←───────────────────┘
                        │ 2,424 LOC    │
                        └──────────────┘

   ┌─────────────────────────────────────────────────────────────────────┐
   │  CLI ENTRY POINTS THAT TIE IT ALL TOGETHER                         │
   │  ┌──────────┐  ┌──────────────┐  ┌──────────────┐                 │
   │  │ cli/run  │  │cli/benchmark │  │  cli/helpers │  ← research deps │
   │  │ 1,344 LOC│  │  104 LOC     │  │  699 LOC     │                 │
   │  └──────────┘  └──────────────┘  └──────────────┘                 │
   └─────────────────────────────────────────────────────────────────────┘

   OPERATOR CODE (survives Wave 2):
   ┌─────────────────────────────────────────────────────────────────────┐
   │  live/ (3,888 LOC) — 2 imports into research modules               │
   │  risk/ (650 LOC) — ZERO research imports                           │
   │  cli/paper.py, cli/sodex.py, cli/deploy.py, cli/market.py          │
   │  tui/ (6,876 LOC) — ZERO research imports (all TUI-internal)       │
   │  data/ (3,906 LOC) — ZERO research imports (self-contained)        │
   └─────────────────────────────────────────────────────────────────────┘
```

---

## 2. Four Extraction Points (Critical for Wave 2)

### Extraction Point #1: `compile_spec()` — evaluator/compile.py → live/signal_compile.py

**Current state:**
- `live/runtime.py:9` → `from siglab.evaluator.compile import compile_spec`
- `evaluator/` is a **dead shim** (7 files, 146 LOC) — `evaluator/__init__.py` uses `__getattr__` to re-export from `evaluation/runner`
- `evaluator/compile.py` (22 LOC) is also a shim — uses `__getattr__` to delegate to `evaluation/compile`
- The **real** `compile_spec()` lives at `evaluation/compile.py:60-113` (1,522 LOC total file)

**Why this matters:**
- `runtime.py` is the heart of live execution. It calls `compile_spec()` in `_latest_target_snapshot()` (line 432) to compute target weights.
- `evaluation/compile.py` (1,522 LOC) is a massive file with 6-branch family compilation — only ~60 LOC of the `compile_spec` function signature is needed by runtime.

**Extraction plan:**
```
1. Create siglab/live/signal_compile.py (~80 LOC)
   → Copy compile_spec() function signature + minimal dependencies
   → Import from siglab.evaluation.compile or inline the needed path
2. Change siglab/live/runtime.py:9
   → from siglab.evaluator.compile import compile_spec
   → from siglab.live.signal_compile import compile_spec
3. Update siglab/orchestration/writer_runner.py:9 (same import)
   → This file is deleted in Wave 2, so no change needed
```

**Risk:** LOW — narrow extraction. The `compile_spec` function itself is well-defined. Need to verify `SignalSpec`, `MarketDataProvider`, and `SiglabConfig` dependencies are available without the rest of `evaluation/`.

### Extraction Point #2: `LineageStore.deployment()` / `experiment_detail()` — search/lineage.py → data/deployment_store.py

**Current state:**
- `live/exporter.py:12` → `from siglab.search.lineage import LineageStore`
- `cli/deploy.py:12` → `from siglab.search.lineage import LineageStore`
- `LineageStore` (1,185 LOC) is a full SQLite experiment database with events, deployments, and experiments tables
- Only ~80 LOC is needed by operator code:
  - `LineageStore.deployment()` at line 494 — reads deployment records
  - `LineageStore.experiment_detail()` at line 699 — reads experiment detail
  - `LineageStore.__init__()` at line 52 — DB init
  - `LineageStore.list_deployments()` (if used) — listing deployments

**Why this matters:**
- `exporter.py` and `deploy.py` are operator-critical files. They cannot be deleted.
- The full `search/` module (5,294 LOC) is designated for deletion — its genetic algorithm, lineage analysis, and selection logic have no operator value.

**Extraction plan:**
```
1. Create siglab/data/deployment_store.py (~80 LOC)
   → Minimal SQLite store with deployment() and experiment_detail()
   → Reuse the same `deployments` and `experiments` table schemas
2. Change siglab/live/exporter.py:12
   → from siglab.search.lineage import LineageStore
   → from siglab.data.deployment_store import DeploymentStore
3. Change siglab/cli/deploy.py:12
   → from siglab.search.lineage import LineageStore
   → from siglab.data.deployment_store import DeploymentStore
```

**Risk:** LOW — extraction targets two well-defined query methods. No write operations are needed by operator code (deployment creation can stay in the original research flow until deletion).

### Extraction Point #3: Research Helpers in `cli/helpers.py` (lines 451, 485)

**Current state:**
- `cli/helpers.py:451` → `from siglab.orchestration.contracts import motif_signature` (inside function `_negative_streak()`)
- `cli/helpers.py:485` → `from siglab.orchestration.trials import summarize_generalization` (inside function `summarize_generalization_from_lib()`)
- Both are lazy imports inside functions (not at module level), so they don't affect import time
- The surrounding functions (`_negative_streak()` and `summarize_generalization_from_lib()`) are called by research-only CLI code

**Why this matters:**
- `cli/helpers.py` (699 LOC) is a mix of operator helpers (~100 LOC) and research helpers (~599 LOC)
- The research helpers should be identified and trimmed to leave only operator-relevant utilities

**Operator helpers to KEEP in helpers.py (~100 LOC):**
- `deployment_eligible()` / `deployment_ineligible_reasons()` — used by `cli/deploy.py`
- `sodex_preflight_report()` — used by `cli/sodex.py`
- `require_sosovalue_config()` — used by several CLI commands
- `display_deployment_record()` — used by deploy status
- `parse_sodex_enum()` — used by `cli/sodex.py`

**Research helpers to REMOVE (lazy imports at lines 451 and 485, plus surrounding functions):**
- `_negative_streak()` — calls `motif_signature` from orchestration/contracts
- `summarize_generalization_from_lib()` — wraps from orchestration/trials
- Various other research-oriented helpers (~599 LOC total)

**Risk:** LOW — lazy imports mean no import-time breakage. The functions will simply stop working when research modules are deleted, but they are only called by research CLI commands (which are also deleted).

### Extraction Point #4: `cli/run.py` — Entire File Targeted for Wave 2 Deletion

**Current state:**
- `cli/run.py` (1,344 LOC) — the research loop CLI entry point
- Imports from 7 different research modules (orchestration, research, search, workspace, evaluator, and cli/helpers)
- The largest single CLI file

**Imports to remove (all of them):**
```python
from siglab.evaluator import ResearchEvaluator            # line 16
from siglab.orchestration import (...)                     # line 18-24
from siglab.orchestration.run_context import build_run_context  # line 25
from siglab.orchestration.trials import (...)               # line 26-28
from siglab.research import HypothesisSandbox, WebResearcher # line 29
from siglab.run_config import (...)                         # line 30-36
from siglab.search import (...)                             # line 38-42
from siglab.workspace import WorkspaceBuilder, WorkspaceSession  # line 44
from siglab.orchestration.contracts import motif_signature  # line 733 (lazy)
```

**Why this matters:**
- This file is the primary entry point for the autonomous research loop
- Every import in it ties to a research module that will be deleted
- No operator functionality exists here — all research

**Risk:** NONE — no operator code depends on `cli/run.py`. When deleted, all dependencies on research modules are automatically severed.

---

## 3. Clean Architecture Layer Mapping

Based on Clean Architecture principles for Python trading systems (sources: Medium "Clean Architecture with Python", LinkedIn Watanabe, Hacker News "Architecture Patterns with Python"):

### The Four Layers

| Layer | Name | Responsibility | Dependencies |
|-------|------|----------------|--------------|
| **Layer 1** | **Domain** (Entities) | Core business models, value objects, schemas — no framework dependencies | None (pure Python) |
| **Layer 2** | **Use Cases** (Application) | Orchestration of business flows, decision logic, signal computation | Domain layer only |
| **Layer 3** | **Adapters** (Interface) | Translation between use cases and external systems, CLI commands, TUI screens, API surfaces | Use Cases + Domain |
| **Layer 4** | **Drivers** (Frameworks) | External systems: databases, web servers, LLM APIs, WebSocket connections, SoDEX API, file I/O | Dependency inversion — implements adapter interfaces |

### Dependency Rule

> Source code dependencies must point **inward**. Nothing in an inner layer can know about anything in an outer layer.

### Current SigLab Module → Layer Mapping

| Module | Current Layer | Justification | Survives Wave 2? |
|--------|--------------|---------------|-------------------|
| `siglab/schemas.py` (109 LOC) | **Domain** | `SignalSpec`, `CompiledChild` — core business value objects | ✅ KEEP |
| `siglab/config.py` (196 LOC) | **Adapters** | Configuration loading from env/files — infrastructure concern | ✅ KEEP |
| `siglab/utils.py` (149 LOC) | **Domain** | Pure utility functions, no framework deps | ✅ KEEP |
| `siglab/io_utils.py` (57 LOC) | **Adapters** | File I/O — infrastructure | ✅ KEEP |
| `siglab/path_utils.py` (28 LOC) | **Adapters** | Path resolution | ✅ KEEP |
| `siglab/families.py` (48 LOC) | **Domain** | Family capability metadata | ❌ DELETE W2 |
| `siglab/track_registry.py` (59 LOC) | **Domain** | Track name resolution | ❌ DELETE W2 |
| `siglab/llm_metadata.py` (153 LOC) | **Adapters** | LLM provider metadata | ❌ DELETE W2 |
| `siglab/visualization.py` (109 LOC) | **Adapters** | HTML chart generation | ❌ DELETE W2 |
| `siglab/benchmark.py` (732 LOC) | **Use Cases** | Benchmark orchestration | ❌ DELETE W2 |
| `siglab/hardening_profile.py` (301 LOC) | **Use Cases** | Hardening profile computation | ❌ DELETE W2 |
| `siglab/run_config.py` (171 LOC) | **Adapters** | Research run configuration | ❌ DELETE W2 |
| `siglab/telemetry.py` (223 LOC) | **Use Cases** | Provider telemetry aggregation | ❌ DELETE W2 |
| `siglab/live/` (3,888 LOC) | **Use Cases + Adapters** | `runtime.py`, `exporter.py` = Use Cases (execution flow). `sodex_client.py`, `sodex_signing.py` = Adapters (external system interfaces) | ✅ KEEP |
| `siglab/risk/` (650 LOC) | **Use Cases** | Risk scoring — pure application logic | ✅ KEEP |
| `siglab/data/` (3,906 LOC) | **Adapters + Drivers** | `sodex_client.py` = Adapter. `sosovalue_client.py` = Driver (HTTP API). `sodex_feeds.py` = Adapter/Driver mix | ✅ KEEP (trimmed) |
| `siglab/cli/` (4,802 LOC) | **Adapters** | CLI dispatch — pure interface layer | ✅ KEEP (trimmed) |
| `siglab/tui/` (6,876 LOC) | **Adapters** | TUI screens — pure interface layer | ✅ KEEP (simplified) |
| `siglab/dashboard/` (2,342 LOC) | **Adapters + Drivers** | Web server = Driver. Routes/WS = Adapters | ❌ DELETE W1.4 |
| `siglab/evaluation/` (6,473 LOC) | **Use Cases** | Backtesting, score computation, feature DSL — application logic for research | ❌ DELETE W2* |
| `siglab/evaluator/` (146 LOC) | **Adapters** (dead shim) | Lazy re-export shim to evaluation/ — zero business logic | ❌ DELETE W2 |
| `siglab/orchestration/` (4,991 LOC) | **Use Cases** | AI research loop orchestration — application logic | ❌ DELETE W2 |
| `siglab/search/` (5,294 LOC) | **Use Cases + Drivers** | Genetic algorithm = Use Case. SQLite LineageStore = Driver | ❌ DELETE W2 |
| `siglab/workspace/` (2,975 LOC) | **Adapters + Drivers** | File system management, artifact rendering | ❌ DELETE W2 |
| `siglab/research/` (2,424 LOC) | **Use Cases** | Hypothesis testing, web research — application logic | ❌ DELETE W2 |
| `siglab/tools/` (279 LOC) | **Adapters** | Workspace tool interfaces for LLM | ❌ DELETE W2 |
| `siglab/llm/` (2,278 LOC) | **Adapters + Drivers** | Claude client = Driver. Policy = Adapter | ✅ TRIM to ~400 LOC |

*\* evaluation/ is preserved in Wave 1 per conflict resolution C1 (needed for A5 signal narrative). Deletion deferred to Wave 2.*

### Target Clean Architecture Post-Wave 2

```
  LAYER 1: DOMAIN (entities, value objects)
  ─────────────────────────────────────────
  siglab/schemas.py           SignalSpec, CompiledChild
  siglab/utils.py             safe_float, short_hash, feature_hash
  [extracted from families]   PAIR_TRADE_FAMILIES (if needed)

  LAYER 2: USE CASES (application logic)
  ─────────────────────────────────────────
  siglab/live/runtime.py      DirectionalPerpsSigLabStrategy, execution flow
  siglab/live/exporter.py     LiveDeploymentManager, deployment orchestration
  siglab/live/promotion.py    Paper-to-live promotion logic
  siglab/risk/guardian.py     Risk scoring, composite score
  siglab/data/sodex_rate_limit.py  Rate limiting use case

  LAYER 3: ADAPTERS (interfaces)
  ─────────────────────────────────────────
  siglab/cli/                 CLI commands (paper, sodex, deploy, market, __init__)
  siglab/tui/                 TUI screens (positions, market, risk)
  siglab/data/deployment_store.py  Deployment data access (extracted from search/lineage)
  siglab/llm/policy.py        LLM routing policy
  siglab/config.py            Configuration adapter
  siglab/io_utils.py          File I/O adapter
  siglab/path_utils.py        Path resolution adapter

  LAYER 4: DRIVERS (external systems)
  ─────────────────────────────────────────
  siglab/data/sosovalue_client.py  SoSoValue HTTP API client
  siglab/data/sodex_client.py      SoDEX public REST client
  siglab/data/sodex_feeds.py       SoDEX WebSocket data feeds
  siglab/live/sodex_client.py      SoDEX signed execution client
  siglab/live/sodex_signing.py     EIP-712 signing infrastructure
  siglab/live/sodex_ws.py          SoDEX WebSocket streaming
  siglab/live/paper_client.py      Paper trading engine
  siglab/llm/client.py             Simplified LLM HTTP client (replacement for claude.py + llm.py)
```

---

## 4. Conflict Resolution Documentation

The following conflict resolutions are in effect per `wave_conflict_map.md` and the Wave 1 plan decisions:

### C1: `evaluation/` — KEPT (needed for A5 signal narrative)

| Detail | Value |
|--------|-------|
| Conflict | A4 wants to DELETE (W1.2); A5 wants to refactor (W1A-W1C) |
| **Resolution** | **KEPT** — evaluation/ preserved through Wave 1. A5's signal_narrative.py depends on it. Deletion deferred to Wave 2. |
| Implication | A4 must skip evaluation/ deletion commands during Wave 1 execution. All 6,473 LOC remain. |

### C2: `dashboard/` — KEPT (needed for A1 demo hosting)

| Detail | Value |
|--------|-------|
| Conflict | A4 wants to DELETE (W1.4); A1 wants to improve (W2-W3) |
| **Resolution** | **KEPT** through Wave 1. Dashboard serves as the demo hosting surface for A1's SoSoValue evidence flow. Deletion deferred to post-W3 evaluation. |
| Implication | A4 must NOT include dashboard/ in its deletion scope during Wave 1. The `dashboard/server.py` and `dashboard/app.py` imports from `search/lineage` will need extraction as part of the general extraction plan. |

### C3: `demo.py` — KEPT (needed for A3 judge evaluation path)

| Detail | Value |
|--------|-------|
| Conflict | A4 wants to DELETE (W1.2); A3 wants to restructure (W1-W3) |
| **Resolution** | **KEPT** — `cli/demo.py` is the primary judge evaluation path. The one-command demo pattern (Wave 1 Track 1.3) depends on it. |
| Implication | A4 must skip demo.py and demo_run.py deletion. These are A3's domain. |

### C4: `evidence.py` — KEPT (needed for demo proof chain)

| Detail | Value |
|--------|-------|
| Conflict | A4 classifies as "research artifact" and wants to DELETE; A1 treats as core evidence pipeline |
| **Resolution** | **KEPT** — The SoSoValue evidence proof chain (`evidence-build`, `evidence-map`) is a core part of the demo flow per AGENTS.md. |
| Implication | `data/evidence.py` (388 LOC) and `cli/evidence.py` (165 LOC) remain. A1's simplifications proceed. |

### Summary of Preserved Directories

| Directory | LOC | Reason Preserved | Deletion Target |
|-----------|-----|------------------|-----------------|
| `evaluation/` | 6,473 | A5 signal narrative (Wave 1) | Wave 2 (after A5 completes) |
| `dashboard/` | 2,342 | A1 demo hosting (Waves 2-3) | Post-Wave 3 (evaluation needed) |
| `demo.py` | 494 | A3 judge evaluation path (Waves 1-3) | Wave 2 (A3's timeline) |
| `evidence.py` | 388 | Demo proof chain (AGENTS.md requirement) | Wave 2 (deferred) |

---

## 5. Lines of Code per Module

### 5.1 Per-Directory Breakdown

| # | Directory | Files | LOC | % of Total |
|---|-----------|-------|-----|------------|
| 1 | `siglab/tui/` | 21 | 6,876 | 13.8% |
| 2 | `siglab/evaluation/` | 10 | 6,473 | 13.0% |
| 3 | `siglab/search/` | 6 | 5,294 | 10.7% |
| 4 | `siglab/orchestration/` | 14 | 4,991 | 10.0% |
| 5 | `siglab/cli/` | 19 | 4,802 | 9.7% |
| 6 | `siglab/data/` | 9 | 3,906 | 7.9% |
| 7 | `siglab/live/` | 10 | 3,888 | 7.8% |
| 8 | `siglab/workspace/` | 5 | 2,975 | 6.0% |
| 9 | `siglab/llm/` | 4 | 2,278 | 4.6% |
| 10 | `siglab/research/` | 3 | 2,424 | 4.9% |
| 11 | `siglab/dashboard/` | 6 | 2,342 | 4.7% |
| 12 | `siglab/risk/` | 2 | 650 | 1.3% |
| 13 | `siglab/tools/` | 4 | 279 | 0.6% |
| 14 | `siglab/evaluator/` | 7 | 146 | 0.3% |
| — | **Subdirectory subtotal** | **120** | **47,324** | **95.3%** |
| — | Root `siglab/*.py` | 15 | 2,344 | 4.7% |
| | **TOTAL** | **135** | **49,668** | **100%** |

### 5.2 Largest Files (Top 15)

| Rank | File | LOC | Directory |
|------|------|-----|-----------|
| 1 | `evaluation/runner.py` | 3,537 | evaluation |
| 2 | `research/hypothesis.py` | 2,033 | research |
| 3 | `search/mutate.py` | 1,943 | search |
| 4 | `workspace/builder.py` | 1,740 | workspace |
| 5 | `evaluation/compile.py` | 1,522 | evaluation |
| 6 | `data/feeds.py` | 1,341 | data |
| 7 | `search/lineage_analysis.py` | 1,331 | search |
| 8 | `live/paper_client.py` | 1,243 | live |
| 9 | `search/lineage.py` | 1,185 | search |
| 10 | `llm/llm.py` | 1,128 | llm |
| 11 | `dashboard/server.py` | 1,098 | dashboard |
| 12 | `llm/claude.py` | 1,032 | llm |
| 13 | `tui/screens/paper.py` | 950 | tui |
| 14 | `orchestration/writer_runner.py` | 936 | orchestration |
| 15 | `orchestration/trials.py` | 904 | orchestration |

### 5.3 Smallest Files (Bottom 10)

| Rank | File | LOC | Notes |
|------|------|-----|-------|
| 1 | `live/deployed_agents/__init__.py` | 1 | Empty init |
| 2 | `tui/styles/__init__.py` | 1 | Empty init |
| 3 | `__init__.py` | 3 | Root package |
| 4 | `workspace/__init__.py` | 4 | Exports |
| 5 | `research/__init__.py` | 5 | Exports |
| 6 | `__main__.py` | 6 | Entry point |
| 7 | `cli/__main__.py` | 6 | CLI entry |
| 8 | `evaluator/gates.py` | 6 | Dead shim |
| 9 | `dashboard/__init__.py` | 10 | Exports |
| 10 | `evaluator/events.py` | 10 | Dead shim |

---

## 6. Research vs Operator Classification

### 6.1 Classification Key

| Classification | Meaning | Action |
|----------------|---------|--------|
| **OPERATOR** | Needed for live trading, risk management, or market data operations | KEEP |
| **MIXED** | Contains both operator and research code | TRIM — keep operator subset |
| **RESEARCH** | Only used by autonomous AI research loop, genetic algorithm, backtesting, or workspace artifacts | DELETE |

### 6.2 Per-Module Classification

| Module | LOC | Classification | % Operator | % Research | Operator Subset |
|--------|-----|----------------|-----------|------------|-----------------|
| `siglab/live/` | 3,888 | **OPERATOR** | 100% | 0% | All files |
| `siglab/risk/` | 650 | **OPERATOR** | 100% | 0% | All files |
| `siglab/data/` | 3,906 | **MIXED** | ~60% | ~40% | `sodex_client.py`, `sodex_feeds.py`, `sodex_rate_limit.py`, `sosovalue_client.py` (trimmed), `store.py` (simplified). Research: `evidence.py` |
| `siglab/cli/` | 4,802 | **MIXED** | ~15% | ~85% | Operator: `paper.py`, `sodex.py`, `deploy.py`, `market.py`, `__init__.py` (dispatch), `rich_utils.py`. Research: `run.py`, `benchmark.py`, `evidence.py`, `profile.py`, `ancestry_cmd.py`, `demo.py`, `config_cmd.py`, `api.py`, `telemetry.py`, `dashboard.py`, `helpers.py` (most) |
| `siglab/tui/` | 6,876 | **MIXED** | ~20% | ~80% | Operator: 3 screens (positions/paper, market, risk). Research: strategy, telemetry, evidence screens |
| `siglab/llm/` | 2,278 | **MIXED** | ~15% | ~85% | Operator: simplified chat client (~200 LOC). Research: claude.py (1,032 LOC), llm.py (1,128 LOC) |
| `siglab/dashboard/` | 2,342 | **RESEARCH** | 0% | 100% | Replaced by TUI |
| `siglab/evaluation/` | 6,473 | **RESEARCH** | 0% | 100% | Full backtesting engine — no operator value |
| `siglab/evaluator/` | 146 | **RESEARCH** | 0% | 100% | Dead shim — replaced by evaluation/ |
| `siglab/orchestration/` | 4,991 | **RESEARCH** | 0% | 100% | AI research loop — no operator value |
| `siglab/search/` | 5,294 | **RESEARCH** | 0% | 100% | Genetic algorithm + lineage — no operator value (except 2 extraction points) |
| `siglab/workspace/` | 2,975 | **RESEARCH** | 0% | 100% | Research artifact management — no operator value |
| `siglab/research/` | 2,424 | **RESEARCH** | 0% | 100% | Hypothesis sandbox — no operator value |
| `siglab/tools/` | 279 | **RESEARCH** | 0% | 100% | Planner tool interfaces — no operator value |
| Root `siglab/*.py` | 2,344 | **MIXED** | ~35% | ~65% | Operator: `config.py`, `schemas.py`, `utils.py`, `io_utils.py`, `path_utils.py`. Research: `benchmark.py`, `hardening_profile.py`, `run_config.py`, `families.py`, `track_registry.py`, `llm_metadata.py`, `visualization.py`, `telemetry.py` |

### 6.3 Summary

| Classification | Files | LOC | % of Total |
|----------------|-------|-----|------------|
| **OPERATOR** (pure) | ~22 | ~5,200 | ~10.5% |
| **MIXED** (trim to operator subset) | ~68 | ~22,200 | ~44.7% |
| **RESEARCH** (delete) | ~45 | ~22,268 | ~44.8% |
| **TOTAL** | **135** | **49,668** | **100%** |

### 6.4 Target Post-Wave 2 Architecture

After extracting 4 operator dependencies and deleting research modules:

| Module | Target LOC | Change |
|--------|-----------|--------|
| `siglab/live/` | ~3,900 | No significant change |
| `siglab/risk/` | ~650 | No change |
| `siglab/data/` | ~2,500 | Trim ~1,400 LOC (remove evidence.py, simplify feeds/store) |
| `siglab/cli/` | ~800 | Trim ~4,000 LOC (keep 4-5 operator commands) |
| `siglab/tui/` | ~1,500 | Simplify from 6,876 LOC |
| `siglab/llm/` | ~400 | Trim ~1,900 LOC |
| `siglab/dashboard/` | 0 | DELETE (2,342 LOC) |
| `siglab/evaluation/` | 0 | DELETE (6,473 LOC) |
| `siglab/evaluator/` | 0 | DELETE (146 LOC) |
| `siglab/orchestration/` | 0 | DELETE (4,991 LOC) |
| `siglab/search/` | 0 | DELETE (5,294 LOC) |
| `siglab/workspace/` | 0 | DELETE (2,975 LOC) |
| `siglab/research/` | 0 | DELETE (2,424 LOC) |
| `siglab/tools/` | 0 | DELETE (279 LOC) |
| Root `siglab/*.py` | ~800 | Trim ~1,500 LOC |
| **New files** | ~140 | `live/signal_compile.py` (~60), `data/deployment_store.py` (~80) |
| **TOTAL** | **~10,690** | **~78% reduction from 49,668** |

---

## 7. Wave 2 Prerequisites

### What Must Be Done Before Research Module Deletion

| Order | Task | File | Depends On |
|-------|------|------|------------|
| 1 | Extract `compile_spec()` to `live/signal_compile.py` | `evaluator/compile.py` → `live/signal_compile.py` | Wave 1 completion |
| 2 | Extract `LineageStore.deployment()`/`experiment_detail()` to `data/deployment_store.py` | `search/lineage.py` → `data/deployment_store.py` | Wave 1 completion |
| 3 | Update `live/runtime.py` import | Point to `live/signal_compile` | Step 1 |
| 4 | Update `live/exporter.py` import | Point to `data/deployment_store` | Step 2 |
| 5 | Update `cli/deploy.py` import | Point to `data/deployment_store` | Step 2 |
| 6 | Remove research helpers from `cli/helpers.py` | Lines 451, 485 and surrounding functions | N/A (no-op if deleted with research CLI) |
| 7 | Delete `cli/run.py` | Entire file (1,344 LOC) | Steps 1-5 verified |
| 8 | Delete research module directories | orchestration/, search/, evaluation/, evaluator/, workspace/, research/, tools/ | Steps 1-7 complete |

### Verification Gate (After Extraction, Before Deletion)

```bash
python3 -c "from siglab.live.runtime import DirectionalPerpsSigLabStrategy"
python3 -c "from siglab.live.exporter import LiveDeploymentManager"
python3 -c "from siglab.cli.deploy import main as deploy_main"
python3 -c "from siglab.data.deployment_store import DeploymentStore"
python3 -c "from siglab.live.signal_compile import compile_spec"
python3 -m pytest -q tests/live/ -x --timeout=60
python3 -m pytest -q tests/risk/ -x --timeout=60
python3 -m siglab.cli sodex-preflight --json
```

---

## 8. Complete Cross-Module Import Table

### 8.1 Operator → Operator Imports (Safe — no extraction needed)

| Source | Target | File:Line |
|--------|--------|-----------|
| `live/runtime.py` | `data/` (MarketDataProvider, ParquetLake) | `runtime.py:8` |
| `live/runtime.py` | `schemas.py` (SignalSpec) | `runtime.py:10` |
| `live/runtime.py` | `config.py` (load_settings) | `runtime.py:11` |
| `live/runtime.py` | `live/sodex_client.py` | `runtime.py:12` |
| `live/runtime.py` | `live/sodex_signing.py` | `runtime.py:13-18` |
| `live/runtime.py` | `evaluator/compile.py` | **EXTRACTION #1** `runtime.py:9` |
| `live/exporter.py` | `search/lineage.py` | **EXTRACTION #2** `exporter.py:12` |
| `live/exporter.py` | `live/` (internal) | `exporter.py:9-11, 13-19` |
| `cli/deploy.py` | `search/lineage.py` | **EXTRACTION #2** `deploy.py:12` |
| `cli/deploy.py` | `live/` (LiveDeploymentManager) | `deploy.py:10` |
| `cli/deploy.py` | `llm/` (ClaudeClient) | `deploy.py:11` |
| `cli/sodex.py` | `live/sodex_signing.py` | `sodex.py:13-20` |
| `cli/sodex.py` | `live/sodex_ws.py` | `sodex.py:21` |
| `cli/sodex.py` | `data/` (EvidenceStore, sodex_ws_evidence) | `sodex.py:11-12` |
| `cli/sodex.py` | `cli/helpers.py` | `sodex.py:9-10` |
| `cli/paper.py` | `live/paper_client.py` | `paper.py:13` |
| `cli/paper.py` | `live/promotion.py` | `paper.py:9-12` |
| `cli/paper.py` | `data/sodex_feeds.py` | `paper.py:11` |
| `cli/paper.py` | `data/store.py` | `paper.py:12` |
| `cli/market.py` | `cli/helpers.py` | `market.py:9-16` |
| `cli/market.py` | `config.py` | `market.py:17` |
| `cli/market.py` | `path_utils.py` | `market.py:18` |

### 8.2 Research → Research Imports (Delete together — no extraction needed)

| Source | Target | Count |
|--------|--------|-------|
| `orchestration/` | `evaluation/`, `evaluator/`, `llm/`, `research/`, `search/`, `workspace/`, `tools/` | 27+ |
| `search/` | `evaluation/`, `llm/` | 23+ |
| `workspace/` | `evaluation/`, `orchestration/`, `search/` | 10+ |
| `research/` | `evaluation/`, `evaluator/`, `search/` | 5+ |
| `cli/run.py` | `orchestration/`, `research/`, `search/`, `workspace/`, `evaluator/`, `run_config` | 15+ |
| `cli/benchmark.py` | `evaluator/`, `search/` | 3 |

---

## 9. Extraction Point Detail: Code Snippets

### Extraction #1: `compile_spec()` — Current Import Chain

```
live/runtime.py:9
    from siglab.evaluator.compile import compile_spec
        ↓ (shim — 22 LOC, uses __getattr__)
    siglab.evaluator.compile.__getattr__
        ↓ (importlib.import_module)
    siglab.evaluation.compile (1,522 LOC)
        ↓ (real implementation)
    compile_spec() at line ~60
        dependencies: SignalSpec, MarketDataProvider, SiglabConfig
```

The shim chain adds zero value — it exists only to maintain backward compatibility. The extraction removes both shim layers and points `runtime.py` directly to a minimal wrapper in `live/signal_compile.py`.

### Extraction #2: `LineageStore.deployment()` — Current Query

```python
# search/lineage.py:494-519
def deployment(self, spec_hash: str) -> dict[str, Any] | None:
    with self._connect() as connection:
        row = connection.execute("""
            SELECT spec_hash, created_at, strategy_name, strategy_dir,
                   spec_path, manifest_path, readme_path, job_name,
                   interval_seconds, wallet_label, config_path, scheduled,
                   dry_run, llm_finalized, support_status, support_reason,
                   metadata_json
            FROM deployments
            WHERE spec_hash = ?
            LIMIT 1
        """, (spec_hash,)).fetchone()
    ...
```

This is a straightforward SQL query with no dependency on the rest of `LineageStore`. The extracted `DeploymentStore` in `data/deployment_store.py` will replicate just this query + `__init__` + `list_deployments` (~80 LOC total).

### Extraction #3: Research Helpers in cli/helpers.py

```python
# cli/helpers.py:451 — lazy import inside _negative_streak()
def _negative_streak(...) -> int:
    from siglab.orchestration.contracts import motif_signature
    ...

# cli/helpers.py:485 — lazy import inside summarize_generalization_from_lib()
def summarize_generalization_from_lib(...) -> dict[str, Any]:
    from siglab.orchestration.trials import summarize_generalization
    ...
```

These are lazy (function-level) imports — they only trigger when the function is called. The calling functions are themselves only called by research code. When research modules are deleted, these imports will fail, but only if the function is called — which won't happen because the callers are also deleted.

### Extraction #4: cli/run.py — Complete Import List

```python
from siglab.config import load_settings           # KEEP (config survives)
from siglab.data import MarketDataProvider        # KEEP (data survives)
from siglab.evaluator import ResearchEvaluator    # DELETE (research)
from siglab.llm import ClaudeClient               # TRIM (llm survives trimmed)
from siglab.orchestration import (...)             # DELETE (research)
from siglab.orchestration.run_context import ...   # DELETE (research)
from siglab.orchestration.trials import (...)      # DELETE (research)
from siglab.research import ...                    # DELETE (research)
from siglab.run_config import (...)                # DELETE (research)
from siglab.schemas import SignalSpec              # KEEP (domain)
from siglab.search import (...)                    # DELETE (research)
from siglab.track_registry import ...              # DELETE (research)
from siglab.workspace import ...                   # DELETE (research)
from siglab.io_utils import write_json             # KEEP (utils)
from siglab.cli.helpers import (...)               # TRIM (keep operator subset)
```

---

## 10. Verification Gate

```bash
# 1. Document must exist and identify 4 extraction points
grep -c "extraction point\|extract" agent_workspace/judge_audits/architecture_blueprint.md
# Expected: >= 4

# 2. Clean Architecture layer mapping present
grep -q "layer\|Clean Architecture" agent_workspace/judge_audits/architecture_blueprint.md && echo "Layer mapping present" || echo "FAIL"

# 3. Dependency map contains cross-module imports
grep -q "live/runtime.py.*evaluator" agent_workspace/judge_audits/architecture_blueprint.md && echo "Dependency map OK" || echo "FAIL"

# 4. Conflict resolutions documented
grep -q "C1.*evaluation.*KEPT" agent_workspace/judge_audits/architecture_blueprint.md && echo "Conflict map OK" || echo "FAIL"

# 5. Per-directory LOC counts present
grep -q "6,876.*tui" agent_workspace/judge_audits/architecture_blueprint.md && echo "LOC table OK" || echo "FAIL"

# 6. Research vs Operator classification present
grep -q "OPERATOR.*RESEARCH" agent_workspace/judge_audits/architecture_blueprint.md && echo "Classification OK" || echo "FAIL"

# 7. Metrics verified
echo "Total Python files: $(find siglab/ -name '*.py' -not -path '*/__pycache__/*' | wc -l)"
echo "Total LOC: $(find siglab/ -name '*.py' -not -path '*/__pycache__/*' | xargs wc -l | tail -1 | awk '{print $1}')"
```
