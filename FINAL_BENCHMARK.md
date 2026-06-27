# SigLab Final Benchmark + Deep Audit Report

**4 agents: Live run, LLM optimization, deep dead code, accuracy report**
**Date: 2026-06-27 | 36 files, ~14,850 lines, 555 tests pass**

---

## Live Performance

| Metric | Value |
|--------|-------|
| Demo run | **5.16s**, status: READY_FOR_OPERATOR_REVIEW |
| SoSoValue ETF | 610 evidence rows, flow −$444.5M |
| SoDEX REST | 492 evidence rows, BTC bid=$60,078 ask=$60,079 |
| Total evidence | **1,102 rows** across 2 files |
| Test suite (fast) | **548 pass in 4.77s** |
| Dashboard endpoints | **6/7 routes 200 OK** (/config returns 404 — expected) |
| Production readiness | **6.5/10** |

---

## LLM Integration: Verified Working

```python
# ClaudeClient works with OpenModel AI
>>> c.is_configured() == True
>>> c.complete_text("hello") # returns valid response
>>> c.complete_json(...) # returns parsed JSON
```

**LLM is only used in 1 place: `live/exporter.py`** for strategy documentation notes. NOT used in demo, research, or operator pipeline.

---

## New Dead Code Discovered

| Type | Count | Examples |
|------|-------|---------|
| Unused imports | 7 | `sqlite3` in routes.py, `compute_composite_score` in paper_client.py |
| Dead function params | 16 | All `provider` params in llm.py (single-provider world) |
| Dead JS (~240 lines) | 2 blocks | Command palette (190L, no HTML), Help modal (50L, no HTML) |
| Dead CSS (~60 lines) | 6 classes | `quick-action-card`, `mobile-tab-*`, `help-trigger`, `family-guide-panel` |
| Dead template | 1 | `run.html` (106L, no route renders it) |

---

## CSS Issues Found (BNCH-LLM)

| Issue | Location | Detail |
|-------|----------|--------|
| `.toast-error` defined twice | lines 1600+1618 | Could merge |
| `.heatmap-svg` defined twice | lines 1495+1867 | Identical rules |
| `.asset-action-svg` defined twice | lines 1434+1874 | Near-identical |
| Misplaced CSS block | lines 2056-2115 | `.family-pills` inside `.page-btn` — braketing error |
| `.deployment-grid` duplicates `.summary-grid` | | Identical grid layout |
| Mobile tab bar CSS | ~30 lines | No HTML uses it |

---

## Remaining Optimization Opportunities

| Item | Lines Saved | Effort |
|------|:-----------:|:------:|
| Delete dead JS (command palette + help modal) | ~240 | Small |
| Fix CSS braketing error + merge duplicates | ~40 | Small |
| Remove 7 unused imports | ~7 | Trivial |
| Remove 16 dead function params | ~30 | Medium |
| Further compile.py boilerplate extraction (LLM suggestion) | ~95 | Medium |
| Delete dead template run.html | ~106 | Trivial |
| Delete dead mobile-tab CSS | ~30 | Trivial |
| **Total** | **~548** | |

---

## Summary

| Metric | Value |
|--------|-------|
| **Working features** | 13 (market report, SoSoValue, SoDEX, dashboard, feature DSL, evaluation, paper trading, CLI, config, operator, SoDEX adapter, telemetry, experiment repo) |
| **Non-working/limited** | 5 (live trading, LLM pipeline integration, E2E flows, WebSocket, dashboard UI polish) |
| **Production readiness** | **6.5/10** |
| **Top blocker** | Enable live SoDEX execution (dry-run only) |
| **Second blocker** | Complete WS market data integration |
| **Third blocker** | Integrate LLM into operator pipeline |
