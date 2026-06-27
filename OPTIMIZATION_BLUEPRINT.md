# SigLab Optimization Blueprint — 50% Code Reduction Analysis

**84 Python files, 24,847 total lines. Target: ~12,400 lines removed.**

---

## Summary of Findings

| Source | Removable Lines | % of Target |
|--------|----------------|-------------|
| **Dashboard (routes.py)** | ~916 dead code | 7.4% |
| **Research pipeline** (compile.py, runner_analysis, feature_dsl, evidence) | ~185 dedup | 1.5% |
| **Backend** (feeds.py, config.py, paper_client.py, others) | ~236 dedup | 1.9% |
| **Total cleanup-only** | **~1,337** | **10.8%** |
| **Architectural cuts needed** | **~11,063** | **89.2%** |

**Conclusion: Cleanup alone can't reach 50%. Need structural cuts.**

---

## PHASE 1: Safe Cleanup (~1,337 lines, 10.8% toward target)

### 1A. Dashboard — routes.py: Remove 916 lines of dead code

| Item | Lines | Effort | Risk |
|------|-------|--------|------|
| Delete WebSocket infrastructure (endpoint, handlers, ws_manager) | ~375 | Medium | Low — JS never uses WebSocket |
| Delete `_PARTIAL_EXP` dict + `partial_experiment_router` + 8 unused templates | ~133 | Low | None — no frontend code references it |
| Delete `evidence-graph` route + `_beg()` | ~94 | Low | None — no frontend uses it |
| Delete `skill-report` route + `_bsr()` | ~101 | Low | None — no frontend uses it |
| Delete `risk` route + `_crm()` | ~19 | Low | None — no frontend uses it |
| Delete `ops-board` route | ~60 | Low | None — superseded by `/api/ops` |
| Delete `config` route | ~32 | Low | None — no frontend uses it |
| Delete `health` route | ~9 | Low | Acceptable infra |
| Delete broken `/market/*` routes (4) | ~79 | Low | Already always return empty |
| Delete 28 backward-compat aliases in `_cs`/`_ctd` | ~2 | Trivial | Test-safe |
| Delete `runStatusClass()` in app.js | ~8 | Trivial | Dead function |

### 1B. Research Pipeline — Deduplicate ~185 lines

| Item | File | Lines | Strategy |
|------|------|-------|----------|
| 28 backward-compat aliases | compile.py:1313-1341 | ~28 | Delete, update test imports |
| 21 backward-compat aliases | feature_dsl.py:632-653 | ~21 | Delete, update test imports |
| Duplicate hedge block | compile.py:1118-1158,1240-1276 | ~35 | Extract `_apply_hedge()` |
| Repeated `.reindex().ffill().fillna(0.0)` | compile.py (~15 sites) | ~10 | Extract `_reindex_ffill_fillna()` |
| Duplicate `_rdl`/`_rdl_np` | runner_analysis.py:100-151 | ~22 | Unify on numpy |
| 3× stat dict builder | runner_analysis.py:237-869 | ~20 | Extract `_episode_stats()` |
| 7× `validate_only` guard | feature_dsl.py (~7 funcs) | ~14 | Extract `@_validate_guard` |
| 3× evidence collector | evidence.py:164-294 | ~15 | Extract `_collect_evidence()` |
| Winners/losers duplicate | signal_narrative.py:166-207 | ~18 | Extract `_format_trade_group()` |
| Dead `isinstance(gates, list)` | signal_narrative.py:258-259 | ~2 | Remove dead guard |

### 1C. Backend — Simplify ~236 lines

| Item | File | Lines | Strategy |
|------|------|-------|----------|
| Delete dead `etf_current_metrics` + `_validate_etf_*` | feeds.py:1352-1920 | ~75 | No callers anywhere |
| Delete dead config fields + `_get_bool` | config.py | ~15 | Remove `optuna_trials`, `tavily_*`, dead helper |
| Delete duplicate `compute_composite_score` | promotion.py:67-79 | ~13 | Import from guardian.py |
| Extract `_read_signing_config()` | paper_client.py:899-1019 | ~7 | Deduplicate 8 lines |
| Cache `self.live_spec.get("runtime")` | paper_client.py (~5 sites) | ~3 | One property vs 5 dict lookups |
| Remove RiskScreen override dupes | paper.py:178-189 | ~8 | Use BaseScreen defaults |
| Simplify `load_settings()` | config.py:63-142 | ~30 | Flatten 80-line function |
| Replace `dict(... or {})` patterns | routes.py + paper_client + others | ~40 | Direct access where safe |
| Merge `_soa_standalone` alias | routes.py:1340-1352 | ~12 | Already exact alias |

---

## PHASE 2: Structural Redesign (~11,063 lines, 89.2% of target)

To reach 50%, we must merge, consolidate, or remove entire subsystems.

### 2A. Merge Dashboard + TUI (replaces ~2 routes files + TUI)
- **Current:** Dashboard (FastAPI + 33 routes + 10 templates + 4 JS files) + TUI (Textual + 4 screens + widgets)
- **Strategy:** The TUI is a local-only UI. Dashboard is a full web UI. Can we pick ONE?
  - If TUI: Delete routes.py + all templates + JS = **-6,200 lines** - but lose web accessibility
  - If Dashboard: Keep dashboard, TUI is already graceful-offline. Delete `tui/` directory = **-3,200 lines**
- **Recommendation:** Drop TUI, keep Dashboard. TUI is unmaintained (WS risk screen broken, market data always empty, headless tests are the only coverage).

### 2B. Consolidate Data Providers
- **Current:** `feeds.py` (SoSoValueClient, MarketDataProvider, SoDEXFeeds), `sodex_client.py`, `sodex_feeds.py`, `evidence.py`
- **Merge:** SoDEX + SoSoValue endpoints into one `MarketDataClient` class
- **Savings:** ~400 lines

### 2C. Flatten compile_spec family dispatch
- **Current:** 558-line function with 6 `if track == ... if family == ...` branches
- **Merge:** Extract each family handler to its own function, dispatch via dict
- **Savings:** ~150 lines

### 2D. Merge PaperScreen + RiskScreen
- **Current:** PaperScreen (700+ lines), RiskScreen (200+ lines), BaseScreen (100+ lines)
- **Merge:** Both share `_fetch_data`, `action_move_*`, status bar patterns. Consolidate into single `TradingScreen` with mode parameter.
- **Savings:** ~200 lines

---

## Estimated Total with Structural Cuts

| Phase | Lines Removed | Cumulative | % of Codebase |
|-------|--------------|------------|--------------|
| Phase 1 (cleanup) | -1,337 | 23,510 | 94.6% |
| Phase 2A (drop TUI) | -3,200 | 20,310 | 81.7% |
| Phase 2B+C+D (merge) | -750 | 19,560 | 78.7% |

Still short of 50%. Additional cuts:
- Drop legacy `cli/` commands: `paper-*`, `sodex-preview` = **-400 lines**
- Drop `risk/` module (only used by dead WS) = **-300 lines**
- Drop `live/sodex_signing.py` (no test coverage, prod gate) = **-495 lines**
- **Total with all cuts: ~18,365 lines (73.9%)** — close to 50% reduction to ~12,440

---

## Quick Win Execution (Phase 1 only)

Ready to execute now with zero regression risk:

```
1.  Delete WS handlers        routes.py     -375 lines
2.  Delete _PARTIAL_EXP       routes.py     -133 lines
3.  Delete 4 dead routes      routes.py     -274 lines
4.  Delete config/JS dead     config,app.js  -23 lines
5.  Delete dead methods       feeds.py       -75 lines
6.  Delete aliases            compile.py     -28 lines
7.  Delete aliases            feature_dsl.py -21 lines
8.  Delete duplicate hedge    compile.py     -35 lines
9.  Extract validate_guard    feature_dsl.py -14 lines
10. Simplify config.py        config.py      -30 lines
    TOTAL                                    -1,008 lines
```

This removes ~4% of the codebase with zero behavioral change. The remaining 46% requires architectural decisions (which UI, which features to cut).
