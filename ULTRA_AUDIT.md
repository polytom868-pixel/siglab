# Ultra Deep Audit — All Findings

**4 agents: Function chain trace, dead code hunter, duplication auditor, docs/dashboard audit**
**Date: 2026-06-27 | 36 files, ~15,500 lines, 555 tests pass**

---

## 1. Function Call Chain (ULTRA-Trace)

**678 total functions across 27 files.** Demo run chain depth: 14 calls.

### Redundant Calls in demo-run

| Call | Issue | Times Called |
|------|-------|-------------|
| `sodex_preflight_report()` | Called 3× in same run | **3** |
| `trace_paths_for_telemetry()` | Called 2× | **2** |
| `provider_metric_paths_for_telemetry()` | Called 2× | **2** |
| `evidence_paths_for_telemetry()` | Called 2× | **2** |
| `build_telemetry_payload()` | Called 2× | **2** |

**Fix:** Cache preflight + telemetry results for the duration of one run.

### Near-Duplicate Functions

| Function 1 | Function 2 | Overlap | Recommendation |
|------------|-----------|---------|----------------|
| `_gen_evidence()` (demo.py) | `_async_evidence_build()` (evidence.py) | ~85% | Consolidate into one |
| `float_or_none()` | `safe_float()` (utils.py) | 100% | Merge |
| `read_jsonl()` | `read_jsonl_with_stats()` | 95% | Inline into single function |
| `_record_sort_key()` | `_record_sort_key_internal()` | 100% | Unused |

---

## 2. Dead Code (ULTRA-Dead)

[Full report at `agent://ULTRA-Dead` — 7KB of findings]
Key findings (see full report for file:line):

- **EvidenceStore**: 4 methods dead (already removed in P-03, but evidence.py still has older dead code)
- **Module-level vars**: Several unused `__all__` exports and constants
- **Conditional branches**: All 6 compile_spec family branches are reachable — none dead
- **Zero-import files**: `siglab/evaluation/__init__.py`, `siglab/__init__.py` — trivial package markers

---

## 3. Duplicated Logic (ULTRA-Dupe)

### HIGH Severity

| Duplicate | Files | Overlap | Fix |
|-----------|-------|---------|-----|
| `_dr` vs `raw_experiments` | routes.py:82-133 / experiment_repo.py:16-74 | ~85% | Delete `_dr`, import `raw_experiments` |
| runs_map vs `raw_runs` | routes.py:141-175 / experiment_repo.py:76-156 | ~85% | Use `raw_runs` directly |
| `_EndpointMetrics` vs `_Metrics` | feeds.py:1270-1277 / feeds.py:2058-2065 | **100%** | Single shared dataclass |

### MEDIUM Severity

| Duplicate | Files | Fix |
|-----------|-------|-----|
| 6× `add_subparser()` | All CLI files | Centralize CLI registration |
| `_DEFAULT_PORT` | dashboard.py:11 + routes.py:1274 | Define once in config |

### LOW Severity

| Duplicate | Count | Fix |
|-----------|-------|-----|
| `to_dict()` methods | 6 classes | Use `dataclasses.asdict()` |
| `from __future__ import annotations` | 6 CLI files | Shared init pattern |
| JSON serializer settings | 2 files | Shared json helper |

---

## 4. Dashboard + Documentation (ULTRA-Docs)

### Server State
| Issue | Detail |
|-------|--------|
| **SERVER IS STALE** | Started 16:04, code changed 23:22. Running OLD routes.py. **MUST RESTART** |
| `run.html` template | Exists but no route renders it (`/runs/{run_id}` redirects to `/`) |
| `macros.html` templates | 2 orphan files in templates/ |
| `partials/dashboard/` + `partials/run/` | Empty directories (all partials deleted) |
| **57 unused CSS selectors** | Command-palette(9), help-modal(8), quick-actions(6), breadcrumb(4), etc. |

### Documentation Staleness

| Doc | Status | Issues |
|-----|--------|--------|
| `docs/module-dashboard.md` | ❌ **CRITICAL** | Port wrong (3100→8080), refs deleted server.py/ws.py, stale routes |
| `docs/module-tui-app.md` | ❌ **CRITICAL** | **403 lines, ENTIRELY stale** — TUI was deleted |
| `docs/demo-script.md` | ✅ **Accurate** | All CLI commands verified correct |

5 more docs have stale references to deleted TUI, WebSocket, and risk modules.

---

## 5. Consolidated Action Plan

| Priority | Issue | Lines Saved | Effort |
|----------|-------|:-----------:|:------:|
| 🔴 HIGH | Consolidate `_dr`/`raw_experiments` + runs_map/raw_runs | ~100 | Small |
| 🔴 HIGH | Merge `_EndpointMetrics`/`_Metrics` in feeds.py | ~16 | Trivial |
| 🔴 HIGH | Cache preflight + telemetry (eliminate 2× calls) | ~10 | Small |
| 🟡 MED | Consolidate `_gen_evidence`/`_async_evidence_build` | ~80 | Medium |
| 🟡 MED | Merge `read_jsonl`/`read_jsonl_with_stats` | ~10 | Trivial |
| 🟡 MED | Merge `float_or_none`/`safe_float` | ~4 | Trivial |
| 🟡 MED | Restart dashboard server | 0 | **NOW** |
| 🟢 LOW | Prune 57 unused CSS selectors | ~57 | Small |
| 🟢 LOW | Update 6 stale doc files | ~0 | Small |
| 🟢 LOW | Use `dataclasses.asdict()` for 6 classes | ~60 | Medium |
| 🟢 LOW | Centralize `_DEFAULT_PORT` | ~2 | Trivial |
