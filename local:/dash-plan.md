# Dashboard Simplification Plan

Current dashboard/ directory totals: 11,350 lines (Python 3,552 + Templates 1,287 + Static JS 2,883 + CSS 2,565 + SVG 1)

---

## PHASE 1: DEAD CODE DELETION (LOW risk, ~1,050 lines)

### D1. Delete entire `experiment_enricher.py` — 638 lines saved
**File:** `siglab/dashboard/experiment_enricher.py`
**Risk: LOW** — Every method in `ExperimentEnricher` class is a direct duplicate of a method in `DashboardState` (in routes.py). The enricher is instantiated in `lifespan()` at routes.py:1933 but **never called** by any DashboardState method. DashboardState uses its own inline `_ae()`, `_ap()`, `_ar()`, `_ns()`, `_rss()`, `_svr()`, `_soa()`, `_dp()`, `_dd()`, `_ni()`, `_lp()`, `_lm()` — all self-contained.

| Enricher method | Duplicates DashboardState |
|---|---|
| `enrich_experiment()` (lines 276-501, 226 lines) | `_ae()` (lines 588-810, 223 lines) |
| `annotate_positions()` (lines 503-530) | `_ap()` (lines 812-833) |
| `annotate_canonical_run()` (lines 532-572) | `_ar()` (lines 835-871) |
| `summarize_ops()` (lines 574-638) | `_soa()` (lines 1108-1175) |
| `normalize_deployment()` (lines 84-99) | `_dd()` (lines 283-295) |
| `display_path()` (lines 75-82) | `_dp()` (lines 274-281) |
| `llm_provider_label()` / `llm_model_label()` | `_lp()` / `_lm()` |
| `normalize_stage()` (lines 107-147) | `_ns()` (lines 313-353) |
| `research_stages()` (lines 149-198) | `_rss()` (lines 355-400) |
| `skill_value_report()` (lines 200-272) | `_svr()` (lines 402-473) |
| `now_iso()` | `_ni()` (line 579-580) |
| `_classify_skill()` (lines 26-45) | `_cs()` in routes.py (lines 62-81) — itself dead code |

**Action:** Delete file. Remove instantiations from `lifespan()` (lines 1929-1933) and import lines 11-12 from routes.py. Also stop importing from `siglab.dashboard.experiment_repo` in enricher (already imported in routes.py).

### D2. Remove `ExperimentRepo` dataclass from `experiment_repo.py` — 194 lines saved
**File:** `siglab/dashboard/experiment_repo.py`, lines 164-357 (the `@dataclass ExperimentRepo` class)
**Risk: LOW** — DashboardState has its own inline `_lj()` (load_json, line 297), `_loa()` (load_artifact, line 1048), `_dbp()` (line 310), `_ed()` (experiment_detail, line 557), `_wsp()` (workspace_placeholders, line 475). The module-level functions `raw_experiments()` (lines 21-79, needed by routes.py `_dr()`) and `raw_runs()` (lines 82-161, needed by `_rs()`) must be kept.

**Action:** Delete `ExperimentRepo` class. Keep `raw_experiments()` and `raw_runs()` module-level functions.

### D3. Delete `_cs()` function in routes.py — 20 lines saved
**File:** `siglab/dashboard/routes.py`, lines 62-81
**Risk: LOW** — `_cs()` is defined but never called in routes.py (confirmed by grep). `_classify_skill()` in experiment_enricher.py is the same dead function (will be deleted by D1).

**Action:** Delete function `_cs()` and its comments.

### D4. Delete 4 dead `/templates/*` route handlers — 32 lines saved
**File:** `siglab/dashboard/routes.py`
- Lines 1846-1854: `template_dashboard()` — duplicate of `create_app()` `/` route
- Lines 1857-1865: `template_run()` — duplicate of redirector route
- Lines 1868-1876: `template_experiment()` — duplicate of `create_app()` `/experiments/{hash}` route
- Lines 1879-1883: `template_ops()` — duplicate of `create_app()` `/ops` route

**Risk: LOW** — None of these routes are called from any frontend HTML/JS (confirmed by grep across the entire repo). The `create_app()` routes serve the same pages directly.

**Action:** Delete all 4 route handler functions + their `@router.get(...)` decorators.

### D5. Delete dead `partials/run/detail_panel` route — 21 lines saved
**File:** `siglab/dashboard/routes.py`, lines 1824-1844
**Risk: LOW** — Route `partials/run/detail_panel` is never referenced from any template's `hx-get`, any JS `fetch()`, or any `apiFetch()` call. The template it renders (`_detail_panel.html`) is thus dead too.

**Action:** Delete route handler function + `@router.get(...)` decorator.

### D6. Delete `_detail_panel.html` template — 134 lines saved
**File:** `siglab/dashboard/templates/partials/run/_detail_panel.html`
**Risk: LOW** — Only the dead route rendered this. The detail panel is rendered client-side by `app.js:renderDetail()` via `/api/experiments/{hash}`.

**Action:** Delete file.

### D7. Remove unused imports from lifespan() — 5 lines saved
**File:** `siglab/dashboard/routes.py`, lines 1929-1933
**Risk: LOW** — These imports `ExperimentRepo` and `ExperimentEnricher` and instantiate them, but `DashboardState` never reads `self.repo` or `self.enricher` after construction.

**Action:** Delete these lines.

### Total Phase 1: ~1,044 lines saved

---

## PHASE 2: MODULE MERGE + CONSOLIDATION (MEDIUM risk, ~200-400 lines)

### M1. Inline `experiment_repo.py` module-level functions into routes.py — 80 lines moved
**File:** `siglab/dashboard/experiment_repo.py`, keep `raw_experiments()` and `raw_runs()` but move to routes.py
**Risk: LOW** — These are only called from routes.py `_dr()` and `_rs()`. Moving them avoids the cross-module dependency. Saves no lines but reduces file count.

**Action:** Move `raw_experiments()` (lines 21-79) and `raw_runs()` (lines 82-161) into routes.py. Delete experiment_repo.py entirely.

### M2. Merge `_PARTIAL_OPS` map into route handler — 30 lines saved
**File:** `siglab/dashboard/routes.py`, lines 1635-1703
**Risk: LOW** — The dictionary maps strings to template paths + lambdas. These can be inlined into a single function without the lookup table.

**Action:** Replace `_PARTIAL_OPS` dict + `partial_ops_router()` with a single function using `match`/if-else. The dictionary itself is ~35 lines of boilerplate; a match statement would be ~25 lines. Net: -10 lines.

### M3. Delete duplicated `_tmpl()` function — 4 lines saved
**File:** `siglab/dashboard/routes.py`, lines 1310-1313
**Risk: LOW** — `_tmpl()` is identical to `_ts()` (lines 49-51). Both do the same `templates or error dict` check. `_ts()` is dead code (unused). `_tmpl()` is used by all partial/template routes.

**Action:** Keep `_tmpl()`, delete `_ts()` (lines 49-51).

### M4. Remove `SIGLAB_VERSION` constant — 2 lines saved
**File:** `siglab/dashboard/routes.py`, line 1317
**Risk: LOW** — Never used.

**Action:** Delete line.

### Total Phase 2: ~46 lines saved + file count reduction

---

## PHASE 3: STRUCTURAL SIMPLIFICATION (MEDIUM-HIGH risk, ~500-1,200 lines)

### S1. Remove `chart-engine.js` — 1,062 lines freed (but cannot fully delete)
The chart engine is used by both `app.js` (run page) and `experiment.js` (experiment detail page). Deleting it would require either:
- **Option A (MEDIUM risk):** Strip the interactive chart and fall back to server-rendered SVG sparklines (already in `_improvement_chart.html`). The Sparkline chart in the dashboard cards is already server-rendered via Jinja2. The Chart.js interactive version adds detail panel interactivity (tooltip on hover, zoom). If the detail panel doesn't need pointer-level precision, the SVG version suffices. Savings: -1,062 lines.
- **Option B (HIGH risk):** Merge chart-engine.js into app.js. This doesn't save lines but reduces file count.

**Recommended:** Keep chart-engine.js for now — interactive chart is core UX for experiment detail page.

### S2. Merge `home.js` into `app.js` — no line savings, but reduces redundancy
Both files import the same utilities from common.js/constants.js and have similar rendering logic. `home.js` (256 lines) and `app.js` (850 lines) share 90% of the same imports and pattern. 

**Action:** Add `home.js` functionality into `app.js` (guarded by page detection), then delete `home.js`. Net: line-neutral or slightly negative (adds a page detection check). Reduces file count.

### S3. Eliminate HTMX partial rendering in favor of pure JS — 107 lines saved
The dashboard uses TWO rendering paths:
1. HTMX partials (`/partials/dashboard/summary`, `/partials/dashboard/runs`) — server-rendered HTML
2. JS fetch (`/api/experiments`, `/api/runs`) + client-side rendering

**Rationale:** The HTMX partials duplicate the JS rendering. All interactive views (detail panel, chart, experiment table) use JS rendering anyway. The HTMX handles only summary cards and run cards.

**Action:** Remove HTMX from `dashboard.html` and `run.html`. Remove `/partials/dashboard/summary`, `/partials/dashboard/runs`, `/partials/run/summary`, `/partials/run/family_pills`, `/partials/run/improvement_chart`, `/partials/run/experiment_table` routes. Remove all partial templates they reference:
- `_summary_cards.html` (24 lines)
- `_run_cards.html` (102 lines)
- `_run_summary.html` (66 lines)
- `_improvement_chart.html` (127 lines)
- `_experiment_table.html` (31 lines)
- `_family_pills.html` (11 lines)

**Savings:** ~170 lines Python + ~361 lines templates = 531 lines. But requires ensuring JS rendering handles all cases.

**Risk: MEDIUM** — Removes server-rendered fallback. If JS fails to load, users see blank pages. The JS rendering already runs the same data through the same logic and would be the correct replacement. The HTMX routes were a progressive enhancement layer.

### S4. Flatten the `DashboardState` class by removing redundant internal helpers — 100 lines saved
Several private methods are one-liners that call other methods:
- `_aes()` (~10 lines, calls `_dr` + `_ap` + `_ae` — inline it)
- `_ni()` (2 lines — inline `_now_iso()`)
- `_dbp()` (3 lines — inline `self.config.ancestry_db_path if self.config else None`)
- `_lp()` (2 lines — inline)
- `_lm()` (4 lines — inline)

**Savings:** ~20 lines. Trivial.

### S5. Remove `deploy_experiment()` and deploy route — 180+ lines saved
**File:** routes.py lines 1273-1305 (method) + 1368-1380 (route handler)
**Risk: HIGH** — This would break the "Deploy" button on the experiment detail page, which calls `POST /api/experiments/{hash}/deploy`. Only do this if deploy-from-dashboard is not a demo requirement.

### Total Phase 3: 531-1,711 lines, depending on scope

---

## PHASE 4: RISK_UTILS EXTRACTION (no line savings, structural improvement)

### E1. Move `risk_utils.py` out of dashboard/ — 519 lines moved (not saved)
**File:** `siglab/dashboard/risk_utils.py`
**Risk: MEDIUM** — Still used by `live/paper_client.py` and `operator/pipeline.py`. Moving to `siglab/utils/risk.py` or a new `siglab/risk/` module would clean up the dashboard/ directory but requires updating 2 import paths.

**Action:** Move file to `siglab/risk_utils.py`. Update imports in:
- `siglab/live/paper_client.py` line 38
- `siglab/operator/pipeline.py` line 11

---

## SUMMARY TABLE

| Item | Lines Saved | Risk | Type |
|------|------------|------|------|
| D1. Delete experiment_enricher.py | 638 | LOW | Python dead code |
| M1. Inline experiment_repo.py → routes.py | 0 (merge) | LOW | Consolidation |
| D2. Delete ExperimentRepo class | 194 | LOW | Python dead code |
| D6. Delete _detail_panel.html | 134 | LOW | Template dead code |
| S3. Remove HTMX partial rendering | 531 | MED | Structural |
| D5. Delete detail_panel route | 21 | LOW | Python dead code |
| D4. Delete /templates/* routes | 32 | LOW | Python dead code |
| D3. Delete _cs() function | 20 | LOW | Python dead code |
| D7. Remove unused lifespan imports | 5 | LOW | Python dead code |
| M3. Delete _ts() duplicate | 4 | LOW | Python dead code |
| M2. Flatten _PARTIAL_OPS dict | 10 | LOW | Consolidation |
| M4. Remove SIGLAB_VERSION | 2 | LOW | Python dead code |

### Safe minimum (LOW risk only, D1-D7 + M1-M4): ~1,060 lines saved
### With HTMX removal (S3): ~1,591 lines saved  
### With deploy removal (S5): ~1,771 lines saved
### With chart-engine extraction (S1): ~2,833 lines saved (full target)

---

## CUT PLAN — RECOMMENDED EXECUTION ORDER

### Phase A (LOW risk, immediate, ~1,044 lines):
1. Delete experiment_enricher.py
2. Delete ExperimentRepo class from experiment_repo.py  
3. Move experiment_repo.py module-level functions into routes.py, delete the file
4. Delete _detail_panel.html
5. Delete _cs(), dead template routes, detail_panel route, _ts(), SIGLAB_VERSION, unused imports

### Phase B (MEDIUM risk, ~531 lines):
5. Remove HTMX partial rendering (6 routes, 6 partial templates)
6. Verify JS rendering covers all cases

### Phase C (HIGH risk, optional):
7. Remove deploy route + deploy_experiment() method (~50 lines)
8. Move risk_utils.py out of dashboard/ (structural, 519 lines moved)

---

## RISK NOTES

- **Delete enricher (D1):** Verify by searching for `self.enricher` or `state.enricher` — confirmed zero reads after construction.
- **Delete repo class (D2):** Search for `self.repo` in DashboardState — confirmed zero reads. `repo.config` assignment at line 1932 is a no-op.
- **HTMX removal (S3):** Requires ensuring `dashboard.html` and `run.html` load their JS scripts on initial render. The `home.js` and `app.js` already call `apiFetch()` on DOMContentLoaded, so the data loads on first paint without HTMX. The HTMX was an optimization for partial refreshes (every 30s), which the JS `setInterval` already handles.
- **Deploy removal (S5):** Breaking change for the deploy-from-UI feature. Check if users depend on this or if it's only used from CLI.
