"""Validation helpers for the planner subsystem."""

from __future__ import annotations

from typing import Any

from siglab.llm import ClaudeTool
from siglab.orchestration.contracts import PlannerOutput

from .planner_types import (
    ACTION_KEYWORDS,
    MAX_PLANNER_TOOL_CALLS,
    string_list,
    unique_strings,
)


def planner_semantic_issues(
    *,
    note_text: str,
    planner_contract: PlannerOutput,
    tools: list[ClaudeTool],
    trace: dict[str, Any],
    requires_tool_use: bool,
) -> list[str]:
    """Aggregate all semantic validation issues."""
    issues: list[str] = []
    issues.extend(semantic_note_issues(note_text=note_text, planner_contract=planner_contract))
    issues.extend(planner_tool_usage_issues(tools=tools, trace=trace, requires_tool_use=requires_tool_use))
    issues.extend(planner_probe_claim_issues(note_text=note_text, trace=trace))
    issues.extend(planner_probe_budget_issues(trace=trace))
    issues.extend(planner_total_tool_budget_issues(trace=trace))
    issues.extend(planner_finish_issues(trace=trace, note_text=note_text))
    return issues


def planner_finish_issues(*, trace: dict[str, Any], note_text: str) -> list[str]:
    issues: list[str] = []
    finish_reason = str(trace.get("response_finish_reason") or "").strip().lower()
    if finish_reason in {"length", "max_tokens"}:
        issues.append(f"planner_response_truncated:{finish_reason}")
    trace_error = str(trace.get("error") or "").strip()
    if trace_error:
        issues.append(f"planner_trace_error:{trace_error}")
    stripped = note_text.rstrip()
    if stripped.endswith(("-", "*", "1.", "2.", "3.", "4.", "5.")):
        issues.append("planner_note_ends_mid_list")
    return issues


def semantic_note_issues(
    *,
    note_text: str,
    planner_contract: PlannerOutput,
) -> list[str]:
    stripped = note_text.strip()
    issues: list[str] = []
    if len(stripped) < 80:
        issues.append("research_note_too_short")
    lowered = stripped.lower()
    if not any(keyword in lowered for keyword in ACTION_KEYWORDS):
        issues.append("no_clear_proposed_test")
    if not str(planner_contract.get("target_family") or "").strip():
        issues.append("no_target_family")
    if not str(planner_contract.get("must_answer") or "").strip():
        issues.append("no_concrete_question")
    if not str(planner_contract.get("informative_test") or "").strip():
        issues.append("no_informative_test")
    return issues


def planner_tool_usage_issues(
    *,
    tools: list[ClaudeTool],
    trace: dict[str, Any],
    requires_tool_use: bool,
) -> list[str]:
    if not tools or not requires_tool_use:
        return []
    try:
        tool_rounds_used = int(trace.get("tool_rounds_used") or 0)
    except (TypeError, ValueError):
        tool_rounds_used = 0
    tool_calls = trace.get("tool_calls")
    if tool_rounds_used > 0 or (isinstance(tool_calls, list) and len(tool_calls) > 0):
        return []
    return ["planner_did_not_call_workspace_or_probe_tool"]


def planner_probe_claim_issues(
    *,
    note_text: str,
    trace: dict[str, Any],
) -> list[str]:
    probe_names = {
        "probe_feature_forward_stats",
        "probe_spec_gate_impact",
        "compare_intended_vs_frozen_spec",
    }
    lowered = note_text.lower()
    mentioned = {
        probe_name
        for probe_name in probe_names
        if probe_name.lower() in lowered
    }
    if not mentioned:
        return []
    called = {
        str(call.get("name") or "").strip()
        for call in list(trace.get("tool_calls") or [])
        if isinstance(call, dict)
    }
    missing = sorted(mentioned - called)
    if not missing:
        return []
    return [f"planner_named_uncalled_probe:{probe_name}" for probe_name in missing]


def planner_probe_budget_issues(*, trace: dict[str, Any]) -> list[str]:
    exhausted: list[str] = []
    for call in list(trace.get("tool_calls") or []):
        if not isinstance(call, dict):
            continue
        result = call.get("result")
        if not isinstance(result, dict):
            continue
        if result.get("error") == "planner_probe_budget_exhausted":
            exhausted.append(str(result.get("probe_type") or call.get("name") or "probe"))
    if not exhausted:
        return []
    names = ",".join(unique_strings(exhausted))
    return [f"planner_probe_budget_exhausted:{names}"]


def planner_total_tool_budget_issues(*, trace: dict[str, Any]) -> list[str]:
    count = len(list(trace.get("tool_calls") or []))
    if count <= MAX_PLANNER_TOOL_CALLS:
        return []
    return [f"planner_tool_call_budget_exceeded:{count}>{MAX_PLANNER_TOOL_CALLS}"]


def merge_trace_tool_usage(
    planner_contract: PlannerOutput,
    *,
    trace: dict[str, Any],
) -> None:
    tool_names = trace_tool_names(trace)
    if not tool_names:
        return
    planner_contract["tools_used"] = unique_strings(
        [
            *string_list(planner_contract.get("tools_used")),
            *tool_names,
        ]
    )


def trace_tool_names(trace: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for call in list(trace.get("tool_calls") or []):
        if not isinstance(call, dict):
            continue
        name = str(call.get("name") or "").strip()
        if name:
            names.append(name)
    return unique_strings(names)


def should_disable_tools_for_repair(feedback: dict[str, Any] | None) -> bool:
    if not feedback:
        return False
    semantic_issues = [str(item) for item in list(feedback.get("semantic_issues") or [])]
    return any(
        issue.startswith(
            (
                "planner_trace_error:",
                "planner_response_truncated:",
                "planner_probe_budget_exhausted:",
                "planner_tool_call_budget_exceeded:",
                "planner_note_ends_mid_list",
            )
        )
        for issue in semantic_issues
    )
