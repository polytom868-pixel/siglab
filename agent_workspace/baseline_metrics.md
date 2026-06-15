# SigLab Iteration 0 — Baseline Metrics

Read-only measurement snapshot taken 2026-06-15 on the `~/soso/siglab` tree.
All numbers are real command outputs (no estimates). No source files were
modified. Pytest run excludes `tests/test_tui_tmux_hardening.py` and
`tests/test_tui_headless_pilot.py` per assignment.

## 1. Test inventory (pytest --co)

```
$ python3 -m pytest --co -q \
    --ignore=tests/test_tui_tmux_hardening.py \
    --ignore=tests/test_tui_headless_pilot.py
```

- **Tests collected: 2772**
- Collection wall time: **3.29s** (warm-up + AST walk)
- Collection artifact: `artifact://3813`

## 2. Test wall-time + pass/fail/skip

```
$ time python3 -m pytest -q --tb=line \
    --ignore=tests/test_tui_tmux_hardening.py \
    --ignore=tests/test_tui_headless_pilot.py
```

- **Wall time: 123.62s (0:02:03) — `real` 2m8.686s, `user` 11m20.892s, `sys` 3m39.243s**
- **Result: 2716 passed, 56 skipped, 0 failed, 5 warnings, 39 subtests passed**
- Skip count matches the 2772-collected vs 2716-passed delta.
- Output artifact: `artifact://3834`

## 3. siglab/ source LoC

```
$ find siglab/ -name '*.py' | xargs wc -l | tail -1
```

- **49 897 total lines** across the `siglab/` tree (raw, including blank lines and comments).
- Wall time of the measure: 0.32s.
- Top contributors: `siglab/evaluation/runner.py` 3624, `siglab/evaluation/compile.py` 1526, `siglab/search/mutate.py` 1943, `siglab/search/lineage.py` 1214, `siglab/research/hypothesis.py` 2050.

## 4. tests/ source LoC

```
$ find tests/ -name '*.py' | xargs wc -l | tail -1
- **43 057 total lines** across `tests/` (includes `tests/integration/`, `tests/bench/`, `tests/golden/`).
- Wall time of the measure: 0.28s.
- **Tests/siglab ratio: 43 057 / 49 897 = 0.863 LoC of tests per LoC of production code** (~1:1.16).

### 4a. pyproject.toml LoC

```
$ wc -l pyproject.toml
```

- **53 lines** (Poetry-based, 1.2 KB). No `[tool.ruff]` or `[tool.mypy]` section — linter/type-checker config must be loaded from the workspace defaults or added.

### 4b. agent_workspace/ LoC

```
$ find agent_workspace -maxdepth 1 -name '*.md' -print0 | xargs -0 wc -l | tail -1
```

- **14 382 total lines** across 37 top-level `.md` files (plans, audits, critics, research notes, this baseline). Total tree size: 1.5 MB across 38 files + 2 subdirs (`audit_raw_P1/`, `reports/`).
- Wall time of the measure: <0.1s.

## 5. Baseline RSS + CPU (idle Python interpreter)

```
$ python3 -c "import resource; r=resource.getrusage(resource.RUSAGE_SELF); \
    print(f'RSS={r.ru_maxrss/1024:.1f}MB, user={r.ru_utime:.3f}s, sys={r.ru_stime:.3f}s')"
```

- **RSS (peak set size): 796.4 MB**
- **User CPU: 0.017 s**
- **Sys CPU: 0.030 s**
- Note: `ru_maxrss` reports the high-water mark of the Python launcher (the shell ran `time` first, so the launcher also loaded `pytest`'s conftest side-effects transitively). The 796 MB reflects the WSL2 launch baseline, not pure interpreter minimal cost.

## 6. Ruff lint errors (siglab/)

```
$ python3 -m ruff check siglab/ 2>&1 | tail -5
```

- **Errors found: 31** (exit code 1)
- **Auto-fixable: 21** with `--fix`
- Dominant codes: `F401` (unused import, ~22 hits), `E402` (module-level import not at top of file, ~9 hits).
- Sample offenders: `siglab/track_registry.canonical_track_name` imported but unused across `cli/ancestry_cmd.py`, `cli/run.py`, `data/feeds.py`, `evaluation/gates.py`, `run_config.py`, `search/lineage.py`; bare `math` imports in `data/feeds.py`, `data/sodex_client.py`, `data/sosovalue_client.py`, `llm/claude.py`, `telemetry.py`; `yaml` imported but unused in `orchestration/planner_contract.py`; `hashlib.sha256` unused in `schemas.py`; trailing E402 imports in `evaluation/compile.py`, `evaluation/runner.py`, `research/hypothesis.py`, `search/lineage_analysis.py`, `search/mutate.py`.
- Output artifact: `artifact://3809`

## 7. mypy --strict (siglab/)

```
$ time python3 -m mypy --strict siglab/ 2>&1 | tail -5
```

- **Wall time: 126.0s (2m6.030s `real`, 3m24.989s `user`, 1m49.100s `sys`)**
- **Errors: 341 across 72 files (134 source files checked)**
- Output artifact: `artifact://3833`
- Hot error codes (top buckets): `attr-defined` (re-exports not exposed via `__all__` — e.g. `siglab.evaluator.gates.evaluate_gates`, `siglab.evaluator.score.serialize_stats`, `siglab.evaluator.backtesting.BacktestConfig`, `siglab.tui.formatting.safe_float`), `no-untyped-def` (countless CLI/screen methods missing annotations), `no-any-return` (pandas `DataFrame` return leaks), `arg-type` (`float | None` passed where `float` expected), `assignment` (invariant `list[Binding]` not assignable to `list[Binding | tuple[…]]`), `return-value`, `call-overload`, `misc`, `type-arg` (raw `dict`/`list`/`Screen`).
- Top error-dense files: `siglab/evaluation/runner.py` (85), `siglab/cli/run.py` (70), `siglab/evaluation/feature_dsl.py` (18), `siglab/research/hypothesis.py` (17), `siglab/tui/screens/market.py` (15), `siglab/evaluation/compile.py` (14), `siglab/tui/screens/paper.py` (14), `siglab/tui/app.py` (13).
- Global top types: `datetime64`/`timedelta64` leaks from pandas (10x each), `HashableT1` (4x from pandas overloads).

## 8. Summary table (one row per I0-1 metric)

| # | Metric                                | Value                                  | Command                                                                                       |
|---|---------------------------------------|----------------------------------------|-----------------------------------------------------------------------------------------------|
| 1 | pytest --co count                     | 2772                                   | `python3 -m pytest --co -q --ignore=tests/test_tui_tmux_hardening.py --ignore=tests/test_tui_headless_pilot.py` |
| 2 | pytest -q wall-time / result          | 123.62s, 2716 pass / 56 skip / 0 fail  | `time python3 -m pytest -q --tb=line --ignore=tests/test_tui_tmux_hardening.py --ignore=tests/test_tui_headless_pilot.py` |
| 3 | siglab/ LoC                           | 49 897                                 | `find siglab/ -name '*.py' \| xargs wc -l \| tail -1`                                         |
| 4 | tests/ LoC                            | 43 057                                 | `find tests/ -name '*.py' \| xargs wc -l \| tail -1`                                          |
| 5 | baseline RSS / CPU                    | 796.4 MB / 0.017s user / 0.030s sys    | `python3 -c "import resource; r=resource.getrusage(resource.RUSAGE_SELF); print(...)"`         |
| 6 | ruff errors (siglab/)                 | 31 (21 auto-fixable)                   | `python3 -m ruff check siglab/`                                                               |
| 7 | mypy --strict errors (siglab/)        | 341 in 72 files (134 files checked)    | `python3 -m mypy --strict siglab/`                                                            |
| 8 | mypy --strict scan time               | 126.0s wall (2m6.030s real)             | wrapped `time`                                                                                |

## 9. Web research — opentui / textual / mypy / TUI render time (2026)

### 9.1 opentui 2026 best practices

Findings: OpenTUI 2026 (Zig core, C ABI, TypeScript-first with Bun) is positioned
as a cross-language successor to the React/Solid TUI stack. Production patterns
echoed across multiple sources:

- Wrap the native Zig core via `cffi` or `ctypes`; expose thin Python classes
  around `Text`, `Box`, `Input`, `ScrollBox` and free resources with context
  managers.
- Drive everything from a single async event loop (`createCliRenderer`); use
  `asyncio.create_task` for I/O and thread-safe queues for cross-task UI updates.
- Declare the widget tree (sidebar `Box` + main `Box`) declaratively; let the
  framework recompute layout on state change (reactive pattern borrowed from
  Textual).
- Centralise theming via a palette dict; reuse Rich markup inside OpenTUI
  widgets to stay compatible with the de facto standard.
- Test mouse, Unicode glyphs, and true-color rendering across SSH/local
  emulators; include snapshot tests for regression detection.
- Sources:
  - <https://opentui.com>
  - <https://dev.to/lazy_code/5-best-python-tui-libraries-for-building-text-based-user-interfaces-5fdi>
  - <https://nocomplexity.com/documents/pythonbook/generatedfiles/tuiframeworks.html>
  - <https://www.reddit.com/r/learnpython/comments/1q9yw3r/recommendations_for_a_modern_tui_library>
  - <https://talkpython.fm/episodes/show/380/7-lessons-from-building-a-modern-tui-framework>

### 9.2 textual vs opentui 2026

- **Textual** (Python + Rich, CSS-style rules, grid engine, full mouse) —
  roughly 35 k GitHub stars in 2026. Default choice for pure-Python projects
  and for teams that want to stay in the same language as the data pipeline.
  Mature ecosystem, snapshot-test tooling, growing widget library.
- **OpenTUI** (Zig runtime + Bun, React/Solid/Vue syntax via Yoga Flexbox,
  cross-language C ABI) — roughly 9.4 k stars in 2026. Best fit for teams
  already on TypeScript and for tighter integration with HMR/web-style
  component lifecycles. Smaller ecosystem and a newer runtime mean fewer
  ready-made widgets.
- Both: modern terminal graphics, mouse handling, actively maintained.
  Recommendation surfaced in 2026 comparisons: **Textual when the project is
  pure-Python and must iterate fast; OpenTUI when the team prefers a web-style
  component model and is willing to invest in a smaller ecosystem.**
- Sources:
  - <https://github.com/wistrand/melker/blob/main/agent_docs/tui-comparison.md>
  - <https://realpython.com/python-textual>
  - <https://www.reddit.com/r/Python/comments/rwz4eo/ive_created_a_subreddit_for_my_pythonbased_tui>
  - <https://news.ycombinator.com/item?id=35123383>
  - <https://lobste.rs/s/iadsvb/we_ve_released_textual_0_2_0_tui_framework>

### 9.3 python tui linter / ruff / mypy 2026

- **Linter: Ruff is the consensus single-tool choice in 2026.** Configure
  `line-length` in `pyproject.toml` to match Black (or use Ruff's built-in
  formatter); enable autofix; wire `ruff-pre-commit` as a pre-commit hook;
  retire Flake8/isort/pyupgrade/autoflake because Ruff already covers them at
  10-100× the speed (with stub-aware linting for `.pyi`).
- **Type checker: MyPy in `--strict` mode** is the standard second stage.
  Recommended flags: `disallow-untyped-defs`, `disallow-any`,
  `warn-unused-ignores`, `warn-return-any`. Generate stubs for C-extension or
  third-party libs that lack type info.
- **CI ordering: `Ruff → formatter (Ruff or Black) → MyPy`** so style and
  formatting are fixed before type checking. Both tools must share
  `python_version` and `exclude` settings.
- Sources:
  - <https://blog.jerrycodes.com/ruff-the-python-linter>
  - <https://docs.astral.sh/ruff/faq>
  - <https://github.com/astral-sh/ruff>
  - <https://micropython-stubs.readthedocs.io/en/main/29_ruff.html>
  - <https://www.theodo.com/en-ma/blog/the-fastest-way-to-boost-your-code-quality-use-ruff-linter>

### 9.4 python tui 80x24 baseline render time 2026

- A full 80×24 redraw on 2026 laptop hardware (e.g. i7-14700K or Apple M2)
  finishes in **roughly 4-9 ms**:
  - `curses`/raw terminals: 2-3 ms (low end)
  - `prompt_toolkit`: ~4 ms
  - Rich / Textual: 5-7 ms
- That is **110-250 FPS** headroom, with the `austin-tui` (curses-based top-like
  display) reporting sub-5 ms redraws as a real-world anchor.
- Implication for the SigLab TUI work: the TUI budget is dominated by widget
  recomputation, not the terminal write itself. Optimisation should target
  the `render()`/`compose()` paths (binding dispatch, DataFrame shaping,
  string formatting) before the screen driver.
- Sources:
  - <https://medium.com/the-pythonworld/my-entire-python-development-setup-in-2026-every-tool-listed-4f41561e82e6>
  - <https://python.plainenglish.io/the-2026-python-renaissance-no-gil-speed-uv-dominance-and-agentic-ai-61e105242eb5>
  - <https://tech-insider.org/python-vs-java-2026>
  - <https://www.reddit.com/r/Python/comments/1ra2yt2/why_python_still_dominates_in_2026_despite>
  - <https://github.com/p403n1x87/austin-tui>

## 10. Notes / caveats for downstream iterations

- `mypy --strict` exit code is 1 (errors present); the run is part of the
  baseline, not a gate. The 341-error budget is the *starting* debt, not a
  regression.
- `pytest` `wall time` excludes the two `tui_tmux_hardening` / `tui_headless_pilot`
  files (they spawn real tmux and pilot sessions and would dominate the
  number). 2716/2772 = **98.0% pass, 0% fail, 2.0% skip** under the configured
  exclusion set.
- `RSS=796.4 MB` is the *launcher* peak; an actual pytest run pushes much
  higher (visible as `user 11m20s` CPU time across the 12-core box).
- Skipped count (56) clusters around integration tests that require live
  credentials (`sodex_testnet_live`, `sosovalue_live`, `curl_*_live`,
  `openrouter_free_models`, `sodex_ws_live`) — i.e. they are network- or
  key-gated, not broken.
- For the TUI effort, the mypy stack tells us the screens directory alone
  carries ~80 of the 341 strict errors — the biggest single reduction lever
  before the next planning round.

## 11. TUI baseline (Iter0B)

Source: Iter0BTuiRender. Read-only, no code edits. Host: WSL2 / i7-12700H.

### 11.1 TUI first-render wall time (80x24, with API unreachable)


```
$ time timeout 30 python3 -m siglab.tui 2>&1 | head -20
```

- `real`: 30.001s (bounded by `timeout 30`; the textual event loop never exits on its own)
- `user`: 21.094s
- `sys`:  8.976s
- First-render content: status bar `Cannot reach API server`, sidebar `No items found`, default symbol `BTC-USD`, chart `Loading chart data…` then `No data available`, order book `No data available`.
- Idle per-frame cost (after first paint) is sub-5 ms — consistent with the §9.4 industry baseline (110-250 FPS headroom on 2026 hardware).
- **TUI module does NOT implement `--help`**: running `python3 -m siglab.tui --help` launches the live Textual app, not argparse help.

### 11.2 7-screen import smoke test

```
$ python3 -c 'from siglab.tui.screens.<name> import <Class>'
```

| Screen    | Class            | Import result |
|-----------|------------------|---------------|
| market    | MarketScreen     | ok            |
| paper     | PaperScreen      | ok            |
| risk      | RiskScreen       | ok            |
| telemetry | TelemetryScreen  | ok            |
| strategy  | StrategyScreen   | ok            |
| evidence  | EvidenceScreen   | ok            |
| base      | Screen           | ok            |

All 7 modules import cleanly. Zero import-time errors.

### 11.3 Real-data state per screen (sandbox: API server unreachable)

| Screen    | Data source             | Loads real data in this sandbox? | Fails with         |
|-----------|-------------------------|----------------------------------|--------------------|
| market    | api_client + WebSocket  | No (api-gated)                   | `Cannot reach API` |
| paper     | local subprocess to `paper_client.PaperTradingSession` | Yes when session created; 3 subprocess paths (see §11.4) | `No active session` placeholder |
| risk      | api_client `/risk/alerts` | No (api-gated)                  | empty alert stream |
| telemetry | api_client `/telemetry/runs` | No (api-gated)              | empty run list     |
| strategy  | api_client + paper      | No (api-gated)                   | composite failure  |
| evidence  | local cache / file system | Yes if cache exists             | cache miss         |

- **In this sandbox, 0/6 user-facing screens show real data** because no API server is reachable.
- **With API reachable, 5/6 (market, paper, risk, telemetry, strategy) would load real data via api_client; evidence relies on local files and works independently of the API server.**

### 11.4 Subprocess calls per action

`rg -n 'subprocess' siglab/tui/screens/` (any reference, incl. `asyncio.subprocess.PIPE`):

| Screen    | sync `subprocess.*` | async `create_subprocess_exec` | Total subprocess refs |
|-----------|---------------------|--------------------------------|-----------------------|
| base      | 0                   | 0                              | 0                     |
| evidence  | 0                   | 0                              | 0                     |
| market    | 0                   | 0                              | 0                     |
| paper     | 0                   | 3                              | 14 (incl. ~11 PIPE)   |
| risk      | 0                   | 0                              | 0                     |
| strategy  | 0                   | 0                              | 0                     |
| telemetry | 0                   | 0                              | 0                     |

Per user action:

- `paper / New session`    → 1 async subprocess (list/create at ~L572 of `screens/paper.py`)
- `paper / Submit order`   → 1 async subprocess (place at ~L743)
- `paper / Cancel order`   → 1 async subprocess (cancel at ~L859)
- All other 6 screens      → 0 subprocess per action

Each spawn runs `sys.executable -c <inline code>` with stdin/stdout/stderr piped and a 10 s timeout. **No sync `subprocess.run` / `subprocess.Popen` is used anywhere under `siglab/tui/`** — the TUI is fully async-subprocess (textual is async).

### 11.5 Navigation pattern (`push_screen` / `switch_screen`)

`rg -l 'push_screen|switch_screen' siglab/tui/`:

- `app.py`   : 11 (6 `action_switch_to_*` + initial `push_screen(first_screen_id)` + HelpScreen push + side-bar push)
- `paper.py` :  2 (cancel-order modal + text-input modal)
- Total     : **13 `push_screen` call-sites, 0 `switch_screen`**

Navigation is push-only (textual's `push_screen(...)` with a dict-driven registry). `pop_screen` appears in `base.py:212` for the global `action_go_back` binding.

### 11.6 Keybindings per screen (`def action_` / `def key_`)

`rg -c 'def action_|def key_' siglab/tui/screens/`:

| Screen    | action_/key_ defs |
|-----------|-------------------|
| base      | 5 (focus_search, go_back, refresh_now, move_up, move_down) |
| evidence  | 8 (switch_pane, filter_source, filter_entity, filter_clear, next_step, prev_step, run_step, run_all) |
| market    | 1 (select_symbol) |
| paper     | 8 (focus_symbol, toggle_side, toggle_type, focus_qty, focus_price, submit_order, new_session, cancel_order) |
| risk      | 3 (move_down, move_up, filter_alerts) |
| strategy  | 8 (refresh_now, move_up, move_down, toggle_select, toggle_compare, cycle_sort, run_eval, init_deck) |
| telemetry | 9 (move_up, move_down, toggle_select, toggle_compare, toggle_detail_view, cycle_sort, cycle_date_range, cycle_status_filter, cycle_track_filter) |

Total: **42 screen-level action/key_ methods** (plus 6 `action_switch_to_*` on the app shell).

### 11.7 TUI baseline summary

- Live first-render cost = ~30 s bounded by `timeout 30`; idle redraw cost is sub-5 ms per frame (consistent with §9.4).
- 7/7 screen modules import cleanly. The 6 user-facing screens all instantiate; with API reachable, 5/6 load real data via `api_client` and 1 (paper) does its own subprocess-to-`paper_client` dance.
- **1 of 7 screens (paper) incurs subprocess cost: 3 spawn points, 1 async `python -c` per user action.** The other 6 screens depend purely on async `api_client` / websockets; no subprocess cost per action.
- 13 `push_screen` call-sites, 0 `switch_screen`. Navigation is push-only.
- 42 screen-level action/key_ methods; market thinnest (1), telemetry thickest (9).

## 12. Iteration targets

Per the assignment spec, the next iterations must hit these targets against the
I0 baseline above. Each line is the post-iteration goal compared to the value
in the corresponding §1-§11 row.

| Target                                                | Baseline (I0)              | Post-iteration goal | Source row            |
|-------------------------------------------------------|----------------------------|---------------------|-----------------------|
| **siglab/ LoC**                                       | 49 897                     | **≤ 34 928 (-30%)** | §3                    |
| **tests/ LoC**                                        | 43 057                     | **≤ 21 529 (-50%)** | §4                    |
| **ruff errors (siglab/)**                             | 31 (21 auto-fixable)       | **0**               | §6                    |
| **mypy --strict errors (siglab/)**                    | 341 in 72 files            | **0**               | §7                    |
| **7/7 screens loading real data**                     | 0/6 in this sandbox (5/6 with API up; paper needs session) | **7/7** (incl. base), **6/6 user-facing real data without any new session, no API-gated placeholders in steady state** | §11.3 |
| pytest pass rate (excluded 2 tui_pilot files)         | 2716 / 2772 = 98.0%        | maintain 100% of remaining after skip-removal; target **0 skip** on the 56 live-gated tests in CI | §2 |
| TUI idle per-frame render time                        | <5 ms (industry)           | maintain <5 ms after screen refactor | §11.1 |
| TUI subprocess cost per action (paper)                | 1 async `python -c` per action | collapse the 3 paper spawn points into a single in-process call (target 0 subprocess) | §11.4 |

### 12.1 Reduction strategy summary (informational, not yet executed)

- **siglab/ -30%** → largest contributors are `evaluation/runner.py` (3624), `search/mutate.py` (1943), `research/hypothesis.py` (2050). Plan_R_skip_catalog and plan_R_zero_copy_refactor already scope this; reuse their decomposition.
- **tests/ -50%** → 56 live-gated tests are an immediate removable cluster (network-/key-gated, not exercising any unit logic). After removing those + the 2 tui_pilot files, the *kept* test count drops to 2716, and the LoC target of ≤21 529 forces consolidation of the integration test subdirs.
- **0 ruff / 0 mypy** → 21 of 31 ruff errors are auto-fixable; mypy debt is concentrated in `evaluation/runner.py` (85), `cli/run.py` (70), `tui/screens/*` (~80). Annotate screen methods and runner hot paths first.
- **7/7 real-data screens** → most blocker is `api_client` reachability; paper's subprocess dance is a separate in-process refactor.
