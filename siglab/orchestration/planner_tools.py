"""Tool construction for the planner subsystem."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from siglab.llm import ClaudeTool
from siglab.research import HypothesisSandbox, WebResearcher
from siglab.tools import (
    inspect_feature,
    open_workspace_file,
    search_features,
    search_workspace,
    search_workspace_text,
    suggest_feature_set,
)
from siglab.workspace.builder import WorkspaceBuilder, WorkspaceSession

from .planner_types import MAX_PROBE_CALLS_PER_TOOL, MAX_PROBE_TOOL_CALLS


@dataclass
class ToolBudget:
    total: int = 0
    per_tool: dict[str, int] = field(default_factory=dict)


def build_planner_tools(
    *,
    session: WorkspaceSession,
    iteration_number: int,
    parent: Any,
    market_bundle: dict[str, Any],
    tool_refs: list[str],
    hypothesis_sandbox: HypothesisSandbox,
    web_researcher: WebResearcher,
    workspace_builder: WorkspaceBuilder,
) -> list[ClaudeTool]:
    tools: list[ClaudeTool] = []
    probe_budget = ToolBudget()

    tools.append(
        ClaudeTool(
            name="search_workspace",
            description="Search current-run workspace indexes and card metadata by semantic query and optional filters.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "kind": {"type": "string"},
                    "family": {"type": "string"},
                    "outcome": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                "required": ["query"],
            },
            handler=lambda arguments: search_workspace(
                workspace_root=session.root,
                query=str(arguments.get("query") or ""),
                kind=str(arguments.get("kind") or "") or None,
                family=str(arguments.get("family") or "") or None,
                outcome=str(arguments.get("outcome") or "") or None,
                limit=int(arguments.get("limit", 8)),
            ),
        )
    )
    tools.append(
        ClaudeTool(
            name="search_workspace_text",
            description=(
                "Search literal text inside current-run workspace files only. "
                "Use this for metric keys, exact snippets, or probe fields like median_spearman."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "path_glob": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                "required": ["query"],
            },
            handler=lambda arguments: search_workspace_text(
                workspace_root=session.root,
                query=str(arguments.get("query") or ""),
                path_glob=str(arguments.get("path_glob") or "") or None,
                limit=int(arguments.get("limit", 8)),
            ),
        )
    )
    tools.append(
        ClaudeTool(
            name="open_file",
            description="Open a workspace file by relative path, optionally extracting one heading section.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "section": {"type": "string"},
                    "max_chars": {"type": "integer", "minimum": 100, "maximum": 20000},
                },
                "required": ["path"],
            },
            handler=lambda arguments: open_workspace_file(
                workspace_root=session.root,
                path=str(arguments.get("path") or ""),
                section=str(arguments.get("section") or "") or None,
                max_chars=int(arguments.get("max_chars", 6000))
                if arguments.get("max_chars") is not None
                else None,
            ),
        )
    )
    tools.append(
        ClaudeTool(
            name="search_features",
            description="Search the generated feature catalog by semantic query and optional family/kind filters.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "family": {"type": "string"},
                    "kind": {"type": "string"},
                    "subkind": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                "required": ["query"],
            },
            handler=lambda arguments: search_features(
                workspace_root=session.root,
                query=str(arguments.get("query") or ""),
                family=str(arguments.get("family") or "") or None,
                kind=str(arguments.get("kind") or "") or None,
                subkind=str(arguments.get("subkind") or "") or None,
                limit=int(arguments.get("limit", 8)),
            ),
        )
    )
    tools.append(
        ClaudeTool(
            name="inspect_feature",
            description="Inspect one feature alias in the generated feature catalog.",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "family": {"type": "string"},
                },
                "required": ["name"],
            },
            handler=lambda arguments: inspect_feature(
                workspace_root=session.root,
                name=str(arguments.get("name") or ""),
                family=str(arguments.get("family") or "") or None,
            ),
        )
    )
    tools.append(
        ClaudeTool(
            name="suggest_feature_set",
            description="Suggest a small semantically diverse feature set from the feature catalog for a given family and hypothesis.",
            parameters={
                "type": "object",
                "properties": {
                    "family": {"type": "string"},
                    "hypothesis": {"type": "string"},
                    "avoid": {"type": "array", "items": {"type": "string"}},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 8},
                },
                "required": ["family", "hypothesis"],
            },
            handler=lambda arguments: suggest_feature_set(
                workspace_root=session.root,
                family=str(arguments.get("family") or ""),
                hypothesis=str(arguments.get("hypothesis") or ""),
                avoid=[str(item) for item in list(arguments.get("avoid") or [])],
                limit=int(arguments.get("limit", 4)),
            ),
        )
    )

    sandbox_tools = {
        tool.name: tool
        for tool in hypothesis_sandbox.claude_tools(
            track=session.track,
            parent=parent,
            memory_scope=session.memory_scope,
            run_session_id=session.run_session_id,
        )
        if tool.name in {
            "probe_feature_forward_stats",
            "probe_spec_gate_impact",
            "compare_intended_vs_frozen_spec",
        }
    }
    for tool_name in [
        "probe_feature_forward_stats",
        "probe_spec_gate_impact",
        "compare_intended_vs_frozen_spec",
    ]:
        tool = sandbox_tools.get(tool_name)
        if tool is None:
            continue
        tools.append(
            ClaudeTool(
                name=tool.name,
                description=tool.description,
                parameters=tool.parameters,
                handler=wrap_probe_tool(
                    session=session,
                    iteration_number=iteration_number,
                    parent=parent,
                    market_bundle=market_bundle,
                    tool=tool,
                    tool_refs=tool_refs,
                    probe_budget=probe_budget,
                    workspace_builder=workspace_builder,
                ),
            )
        )

    if web_researcher.is_configured:
        tools.append(
            ClaudeTool(
                name="web_search",
                description="Search the public web for current information.",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "max_results": {"type": "integer", "minimum": 1, "maximum": 5},
                    },
                    "required": ["query"],
                },
                handler=lambda arguments: web_researcher._tool_tavily_search(arguments),
            )
        )
        tools.append(
            ClaudeTool(
                name="web_fetch",
                description="Fetch and summarize a specific public URL.",
                parameters={
                    "type": "object",
                    "properties": {"url": {"type": "string"}},
                    "required": ["url"],
                },
                handler=lambda arguments: web_researcher._tool_web_fetch(arguments),
            )
        )

    tools.append(
        ClaudeTool(
            name="think",
            description="Write a short private reasoning note and return it unchanged.",
            parameters={
                "type": "object",
                "properties": {"note": {"type": "string"}},
                "required": ["note"],
            },
            handler=lambda arguments: {"ok": True, "note": str(arguments.get("note") or "")},
        )
    )
    return tools


def wrap_probe_tool(
    *,
    session: WorkspaceSession,
    iteration_number: int,
    parent: Any,
    market_bundle: dict[str, Any],
    tool: ClaudeTool,
    tool_refs: list[str],
    probe_budget: ToolBudget,
    workspace_builder: WorkspaceBuilder,
):
    async def _handler(arguments: dict[str, Any]) -> Any:
        tool_count = probe_budget.per_tool.get(tool.name, 0)
        total_count = probe_budget.total
        if total_count >= MAX_PROBE_TOOL_CALLS or tool_count >= MAX_PROBE_CALLS_PER_TOOL:
            return {
                "ok": False,
                "error": "planner_probe_budget_exhausted",
                "probe_type": tool.name,
                "total_probe_calls": total_count,
                "tool_probe_calls": tool_count,
                "max_total_probe_calls": MAX_PROBE_TOOL_CALLS,
                "max_probe_calls_per_tool": MAX_PROBE_CALLS_PER_TOOL,
            }
        probe_budget.total = total_count + 1
        probe_budget.per_tool[tool.name] = tool_count + 1
        outcome = tool.handler(arguments)
        result = await outcome if hasattr(outcome, "__await__") else outcome
        probe_ref = workspace_builder.record_probe(
            session=session,
            iteration_number=iteration_number,
            probe_type=tool.name,
            family=parent.family,
            universe=list(parent.universe.basis_groups),
            bundle_id=str(market_bundle.get("bundle_id") or ""),
            arguments=arguments,
            result=dict(result) if isinstance(result, dict) else {},
            tracking_tags=[parent.family, tool.name],
        )
        tool_refs.append(probe_ref)
        enriched = dict(result) if isinstance(result, dict) else {}
        enriched["workspace_probe_ref"] = probe_ref
        return enriched

    return _handler
