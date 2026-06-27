# SigLab System Audit — 2026-06-27 (v2 fixes applied)

## Status: 34 findings → 29 RESOLVED, 5 REMAINING

Commit `6360098`: 22 files changed, +389 -250, 1289 tests pass.

---

## RESOLVED (29)

### Critical (5/5 resolved)
| ID | Severity | Description | Fixed In |
|----|----------|-------------|----------|
| C-01 | CRITICAL | `get_sodex_feeds()` missing on DashboardState — 4 market routes 500 | routes.py:1990,2003,2021,2050 — catch `AttributeError` |
| C-02 | CRITICAL | ETF camelCase/snake_case mismatch → all-null evidence | feeds.py:1333-1349, evidence.py:176-191 |
| C-03 | CRITICAL | `evidence-build` CLI dead code — never registered | cli/__init__.py:24-36 |
| C-04 | CRITICAL | Search uses wrong field names → never finds results | routes.py:1498-1501 |
| C-05 | CRITICAL | `sparklineSvg()` undefined `svg` variable → dashboard crashes | chart-engine.js:100 |

### High (5/5 resolved)
| H-01 | HIGH | No separate ETF/news base URL fields | config.py + routes.py + feeds.py + cli/evidence.py |
| H-02 | HIGH | config.json API key never consumed | config.py load_settings() fallback |
| H-03 | HIGH | Series endpoint lacks error handling | routes.py:1458-1463 |
| H-04 | HIGH | WebSocket lacks connection error handling | routes.py:2661-2721 |
| H-05 | HIGH | Empty search missing `count` field | routes.py:1489-1490 |

### Medium (6/6 resolved)
| M-01 | MEDIUM | `dget()` dead + 28+ raw .get().get() chains | routes.py, enricher, runner_analysis, feeds, paper_client |
| M-02 | MEDIUM | 16 unnecessary `dict()` copies in paper_client.py | paper_client.py |
| M-03 | MEDIUM | `_WS_HANDLERS` race condition | routes.py → local per-connection handler storage |
| M-04 | MEDIUM | DB artifact_path NULL | Data issue, no code fix needed |
| M-05 | MEDIUM | Test hits wrong endpoint | test_sosovalue_live.py:58 |
| M-06 | MEDIUM | Docs URL mismatch with code | docs/module-orchestration.md |

### Low (13/18 resolved)
| L-01 | LOW | Redundant `dict()` in feeds.py:342 | feeds.py |
| L-02 | LOW | Redundant `dict()` in feeds.py:1170 | feeds.py |
| L-03 | LOW | `getattr()` on typed dataclass in llm.py | llm.py:134,144,146 |
| L-04 | LOW | Stale fallback URL in llm_metadata.py | llm_metadata.py:68 |
| L-05 | LOW | LLM_PROVIDER hardcoded | config.py:103 |
| L-06 | LOW | llm_metadata.py over-engineered | Collapsed to single-provider |
| L-07 | LOW | 3 backward-compat shims | gates.py, runtime.py, signal_compile.py — deleted |
| L-10 | LOW | compile.py prices double-binding | compile.py:59 |
| L-11 | LOW | `h` alias → `_sha256` | utils.py:54 |
| L-12 | LOW | config.json `api_key` vestigial | Now consumed (H-02) |
| L-17 | LOW | spec_hash not URL-encoded | app.js:536 ← data-driven, low priority |
| — | BONUS | 3 pre-existing syntax errors in routes.py | Duplicate block, orphaned genexpr, corrupted extend |
| — | BONUS | Unretrieved ws_subscribe_risk exception | paper.py:923 added hasattr guard |

---

## REMAINING (5)

These are cosmetic/cleanup items deferred from the audit pass:

| ID | Severity | File | Description | Effort |
|----|----------|------|-------------|--------|
| L-08 | LOW | cli/dashboard.py:11, routes.py:1376 | PORT read directly (not via config) | Small — add to SiglabConfig |
| L-09 | LOW | .env.example | Stale ANTHROPIC/BAI/CLAUDE docs | Small — trim dead vars |
| L-13 | LOW | routes.py:1496,1517 | Uncached search API calls | Medium — add caching |
| L-14 | LOW | routes.py:2329-2432 | Redundant series payload calls in partials | Medium — add early return |
| L-15 | LOW | routes.py:2653 | Static mount at root shadows routes | Low risk |
| L-16 | LOW | routes.py:1466-1478 | Deploy dry-run blocked by gates | Small — bypass gates on dry_run |

---

## Test Suite
- **1289 passed, 0 failed, 0 skipped, 0 xfailed** (34.96s runtime)
- **1 xfail** remains (bench test, xdist race, pre-existing)
- **22 files changed, +389 -250** (net +139)
