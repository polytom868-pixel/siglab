# SigLab Terminal-Test Tooling Research

Read-only research for clearing the 17 timeouts and making the suite more functional.
Internal refs use `file:line` from `~/soso/siglab`. Word count target: ≤ 2500.

---

## 1. Textual `App.run_test()` and `Pilot`

**Sources**
- Textual testing guide — https://textual.textualize.io/guide/testing
- `textual.pilot` API — https://textual.textualize.io/api/pilot
- "A year of building for the terminal" blog — https://textual.textualize.io/blog/2022/12/20/a-year-of-building-for-the-terminal

**Quote (testing guide, "Testing apps")**

> "To test our simple app we will use the `run_test()` method on the `App` class. This replaces the usual call to `run()` and will run the app in *headless* mode, which prevents Textual from updating the terminal but otherwise behaves as normal. The `run_test()` method is an *async context manager* which returns a `Pilot` object."

`Pilot` exposes `press`, `click`, `hover`, `mouse_down`/`up`, `double_click`, `triple_click`, `resize_terminal`, `pause(delay=None)` (None = wait for cpu idle), plus `wait_for_*`. `Pilot.app` returns the live `App`, so `pilot.app.query_one(...)` and `pilot.app.query(...)` assert on the real widget tree.

**Quote (testing guide, "Pausing the pilot")**

> "Some actions in a Textual app won't change the state immediately... You can generally solve this by calling `pause()` which will wait for all pending messages to be processed."

`run_test()` is in-process and headless: it cannot catch real-TTY bugs (alt-screen, SIGWINCH, OSC, tmux passthrough) and cannot exercise the `python -m siglab.tui` subprocess that the tmux suite drives. Its strength is being fast, deterministic, and assertion-rich for the widget tree.

**Implication for SigLab**

The 17 timeouts live in `tests/test_tui_tmux_hardening.py`, where each test boots a real subprocess and waits `_SETTLE_SECS = 4.0` (line 37), `_NAVIGATE_SECS = 2.5` (line 40), `_RESIZE_SECS = 2.0` (line 42), `_OVERLAY_SECS = 1.5` (line 43). Routing widget-tree assertions through `run_test()` drops wall time from seconds to milliseconds and turns "after `r`, the table has N+1 rows" into `pilot.app.query_one(DataTable).row_count`. The microchange-wave Lesson 1 proof at `microchange-wave/SKILL.md:22-32` is satisfied by `pytest tests/test_tui_pilot.py::TestWidgetTree::test_refresh_updates_row_count -x --timeout=10` — under 5 min, dev deps already at `pyproject.toml:45-51`.

---

## 2. libtmux: waiting for a regex in a pane

**Sources**
- libtmux automation patterns — https://github.com/tmux-python/libtmux/blob/master/docs/topics/automation_patterns.md
- libtmux Pane API — https://libtmux.git-pull.com/api/panes
- libtmux repo — https://github.com/tmux-python/libtmux

**Quote (automation patterns, "Waiting for specific output")**

The upstream docs ship a canonical helper:

```python
def wait_for_output(pane, text, timeout=5.0, poll_interval=0.1):
    start = time.time()
    while time.time() - start < timeout:
        output = '\n'.join(pane.capture_pane())
        if text in output:
            return True
        time.sleep(poll_interval)
    return False
```

This is **synchronous and blocking**. There is no native `await pane.wait_for_content(pattern)` primitive on `Server` or `Pane` — libtmux is a thin sync wrapper around tmux commands. For regex, lift the body to `re.compile(pattern).search('\n'.join(pane.capture_pane()))`; for async use, run the helper in `await asyncio.to_thread(wait_for_output, pane, ...)` so the event loop stays free.

**Implication for SigLab**

Two wins. First, replace the bare `time.sleep(_SETTLE_SECS)` in `tests/test_tui_tmux_hardening.py:37-43` with a tight poll for the deterministic keyword in `_SCREEN_KEYWORDS` (line 46). That converts fixed 4 s waits into wait-until-ready semantics and clears the timeouts deterministically. Second, for any new async-aware code path, write `async def wait_for(pane, pattern, timeout)` that calls the sync helper in `asyncio.to_thread`. Both compose with the existing `pane.capture_pane` calls in the harness and with `--dist=loadfile` from §3.

---

## 3. pytest-xdist + tmux: per-worker tmux server pattern

**Sources**
- pytest-xdist how-to — https://pytest-xdist.readthedocs.io/en/stable/how-to.html
- pytest-xdist 2.4.0 — https://pypi.org/project/pytest-xdist/2.4.0
- Issue #783 — https://github.com/pytest-dev/pytest-xdist/issues/783

**Quote (xdist how-to, "Identifying the worker process")**

> "If you need to determine the identity of a worker process in a test or fixture, you may use the `worker_id` fixture to do so… When `xdist` is disabled (running with `-n0` for example), then `worker_id` will return `"master"`. Worker processes also have the following environment variables defined: `PYTEST_XDIST_WORKER` — e.g., `"gw2"`. `PYTEST_XDIST_WORKER_COUNT` — e.g., `"4"` when `-n 4` is given."

**Quote (xdist how-to, "Making session-scoped fixtures execute only once")**

> "`pytest-xdist` is designed so that each worker process will perform its own collection and execute a subset of all tests. This means that tests in different processes requesting a high-level scoped fixture (for example `session`) will execute the fixture code more than once, which breaks expectations and might be undesired in certain situations. While `pytest-xdist` does not have a builtin support for ensuring a session-scoped fixture is executed exactly once, this can be achieved by using a lock file for inter-process communication."

The canonical pattern: a session-scoped fixture keyed on `worker_id` and fronted by a `FileLock`. For tmux specifically, each worker starts its own server with `-L siglab_{worker_id}`, exports `TMUX_TMPDIR` / `TMUX` in the fixture, and `--dist=loadfile` keeps the file's tests on one worker. Env-vars are copied at fork, so all subprocesses inherit the per-worker socket.

**Implication for SigLab**

The 17 timeouts are partly a parallel-safety problem: two workers launching `python -m siglab.tui` against the same tmux server collide on socket and session names. Promote the existing `tests/test_tui_tmux_hardening.py:208-214` fixture to a session-scoped, `worker_id`-keyed fixture that boots a worker-local tmux server (`tmux -L siglab_{worker_id} new-session -d -s tui`) and tears it down at session end. Combine with `--dist=loadfile` so all tmux tests run on one worker while fast `pilot`-based tests fan out. This is the safer smaller-delta from microchange-wave Lesson 4 (`SKILL.md:74-105`): ship the per-worker socket fix before any further refactor and re-measure.

---

## 4. pytest-timeout vs pytest-custom-scheduling vs pytest-slow

**Sources**
- pytest-timeout — https://pypi.org/project/pytest-timeout
- pytest-timeout issue #190 (slow-marker integration) — https://github.com/pytest-dev/pytest-timeout/issues/190
- pytest-slow-order blog (Brian Okken) — https://pythontest.com/pytest-slow-order
- pytest-skip-slow — https://pypi.org/project/pytest-skip-slow/
- pytest-order — https://pypi.org/project/pytest-order/

**Quote (pytest-timeout README, "warning")**

> "This plugin is designed to catch excessively long test durations like deadlocked or hanging tests, it is not designed for precise timings or performance regressions. Remember your test suite should aim to be **fast**, with timeouts being a last resort, not an expected failure mode."

**Quote (pytest-slow-order blog)**

> "It's possible to mark slow tests with `@pytest.mark.slow` and then either run or skip the slow tests. To run slow tests: `pytest -m slow`. To skip slow tests: `pytest -m "not slow"`. With the pytest-skip-slow plugin, you can: skip the `@pytest.mark.slow` tests by default, include them with `pytest --slow`."

**Verdict on the canonical choice**

- `pytest-timeout` is canonical for *hard kill* behaviour — the only one that actually aborts a hung test.
- `pytest-skip-slow` is the canonical way to express "opt-in slow tests" — a thin wrapper around `@pytest.mark.slow` that flips default skip via a CLI flag. There is no first-party `pytest-slow` package; the alternative is a custom marker + `pytest_collection_modifyitems` hook (the Okken pattern).
- `pytest-custom-scheduling` is not a published PyPI package. Slow-first/slow-last ordering is built on `pytest-order`'s `@pytest.mark.order` plus a `conftest.py` hook that maps `@pytest.mark.slow` to `order("first"|"last")`.

**Implication for SigLab**

SigLab already declares `pytest-timeout` (`pyproject.toml:48`) and an empty `slow` marker (line 40). The 17 timeouts are the failure mode `pytest-timeout` is designed for: tests hang on `time.sleep(_SETTLE_SECS)` past the per-test cap. The warning block above is explicit — the suite should be fast, not have its cap raised. The right move is the §2 `wait_for_output` conversion, plus making the `slow` marker meaningful: a `conftest.py` hook that exposes `--slow-first`/`--slow-last` after the Okken pattern. No new dependency required beyond `pytest-skip-slow` (one line in `pyproject.toml`).

---

## 5. ptyprocess / pexpect vs tmux for TUI tests

**Sources**
- pexpect FAQ — https://pexpect.readthedocs.io/en/stable/FAQ.html
- ptyprocess description — https://doc.sagemath.org/html/en/reference/spkg/ptyprocess.html
- termshark issue #118 — https://github.com/gcla/termshark/issues/118

**Quote (pexpect FAQ, "Can I do screen scraping with this thing?")**

> "If your application just does line-oriented output then this is easy. If a program emits many terminal sequences, from video attributes to screen addressing, such as programs using curses, then it may become very difficult to ascertain what text is displayed on a screen. We suggest using the `pyte` library to screen-scrape."

**Quote (pexpect FAQ, "Why not just use a pipe (popen())?")**

The FAQ walks through the line-buffering / block-buffering problem that motivated ptyprocess; the conclusion is that a pseudo-TTY is the only reliable way to control an interactive program.

**Decision rule**

- **pexpect / ptyprocess** — line-oriented prompts: passwords, SSH, REPLs, `pdb`. Regex-on-byte-stream. Cannot reliably interpret a full-screen redraw.
- **tmux + libtmux** — full-screen TUIs emitting cursor moves, alt-screen, OSC. You give up `expect()` matching and gain `capture_pane()` plus a real terminal emulator. This is what `tests/test_tui_tmux_hardening.py:1-797` already uses, and it is the right choice for `python -m siglab.tui`.
- **Textual Pilot** — in-process widget tree assertions. Fastest, but cannot catch shell-level regressions.

**Implication for SigLab**

Do not migrate to pexpect. SigLab's TUI is a full-screen Textual app, and the pexpect FAQ is unambiguous that curses-style output "may become very difficult to ascertain." The split is: tmux for the few smoke tests that exercise the real subprocess; `Pilot` for the bulk of widget-tree assertions; pexpect reserved for any future line-oriented CLI helpers (e.g. `siglab sodex-preflight`). The microchange-wave skill already documents this layering at `SKILL.md:42-49` (Lesson 2 reset helpers at `tests/test_tui_tmux_hardening.py:145-178`).

---

## 6. Headless terminal testing: snapshot tools, 2025-2026

**Sources**
- pytest-textual-snapshot — https://github.com/Textualize/pytest-textual-snapshot
- Textual testing guide "Snapshot testing" — https://textual.textualize.io/guide/testing
- A year of building for the terminal — https://textual.textualize.io/blog/2022/12/20/a-year-of-building-for-the-terminal

**Quote (pytest-textual-snapshot README, "About")**

> "A `pytest-textual-snapshot` test saves an SVG screenshot of a running Textual app to disk. The next time the test runs, it takes another screenshot and compares it to the saved one. If the new screenshot differs from the old one, the test fails. This is a convenient way to quickly and automatically detect visual regressions in your applications."

**Quote (year blog, "Snapshot testing for terminal apps")**

> "Snapshot testing is used to ensure that Textual output doesn't unexpectedly change… The snapshot testing functionality itself is implemented as a pytest plugin, and it builds on top of a snapshot testing framework called syrupy."

**API surface**

`snap_compare(app_or_path, press=[...], terminal_size=(W, H), run_before=async_fn)`. The fixture handles the headless lifecycle internally; works on a non-running `App` instance or a path to the file containing the `App` subclass. `--snapshot-update` rewrites the saved SVG after human review.

**Implication for SigLab**

The 17 timeouts include a "screen looks right" assertion implemented as "after `time.sleep(_SETTLE_SECS)`, the captured pane contains the keyword from `_SCREEN_KEYWORDS`." That checks one string. `pytest-textual-snapshot` checks the entire rendered SVG, catching layout regressions keyword matching cannot (button wrapping, status bar disappearing, palette drift). Recommended first microchange: add the plugin to `pyproject.toml` dev deps, write one snapshot test per screen in `TestResizeBehavior` (`tests/test_tui_tmux_hardening.py:598-682`), and re-record SVGs once. The microchange-wave Lesson 1 proof is `pytest tests/test_tui_snapshots.py --snapshot-update -x`. High-value, low-risk: snapshots are additive and catch a new failure mode without changing existing keyword assertions.

---

## 7. The microchange-wave skill (apply-agent protocol)

**Source**

- `/home/eya/soso/siglab/.agents/skills/microchange-wave/SKILL.md` (read at `microchange-wave/SKILL.md:1-144`)

**Quote (SKILL.md:1-4, front matter)**

> "Apply one targeted SigLab edit and verify it landed safely. Use after a planner_runner / writer_runner plan produces a single concrete change. Always run the post-edit safety check (Lesson 3) and the smaller-delta gate (Lesson 4) before reporting success."

**Quote (SKILL.md:22-32, Lesson 1)**

> "Any plan section that includes an assertion-tolerance claim … MUST carry a `PROOF_COMMAND:` line … runnable from `~/soso/siglab` without setup beyond `python3 -m pytest` and the dev dependencies declared in `pyproject.toml:45-51`; completes in <5 minutes; exits 0 on success and non-zero on failure; scopes to a single `TestClass::test_name`."

**Quote (SKILL.md:43-49, Lesson 2)**

> "When promoting a function-scoped test fixture to a wider scope, the apply agent MUST pair the scope change with per-test reset helpers that keep test isolation intact. The reference shape is the fixture at `tests/test_tui_tmux_hardening.py:208-214` and the reset helpers at `tests/test_tui_tmux_hardening.py:145-178` (`pop_to_base()`, `resize()` — each ≤5 lines)."

**Quote (SKILL.md:54-72, Lesson 3)**

> "Three single-line checks the apply agent runs as the LAST step before reporting success. Each must produce exactly one stdout line of the form `CHECK_N: <result>`. `CHECK_1: grep -c '</input>\|</output>'` must return `0`. `CHECK_2: wc -l` must equal the pre-edit expected line count, printed as `<actual> / <expected>`. `CHECK_3: python3 -c 'import ast; ast.parse(...)'` must exit `0`."

**Quote (SKILL.md:74-105, Lesson 4)**

> "If `SMARTER_DELTA > 0` and the edit lands in a high-risk class (CONST, fixture scope, test body, public interface), default to `SMARTER_DELTA`. Record `DELTA_SHIPPED: BIG|SMARTER`. ... Anti-pattern (banned): shipping the bigger-delta 'to get the full savings' when the smaller-delta is the demonstrably safer path."

**Implication for SigLab**

The apply agent's contract is rigid. Every "this won't break X" claim needs a `PROOF_COMMAND:`. Every fixture-scope change needs the `pop_to_base()` / `resize()` reset helpers (with the autouse guard against the mutation-order risk at `tests/test_tui_tmux_hardening.py:598-682`). Every edit ends with the three `CHECK_N:` lines. The microchange_card template lives at `templates/microchange_card.template.md` (templates lane), the safety oneshot at `templates/safety_oneshot.template.sh`. For the 17 timeouts, the apply agent MUST compute `SMARTER_DELTA` before each edit: the safer plan is *convert `time.sleep` to `wait_for_output`* (zero risk, identical behaviour) over *bump the timeout cap* (smell, defeats the pytest-timeout warning block). Ship the conversion first; measure; only escalate if the conversion is genuinely insufficient.

---

## One-line recommendations

1. **Pilot** — swap `time.sleep` for `await pilot.press(...)` + `pilot.app.query_one(...)` in widget-tree tests; keep tmux for one or two smoke tests.
2. **libtmux** — adopt upstream `wait_for_output(pane, text, timeout, poll_interval)`; wrap in `asyncio.to_thread` for async paths.
3. **xdist + tmux** — promote the harness to a `worker_id`-keyed session fixture with `-L siglab_{worker_id}` sockets; use `--dist=loadfile`.
4. **Timeouts** — keep `pytest-timeout` as the kill switch; add `pytest-skip-slow` + Okken `--slow-first` hook; never bump the cap.
5. **pexpect vs tmux** — keep tmux for the TUI; reserve pexpect for future line-oriented CLIs.
6. **Snapshots** — install `pytest-textual-snapshot`, add one snapshot per screen, gate `--snapshot-update` on human review.
7. **microchange-wave** — the apply agent must compute `SMARTER_DELTA` (wait_for_output conversion) before any cap-bump; emit a full `microchange_card` with the three `CHECK_N:` lines from `SKILL.md:55-72`.
