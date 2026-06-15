# SigLab TUI Testing Best Practices (research)

**Top 5 patterns to adopt**
1. **Skip `app.run_test()` for pure-function tests.** Screen.parse / format / transform should be tested by instantiating the screen, calling `compose()` (or the method) directly, and asserting. `run_test()` boots the full event loop, mounts the whole widget tree, and is the dominant cost.
2. **Call `action_*` and `on_*` directly; reserve `pilot.press`/`pilot.click` for wiring tests.** For an action test, `await screen.action_xxx()` runs the method body; for a binding test, `await pilot.press(key)` runs the full pipeline. Pilot.click "bypasses the normal event processing in `App.on_event`" — fine for clicks, but a poor signal of end-to-end binding flow.
3. **Use `pilot.pause()` only when state is async, never prophylactically.** `pause(delay=None)` waits for CPU idle; calling it after every press in `tests/test_tui_validation_contract.py` lines 141, 150-153, 509-543 is the single biggest LoC + time drain. Drop them unless the next assert is racing a `post_message` / `run_worker`.
4. **One `app = SigLabTUI()` per test, not per class.** Each `run_test()` context manager spins up a new headless terminal; reusing it across tests is impossible because the context manager closes it. Extract a small async helper (`async def booted(app): ...`) and parameterize with `pytest.mark.parametrize` over (input, expected_state) to collapse near-duplicate cases.
5. **Snapshot `query_one(...)` results, not `app.export_screenshot()`.** For state assertions `pilot.app.query_one("#status-bar", Static).renderable` is 10–100× cheaper than SVG diffs. Reserve `pytest-textual-snapshot` (SVG `snap_compare`) for *visual regression* of two or three key screens only.

**Top 3 anti-patterns to avoid**
- `pilot.press(*[c for c in "long string"])` — each char is a full event round-trip; typing 19 chars took 15s in Textual's own perf report. `input.value = "..."` is the test path, not the user path.
- Mocking the whole `App` subclass to "avoid booting" — defeats the point. Either boot it (true coverage) or call the method on a bare `BaseScreen()` (true speed). Mocking belongs at the `TuiApiClient` boundary only.
- `await pilot.wait_for_animation()` — animations don't run in headless mode; this is a no-op that costs a wait. See the official guide: "wait for all pending messages to be processed" via `pilot.pause()`, never `wait_for_animation()`.

**Specific code examples (apply directly)**
- Replace `async with SigLabTUI().run_test() as pilot: ... await pilot.press("1"); await pilot.pause()` with: `app = SigLabTUI(); async with app.run_test() as pilot: await pilot.press("1"); assert app.query_one(Static).renderable == "..."`. (Drop the `pause()`.)
- For pure action logic: `app = SigLabTUI(); screen = SomeScreen(); screen.action_refresh_now(); assert screen.is_loading is True` — no `run_test()` at all.
- For Input-free key sequences: `await pilot.press("j", "k", "escape")` in one call (Pilot accepts varargs) instead of three separate `press + pause` pairs.
- Parametrize: `@pytest.mark.parametrize("key,expected_id", [("1", "market"), ("2", "paper"), ...])` to fold 6 near-identical tests into one.

**Performance optimization tricks**
- `asyncio_mode = auto` in `pyproject.toml` is already implied; verify it's set so `@pytest.mark.asyncio` doesn't repeat. The SigLab repo already has pyproject settings — confirm `pytest-asyncio` mode.
- Run the 14 `test_tui_*.py` files under `pytest-xdist -n auto` — Textual's own maintainer notes 3000+ tests run in 22s with xdist, vs minutes serial.
- `app.run_test(size=(80, 24))` — defaults are fine, but passing `size` is cheap insurance against layout flapping in CI.
- For DataTable-heavy screens (risk/strategy/telemetry), assert on `dt.row_count` and `dt.get_row_at(idx)` rather than rebuilding and stringifying the whole grid.
- `pilot.app.query_one(selector, Type)` raises fast on miss — use it to assert presence (`query_one` succeeds) and absence (`app.query(sel)` returns `[]`).

**Coverage strategies**
- **State coverage** (cheap, fast): boot once, mutate, assert reactive + widget renderable. This is the 80%.
- **Wiring coverage** (medium): one test per `BINDINGS` key verifies the action fires; pilot.press suffices.
- **Visual coverage** (expensive, rare): 1–2 `snap_compare` tests per screen for the SVG; CI fails on accidental CSS drift. Skip for the 11 other test files.
- **Pure-logic coverage** (cheapest): `BaseScreen._format_status(payload)` etc. should have zero-pilot tests against the method, not the screen.

**Sources**
- [Textual Testing guide](https://textual.textualize.io/guide/testing)
- [textual.pilot API](https://textual.textualize.io/api/pilot)
- [Textualize/textual discussion #5068 — Input widget perf, xdist recommendation](https://github.com/Textualize/textual/discussions/5068)
- [pytest-textual-snapshot plugin](https://github.com/Textualize/pytest-textual-snapshot)
