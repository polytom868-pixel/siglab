from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from siglab.config import SiglabConfig
from siglab.orchestration.writer_runner import SpecWriterRunner
from siglab.search.lineage import LineageStore
from siglab.search.mutate import SpecMutator
from siglab.workspace import WorkspaceBuilder


def make_minimal_settings(**overrides) -> SiglabConfig:
    base = SiglabConfig(
        root_dir=Path("/tmp"),
        sosovalue_config_path=Path("/tmp/config.json"),
        generated_strategy_dir=Path("/tmp/deployed_agents"),
        data_lake_dir=Path("/tmp"),
        artifact_dir=Path("/tmp"),
        live_dir=Path("/tmp/live"),
        ancestry_db_path=Path("/tmp/siglab_test.db"),
        sosovalue_api_key_override=None,
        claude_api_key=None,
        claude_model="claude-k2.5",
        claude_base_url="https://api.moonshot.ai/v1",
        claude_max_tokens=1024,
        claude_temperature=1.0,
        claude_top_p=0.95,
        claude_timeout_s=30.0,
        population_size=1,
        llm_provider="bai",
        bai_api_key="sk-test",
        bai_base_url="https://api.b.ai",
        bai_model="deepseek-v4-flash",
    )
    return replace(base, **overrides)


def make_workspace_triple(settings=None):
    settings = settings or make_minimal_settings()
    ancestry = LineageStore(settings.ancestry_db_path)
    mutator = SpecMutator(settings, MagicMock())
    builder = WorkspaceBuilder(settings=settings, ancestry=ancestry, mutator=mutator)
    return ancestry, mutator, builder


def make_runner(**overrides):
    settings = SimpleNamespace(
        root_dir=Path("/fake/root"),
        claude_timeout_s=90,
        llm_provider="test",
        **overrides,
    )
    runner = object.__new__(SpecWriterRunner)
    runner.settings = settings
    runner.claude = MagicMock()
    runner.mutator = MagicMock()
    runner.hypothesis_sandbox = None
    return runner


class FakeClaude:
    def __init__(self) -> None:
        self.calls: list[list[dict[str, str]]] = []
        self.last_trace = {"ok": True}
        self.last_exchange = {"ok": True}
        # Canned response values (set by make_fake_claude)
        self._text_return: str = ""
        self._json_return: dict[str, object] = {}
        self._metrics: dict[str, object] = {}

    async def complete_text_with_tools(self, **_kwargs: object) -> str:
        return self._text_return

    async def complete_text(self, **_kwargs: object) -> str:
        return self._text_return

    async def complete_json_messages(self, **kwargs: object) -> dict[str, object]:
        messages = list(kwargs["messages"])
        self.calls.append(messages)
        return self._json_return

    def metrics_snapshot(self) -> dict[str, object]:
        return self._metrics


def make_sosovalue_envelope(rows: list[dict] | None = None) -> dict:
    return {"code": 0, "message": "success", "data": rows or []}


def make_mock_settings(**overrides) -> MagicMock:
    """Build a mock SiglabConfig with the same field defaults as
    make_minimal_settings() but as a MagicMock (faster construction,
    no validation). Use when tests don't introspect the Settings type.
    Replaces the 4 inline _make_mock_settings() copies in test_evaluator_core,
    test_evaluator_compile, test_evaluator_engine, test_evaluator_backtesting.
    """
    settings = MagicMock()
    settings.root_dir = "/tmp"
    settings.sosovalue_config_path = "/tmp/soso.json"
    settings.generated_strategy_dir = "/tmp/strategies"
    settings.data_lake_dir = "/tmp/lake"
    settings.artifact_dir = "/tmp/artifacts"
    settings.live_dir = "/tmp/live"
    settings.ancestry_db_path = "/tmp/ancestry.db"
    settings.sosovalue_api_key_override = None
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


# Common YAML return value used by FakeClaude.complete_text_with_tools in test_workspace_flow.
# Centralized so 14 inline copies (~37 lines each) become a single import.
_REFINE_CARRY_YAML = """---
decision: refine_current_family
search_mode: branch_same_family
target_family: perp_multi_asset_carry
target_universe: [BTC, ETH, SOL, HYPE]
core_hypothesis: generic top-level note
informative_test: generic top-level test
expected_success: [better validation robustness]
expected_failure: [no measurable change]
evidence_paths: []
tools_used: []
tracking_tags: [perp_multi_asset_carry]
must_answer: Does one concrete regime discriminator improve pre-audit return without making validation negative for `perp_multi_asset_carry`?
required_feature_roles: [one core_carry feature, one orthogonal_regime feature]
forbidden_motifs: [second pure trend overlay]
gate_intent: {}
writer_inputs: [manifests/family/perp_multi_asset_carry.md]
---

```yaml
---
target_family: perp_multi_asset_carry
must_answer: Does adding a market_volatility_168h gate improve pre-audit return above 0.336 while keeping validation positive for `perp_multi_asset_carry`?
required_features:
  - funding_carry_to_vol
  - market_volatility_168h
required_gate_dimensions:
  - market_volatility_168h
forbidden_motifs:
  - perp_multi_asset_carry|unspecified|core_carry+funding+orthogonal_regime|funding_dispersion_72h
---
```

## Diagnosis
Use the embedded spec, not the generic one.
"""


def make_fake_claude(
    text_return: str = _REFINE_CARRY_YAML,
    json_return: dict[str, object] | None = None,
    metrics: dict[str, object] | None = None,
) -> FakeClaude:
    """Build a FakeClaude that records calls and returns canned responses.

    Centralizes the 14+ inline FakeClaude classes in test_workspace_flow.py
    and 3 in test_cli_agent_safety.py.
    """
    if json_return is None:
        json_return = {}
    if metrics is None:
        metrics = {
            "provider": "bai",
            "model": "deepseek-v4-flash",
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "credits_estimate": 0.3,
                "cost_usd": None,
            },
            "context_pressure": {"event_count": 0, "latest": None},
            "credit_pressure": {"event_count": 0, "latest": None},
        }
    fake = FakeClaude()
    fake._text_return = text_return
    fake._json_return = json_return if json_return is not None else {}
    fake._metrics = metrics if metrics is not None else FakeClaude()._metrics
    return fake


def make_lineage_store() -> LineageStore:
    """Build a LineageStore rooted at a tempdir / "ancestry.db".

    Helper for tests that previously did::

        with tempfile.TemporaryDirectory() as tmp:
            ancestry = LineageStore(Path(tmp) / "ancestry.db")

    Can be replaced with::

        ancestry = make_lineage_store()
    """
    import tempfile as _tempfile
    with _tempfile.TemporaryDirectory() as tmp:
        return LineageStore(Path(tmp) / "ancestry.db")
