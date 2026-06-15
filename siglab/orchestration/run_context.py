from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from siglab.data import ParquetLake
from siglab.llm import ClaudeClient
from siglab.search import LineageStore


@dataclass
class RunContext:
    settings: Any
    lake: ParquetLake
    claude: ClaudeClient | None
    ancestry: LineageStore | None


def build_run_context(
    settings: Any,
    *,
    require_claude: bool = True,
    require_ancestry: bool = True,
) -> RunContext:
    lake = ParquetLake(settings.data_lake_dir)
    claude = ClaudeClient(settings) if require_claude else None
    ancestry = LineageStore(settings.ancestry_db_path) if require_ancestry else None
    return RunContext(
        settings=settings,
        lake=lake,
        claude=claude,
        ancestry=ancestry,
    )
