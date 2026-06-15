# Conftest `_fast_tui_api` review — iter17

## What it does

`tests/conftest.py:250-282` defines an `autouse=True` fixture `_fast_tui_api` that monkeypatches `TuiApiClient._request_with_retry` with an async no-op returning `{}`. When `run_test()` launches `SigLabTUI`, the active screen's `on_mount` fires ~3 HTTP calls that each retry-then-fail with a 0.5s sleep (~2.5s wasted per pilot test). The stub cuts ~25s from the pilot suite while leaving widget / binding machinery fully exercised; only network I/O is short-circuited. The patch targets the class object imported at conftest load, so any singleton or fresh `TuiApiClient(...)` inside a screen sees the stub; `monkeypatch.setattr` reverts it per-test.

## Current opt-in / opt-out

Opt-in (`_FAST_TUI_API_FILES`, lines 236-247) — eight per-screen pilot files: `test_tui_validation_contract.py`, `test_tui_foundation.py`, `test_tui_market.py`, `test_tui_paper_trading.py`, `test_tui_risk_screen.py`, `test_tui_strategy.py`, `test_tui_evidence.py`, `test_tui_telemetry.py`.

Opt-out (lines 264-269 early-return): nodeid containing `/test_tui_api_client.py`, `/test_tui_tmux_hardening.py`, or `TestApiClientMarketMethods`.

## Interaction with documented fixtures

Not in `docs/module-testing.md` (which lists `sample_spec`, `sample_spec_minimal`, `mock_settings`, `deterministic_provider` at lines 162-189) and does not touch them — it patches the API client class only. Like `_seed_global_random` (conftest.py:32-35), it is autouse but undocumented; this gap is what made the review necessary.

## Gaps — files that should be in the opt-in set

1. `test_tui_headless_pilot.py` (162 LoC) — the in-process `run_test()` pilot harness. Same retry-then-fail pattern; obvious win.
2. `test_tui_group_c_validation.py` (1483 LoC, largest pilot file) — group-C validation flows spin up `SigLabTUI` and pay the same 2.5s/test cost.
3. `test_tui_formatting.py` and `test_tui_data_views.py` — pure formatting / zero-copy view classes with no network; stub is a no-op there, but including them keeps the allowlist semantics "anything TUI-shaped".

## Recommendations

1. **Add the missing files to `_FAST_TUI_API_FILES`**: at minimum `test_tui_headless_pilot.py` and `test_tui_group_c_validation.py`. Highest-leverage change — both are large and pilot-bound.
2. **Replace file-name allowlist with a `pytest.mark.fast_tui_api` marker**: turn `_FAST_TUI_API_FILES` into a `SKIP` set and require pilot tests to opt in via `pytestmark`. File-name substring matching is fragile — renames silently disable the speedup, new pilot files accidentally miss it.
3. **Tighten the early-return checks**: `"/test_tui_api_client.py" in nodeid` matches any future path containing that substring. Pin to `request.node.fspath.name` (or `nodeid.endswith(...)`) against the file basename.
4. **Expose a non-autouse `tui_api_stub` fixture** yielding a `dict` so opt-in pilot tests can pre-fill canned responses and assert widget state without touching the real client. The autouse stub is a black box returning `{}`; making it a real fixture unlocks assertion-level coverage on the speedup path.
5. **Document the fixture in `docs/module-testing.md`** alongside the other Fixtures entries (line 189). `module-testing.md` lists four fixtures but the conftest contains six; `_fast_tui_api` and `_seed_global_random` are invisible to readers, which is what made this review necessary.
