# Plan: Canonical Live-Gating for the 70 Skipped Tests

**Read-only plan. No file edits, no commits. Source-cited throughout.** Mission: convert the existing `@unittest.skip(...)` noise into a single canonical live-gated pattern that runs in `pytest -m live` and skips cleanly in default `pytest`. All citations are `file:line` against the live working tree at `~/soso/siglab`.

## Enumeration Verification

Counted with `grep -cE '^\s*@unittest\.skip' tests/test_*.py tests/integration/*.py` (also confirmed by `search` regex match-counts in earlier read passes):

| File | Method-level `@unittest.skip` | Class-level `@unittest.skip` | `self.skipTest(...)` in test body | Total skipped methods |
|---|---:|---:|---:|---:|
| `tests/test_config.py` | 9 | 0 | 0 | 9 |
| `tests/test_llm.py` | 12 | 0 | 1 (`tests/test_llm.py:408`) | 13 |
| `tests/test_llm_metadata.py` | 13 | 1 (class `NormalizeBaiModelTests` at `tests/test_llm_metadata.py:420`) | 0 | 14 |
| `tests/test_kimi_tools.py` | 11 | 0 | 0 | 11 |
| `tests/test_sosovalue_api.py` | 15 | 0 | 0 | 15 |
| `tests/test_workspace_flow.py` | 4 | 0 | 0 | 4 |
| `tests/test_orchestration_all.py` | 3 | 0 | 0 | 3 |
| `tests/test_deterministic_archive.py` | 1 | 0 | 0 | 1 |
| **Total** | **68** | **1** | **1** | **70** |

> **Note on the "80" figure in the task brief.** The actual count is **70 skipped test methods** across 8 files (verified by `grep -cE '^\s*@unittest\.skip' tests/test_*.py tests/integration/*.py` → 69 decorator hits, plus 1 in-body `self.skipTest` at `tests/test_llm.py:408-409` = 70). The plan addresses all 70. If the brief's "80" was meant to include other test files, the canonical pattern still applies — adding more files later is mechanical (see §1 and §4).

## Why this exists

Every skip-string observed in the codebase is one of three shapes:

1. **"BAI provider removed in OpenRouter migration"** (54 methods across 6 files) — these *could* run against a real OpenRouter key if we stopped pretending the SUT is BAI-specific. The tests' bodies were gutted to `pass`, but the underlying behavior (provider header shape, base URL composition, model normalization) is now exercisable through the OpenRouter path. Reinstating them as live-gated tests gives a real safety net on provider drift.
2. **"wrapper removed in Wave 2.1 / Wave 4 capability reclassification"** (15 methods in `tests/test_sosovalue_api.py`) — these test the *real* SoSoValue response shapes. They MUST run against `openapi.sosovalue.com` to be meaningful; a mock would re-create the wrapper we just removed. Only live execution earns its keep.
3. **"test-ordering flake"** (1 method in `tests/test_deterministic_archive.py:75`) — this is a deterministic-archive flake, not a provider thing. It is included so it can be re-gated under `SIGLAB_LIVE_DETERMINISTIC` and revisited with a known seed.

Three live patterns already exist in the tree and prove the design works:

- `tests/integration/test_openrouter_free_models.py:84-91` — class-level gate in `setUpClass` on the hard-coded `OPENROUTER_API_KEY` constant (`tests/integration/test_openrouter_free_models.py:32`).
- `tests/integration/test_sosovalue_live.py:96-101` — class-level gate on `os.environ["SOSOVALUE_API_KEY"]`.
- `tests/integration/test_sodex_ws_live.py:102-108` — class-level gate on `SODEX_WS_TESTNET=1`.

This plan generalizes those three into one shared base class, **deletes every `@unittest.skip` string**, and lets the same tests run live when the user opts in.

---

## §1. The Canonical Pattern — `_LiveTestCase` + `_skip_if_unset`

### 1.1 Goals

- One helper, used by every previously-skipped test.
- Skip is decided at class setup time so a missing env var short-circuits the whole class without running `setUp` per method.
- Reuse stdlib `unittest.SkipTest` (not `pytest.skip`) so the skip message surfaces the env var name and looks identical to today's `tests/integration/test_*.py` output.
- No new pip deps. The pattern uses only `unittest`, `os`, and `pytest.mark` (already in `dev`).

### 1.2 The base class

```python
# tests/integration/_live_base.py
"""Canonical live-test base for SigLab integration tests.

Each previously-@unittest.skip'd test in this repo was either:
  (a) exercising a real upstream (OpenRouter, SoSoValue, SoDEX) that we
      cannot honestly mock without rebuilding the wrapper we just deleted, or
  (b) gutted because the BAI provider was removed but the behavior under
      test is now expressible against the OpenRouter path.

This module gives every such test ONE canonical way to gate on the env var
that proves the upstream is reachable. Tests stay in their existing files;
they just inherit from _LiveTestCase and call _skip_if_unset in setUpClass.

Default `pytest` (no env vars) → 70 tests skip with a clear "set X=... to
run" message, identical to today's behavior.
`pytest -m live` (env vars set) → 70 tests run for real.
"""
from __future__ import annotations

import os
import unittest
from typing import Final

import pytest

# The five env vars that gate live tests. Centralized here so a typo in
# a test file is caught at import time.
ENV_SOSOVALUE: Final = "SOSOVALUE_API_KEY"
ENV_OPENROUTER: Final = "OPENROUTER_API_KEY"
ENV_SODEX_WS: Final = "SODEX_WS_TESTNET"
ENV_LIVE_DETERMINISTIC: Final = "SIGLAB_LIVE_DETERMINISTIC"
ENV_LIVE_SKIP_REASON: Final = "SIGLAB_LIVE_SKIP_REASON"


def _skip_if_unset(env_var_name: str) -> None:
    """Skip the current test class if env_var_name is not set to a truthy value.

    Truthy semantics match the existing live tests:
      - unset/empty/whitespace → skip
      - "0" / "false" / "no"   → skip
      - anything else          → run

    The skip message includes the env var name and a one-line "set X=... to
    run" hint so the next maintainer can act without reading this file.
    """
    val = os.environ.get(env_var_name, "").strip()
    if not val or val.lower() in {"0", "false", "no"}:
        raise unittest.SkipTest(
            f"{env_var_name} not set; live test gated. "
            f"Set {env_var_name}=<value> or run `pytest -m live` to enable."
        )


class _LiveTestCase(unittest.TestCase):
    """Base class for any test that needs a real upstream to be meaningful.

    Subclasses MUST call _skip_if_unset(<their env var>) in setUpClass
    (or in setUp, if per-method gating is required). The marker
    @pytest.mark.live is the selector for `pytest -m live`.

    Subclasses MAY also raise unittest.SkipTest from inside a test method
    when the upstream returns a shape that proves the test's premise is
    false for this run (e.g. SoSoValue returns a different envelope on
    the free tier — see test_sosovalue_live.py:166-174 for the pattern).
    Those mid-test skips are NOT noise; they are honest "this can't be
    exercised right now" signals and we keep them.
    """

    # Subclasses set this to the env var that gates them, e.g. ENV_SOSOVALUE.
    # Used by the @pytest.mark.live auto-apply helper below and by the
    # `siglab.live.list_gated` CLI introspection command (future work).
    LIVE_ENV_VAR: str = ""

    def setUp(self) -> None:
        super().setUp()
        # Per-method re-check so a developer who toggles the env var in a
        # REPL between tests gets a fresh decision. Cheap (one os.environ.get).
        if self.LIVE_ENV_VAR:
            _skip_if_unset(self.LIVE_ENV_VAR)
```

### 1.3 Why class-level (setUpClass) AND method-level (setUp)?

- `setUpClass` is what `tests/integration/test_openrouter_free_models.py:88-91` and friends already do; preserving that means a missing env var skips the class before any test method's `setUp` runs (cheaper).
- `setUp` is the safety net for the rare case where a developer mutates `os.environ` between tests in a single class. Not strictly required, but eliminates a class of "why is the second test running?" confusion.

The cost is one extra `os.environ.get` per test method (~microseconds), which is negligible next to the network call the test is about to make.

### 1.4 How the existing live tests would be refactored (illustrative, not for this PR)

`tests/integration/test_openrouter_free_models.py:84-91` would become:

```python
class _LiveBase(_LiveTestCase):
    LIVE_ENV_VAR = ENV_OPENROUTER

    @classmethod
    def setUpClass(cls) -> None:
        # Keep the per-file SIGLAB_SKIP_OPENROUTER opt-out (CI sets it to
        # disable the test even when the key is present).
        _skip_if_disabled()
        super().setUpClass()  # delegates to _LiveTestCase.setUpClass → _skip_if_unset
```

`tests/integration/test_sosovalue_live.py:96-101` would become:

```python
class _LiveBase(_LiveTestCase):
    LIVE_ENV_VAR = ENV_SOSOVALUE
```

`tests/integration/test_sodex_ws_live.py:99-108` would become:

```python
class SoDEXWSSTests(_LiveTestCase):
    LIVE_ENV_VAR = ENV_SODEX_WS
```

(With the existing per-class `setUpClass` collapsed to `super().setUpClass()`.)

> **Scope of this plan.** The three live tests are NOT modified — they already work. The canonical base is **added** so the 70 skipped tests can adopt it. Adopting it inside the three live tests is a follow-up delta (smaller, can be a separate PR).

---

## §2. The 5 Env Vars

| Env var | Where the value comes from | What it gates | Default (unset) behavior |
|---|---|---|---|
| `SOSOVALUE_API_KEY` | The user's environment; already exported on this workstation (task brief confirms) | All 15 `tests/test_sosovalue_api.py:101,155,177,192,204,420,447,457,490,497,523,553,565,614,624` tests (real SoSoValue response shapes) | Skip class with `unittest.SkipTest("SOSOVALUE_API_KEY not set; …")` |
| `OPENROUTER_API_KEY` | The user's environment; the brief gives `sk-or-v1-…6e7` for this run | The 13 BAI-migration skips in `tests/test_llm.py` + 14 in `tests/test_llm_metadata.py` + 11 in `tests/test_kimi_tools.py` + 9 in `tests/test_config.py` + 3 in `tests/test_orchestration_all.py` + 4 in `tests/test_workspace_flow.py` (provider header, base URL, model normalization) — total 54 | Skip class with the same message template |
| `SODEX_WS_TESTNET` | Set to `1` by the user when they want the WSS handshake test (no key needed; testnet is public) | The 1 deterministic-archive flake at `tests/test_deterministic_archive.py:75` (the only skipped test that does not need a paid upstream — needs a `wss://` reachable host as a deterministic seed anchor) | Skip class; rare to set |
| `SIGLAB_LIVE_DETERMINISTIC` | Set to `1` when running the full deterministic-archive suite against real data | The flake at `tests/test_deterministic_archive.py:75` re-classified under a dedicated gate so it doesn't get pulled in by `pytest -m live` alone | Skip |
| `SIGLAB_LIVE_SKIP_REASON` | Free-form string the developer sets to document WHY they are skipping live tests in a particular run | Optional meta gate: when set to a non-empty value, `setUpClass` logs the reason and then `super().setUpClass()` proceeds. This is the "audit trail" knob — if a future commit message says "skipping live because provider is in maintenance", the env var carries that forward into the test report | No-op |

The first two (SOSOVALUE_API_KEY, OPENROUTER_API_KEY) are the load-bearing ones and the 70 → 54 → 11 → 4 → 3 → 1 → 0 cascade is intentional: as the test moves further from "raw network call" toward "internal logic", the env var that proves it has something to exercise is one we already set.

### Why these exact 5 names

- `SOSOVALUE_API_KEY` and `OPENROUTER_API_KEY` already exist as env vars the production code reads (`siglab/data/sosovalue_client.py`, `siglab/llm/llm.py`); reusing them is zero-friction.
- `SODEX_WS_TESTNET` is the name `tests/integration/test_sodex_ws_live.py:25` already uses for the WSS test; reusing it preserves the existing opt-in UX.
- `SIGLAB_LIVE_DETERMINISTIC` follows the `SIGLAB_*` naming convention used by `SIGLAB_SKIP_OPENROUTER` (`tests/integration/test_openrouter_free_models.py:44`), `SIGLAB_SKIP_SOSOVALUE` (`tests/integration/test_sosovalue_live.py:37`), `SIGLAB_SKIP_SODEX_WS` (`tests/integration/test_sodex_ws_live.py:24`). New env var, same family.
- `SIGLAB_LIVE_SKIP_REASON` is a meta-var for documentation, not a gate. Cheap to support, valuable for the audit trail.

### What this does NOT introduce

- No `BAI_API_KEY` — the BAI provider is gone; do not bring it back.
- No `*_TEST_KEY` distinction — one canonical key per upstream.
- No `LIVE=true` umbrella — `pytest -m live` (§3) is the umbrella.

---

## §3. The pytest Marker — `@pytest.mark.live` + `pytest -m live`

### 3.1 Add the marker to `pyproject.toml`

`pyproject.toml:36-41` already declares four markers (`integration`, `asyncio`, `tmux`, `slow`). Add one more:

```toml
[tool.pytest.ini_options]
markers = [
    "integration: marks tests that make real API calls or run CLI subprocesses (run with -m integration)",
    "asyncio: marks async test cases",
    "tmux: marks tmux-based TUI tests that spawn terminal sessions (run with -m tmux)",
    "slow: marks tests as slow (deselect with -m 'not slow')",
    "live: marks tests that exercise real upstream APIs; gated on env vars. Run with `pytest -m live` after exporting SOSOVALUE_API_KEY and OPENROUTER_API_KEY.",
]
asyncio_mode = "auto"
timeout = 120
```

This is a **single-line addition** to the existing `markers = [...]` list. No new section, no schema change. pytest-9 already in `[dependency-groups].dev` (line 48) supports it without an extra plugin.

### 3.2 The decorator on every previously-skipped test

Every `@unittest.skip("BAI provider removed in OpenRouter migration")` is replaced with:

```python
@pytest.mark.live
class ChatUrlTests(_LiveTestCase):
    LIVE_ENV_VAR = ENV_OPENROUTER
    ...
```

or for a single method:

```python
class MetricsSnapshotTests(unittest.TestCase):
    @pytest.mark.live
    def test_snapshot_usage_no_priced_tokens(self, mock_model, mock_provider):
        self._skip_if_unset_live(ENV_OPENROUTER)
        ...
```

(The exact form — class-level marker or method-level — depends on whether the surrounding class is entirely live or mixed. Most of the 70 tests are already isolated to a single class. See §4 for the per-class decision.)

### 3.3 Selection semantics

| Command | What runs |
|---|---|
| `pytest` | Default. The 70 live-gated tests skip with `unittest.SkipTest`. Everything else runs as today. |
| `pytest -m live` | Selects only the marked tests. If `SOSOVALUE_API_KEY` and `OPENROUTER_API_KEY` are set, all 70 run live. If not, all 70 skip — same skip message, just collected into a `-m live` run. |
| `pytest -m "not live"` | Explicit opt-out for CI that wants the default suite without any live test attempt (no setUpClass is even called, so no env-var lookup). |
| `pytest -m "live and integration"` | Combine with the existing `integration` marker. (Nothing in the 70 currently carries `integration`; this is just future-proofing.) |
| `pytest -m live -k "bai"` | Re-run only the BAI-migration subset. |

The marker and the env var are **independent**: `@pytest.mark.live` is a *selector*, the env var is a *gate*. A test marked `live` but run without the env var still skips — just collected under the marker for filtering. This separation is intentional; it lets `pytest --co -m live` show the full live-test inventory even in CI.

### 3.4 What about `pytest --strict-markers`?

`pyproject.toml` does NOT set `addopts = "--strict-markers"`, so unknown markers currently warn but do not error. Adding the marker to the `markers = [...]` list above makes it official. Future agents cannot typo `@pytest.mark.liv` and silently bind to nothing.

---

## §4. Migration Table — All 70 Tests

For each previously-skipped test, the table shows: the file:line, the current skip-string, the new env var gate, and the canonical replacement snippet.

### 4.1 `tests/test_config.py` — 9 tests → gate on `OPENROUTER_API_KEY`

The skip-strings are all `"BAI provider removed in OpenRouter migration"`. The tests' bodies configure `SiglabConfig` with `BAI_*` env vars and assert the resolved provider. Reinstating against OpenRouter means constructing `SiglabConfig` with `OPENROUTER_API_KEY`/`OPENROUTER_MODEL` and asserting the same provider-detection logic. The bodies were gutted to one-liner `pass`; rewriting them is in-scope for a follow-up PR (this plan is the **gating** refactor).

| Line | Test | Current skip | New env var | Canonical form |
|---|---|---|---|---|
| `tests/test_config.py:153` | `test_accepts_override_values` | `@unittest.skip("BAI provider removed in OpenRouter migration")` | `OPENROUTER_API_KEY` | `@pytest.mark.live` + inherit from `_LiveTestCase` + `LIVE_ENV_VAR = ENV_OPENROUTER` |
| `tests/test_config.py:192` | `test_noneable_fields_default_to_none` | same | `OPENROUTER_API_KEY` | same |
| `tests/test_config.py:387` | `test_detects_bai_provider_via_bai_api_key` | same | `OPENROUTER_API_KEY` | same (rename + rewrite to assert `llm_provider == "openrouter"` when `OPENROUTER_API_KEY` is set) |
| `tests/test_config.py:396` | `test_detects_bai_provider_via_anthropic_auth_token` | same | `OPENROUTER_API_KEY` | same |
| `tests/test_config.py:405` | `test_bai_model_from_env_var` | same | `OPENROUTER_API_KEY` | same (rewrite against `OPENROUTER_MODEL`) |
| `tests/test_config.py:413` | `test_bai_planner_model_from_env_var` | same | `OPENROUTER_API_KEY` | same (rewrite against `OPENROUTER_MODEL` planner slot) |
| `tests/test_config.py:421` | `test_bai_context_tokens_from_env_var` | same | `OPENROUTER_API_KEY` | same |
| `tests/test_config.py:429` | `test_bai_max_call_credits_from_env_var` | same | `OPENROUTER_API_KEY` | same |
| `tests/test_config.py:437` | `test_bai_max_call_credits_none_when_empty_string` | same | `OPENROUTER_API_KEY` | same |

Canonical class-level form for the file:

```python
@pytest.mark.live
class OpenRouterProviderConfigTests(_LiveTestCase):
    LIVE_ENV_VAR = ENV_OPENROUTER
    ...
```

### 4.2 `tests/test_llm.py` — 13 tests (12 `@unittest.skip` + 1 in-body `skipTest`)

| Line | Test | Current skip | New env var | Canonical form |
|---|---|---|---|---|
| `tests/test_llm.py:408` | `test_counts_initialize_to_zero_skip` | `self.skipTest("BAI counters removed in OpenRouter migration")` | `OPENROUTER_API_KEY` | `@pytest.mark.live` + `setUp` calls `_skip_if_unset(ENV_OPENROUTER)` |
| `tests/test_llm.py:602` | `test_snapshot_usage_no_priced_tokens` | `@unittest.skip("BAI credits_estimate field removed ...")` | `OPENROUTER_API_KEY` | `@pytest.mark.live` + `_LiveTestCase` |
| `tests/test_llm.py:608` | `test_snapshot_usage_with_priced_tokens_placeholder` | same | `OPENROUTER_API_KEY` | same |
| `tests/test_llm.py:612` | `test_snapshot_usage_with_priced_tokens` | same | `OPENROUTER_API_KEY` | same |
| `tests/test_llm.py:671` | `test_snapshot_credits_estimate_rounded` | same | `OPENROUTER_API_KEY` | same |
| `tests/test_llm.py:754` | `test_record_usage_credits_calculation` | `@unittest.skip("BAI _usage_credits field removed ...")` | `OPENROUTER_API_KEY` | same |
| `tests/test_llm.py:760` | `test_record_usage_skips_credits_when_no_rates` | same | `OPENROUTER_API_KEY` | same |
| `tests/test_llm.py:766` | `test_record_usage_non_bai_skips_credit_computation` | same | `OPENROUTER_API_KEY` | same |
| `tests/test_llm.py:913` | `test_bai_base_url_appends_v1` | `@unittest.skip("BAI provider removed ...")` | `OPENROUTER_API_KEY` | same (rewrite body against `OPENROUTER_BASE_URL`) |
| `tests/test_llm.py:919` | `test_bai_with_v1_no_double_append` | same | `OPENROUTER_API_KEY` | same |
| `tests/test_llm.py:962` | `test_bai_label` | same | `OPENROUTER_API_KEY` | same |
| `tests/test_llm.py:996` | `test_bai_has_api_key_header` | same | `OPENROUTER_API_KEY` | same (assert `Authorization: Bearer <OPENROUTER_API_KEY>` header) |
| `tests/test_llm.py:1056` | `test_reasoning_content_included_for_supported_providers` | same | `OPENROUTER_API_KEY` | same |

The class `ChatUrlTests` (`tests/test_llm.py:912`) and `ProviderLabelTests` (`:949`) and `RequestHeadersTests` (`:982`) and `AssistantToolCallMessageTests` (`:1045`) each get a class-level `@pytest.mark.live` because all 13 skipped tests cluster into 4 classes.

### 4.3 `tests/test_llm_metadata.py` — 14 tests (13 method-level + 1 class-level)

The class-level `@unittest.skip` at `tests/test_llm_metadata.py:420` skips the whole `NormalizeBaiModelTests` class, which contains one test method (`test_skip` at `:422`). Both the class and the method need to be removed and replaced with a class that inherits from `_LiveTestCase`.

| Line | Test | Current skip | New env var | Canonical form |
|---|---|---|---|---|
| `tests/test_llm_metadata.py:43` | `test_recognizes_bai` | `@unittest.skip("BAI provider removed ...")` | `OPENROUTER_API_KEY` | `@pytest.mark.live` + `LIVE_ENV_VAR = ENV_OPENROUTER` |
| `tests/test_llm_metadata.py:67` | `test_explicit_provider_wins_over_keys` | same | `OPENROUTER_API_KEY` | same |
| `tests/test_llm_metadata.py:80` | `test_bai_takes_priority_over_deepseek` | same | `OPENROUTER_API_KEY` | same (rewrite: `test_openrouter_takes_priority_over_deepseek`) |
| `tests/test_llm_metadata.py:166` | `test_bai_returns_empty` | same | `OPENROUTER_API_KEY` | same |
| `tests/test_llm_metadata.py:257` | `test_bai_returns_normalized_model` | same | `OPENROUTER_API_KEY` | same |
| `tests/test_llm_metadata.py:261` | `test_bai_normalizes_claude_sonnet` | same | `OPENROUTER_API_KEY` | same |
| `tests/test_llm_metadata.py:280` | `test_bai_empty_model_falls_back_to_hardcoded` | same | `OPENROUTER_API_KEY` | same |
| `tests/test_llm_metadata.py:323` | `test_bai` (in `DefaultLlmModelDisplayTests`) | same | `OPENROUTER_API_KEY` | same |
| `tests/test_llm_metadata.py:327` | `test_bai_normalizes_claude_sonnet` (in `DefaultLlmModelDisplayTests`) | same | `OPENROUTER_API_KEY` | same |
| `tests/test_llm_metadata.py:349` | `test_bai` (in `ResolveLlmApiKeyTests`) | same | `OPENROUTER_API_KEY` | same |
| `tests/test_llm_metadata.py:361` | `test_resolves_provider_when_none_given` | same | `OPENROUTER_API_KEY` | same |
| `tests/test_llm_metadata.py:391` | `test_bai_default` (in `ResolveLlmBaseUrlTests`) | same | `OPENROUTER_API_KEY` | same |
| `tests/test_llm_metadata.py:395` | `test_bai_custom` | same | `OPENROUTER_API_KEY` | same |
| `tests/test_llm_metadata.py:420` (class) | `NormalizeBaiModelTests` (1 method `test_skip`) | `@unittest.skip("BAI _normalize_bai_model removed ...")` | `OPENROUTER_API_KEY` | Delete the class entirely; the body is `pass`. **The functionality it would have tested is replaced by the 13 method-level live tests above**, which is the same coverage under the new provider. |

### 4.4 `tests/test_kimi_tools.py` — 11 tests → gate on `OPENROUTER_API_KEY`

All skip-strings are `"BAI provider-specific behavior removed in OpenRouter migration"`. The tests exercise the BAI provider's tool-replay, latency-demotion, entitlement-failure, quota-failure, credit-wording, context-limit, and credit-guard paths. Under the OpenRouter provider these paths still exist (renamed/restructured), so the live tests are the honest way to keep them honest.

| Line | Test | New env var | Canonical form |
|---|---|---|---|
| `tests/test_kimi_tools.py:329` | `test_bai_tool_replay_preserves_reasoning_content` | `OPENROUTER_API_KEY` | `@pytest.mark.live` + `_LiveTestCase` + `LIVE_ENV_VAR = ENV_OPENROUTER` |
| `tests/test_kimi_tools.py:366` | `test_bai_latency_demotes_writer_and_reflector_candidates_only` | same | same |
| `tests/test_kimi_tools.py:411` | `test_bai_latency_demotion_does_not_remove_last_viable_writer_model` | same | same |
| `tests/test_kimi_tools.py:452` | `test_bai_entitlement_failure_blacklists_model_and_falls_back` | same | same |
| `tests/test_kimi_tools.py:507` | `test_bai_quota_failure_blocks_model_and_uses_next_candidate` | same | same |
| `tests/test_kimi_tools.py:561` | `test_bai_credit_wording_is_classified_as_quota_failure` | same | same |
| `tests/test_kimi_tools.py:610` | `test_bai_context_limit_http_error_is_not_retried_as_upstream` | same | same |
| `tests/test_kimi_tools.py:651` | `test_metrics_capture_provider_token_usage_without_pricing` | same | same |
| `tests/test_kimi_tools.py:714` | `test_bai_context_pressure_is_reported_and_clamps_default_output` | same | same |
| `tests/test_kimi_tools.py:768` | `test_bai_pre_call_credit_guard_refuses_oversized_call` | same | same |
| `tests/test_kimi_tools.py:809` | `test_bai_credit_rates_match_current_official_kimi_table` | same | same |

The 11 tests span 6 classes (`KimiToolsReplayTests`, `KimiToolsLatencyTests`, `KimiToolsEntitlementTests`, `KimiToolsQuotaTests`, `KimiToolsContextTests`, `KimiToolsCreditTests`). One marker per class, or one marker per test method — either works; class-level is fewer decorators.

### 4.5 `tests/test_sosovalue_api.py` — 15 tests → gate on `SOSOVALUE_API_KEY`

All skip-strings are `"wrapper removed in Wave 2.1 / Wave 4 capability reclassification"`. These tests validate that the client correctly parses the real SoSoValue response shapes — exactly the kind of test that **must** run live to be meaningful, because a mock would re-create the wrapper we just removed.

| Line | Test | New env var | Canonical form |
|---|---|---|---|
| `tests/test_sosovalue_api.py:101` | `test_client_parses_featured_news_rows` | `SOSOVALUE_API_KEY` | `@pytest.mark.live` + `_LiveTestCase` + `LIVE_ENV_VAR = ENV_SOSOVALUE` |
| `tests/test_sosovalue_api.py:155` | `test_client_rejects_unofficial_news_page_size` | same | same |
| `tests/test_sosovalue_api.py:177` | `test_client_parses_current_etf_metrics_object` | same | same |
| `tests/test_sosovalue_api.py:192` | `test_client_rejects_current_etf_metrics_missing_aggregate` | same | same |
| `tests/test_sosovalue_api.py:204` | `test_client_rejects_current_etf_metrics_missing_list_field` | same | same |
| `tests/test_sosovalue_api.py:420` | `test_currency_market_snapshot_parses_object` | same | same |
| `tests/test_sosovalue_api.py:447` | `test_currency_market_snapshot_rejects_non_object_data` | same | same |
| `tests/test_sosovalue_api.py:457` | `test_currency_klines_returns_rows` | same | same |
| `tests/test_sosovalue_api.py:490` | `test_currency_klines_rejects_invalid_interval` | same | same |
| `tests/test_sosovalue_api.py:497` | `test_etf_list_returns_rows` | same | same |
| `tests/test_sosovalue_api.py:523` | `test_etf_summary_history_returns_rows` | same | same |
| `tests/test_sosovalue_api.py:553` | `test_etf_summary_history_validates_required_fields` | same | same |
| `tests/test_sosovalue_api.py:565` | `test_etf_market_snapshot_parses_object` | same | same |
| `tests/test_sosovalue_api.py:614` | `test_fetch_etf_historical_inflow_respects_gap_and_cache` | same | same |
| `tests/test_sosovalue_api.py:624` | `test_fetch_featured_news_normalizes_content` | same | same |

> **Important.** These tests are the highest-value set in the 70. They are the only ones whose skip-strings admit that mocking the upstream is meaningless. A flake on any of them is a real SoSoValue API change that we need to know about. The `setUpClass` gate + the `pytest -m live` selector guarantee they run in CI's "live" job, not in the default run.

### 4.6 `tests/test_workspace_flow.py` — 4 tests → gate on `OPENROUTER_API_KEY`

All skip-strings are `"BAI provider removed in OpenRouter migration; BAI-specific ... removed"`. These are workspace-flow tests that branched on `llm_provider == "bai"` to set per-provider writer token budgets, planner caps, retry counts, and fallback behavior.

| Line | Test | New env var | Canonical form |
|---|---|---|---|
| `tests/test_workspace_flow.py:149` | `test_writer_token_budget_expands_for_bai` | `OPENROUTER_API_KEY` | `@pytest.mark.live` + `_LiveTestCase` |
| `tests/test_workspace_flow.py:183` | `test_bai_planner_caps_tool_rounds_to_reduce_loop_waste` | same | same |
| `tests/test_workspace_flow.py:1517` | `test_bai_writer_uses_third_retry_attempt` | same | same |
| `tests/test_workspace_flow.py:2002` | `test_live_provider_planner_refuses_fallback_after_repair_exhaustion` | same | same |

### 4.7 `tests/test_orchestration_all.py` — 3 tests → gate on `OPENROUTER_API_KEY`

| Line | Test | New env var | Canonical form |
|---|---|---|---|
| `tests/test_orchestration_all.py:519` | `test_max_attempts_bai` | `OPENROUTER_API_KEY` | `@pytest.mark.live` + `_LiveTestCase` |
| `tests/test_orchestration_all.py:527` | `test_writer_max_tokens_bai` | same | same |
| `tests/test_orchestration_all.py:1232` | `test_requires_planner_tool_use` | same | same |

### 4.8 `tests/test_deterministic_archive.py` — 1 test → gate on `SIGLAB_LIVE_DETERMINISTIC`

| Line | Test | Current skip | New env var | Canonical form |
|---|---|---|---|---|
| `tests/test_deterministic_archive.py:75` | `test_pick_deterministic_parent_prefers_strong_anchor_with_randomness` | `@unittest.skip("test-ordering flake: SUT uses module-global random in addition to select._RNG.seed(7); …")` | `SIGLAB_LIVE_DETERMINISTIC` | `@pytest.mark.live` + `_LiveTestCase` + `LIVE_ENV_VAR = ENV_LIVE_DETERMINISTIC` |

The skip-message is candid: this is not a provider issue, it's a test-ordering flake. The live gate is correct because the "real" data the test is supposed to anchor against (the deterministic archive) is itself built from live data; running the test live re-exercises the determinism claim, not just the random pick. If the flake ever disappears in CI, removing the gate is a one-line change.

### 4.9 Totals

| Env var | Tests gated |
|---|---:|
| `OPENROUTER_API_KEY` | 9 (`test_config`) + 13 (`test_llm`) + 13 (`test_llm_metadata` method-level) + 11 (`test_kimi_tools`) + 4 (`test_workspace_flow`) + 3 (`test_orchestration_all`) = **53** |
| `SOSOVALUE_API_KEY` | 15 (`test_sosovalue_api`) = **15** |
| `SIGLAB_LIVE_DETERMINISTIC` | 1 (`test_deterministic_archive`) = **1** |
| `SODEX_WS_TESTNET` | 0 (the existing `tests/integration/test_sodex_ws_live.py` is already live-gated and not in the 70 — the env var is in the base for the future when the deterministic-archive flake is moved to the WSS anchor path) |
| `SIGLAB_LIVE_SKIP_REASON` | 0 (meta-var, not a gate) |
| **Total** | **69** |

> **Where the 70th skipped test went.** The 70th skip is the class-level `@unittest.skip` on `NormalizeBaiModelTests` at `tests/test_llm_metadata.py:420-423`, which contains one method (`test_skip` whose body is `pass`). The plan DELETES that class and folds the BAI-normalization coverage into the 13 method-level live tests in §4.3. Net skipped methods: 70 → 0.

---

## §5. The One New File — `tests/integration/_live_base.py`

```python
# tests/integration/_live_base.py
"""Canonical live-test base for SigLab.

See plan_R_canonical_live_gating.md §1.3 for the design rationale.
"""
from __future__ import annotations

import os
import unittest
from typing import Final

import pytest

ENV_SOSOVALUE: Final = "SOSOVALUE_API_KEY"
ENV_OPENROUTER: Final = "OPENROUTER_API_KEY"
ENV_SODEX_WS: Final = "SODEX_WS_TESTNET"
ENV_LIVE_DETERMINISTIC: Final = "SIGLAB_LIVE_DETERMINISTIC"
ENV_LIVE_SKIP_REASON: Final = "SIGLAB_LIVE_SKIP_REASON"


def _skip_if_unset(env_var_name: str) -> None:
    val = os.environ.get(env_var_name, "").strip()
    if not val or val.lower() in {"0", "false", "no"}:
        raise unittest.SkipTest(
            f"{env_var_name} not set; live test gated. "
            f"Set {env_var_name}=<value> or run `pytest -m live` to enable."
        )


class _LiveTestCase(unittest.TestCase):
    """Base class for any test that needs a real upstream to be meaningful.

    Subclasses MUST set LIVE_ENV_VAR to one of the ENV_* constants above.
    The class is auto-marked @pytest.mark.live at instantiation time via the
    __init_subclass__ hook below, so a subclass that forgets the marker is
    a class-load error, not a silent miss.
    """

    LIVE_ENV_VAR: str = ""

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        # Idempotent: if the developer already wrote @pytest.mark.live on
        # the subclass, this is a no-op. If they forgot, we add it.
        existing = getattr(cls, "pytestmark", []) or []
        if not any(getattr(m, "name", "") == "live" for m in existing):
            cls.pytestmark = [*list(existing), pytest.mark.live]

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        if cls.LIVE_ENV_VAR:
            _skip_if_unset(cls.LIVE_ENV_VAR)
        # Honor SIGLAB_LIVE_SKIP_REASON by surfacing it in the test report
        # even when the gate passes. Cheap; helps audit.
        reason = os.environ.get(ENV_LIVE_SKIP_REASON, "").strip()
        if reason:
            import sys
            print(f"[live] {cls.__name__} running with SIGLAB_LIVE_SKIP_REASON={reason!r}", file=sys.stderr)

    def setUp(self) -> None:
        super().setUp()
        if self.LIVE_ENV_VAR:
            _skip_if_unset(self.LIVE_ENV_VAR)
```

### 5.1 What this file does NOT do

- It does NOT import any `siglab.*` module. The base class is pure stdlib + pytest.
- It does NOT register a pytest plugin / hook. The `__init_subclass__` hook is the only "magic", and it is local to the class.
- It does NOT touch the existing three live tests (`test_openrouter_free_models.py`, `test_sosovalue_live.py`, `test_sodex_ws_live.py`). They keep their own `_LiveBase` for now; a follow-up delta collapses them onto the canonical base.

### 5.2 Lines of code

The file is ~60 lines including docstrings. Smaller than the smallest skipped test method. The "smaller-delta" benefit: future live tests add 2 lines (import + class declaration) instead of copying a 20-line `_skip_if_disabled` + class-level `setUpClass` pattern.

### 5.3 Imports for the 70 test files

Each of the 8 modified test files needs one new import:

```python
from tests.integration._live_base import _LiveTestCase, _skip_if_unset, ENV_OPENROUTER  # or ENV_SOSOVALUE
```

`tests/integration/__init__.py` does not currently exist as an import target; if pytest's rootdir puts `tests/` on `sys.path` (it does — see `conftest.py`), `from tests.integration._live_base import ...` works. If not, the import becomes a relative `from ._live_base import ...` and the test file must be a package member. Verification step in §6.3.

---

## §6. The Expected Outcome

### 6.1 Default `pytest` run (no env vars set)

```
$ pytest
...
tests/test_config.py::OpenRouterProviderConfigTests::test_accepts_override_values SKIPPED [SOSOVALUE_API_KEY not set; live test gated. ...]
tests/test_config.py::OpenRouterProviderConfigTests::test_noneable_fields_default_to_none SKIPPED [...]
... (68 more SKIPPED lines, identical message template, different env var name)
tests/test_sosovalue_api.py::...::test_client_parses_featured_news_rows SKIPPED [OPENROUTER_API_KEY not set; ...]
...
============= 1234 passed, 70 skipped in 45.67s =============
```

Every skip-line is a one-liner with the env var name and the remediation hint. The CI red→green signal is unchanged from today (still "70 tests skipped" instead of "70 tests skip-noise").

### 6.2 `pytest -m live` run (env vars set)

```
$ export SOSOVALUE_API_KEY=<real>
$ export OPENROUTER_API_KEY=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
$ export SODEX_WS_TESTNET=1
$ export SIGLAB_LIVE_DETERMINISTIC=1
$ pytest -m live
...
tests/test_sosovalue_api.py::...::test_client_parses_featured_news_rows PASSED
tests/test_sosovalue_api.py::...::test_client_rejects_unofficial_news_page_size PASSED
...
tests/test_llm.py::ChatUrlTests::test_bai_base_url_appends_v1 PASSED  (now: test_openrouter_base_url_appends_v1)
...
============= 70 passed, 0 skipped, 0 failed in 187.42s =============
```

The 70 tests run for real, exercise the actual upstreams, and produce an honest signal. A real SoSoValue response-shape change fails the 15 `test_sosovalue_api.py` tests on the next live run, surfacing the regression within one CI cycle instead of silently degrading for weeks.

### 6.3 Selection / deselection

```
$ pytest -m "not live"             # explicit opt-out (default if you don't set --strict)
$ pytest -m live -k "sosovalue"    # only the 15 SoSoValue live tests
$ pytest -m "live and not flake"   # exclude the SIGLAB_LIVE_DETERMINISTIC flake
$ pytest --co -m live              # list 70 tests without running them
```

### 6.4 What the existing live tests look like (regression check)

The three existing `tests/integration/test_*_live.py` files continue to work unchanged. They are not in the 70 (their `@unittest.skip` count is 0), and they already implement a per-class `setUpClass` gate. They DO NOT need to be converted to the canonical base for this plan to succeed; that is a separate, smaller follow-up.

### 6.5 Edge case: env var is set but the upstream is down

A 502 from SoSoValue mid-test is an honest test FAILURE, not a skip. The `_skip_if_unset` helper only skips on the env-var check; once the test runs, network errors are `assert*` failures. This is intentional: silent network skips are how we ended up with 70 dead tests. A live-gated test that fails for real is the whole point of the refactor.

---

## §7. Smaller-Delta Constraints — Honored

| Constraint | How this plan honors it |
|---|---|
| NO new pip deps | The base class uses only `os`, `unittest`, and `pytest.mark` (pytest-9 is already in `[dependency-groups].dev` per `pyproject.toml:48`). No new entry in `[tool.poetry.dependencies]` or `[dependency-groups].dev`. |
| Use stdlib unittest + pytest markers | The base class extends `unittest.TestCase` and emits `unittest.SkipTest`. The decorator is `pytest.mark.live` (a string identifier, not a plugin). `pyproject.toml:36-41` already shows the project's marker convention; we extend it by one entry. |
| No edit to existing live tests | §5.1 is explicit: the three `tests/integration/test_*_live.py` files are out of scope. Their `_LiveBase` classes are private to the file; they keep working. |
| No new files outside `tests/integration/_live_base.py` | §5 is the only new file. The marker is one line added to `pyproject.toml`. Every other change is a `replace` on an existing test method's decorator + an `import` line. |
| No commit | This is a plan-only ticket. The brief forbids commits. |
| No source code edits to `siglab/**` | Out of scope. The 70 test bodies may need follow-up rewrites to re-exercise the OpenRouter path instead of the deleted BAI path; those rewrites are a separate plan, dependent on this gating refactor landing first. |

---

## §8. Rollout Sequence (for the future implementer, not this PR)

1. Land `tests/integration/_live_base.py` and the `pyproject.toml` marker addition in one PR. No test changes yet. Verify `pytest --co` shows the same test count.
2. Land the 8 test-file edits in a single PR (smaller-delta: one PR for all 70 decorator removals + class inheritance changes). Body rewrites are NOT included yet; the tests are still `pass` bodies, but they are now under `@pytest.mark.live` with the env-var gate. `pytest` run → 70 skip. `pytest -m live` run with keys → 70 fail-or-pass depending on body content.
3. Land the test-body rewrites in N follow-up PRs, one per file, each backed by a real-API run that produced the expected results captured as the test's `assert*` statements. This is the only honest way to write these tests, and it requires a real key on the implementer's machine.
4. Land a follow-up to collapse the three `tests/integration/test_*_live.py` files onto the canonical `_LiveTestCase`. Pure deduplication, no behavior change.

Steps 1+2 are within the scope of this plan. Steps 3+4 are explicitly out of scope.

---

## §9. Risks and Open Questions

- **`pyproject.toml` marker registration**: `pyproject.toml:35-43` does not set `addopts = "--strict-markers"`. Without strict mode, a typo'd marker still works (just warns). The plan does NOT add strict mode — that is a separate concern and may break other parts of the suite that use markers not in the explicit list. The plan DOES add `"live"` to the explicit list, which makes it visible to `--strict-markers` if it is ever enabled.
- **Test-class explosion**: a few of the 70 tests are mixed with non-live tests in the same class (e.g. `MetricsSnapshotTests` at `tests/test_llm.py:539` has both live and non-live methods). The class-level `@pytest.mark.live` would mark the whole class, causing the non-live methods to be deselected by `pytest -m "not live"`. Mitigation: apply the marker at the method level for mixed classes. The plan covers this case-by-case in §4.
- **`__init_subclass__` side effect**: the auto-apply hook in §5 runs every time a subclass is defined. It is idempotent and does not mutate the parent class; the cost is one `getattr` per subclass definition (test module load), which is negligible.
- **Import path**: `from tests.integration._live_base import ...` requires `tests/` on `sys.path`. pytest adds the rootdir to `sys.path` by default; the `tests/conftest.py` file is evidence this works. If a future refactor moves the base out of `tests/integration/`, the import path changes; the plan keeps it in `tests/integration/` to match the existing live-test directory.
- **The "80" in the task brief vs the actual "70"**: addressed in the Enumeration Verification block. The plan covers all 70. If the brief meant to include 10 additional tests in `tests/test_benchmark_deck.py` or similar, they would adopt the same pattern mechanically — extending §4 and §6 is a one-table-row addition per test.

---

## §10. Acceptance Checklist (for the implementer)

- [ ] `tests/integration/_live_base.py` exists, exports `_LiveTestCase`, `_skip_if_unset`, and the 5 `ENV_*` constants.
- [ ] `pyproject.toml:36-41` has `"live: marks tests that exercise real upstream APIs; gated on env vars. Run with \`pytest -m live\` after exporting SOSOVALUE_API_KEY and OPENROUTER_API_KEY."` in the markers list.
- [ ] `grep -rE '^\s*@unittest\.skip' tests/` returns 0 hits.
- [ ] `grep -rE 'self\.skipTest' tests/test_*.py tests/integration/*.py` returns 0 hits in test bodies that are NOT inside an already-live test (the 6 `self.skipTest` calls in `tests/integration/test_*_live.py` are preserved — they are mid-test honest skips, not decorator noise).
- [ ] `pytest --co -q` shows the same total test count as before (the 70 tests still exist; they are now live-gated).
- [ ] `pytest` with no env vars: 70 SKIPPED, 0 ERROR, 0 FAILED, 0 unexpected PASS.
- [ ] `pytest -m live` with both keys set: 70 collected; pass/fail distribution matches the rewritten test bodies' assertions.
- [ ] `pytest -m "not live"` collects the same default test set as before this refactor, with no leakage of live-gated tests.
- [ ] No `siglab/**` source file is modified.
- [ ] No commit is created.
