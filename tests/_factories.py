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

    async def complete_json_messages(self, **kwargs: object) -> dict[str, object]:
        messages = list(kwargs["messages"])
        self.calls.append(messages)
        return {}


def make_sosovalue_envelope(rows: list[dict] | None = None) -> dict:
    return {"code": 0, "message": "success", "data": rows or []}

def make_soxdex_envelope(rows: list[dict] | None = None) -> dict:
    return {"data": rows or []}
