from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from siglab.io_utils import write_json
from siglab.llm import ClaudeClient
from siglab.strategy_semantics import gate_dimensions, motif_signature
from siglab.workspace.cards import dump_frontmatter, parse_frontmatter
from siglab.workspace.builder import WorkspaceSession


@dataclass
class ReflectionResult:
    lesson_card_path: Path
    trace_path: Path
    frontmatter: dict[str, Any]


class ReflectionRunner:
    REQUIRED_FIELDS = {
        "family",
        "verdict",
        "failure_mode",
        "why_parent_change_failed",
        "failed_motif_signature",
        "one_reusable_lesson",
        "one_next_test",
        "next_move",
        "do_not_repeat",
        "evidence_paths",
        "tracking_tags",
        "status",
    }

    def __init__(
        self,
        *,
        settings: Any,
        claude: ClaudeClient,
    ) -> None:
        self.settings = settings
        self.claude = claude

    async def run(
        self,
        *,
        session: WorkspaceSession,
        spec_hash: str,
        iteration_paths: dict[str, Any],
        evaluation_packet: dict[str, Any],
    ) -> ReflectionResult:
        skill_path = (
            self.settings.root_dir
            / ".agents"
            / "skills"
            / "siglab-post-run-reflector"
            / "SKILL.md"
        )
        system_prompt = skill_path.read_text() if skill_path.exists() else self._fallback_system_prompt()
        user_prompt = self._build_user_prompt(evaluation_packet=evaluation_packet)
        content = await self.claude.complete_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=700,
            timeout_s=max(self.settings.claude_timeout_s, 90.0),
            thinking_override="disabled",
            stage="reflector",
        )
        raw_content = content
        frontmatter, body, parse_error = self._parse_reflection(content)
        frontmatter = self._merged_frontmatter(frontmatter, evaluation_packet)
        body = self._retained_body(
            raw_content=raw_content,
            parsed_body=body,
            evaluation_packet=evaluation_packet,
        )
        content = dump_frontmatter(frontmatter, body)
        lesson_card_path = session.cards_dir / "reflections" / f"{spec_hash}.md"
        lesson_card_path.write_text(content)
        trace_path = iteration_paths["reflector_trace_path"]
        write_json(
            trace_path,
            {
                "stage": "reflector",
                "system_prompt_path": str(
                    (skill_path.relative_to(self.settings.root_dir) if skill_path.exists() else Path("embedded/fallback-reflector-prompt.md"))
                ),
                "raw_reflection": raw_content,
                "frontmatter_parse_error": parse_error,
                "saved_frontmatter": frontmatter,
                "saved_body": body,
                "claude_trace": dict(self.claude.last_trace or {}),
                "claude_exchange": dict(self.claude.last_exchange or {}),
            },
        )
        return ReflectionResult(
            lesson_card_path=lesson_card_path,
            trace_path=trace_path,
            frontmatter=frontmatter,
        )

    def _fallback_system_prompt(self) -> str:
        return "\n".join(
            [
                "You are SigLab's post-run reflector.",
                "Write one short decision memo with YAML frontmatter and a concise body.",
                "Preserve the most important failure mode, reusable lesson, and next move.",
                "Avoid generic language; name the exact motif or structural change.",
            ]
        )

    def _parse_reflection(self, content: str) -> tuple[dict[str, Any], str, str | None]:
        try:
            frontmatter, body = parse_frontmatter(content)
            return frontmatter, body, None
        except Exception as exc:  # pragma: no cover - defensive against malformed model YAML
            return {}, "", f"{type(exc).__name__}: {exc}"

    def _merged_frontmatter(
        self,
        frontmatter: dict[str, Any],
        evaluation_packet: dict[str, Any],
    ) -> dict[str, Any]:
        fallback = self._fallback_frontmatter(evaluation_packet)
        merged = dict(fallback)
        merged.update({str(key): value for key, value in dict(frontmatter or {}).items()})
        for key, fallback_value in fallback.items():
            if key not in merged or self._is_missing_value(merged[key]):
                merged[key] = fallback_value
        merged["family"] = str(merged.get("family") or "")
        merged["verdict"] = str(merged.get("verdict") or "informative_failure")
        merged["failure_mode"] = str(merged.get("failure_mode") or "needs_follow_up")
        merged["why_parent_change_failed"] = str(merged.get("why_parent_change_failed") or "")
        merged["failed_motif_signature"] = str(merged.get("failed_motif_signature") or "")
        merged["one_reusable_lesson"] = str(merged.get("one_reusable_lesson") or "")
        merged["one_next_test"] = str(merged.get("one_next_test") or "")
        merged["next_move"] = str(merged.get("next_move") or "")
        merged["status"] = str(merged.get("status") or "active")
        merged["do_not_repeat"] = self._string_list(merged.get("do_not_repeat"))
        merged["evidence_paths"] = self._string_list(merged.get("evidence_paths"))
        merged["tracking_tags"] = self._string_list(merged.get("tracking_tags"))
        return merged

    def _retained_body(
        self,
        *,
        raw_content: str,
        parsed_body: str,
        evaluation_packet: dict[str, Any],
    ) -> str:
        if parsed_body.strip():
            return parsed_body
        if raw_content.strip():
            return raw_content.strip()
        return self._fallback_body(evaluation_packet)

    def _string_list(self, value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
        if self._is_missing_value(value):
            return []
        return [str(value)]

    def _is_missing_value(self, value: Any) -> bool:
        return value is None or value == ""

    def _build_user_prompt(self, *, evaluation_packet: dict[str, Any]) -> str:
        return "\n".join(
            [
                "Write one short decision memo with YAML frontmatter and a very short body.",
                "The body must stay under about 100 words and should read like instructions for the next planner step, not a diary.",
                "Use the current run plus the recent-completed-runs context to name one exact failed or successful motif, one short do-not-repeat rule, and one directional next move.",
                "Do not recap every metric. Mention only the evidence that changes the recommendation.",
                "Do not give generic advice like 'use better regime discrimination'. Name the exact motif or exact change.",
                "",
                "## Evaluation Packet",
                json.dumps(evaluation_packet, indent=2, ensure_ascii=True, default=str),
            ]
        )

    def _fallback_frontmatter(self, evaluation_packet: dict[str, Any]) -> dict[str, Any]:
        summary = dict(evaluation_packet.get("summary") or {})
        family = str(evaluation_packet.get("family") or "")
        spec = dict(evaluation_packet.get("spec") or {})
        pre_audit = float(summary.get("pre_audit_canonical_total_return") or 0.0)
        verdict = "promising_but_fragile" if pre_audit > 0.0 else "informative_failure"
        parent_delta = dict(evaluation_packet.get("parent_delta") or {})
        failed_motif_signature = str(
            evaluation_packet.get("failed_motif_signature") or motif_signature(spec)
        )
        feature_list = [str(feature) for feature in list(spec.get("features") or [])]
        gate_dims = gate_dimensions(dict(spec.get("regime_gates") or {}))
        why_parent_change_failed = (
            f"Parent delta pre-audit return was {parent_delta.get('pre_audit_canonical_total_return_delta')}; "
            f"motif `{failed_motif_signature}` did not improve realized regime fit enough."
        )
        next_move = str(evaluation_packet.get("suggested_next_move") or "test_one_orthogonal_change")
        do_not_repeat = [f"repeat motif `{failed_motif_signature}`"]
        if gate_dims:
            do_not_repeat.append(
                f"reuse gate dimension `{gate_dims[0]}` without changing a non-regime axis"
            )
        return {
            "family": family,
            "verdict": verdict,
            "failure_mode": str(evaluation_packet.get("dominant_failure_mode") or "needs_follow_up"),
            "why_parent_change_failed": why_parent_change_failed,
            "failed_motif_signature": failed_motif_signature,
            "one_reusable_lesson": (
                f"Do not repeat `{failed_motif_signature}`; the failure came from the exact motif, not just loose thresholds."
            ),
            "one_next_test": next_move,
            "next_move": next_move,
            "do_not_repeat": do_not_repeat,
            "evidence_paths": list(evaluation_packet.get("evidence_paths") or []),
            "tracking_tags": [family, "reflection", *feature_list[:2]],
            "status": "active",
        }

    def _fallback_body(self, evaluation_packet: dict[str, Any]) -> str:
        summary = dict(evaluation_packet.get("summary") or {})
        parent_delta = dict(evaluation_packet.get("parent_delta") or {})
        spec = dict(evaluation_packet.get("spec") or {})
        recent_completed_runs = list(evaluation_packet.get("recent_completed_runs") or [])
        failed_motif_signature = str(
            evaluation_packet.get("failed_motif_signature") or motif_signature(spec)
        )
        recent_motifs = [
            str(item.get("motif_signature") or "")
            for item in recent_completed_runs
            if str(item.get("motif_signature") or "").strip()
        ]
        repeated_recently = failed_motif_signature in recent_motifs
        next_move = str(evaluation_packet.get("suggested_next_move") or "test_one_orthogonal_change")
        return "\n".join(
            [
                f"What changed: tried motif `{failed_motif_signature}` against parent delta {parent_delta.get('pre_audit_canonical_total_return_delta')}.",
                (
                    "Why it failed/worked: "
                    f"pre-audit return was {summary.get('pre_audit_canonical_total_return')}; "
                    "the change did not improve realized regime fit enough."
                ),
                (
                    "Do not repeat: "
                    + (
                        f"motif `{failed_motif_signature}` again without a material structural change."
                        if repeated_recently
                        else f"motif `{failed_motif_signature}` without changing the feature class or gate dimension."
                    )
                ),
                f"Next test: {next_move}",
            ]
        )


