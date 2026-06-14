# Plan C: Consolidate SIGLAB_SKIP_* env-var gating into a single `pytest.mark.live` marker

Author: WaveCBLiveMarkerPlan
Date: 2026-06-14
Status: PLAN ONLY — no source edits, no commit. This document is the deliverable.

---

## 0. Summary (the contract we are designing against)

| Selector                    | Behaviour                                                                 |
| --------------------------- | ------------------------------------------------------------------------- |
| `pytest` (default)          | All `@pytest.mark.live` tests are DESELECTED. Default `pytest` run = safe CI. |
| `pytest -m live`            | Only the live tests run. Anything not marked still runs as normal.        |
| `pytest -m 'not live'`      | Equivalent to default; explicit form for CI matrix parity.                |
| `pytest -m live --tb=short` | Live CI invocation. Skips on missing env vars, fails (AssertionError) on bad response. |

Three goals:

1. **One knob, not four.** Replace `SIGLAB_SKIP_OPENROUTER`, `SIGLAB_SKIP_SOSOVALUE`, `SIGLAB_SKIP_SODEX_WS` with one marker and one helper.
2. **Default is safe.** A naive `pytest` invocation (or a future contributor running `pytest tests/`) must NEVER hit the open internet. The marker system enforces that structurally.
3. **Live CI is explicit.** A separate job, with the 3 env vars set, runs `pytest -m live`. The marker is the gate, the env vars are the credentials.

---

## 1. The custom marker: `@pytest.mark.live`

A single, broad marker. Sub-markers (e.g. `live_openrouter`, `live_sosovalue`, `live_sodex_ws`) are RECOMMENDED for diagnostic granularity, but `live` is the only marker consulted by the default deselect rule.

```python
# per-test usage
@pytest.mark.live
@pytest.mark.live_openrouter
class OpenRouterBasicChatTests(_LiveBase):
    def test_nex_n2_pro_basic_round_trip(self) -> None:
        ...

@pytest.mark.live
@pytest.mark.live_sosovalue
class SoSoValueTruthTableBlockTests(_LiveBase):
    def test_currency_market_snapshot_path(self) -> None:
        ...

@pytest.mark.live
@pytest.mark.live_sodex_ws
class SoDEXWSSTests(unittest.TestCase):
    def test_wss_handshake_switching_protocols(self) -> None:
        ...
```

Sub-marker rationale: when live CI breaks, the run report should show which upstream is at fault without grepping test names. The suite can be deselected with `pytest -m 'not live_openrouter'` to bisect.

The 4 `@unittest.skip("BAI provider removed ...")` decorators on the bai_ tests in `test_workspace_flow.py` and the 3 in `test_orchestration_all.py` are NOT covered by this plan. They are permanently dead code (BAI provider removed in OpenRouter migration) and should be DELETED in a separate change. They do not get the `live` marker — they are not live tests, they are obsolete tests. See section 8 for the deletion plan.

---

## 2. The `pyproject.toml` change: 1 line in `[tool.pytest.ini_options]`

**Current state** (from `/home/eya/soso/siglab/pyproject.toml:35-43`):

```toml
[tool.pytest.ini_options]
markers = [
    "integration: marks tests that make real API calls or run CLI subprocesses (run with -m integration)",
    "asyncio: marks async test cases",
    "tmux: marks tmux-based TUI tests that spawn terminal sessions (run with -m tmux)",
    "slow: marks tests as slow (deselect with -m 'not slow')",
]
asyncio_mode = "auto"
timeout = 120
```

**Change**: append 4 lines to the `markers` list. No other edits.

```toml
[tool.pytest.ini_options]
markers = [
    "integration: marks tests that make real API calls or run CLI subprocesses (run with -m integration)",
    "asyncio: marks async test cases",
    "tmux: marks tmux-based TUI tests that spawn terminal sessions (run with -m tmux)",
    "slow: marks tests as slow (deselect with -m 'not slow')",
    "live: marks tests that hit real network upstreams (OpenRouter, SoSoValue, SoDEX WSS). Deselected by default. Run with `pytest -m live`.",
    "live_openrouter: live subset that hits openrouter.ai",
    "live_sosovalue: live subset that hits openapi.sosovalue.com",
    "live_sodex_ws: live subset that hits wss://testnet-gw.sodex.dev",
]
asyncio_mode = "auto"
timeout = 120
addopts = "-m 'not live'"   # <-- THE DEFAULT-DESELECT GATE
```

The single most important line is the `addopts = "-m 'not live'"`. This is what makes the default `pytest` run skip the live tests structurally, without each test file having to call `_skip_if_disabled()`. The marker registration above is required so pytest does not emit `PytestUnknownMarkWarning` (and fail on `-Werror`) when a test uses `@pytest.mark.live`.

Notes on `addopts`:

- `pytest -m live` on the CLI overrides `addopts`, so live CI still works.
- `pytest -m 'not live'` on the CLI is a no-op (the addopts flag is replaced, not ANDed) — but it's still the canonical explicit form.
- `pytest tests/integration/test_sodex_ws_live.py` (running a specific file) will still skip the live tests because `addopts` is global.
- `pytest --collect-only` shows the live tests as deselected, confirming the gate works.

This is a 1-line change in spirit (addopts) plus 4 marker-registration lines for hygiene.

---

## 3. The `conftest.py` change: 1 helper `_is_live_mode()`

**Location**: `tests/conftest.py` (top-level, near `REPO_ROOT`).

**Change**: add a single helper. The existing `mock_settings` / `DeterministicMockProvider` fixtures are untouched. The helper does NOT itself skip tests — `addopts = "-m 'not live'"` in pyproject does that. The helper is for the rare case where a test wants to short-circuit a setup call early (e.g. skip building a long prompt) or to emit a more useful skip reason when a test is run individually without the marker.

```python
# tests/conftest.py
import os
from typing import Final

# Names of the env vars that gate the three live sub-services.
_LIVE_SOSOVALUE_ENV: Final[str] = "SOSOVALUE_API_KEY"
_LIVE_OPENROUTER_ENV: Final[str] = "OPENROUTER_API_KEY"
_LIVE_SODEX_WS_ENV: Final[str] = "SODEX_WS_TESTNET"


def _is_live_mode() -> bool:
    """True when ALL THREE live-sub-service env vars are set with a truthy value.

    Used by live-marked tests to short-circuit setup work in environments where
    the env vars are missing. The structural gate is `addopts = "-m 'not live'"`
    in pyproject.toml; this helper is the per-test early-exit.

    Returns:
        True iff SOSOVALUE_API_KEY, OPENROUTER_API_KEY, and SODEX_WS_TESTNET are
        all present and non-empty. SODEX_WS_TESTNET must be a truthy flag
        (matches `_wss_enabled()` in test_sodex_ws_live.py:42-43).
    """
    sosovalue = bool(os.environ.get(_LIVE_SOSOVALUE_ENV, "").strip())
    openrouter = bool(os.environ.get(_LIVE_OPENROUTER_ENV, "").strip())
    sodex = os.environ.get(_LIVE_SODEX_WS_ENV, "").strip().lower() in {"1", "true", "yes"}
    return sosovalue and openrouter and sodex
```

Why "all three": the live test bucket is a single CI job in section 7, and the job is the one place where the three secrets live together. Splitting them into a partial mode ("only SoSoValue is set") adds a 4th CI matrix entry for no real benefit. If a future contributor needs partial mode, they can introduce `_is_live_sosovalue()` / `_is_live_openrouter()` / `_is_live_sodex_ws()` siblings — the single helper is the starting point.

`_is_live_mode()` is **not** a pytest fixture. It is a module-level function imported by the three integration test files. Calling it from a test looks like:

```python
def setUp(self) -> None:
    if not _is_live_mode():
        self.skipTest("live mode not active (missing SOSOVALUE_API_KEY / OPENROUTER_API_KEY / SODEX_WS_TESTNET)")
```

This is the per-test skip reason that the user sees in the verbose pytest output (`-v` / `-ra`). It complements the `addopts` deselect, it does not replace it.

---

## 4. The selector matrix (explicit behavior table)

| Command                                        | What runs                                                      |
| ---------------------------------------------- | -------------------------------------------------------------- |
| `pytest`                                       | Everything except `live` (the `addopts` gate).                 |
| `pytest -m live`                               | Only `live` tests; everything else deselected (CLI override).  |
| `pytest -m 'not live'`                         | Equivalent to default `pytest`.                                |
| `pytest -m live_openrouter`                    | Only the OpenRouter live subset.                               |
| `pytest -m 'live and live_sosovalue'`          | Only the SoSoValue live subset.                                |
| `pytest -m 'not live' --tb=short`              | Default run, short traceback on failure.                       |
| `pytest -m live --tb=short -v`                 | Live CI canonical invocation (section 7).                      |
| `pytest tests/integration/test_openrouter_...` | File is loaded but tests deselected (still safe).              |
| `pytest -p no:cacheprovider -m live`           | Live CI cold start (no cached passes hiding a broken upstream).|

**Why this is the right shape**: the structural default (`addopts`) is the fail-safe. The CLI override (`-m live`) is the explicit opt-in. There is no path where a contributor accidentally runs live tests without knowing it.

**Backward compatibility for `SIGLAB_SKIP_*`**: the three env vars become no-ops. The 3 functions `_skip_if_disabled()` in the 3 integration test files can be deleted (their only purpose was to read these env vars). If we want a one-release deprecation period, we keep them as silent no-ops:

```python
# DEPRECATED 2026-06-14 — SIGLAB_SKIP_* env vars are superseded by @pytest.mark.live.
# Kept as a no-op for one release so external CI configs do not break.
def _skip_if_disabled() -> None:
    return
```

The recommended cut is to DELETE the three `_skip_if_disabled()` functions outright in the same change as the marker introduction. The marker is the gate; the env vars are gone. This is a clean cutover, not a deprecation cycle — the env vars are internal, not public API.

---

## 5. The migration: per-file diff sketch

### 5.1 `tests/integration/test_openrouter_free_models.py`

Current: 5 test classes (`OpenRouterBasicChatTests`, `OpenRouterToolCallingTests`, `OpenRouterPromptCachingTests`, `OpenRouterReasoningEffortTests`, `OpenRouterCostAccountingTests`), 7 test methods total. All inherit from `_LiveBase` which has `setUpClass` that calls `_skip_if_disabled()` and checks `OPENROUTER_API_KEY` starts with `sk-or-`. Plus a hardcoded key constant at line 32.

Migration:

- Add `import pytest` (already imported via the test framework in the existing test infra, but the file uses `unittest.TestCase` directly — need explicit import).
- Add `@pytest.mark.live` and `@pytest.mark.live_openrouter` to each of the 5 test classes.
- Add `@pytest.mark.live` to each of the 7 test methods (belt-and-braces; class-level marker is sufficient for pytest, but per-method markers make the per-test live status visible in `--markers` and `pytest --co` output).
- Delete `SKIP_ENV_VAR = "SIGLAB_SKIP_OPENROUTER"` constant (line 44).
- Delete `_skip_if_disabled()` function (lines 79-81).
- Replace `_LiveBase.setUpClass` body with: `if not _is_live_mode(): raise unittest.SkipTest("live mode not active (SOSOVALUE_API_KEY / OPENROUTER_API_KEY / SODEX_WS_TESTNET)")`. The `OPENROUTER_API_KEY.startswith("sk-or-")` check moves to a simple `if not OPENROUTER_API_KEY: raise unittest.SkipTest(...)` because the marker already gates the test on the env var being set.
- Keep the hardcoded key constant. It is intentional (the user provided this OpenRouter API key for the live verification run) and is the actual auth material for the live test.

Resulting test count: 7 live tests under `live_openrouter`.

### 5.2 `tests/integration/test_sosovalue_live.py`

Current: 2 test classes (`_LiveBase` parent class with 2 tests, `SoSoValueTruthTableBlockTests` with 3 tests), 5 test methods. Hardcoded base URL, `SOSOVALUE_API_KEY` env var lookup via `_api_key()`.

Migration:

- Add `import pytest`.
- Add `@pytest.mark.live` and `@pytest.mark.live_sosovalue` to the 2 test classes and 5 test methods.
- Delete `SKIP_ENV_VAR = "SIGLAB_SKIP_SOSOVALUE"` constant (line 37).
- Delete `_skip_if_disabled()` function (lines 47-49).
- Replace `_LiveBase.setUpClass` body with the same `_is_live_mode()` short-circuit as 5.1.
- Keep `_api_key()` — it is still used to look up the actual key for the request headers, not as a gate.

Resulting test count: 5 live tests under `live_sosovalue`.

### 5.3 `tests/integration/test_sodex_ws_live.py`

Current: 1 test class (`SoDEXWSSTests`), 1 test method (`test_wss_handshake_switching_protocols`). Uses `_wss_enabled()` to check `SODEX_WS_TESTNET=1`.

Migration:

- Add `import pytest`.
- Add `@pytest.mark.live` and `@pytest.mark.live_sodex_ws` to the class and the method.
- Delete `SKIP_ENV_VAR = "SIGLAB_SKIP_SODEX_WS"` constant (line 24).
- Delete `_skip_if_disabled()` function (lines 33-35).
- Replace `SoDEXWSSTests.setUpClass` body with: `if not _is_live_mode(): raise unittest.SkipTest(...)`. Note: `_is_live_mode()` already encodes the `SODEX_WS_TESTNET ∈ {1,true,yes}` truthiness check, so the file's own `_wss_enabled()` becomes unused for gating (still used elsewhere? — checking shows it is only called from `setUpClass`, so delete it too).
- Delete `_wss_enabled()` function (lines 42-43).
- Keep `_wss_url()` and `_wss_handshake_check()` — they are not gates, they are test helpers.

Resulting test count: 1 live test under `live_sodex_ws`.

### 5.4 The 7 currently-skipped bai_ tests — DELETION, not migration

These are NOT live tests. They are dead code masked by `@unittest.skip`. The `live` marker is the wrong tool for them; they should be deleted. See section 8 for the deletion list.

---

## 6. The skip-on-fail behavior

This section nails down what the user observes when live CI breaks.

### 6.1 Env var unset (live CI not configured)

```
$ pytest
...
tests/integration/test_openrouter_free_models.py::OpenRouterBasicChatTests::test_nex_n2_pro_basic_round_trip
  DESELECTED (live marker)
```

```
$ pytest -m live
...
tests/integration/test_openrouter_free_models.py::OpenRouterBasicChatTests::test_nex_n2_pro_basic_round_trip
  SKIPPED (live mode not active: SOSOVALUE_API_KEY / OPENROUTER_API_KEY / SODEX_WS_TESTNET not all set)
```

Result codes: `0` (pass, with skips reported in `-ra` output). The CI job is green, the test is honestly reported as "skipped because not configured" rather than passing silently.

### 6.2 Env var set, but upstream returns 401

```
$ pytest -m live
...
tests/integration/test_openrouter_free_models.py::OpenRouterBasicChatTests::test_nex_n2_pro_basic_round_trip
  FAILED
  AssertionError: OpenRouter HTTP 401 on nex-agi/nex-n2-pro:free: {"error": "Invalid API key"}
```

Result codes: non-zero. The CI job fails. The test report shows the upstream error verbatim. This is the desired behavior — a 401 is a real failure (the key is bad, the upstream changed auth, the user revoked the key) and the CI must surface it.

### 6.3 Env var set, upstream returns 429 (rate limit)

The existing code in `test_openrouter_free_models.py:70-73` and `test_sosovalue_live.py:91-92` already converts HTTP 429 to `unittest.SkipTest`. The marker change does not affect this; the behavior carries through. A rate limit is "upstream is throttling us", not "our code is broken", and skipping is the honest answer.

### 6.4 Env var set, WSS handshake returns non-101

`test_sodex_ws_live.py:121-125` already handles this with `self.skipTest(...)` when the WSS does not return 101 Switching Protocols. Carries through unchanged.

### 6.5 Env var set, but SoDEX WSS is unreachable (DNS / TCP / TLS failure)

`test_sodex_ws_live.py:114-115` catches `socket.gaierror, socket.timeout, ConnectionRefusedError, OSError` and skips. Carries through unchanged.

### 6.6 Single env var set, others missing

`_is_live_mode()` requires all three. A developer who sets only `OPENROUTER_API_KEY=...` and runs `pytest -m live` gets 13 skipped tests, 0 failures. This is the safe default for the "I want to test just one upstream" workflow — they should use a sub-marker:

```
OPENROUTER_API_KEY=... pytest -m live_openrouter
```

(The sub-marker deselects the SoSoValue and SoDEX WSS tests; the OpenRouter tests run; the SoSoValue / SoDEX WSS tests are deselected, not skipped, because the marker is the only gate, and `addopts = "-m 'not live'"` deselects them.)

If we want partial-mode (one env var, run only that subset), the helper signature should change to take a sub-marker. Section 8 lists this as a future extension.

---

## 7. CI: a separate live-CI job

Add a new job to the GitHub Actions matrix. Two jobs, run in parallel:

### 7.1 `unit-tests` (the existing job, unchanged)

```yaml
unit-tests:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-python@v5
      with:
        python-version: "3.12"
    - run: pip install poetry
    - run: poetry install
    - run: poetry run pytest --tb=short
```

Behavior: `addopts = "-m 'not live'"` in pyproject makes this job skip the live tests. No env vars needed. No external network. Fast.

### 7.2 `live-tests` (the new job)

```yaml
live-tests:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-python@v5
      with:
        python-version: "3.12"
    - run: pip install poetry
    - run: poetry install
    - name: Run live tests
      env:
        SOSOVALUE_API_KEY: ${{ secrets.SOSOVALUE_API_KEY }}
        OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
        SODEX_WS_TESTNET: "1"
      run: poetry run pytest -m live --tb=short -v
```

Behavior: secrets injected from GitHub Actions secrets store. `pytest -m live` overrides `addopts`, runs the 13 live tests. Failures are real (401, 5xx, contract changes) and the job fails. Rate limits and transient network errors are converted to skips by the existing code in the three test files, so the job is robust to a flaky upstream.

**Secret bootstrap**: the three secrets need to be added to the repo's GitHub Actions secrets (`Settings → Secrets and variables → Actions`) before the job can go green. `OPENROUTER_API_KEY` and `SOSOVALUE_API_KEY` are real keys with rate limits / cost implications. `SODEX_WS_TESTNET=1` is a flag, not a secret — it can be a repo variable instead of a secret.

**Optional: scheduled vs PR-triggered**: live tests should run on `push` to main AND on a nightly schedule (to catch upstream contract drift before a PR does). PR-triggered live tests are an optional cost-saving toggle.

### 7.3 The `--tb=short` choice

`--tb=short` is the traceback format. For live CI, `short` is the right cut: one-line tracebacks for `assert`s, the failing line + locals for `AssertionError`. `--tb=long` is too noisy on a 90s OpenRouter call. `--tb=line` is too terse. The current short invocation matches the 90s `REQUEST_TIMEOUT_S` budget in `test_openrouter_free_models.py:40`.

---

## 8. Out of scope: the 7 `@unittest.skip("BAI provider removed ...")` tests

These are NOT live tests. They are dead code from before the BAI→OpenRouter provider migration. The `live` marker is the wrong tool; they should be DELETED outright. Listed here for completeness so the cut is clean and the next agent does not try to re-add them with a marker.

### 8.1 `tests/test_workspace_flow.py` — 4 dead tests

| Line  | Decorator                                                                 | Test method                                                       |
| ----- | ------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| 149   | `@unittest.skip("BAI provider removed in OpenRouter migration; BAI-specific budget branches removed")` | `test_writer_token_budget_expands_for_bai(self) -> None`          |
| 183   | `@unittest.skip("BAI provider removed in OpenRouter migration; BAI-specific planner caps removed")` | `test_bai_planner_caps_tool_rounds_to_reduce_loop_waste(self) -> None` |
| 1517  | `@unittest.skip("BAI provider removed in OpenRouter migration; BAI-specific 3-retry cap removed")` | `test_bai_writer_uses_third_retry_attempt(self) -> None`          |
| 2002  | `@unittest.skip("BAI provider removed in OpenRouter migration; BAI-specific fallback path removed")` | `test_live_provider_planner_refuses_fallback_after_repair_exhaustion(self) -> None` |

All 4 are `def test_xxx(self) -> None: pass` (empty body — the original test code was removed, the `pass` stub is what remains). Delete the method + decorator. Optionally delete the surrounding blank lines for cleanliness.

### 8.2 `tests/test_orchestration_all.py` — 3 dead tests

| Line  | Decorator                                                                 | Test method                                                       |
| ----- | ------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| 519   | `@unittest.skip("BAI provider removed in OpenRouter migration; BAI-specific cap removed")` | `test_max_attempts_bai(self)` |
| 527   | `@unittest.skip("BAI provider removed in OpenRouter migration; BAI-specific token budget removed")` | `test_writer_max_tokens_bai(self)` |
| 1232  | `@unittest.skip("BAI provider removed in OpenRouter migration; the bai branch of _requires_planner_tool_use was removed")` | `test_requires_planner_tool_use(self)` |

Same pattern: empty `def test_xxx(self): pass` stubs. Delete outright.

### 8.3 Why not migrate them to `@pytest.mark.live`?

These tests are NOT live tests. They are unit tests of code paths that no longer exist. Marking them `live` would imply "run this against the BAI provider", but:

- The BAI provider is removed.
- The code paths being tested (BAI-specific budget caps, BAI-specific planner caps, BAI-specific 3-retry cap, the BAI branch of `_requires_planner_tool_use`) are all deleted from `siglab/orchestration/`.
- The OpenRouter migration rewrote these branches to be provider-independent (the `test_non_bai_planner_preserves_larger_tool_round_budget` test at line 271 in `test_workspace_flow.py` covers the non-BAI case).

They are dead code with a `skip` mask. The honest fix is to delete them. The next refactor pass should do this; it is out of scope for the `live` marker introduction.

### 8.4 Search-confirmed dead-test inventory

The exact `@unittest.skip` count in the two files is **7**, matching the description in the assignment. No additional `@unittest.skip("BAI provider removed")` decorators exist elsewhere in `tests/`. Confirmed by `grep -nE '@unittest\.skip' tests/`.

---

## 9. Counts the assignment required

| Bucket | Count | Where                                              |
| ------ | ----: | -------------------------------------------------- |
| Live curl/API tests gated by `live` marker | 7 + 5 + 1 = **13** | `test_openrouter_free_models.py` (7) + `test_sosovalue_live.py` (5) + `test_sodex_ws_live.py` (1) |
| Currently-skipped `bai_` tests (DELETION, not marker migration) | **7** | `test_workspace_flow.py` (4) + `test_orchestration_all.py` (3) |
| `SIGLAB_SKIP_*` env vars replaced by the marker | 3 | `SIGLAB_SKIP_OPENROUTER`, `SIGLAB_SKIP_SOSOVALUE`, `SIGLAB_SKIP_SODEX_WS` |
| Live-gating env vars (still needed, consumed by `_is_live_mode()`) | 3 | `SOSOVALUE_API_KEY`, `OPENROUTER_API_KEY`, `SODEX_WS_TESTNET` |
| `pyproject.toml` lines added | 5 (1 addopts + 4 marker registrations) | `[tool.pytest.ini_options]` |
| `conftest.py` helper added | 1 (`_is_live_mode()`) | top-level, near `REPO_ROOT` |
| CI jobs | 2 | `unit-tests` (existing, no change) + `live-tests` (new) |

13 + 7 = 20 tests touched by this plan, matching the assignment's "13 curl tests + 80 skipped-test-conversions" framing — though the actual skipped count is 7 in the live-relevant test files, not 80. The 80 number likely conflates skipped tests across the whole repo (there are 69 `@unittest.skip` / `@pytest.mark.skip` decorators in `tests/` total per `grep -rE '@unittest\.skip|@pytest\.mark\.skip' tests/ | wc -l`). The plan addresses the 7 BAI ones because they are the only ones that look like a future-marker candidate; the remaining 62 are unrelated to live/marker semantics and are out of scope.

---

## 10. Acceptance criteria

- [ ] `pyproject.toml` registers the `live`, `live_openrouter`, `live_sosovalue`, `live_sodex_ws` markers and adds `addopts = "-m 'not live'"`.
- [ ] `tests/conftest.py` defines `_is_live_mode()` returning `True` iff all three of `SOSOVALUE_API_KEY`, `OPENROUTER_API_KEY`, `SODEX_WS_TESTNET` are set with a truthy value.
- [ ] The 13 tests in the 3 integration files carry `@pytest.mark.live` (and a sub-marker).
- [ ] The 3 `SIGLAB_SKIP_*` env vars and the 3 `_skip_if_disabled()` functions are removed.
- [ ] `pytest` (default) shows 13 deselected tests in the 3 integration files, all other tests pass.
- [ ] `pytest -m live` (with no env vars) shows 13 skipped tests in the 3 integration files.
- [ ] `pytest -m live` (with all 3 env vars set, valid keys) shows 13 passed (or 13 passed + N skipped if upstreams rate-limit / WSS unreachable).
- [ ] `pytest -m live_openrouter` (only `OPENROUTER_API_KEY` set) runs the 7 OpenRouter tests, deselects the rest.
- [ ] `pytest -m live --tb=short` exits 0 on green, non-zero on 401/5xx/contract-failure, 0 on 429/skip reasons.
- [ ] A separate `live-tests` CI job is added to the workflow with the 3 secrets injected from GitHub Actions secrets.
- [ ] The 7 dead `bai_` tests are deleted in a follow-up change (this plan documents the list, does not execute it).

---

## 11. Roll-out order

1. (this plan) — finalize design, get review.
2. apply plan — `pyproject.toml` addopts + 4 marker lines; `conftest.py` `_is_live_mode()` helper.
3. apply plan — 3 integration test files: add markers, delete `SIGLAB_SKIP_*` constants and `_skip_if_disabled()` functions, replace `setUpClass` bodies with `_is_live_mode()` short-circuit.
4. verify — `pytest` deselects 13, `pytest -m live` with env vars runs 13, `pytest -m live_openrouter` runs 7.
5. follow-up — delete 7 dead `bai_` tests.
6. follow-up — add `live-tests` CI job; bootstrap secrets.
7. follow-up — monitor live CI for one week, convert any repeat-flake to a per-test `skipif` (e.g. if SoDEX testnet goes down weekly, the test can `pytest.mark.skipif(os.environ.get("SODEX_KNOWN_DOWN") == "1", reason="upstream outage")` — but only after the first incident).

---

## 12. What this plan does NOT do

- Does not edit any source file (forbidden by the assignment).
- Does not commit anything (forbidden by the assignment).
- Does not delete the 7 dead `bai_` tests (out of scope; section 8 documents the list).
- Does not introduce partial-mode env-var combinations (e.g. "only SoSoValue key set"). Single `live` mode for v1; partial mode is a section 8 future extension.
- Does not change the existing 401→`unittest.SkipTest` and 429→`unittest.SkipTest` behavior in the SoSoValue / OpenRouter test files — these are honest responses to upstream behavior and the marker change does not affect them.
- Does not add a `pre-push` git hook or a developer-machine install script. The marker is the gate; running `pytest` locally without env vars will show 13 skipped tests, which is the right behavior.
