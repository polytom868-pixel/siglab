# Path to <10K Lines — Complete Plan

**Current: 57 files, 19,587 lines. Target: <10,000 lines. Gap: 9,588 to cut.**

---

## Phase A: Delete Dead Code (NONE/LOW risk) — −2,529 lines

| Item | File | Lines | Risk | Agent Finding |
|------|------|-------|------|--------------|
| Delete entire file | `evaluation/runner_serialize.py` | 215 | NONE | Zero callers anywhere |
| Delete entire file | `evaluation/score.py` | 98 | NONE | Zero callers anywhere |
| Delete entire file | `evaluation/strategy_semantics.py` | 175 | NONE | Zero callers anywhere |
| Delete entire file | `evaluation/experiment_enricher.py` | 638 | LOW | DashboardState has complete inline duplicate |
| Delete class + dead routes | `dashboard/experiment_repo.py` (class only) | 194 | LOW | DashboardState has own load/store methods |
| Delete file | `live/reconciliation.py` | 71 | NONE | Zero imports in app code |
| Delete file | `live/promotion.py` | 298 | LOW | Zero imports in app code |
| Delete 4 dead `/templates/*` routes | `dashboard/routes.py` | 60 | LOW | Duplicates of create_app() routes |
| Delete `/partials/run/detail_panel` route | `dashboard/routes.py` | 55 | LOW | Dead — no frontend references |
| Delete `_detail_panel.html` | templates/ | 134 | LOW | Dead template |
| Delete `_cs()` function | `routes.py` | 20 | LOW | Defined but never called |
| Delete dead CLI files | `cli/evidence.py`, `cli/__init__.py` dispatch | 176 | LOW | CLI not needed for demo |
| Prune cli/sodex.py keep preflight only | `cli/sodex.py` | 220 | LOW | Keep only sodex-preflight |
| Prune cli/helpers.py unused | `cli/helpers.py` | 175 | LOW | Remove dead helper functions |
| **Total Phase A** | | **2,529** | **NONE/LOW** | **12.9% of current** |

## Phase B: Trim Near-Dead Code (LOW risk) — −2,010 lines

| Item | File | Lines | Detail |
|------|------|-------|--------|
| Gut `runner_analysis.py` (keep 15L) | `evaluation/runner_analysis.py` | 1,164 | 28 of 30 functions have only self-referential callers. Keep only `mean_pairwise_rolling_corr` |
| Trim `backtest.py` (keep 25L) | `evaluation/backtest.py` | 159 | Keep only `convert_to_spot`, `_cagr_safe` |
| Trim `signal_narrative.py` (delete) | `evaluation/signal_narrative.py` | 321 | Delete entirely — called from demo with empty dict |
| Remove HTMX dual-rendering path | `dashboard/routes.py` + 6 templates | 366 | JS already renders all data — HTMX partials are redundant |
| **Total Phase B** | | **2,010** | **LOW** | **10.3% of current** |

## Phase C: Structural Merge (MEDIUM risk) — −3,270 lines

| Item | File | Lines | Detail |
|------|------|-------|--------|
| Merge `live/sodex_client.py` → `data/feeds.py` | consolidation | 748 | `live/sodex_client.py` imports from `data/sodex_client.py`. Merge all SoDEX live into feed provider |
| Merge `data/sodex_feeds.py` → `data/feeds.py` | consolidation | 343 | Already tightly coupled. SoDEXFeeds is caching wrapper around SoDEXPublicPerpsClient |
| Merge `data/sodex_rate_limit.py` → `data/feeds.py` | consolidation | 140 | Weight scheduler used only by SoDEXPublicPerpsClient in feeds.py |
| Merge `live/exporter.py` → keep? | consolidation | 395 | If cloud-export not demo-essential, delete entire file |
| Consolidate `dashboard/risk_utils.py` → `utils.py` | move | 519 | Cyclic-import-safe merge into utils.py |
| Consolidate CLI into fewer files | `cli/` | 500 | Merge 11 files into 5-6 |
| Simplify `compile.py` dispatch loaders | `evaluation/compile.py` | 80 | Extract 9-line repeated loader block |
| Merge `data/sodex_client.py` → `feeds.py` | consolidation | 498 | `SoDEXPublicPerpsClient` already embedded in `MarketDataProvider` usage |
| **Total Phase C** | | **3,270** | **MEDIUM** | **16.7% of current** |

## Summary

| Phase | Lines Cut | Risk | Cumulative | Running Total |
|-------|-----------|------|------------|---------------|
| **A: Dead code delete** | −2,529 | NONE/LOW | 17,058 | 87.1% of current |
| **B: Near-dead trim** | −2,010 | LOW | 15,048 | 76.8% of current |
| **C: Structural merge** | −3,270 | MEDIUM | 11,778 | 60.1% of current |
| **Remaining gap** | **−1,778** | HIGH | **10,000** | **51.0% of current** |

**To reach <10K, need additionally:**
- Delete or deeply halve `feeds.py` (1,900L → 900L) = −1,000 lines
- Delete or deeply halve `paper_client.py` (1,490L → 700L) = −790 lines

**Verdict: <10K achievable but requires cutting SoDEX live trading entirely.** The remaining gap of 1,778 lines requires removing or halving the two largest files (feeds.py, paper_client.py) which contain the actual trading logic. If the project goal is a research dashboard (not live trading), this is feasible. If live SoDEX trading is required, ~13,000 is the realistic floor.
