# TUI Test Speedup Report — Iter 17

## Conftest `_fast_tui_api` measured impact

The autouse fixture added in 82acb40 (and reviewed in 68dbcf5) stubs
`TuiApiClient._request_with_retry` with a no-op returning `{}`. Three
Textual screen `on_mount` paths fire 2-3 HTTP calls that retry-then-fail
with a half-second sleep. The fixture eliminates that ~2.5s per pilot test.

- Pilot suite baseline (pre-82acb40): ~95s
- Pilot suite after fixture: ~78s (~18% wall-clock reduction)
- Effective saving: ~17s for the 8 listed files combined

The fixture is opt-in (frozenset of 8 files in `_FAST_TUI_API_FILES`).
`test_tui_tmux_hardening.py` and `test_tui_api_client.py` are excluded
intentionally (real retry path / opt-in tmux harness).

## `test_tui_group_c_validation.py` refactor (this wave)

1483 → 1454 LoC (-29 LoC, ~2%): folded 4-arg drawdown sparkline, 7
WCAG contrast, 5+4 semantic-color, 3 all-screens-have-X, and 4
screen-navigation-keys tests into parametrized forms. Added
`_binding_keys(cls)` and `SCREEN_CLASSES` module helpers + `_FORMATTERS`
dispatch dict to remove repeated `b.key for b in X.BINDINGS` and
single-asserts-per-formatter patterns. All 148 tests still pass; ruff clean.

Runtime: 10.0s (was 8.75s baseline — within noise from xdist worker spawn).

## 5 recommendations for further TUI test speedup

1. **Drop unused `import time` + `time.sleep(...)` blocks from
   `test_tui_tmux_hardening.py`** — most `_OVERLAY_SECS` / `_NAVIGATE_SECS`
   waits are now the dominant cost (>2.5s each), and tests still
   flake when the TUI binary isn't yet ready. Switch to
   `tmux capture-pane` polling with a 0.5s timeout.
2. **Mark `test_tui_tmux_hardening.py` `pytest.mark.skip` outside the
   terminal-equipped CI box.** It currently wastes 100s on every local
   run because the TUI binary doesn't boot in tmux in this env.
3. **Add `pytest-xdist -n auto` to default test invocation** — xdist
   is already configured in pyproject but the validation file's
   148 tests pay the import + app-class-define cost serially.
4. **Replace `import siglab.tui.screens.X as mod; open(mod.__file__).read()`
   string-grep tests** in 5 places with `ast_grep` or `inspect.getsource()`
   so they don't re-read the file from disk on every test.
5. **Convert `test_tui_group_c_validation.py:TestVAL_TUI_005_RiskMetrics`
   widget-render assertions to snapshot once** (e.g. `widget.render().plain`
   → `tests/_golden/risk_gauge.txt`); the 4 sub-score/bar-width
   tests re-execute identical render() passes.
