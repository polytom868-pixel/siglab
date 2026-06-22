"""Run context — lightweight dependency bag for live operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from siglab.data import ParquetLake
from siglab.data.deployment_store import DeploymentStore
from siglab.llm import ClaudeClient

if TYPE_CHECKING:
    from siglab.config import SiglabConfig


@dataclass
class RunContext:
    settings: Any
    lake: ParquetLake
    claude: ClaudeClient | None
    ancestry: DeploymentStore | None


def build_run_context(
    settings: SiglabConfig,
    *,
    require_claude: bool = True,
    require_ancestry: bool = True,
) -> RunContext:
    lake = ParquetLake(settings.data_lake_dir)
    claude = ClaudeClient(settings) if require_claude else None
    ancestry = DeploymentStore(settings.ancestry_db_path) if require_ancestry else None
    return RunContext(settings=settings, lake=lake, claude=claude, ancestry=ancestry)
