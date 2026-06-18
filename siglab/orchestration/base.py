"""Shared lifecycle helpers for the orchestration runners."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from siglab.io_utils import write_json

if TYPE_CHECKING:
    from siglab.llm import ClaudeClient


class BaseRunner:
    """Common base for planner/writer/reflector runners."""

    def __init__(self, *, settings: Any, claude: "ClaudeClient") -> None:
        self.settings = settings
        self.claude = claude

    @property
    def _provider(self) -> str:
        return str(getattr(self.settings, "llm_provider", "") or "").strip().lower()

    @property
    def _is_bai(self) -> bool:
        return self._provider == "bai"

    def skill_dir(self, name: str) -> Path:
        return self.settings.root_dir / ".agents" / "skills" / name

    def skill_path(self, name: str) -> Path:
        return self.skill_dir(name) / "SKILL.md"

    def load_skill(self, name: str, *, fallback: str | None = None) -> tuple[str, Path]:
        path = self.skill_path(name)
        if path.exists():
            return path.read_text(), path
        if fallback is not None:
            return fallback, path
        return path.read_text(), path

    def relative_skill_path(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.settings.root_dir))
        except ValueError:
            return str(path)

    def claude_trace_fields(self) -> dict[str, Any]:
        return {
            "claude_trace": dict(self.claude.last_trace or {}),
            "claude_exchange": dict(self.claude.last_exchange or {}),
        }

    def write_trace(self, path: Path, payload: dict[str, Any]) -> None:
        write_json(path, {**payload, **self.claude_trace_fields()})

    @staticmethod
    def _dict_or_empty(value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {}

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
        if value is None or value == "":
            return []
        return [str(value)]

    @staticmethod
    def _is_missing_value(value: Any) -> bool:
        return value is None or value == ""


__all__ = ["BaseRunner"]
