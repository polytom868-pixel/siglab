"""Shared types and constants for the planner subsystem."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class PlannerResult:
    research_note_path: Path
    planner_contract_path: Path
    trace_path: Path
    frontmatter: dict[str, Any]
    tool_refs: list[str]
    evidence_paths: list[str]
    repaired: bool = False


MAX_REPAIR_ATTEMPTS = 5
MAX_PLANNER_TOOL_CALLS = 24
MAX_PROBE_TOOL_CALLS = 8
MAX_PROBE_CALLS_PER_TOOL = 6

DEFAULT_FILES = (
    "RUNBOOK.md",
    "TASK.md",
    "WORKSPACE_INDEX.md",
    "current/SESSION_STATE.json",
    "current/frontier_brief.md",
    "current/market_brief.md",
    "current/parent_card.md",
    "current/families_index.md",
    "current/incumbent_spec.yaml",
    "current/family_incumbents.json",
    "current/recent_trials.md",
    "manifests/regime_catalog.md",
    "manifests/policy_surface.md",
    "manifests/features/feature_surface.md",
)

ACTION_KEYWORDS = (
    "test ",
    "try ",
    "use ",
    "switch ",
    "return to ",
    "branch ",
    "add ",
    "remove ",
    "replace ",
    "gate ",
    "suppress ",
    "what to test",
    "proposed next experiment",
    "next test",
)


def string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return unique_strings(str(item) for item in value if str(item).strip())


def dict_value(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def unique_strings(values: Any) -> list[str]:
    seen: set[str] = set()
    items: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
    return items
