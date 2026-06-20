# SigLab E2E Test Results

**Agent**: 2B — Run Playwright E2E Tests  
**Date**: 2026-06-20  
**Branch**: `test-data-wave-2`  

---

## Summary

| Metric | Value |
|--------|-------|
| Tests Total | 8 |
| Passed | **8/8 (100%)** |
| Failed | 0 |
| Duration | 35.37s |

---

## Test Results

### Flow 1 — Landing Page (`test_home_page_loads`) ✅
- **What it tests**: Homepage loads run cards (`.run-card`), 4 summary cards (`.summary-card`), filter controls (`#trackFilter`, `#familyFilter`, `#metricFilter`), and hero heading.
- **What happened**: Navigated to `/`, waited for `networkidle`, all elements present and visible.

### Flow 2 — Error Handling (`test_error_handling_on_api_failure`) ✅
- **What it tests**: When `/api/runs` returns 500, the error toast (`#errorToast`) becomes visible with a failure message.
- **What happened**: Route intercept returned 500, `showError()` called by home.js, toast appeared with "Failed to load".

### Flow 3 — Navigation to Ops (`test_navigation_to_ops`) ✅
- **What it tests**: Clicking "Ops" navbar link navigates to `/ops`, `.ops-panel` elements and "Research Operations Board" heading visible.
- **What happened**: Clicked nav link, URL changed to `/ops`, HTMX loaded panels, heading found.

### Flow 4 — Filter Interaction (`test_filter_interaction`) ✅
- **What it tests**: Selecting `trend_signals` in the track filter updates scope summary to show "Directional Perps".
- **What happened**: Selected option, JS refresh ran, `renderScope()` updated `#scopeSummary` text with resolved track label.

### Flow 5 — Experiment Navigation (`test_experiment_navigation`) ✅
- **What it tests**: Clicking "Open Run" on a run card navigates to `/runs/{id}` with experiments table visible.
- **What happened**: Clicked button-link, URL changed to `/runs/{id}`, `#experimentsTable` loaded via HTMX.

### Flow 6 — Auto-Refresh Toggle (`test_auto_refresh_indicator`) ✅
- **What it tests**: Auto-refresh checkbox starts checked, can be unchecked and re-checked.
- **What happened**: Checkbox found checked, `uncheck()` and `check()` toggled state correctly.

### Flow 7 — Theme Toggle (`test_theme_toggle`) ✅
- **What it tests**: Theme toggle button switches `data-theme` attribute between dark and light modes.
- **What happened**: Default dark (no attribute), clicked → light, clicked again → dark.

### Flow 8 — Accessibility Skip Link (`test_accessibility_skip_link`) ✅
- **What it tests**: Skip-to-content link (`.skip-link`) has correct `href="#main-content"` and becomes visible on Tab press.
- **What happened**: Attribute verified, Tab key focused element, CSS `:focus` selector made it visible.

---

## Issues Fixed

### JS Namespace Conflicts (6 files)
The application's JavaScript had a critical bug where multiple scripts declared `const { formatDateTime, ... } = window.SigLabUi;` at the **top level** (not inside a function/module). Because non-module `<script>` tags execute in the global scope, the second such declaration threw `"Identifier 'formatDateTime' has already been declared"`, **preventing ALL JavaScript from executing on the page**.

**Files fixed:**
| File | Fix |
|------|-----|
| `siglab/dashboard/static/common.js` | Replaced `window.SigLabUi = {...}` with `Object.assign(window.SigLabUi, {...})` to preserve constants |
| `siglab/dashboard/static/chart-engine.js` | Removed conflicting destructured names; wrapped in IIFE |
| `siglab/dashboard/static/home.js` | Wrapped in IIFE |
| `siglab/dashboard/static/app.js` | Wrapped in IIFE |
| `siglab/dashboard/static/ops.js` | Wrapped in IIFE |
| `siglab/dashboard/static/experiment.js` | Wrapped in IIFE |

### Test Robustness
- **Filter interaction test**: Changed from `page.wait_for_timeout(1000)` to `expect().to_contain_text(timeout=15000)` for reliable async wait.

---

## How to Re-run

```bash
# From repo root (uses autouse server fixture):
python3 -m pytest tests/e2e/test_demo_flows.py -v --tb=short -p no:xdist -o "addopts="

# Or via runner script:
./tests/e2e/run_e2e.sh
```

---

## Screenshots

### Dashboard Page
![E2E Dashboard](e2e_dashboard.png)

### Ops Board
![E2E Ops Board](e2e_ops.png)
