# Lesson 2 — Reset Helpers

**Lesson**: Microchange waves run dozens of edits against shared state (sqlite lineage, env vars, temp config files, in-process caches). Without a deterministic reset between applies, later changes inherit leaked state from earlier ones, and the verifier's view of "the current code" is a lie.

**Rule**: Every test file touched by a microchange wave MUST expose an autouse fixture that resets the module's mutable state to a known baseline before each test runs. No global `setup_module` / `teardown_module` — those don't fire between tests.

## autouse fixture template

```python
import pytest


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    """Reset all module-level mutable state before this test runs."""
    # 1. Reload the module under test so module-level singletons are fresh.
    import importlib
    import <target_module>
    importlib.reload(<target_module>)

    # 2. Clear any sqlite / on-disk stores the module owns.
    from <target_module> import _LINEAGE_STORE  # adjust name
    _LINEAGE_STORE.clear()  # or .reset() / truncate / etc.

    # 3. Wipe env vars the module reads at import or first-call time.
    monkeypatch.delenv("SIGLAB_CONFIG_PATH", raising=False)
    monkeypatch.delenv("SOSOVALUE_CONFIG_PATH", raising=False)

    yield

    # 4. Optional teardown — most resets are "before" only.
    _LINEAGE_STORE.clear()
```

Required elements:

1. **`autouse=True`** — applies to every test in the file without opt-in. The reset is the default, not a per-test decision.
2. **`monkeypatch` for env** — never mutate `os.environ` directly. Monkeypatch auto-reverts at test teardown.
3. **`importlib.reload`** — covers module-level singletons that were captured at import time. This is the only safe way to get a fresh instance in CPython.
4. **Explicit store reset** — call the store's own `clear()` / `reset()` method, don't drop and recreate the file (that races with parallel test runs).
5. **`yield` for teardown symmetry** — keep teardown minimal; the next test's "before" is what matters.

## Failure mode this lesson prevents

Lesson 2 was learned when an apply step's test passed locally, then failed in the verifier lane with a `KeyError` from a sqlite row inserted by a sibling test two files over. The module-level `LineageStore` was a singleton; test ordering leaked rows. With `_reset_module_state` autouse, every test starts from an empty lineage and ordering stops mattering. Without it, microchange waves produce flaky "fixes" that are really just "this test happens to run before that one."

## How to apply

- When you touch a test file: scan its imports for module-level singletons (classes instantiated at import time, caches keyed on disk paths, env reads). If any are reachable from the tests you're changing, add the autouse fixture.
- When you touch a production file: check whether it owns a singleton. If it does and the test file does not already reset it, your change is a chance to add the fixture.
- Do not rely on `pytest-randomly`-style ordering protection. Determinism is a property of the test, not the runner.
