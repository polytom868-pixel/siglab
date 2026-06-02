from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from siglab.config import SiglabConfig

router = APIRouter()

SIGLAB_VERSION = "0.1.0"

# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    """Return service health with status, version, and uptime."""
    state = request.app.state.dashboard
    uptime_s = time.time() - state.start_time
    return {
        "status": "ok",
        "version": SIGLAB_VERSION,
        "uptime_seconds": round(uptime_s, 3),
    }


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _config_to_dict(config: SiglabConfig) -> dict[str, Any]:
    """Convert SiglabConfig to a serializable dictionary, grouping by section."""
    return {
        "system": {
            "root_dir": str(config.root_dir),
            "data_lake_dir": str(config.data_lake_dir),
            "artifact_dir": str(config.artifact_dir),
            "live_dir": str(config.live_dir),
            "ancestry_db_path": str(config.ancestry_db_path),
            "generated_strategy_dir": str(config.generated_strategy_dir),
            "population_size": config.population_size,
            "llm_provider": config.llm_provider,
            "memory_scope": config.memory_scope,
            "tracks": list(config.tracks),
        },
        "sosovalue": {
            "config_path": str(config.sosovalue_config_path),
            "openapi_base_url": config.sosovalue_openapi_base_url,
            "etf_base_url": config.sosovalue_etf_base_url,
            "news_base_url": config.sosovalue_news_base_url,
            "timeout_s": config.sosovalue_timeout_s,
            "retries": config.sosovalue_retries,
            "api_key_configured": config.sosovalue_api_key_override is not None,
        },
        "claude": {
            "model": config.claude_model,
            "base_url": config.claude_base_url,
            "max_tokens": config.claude_max_tokens,
            "temperature": config.claude_temperature,
            "timeout_s": config.claude_timeout_s,
            "api_key_configured": config.claude_api_key is not None,
        },
    }


@router.get("/config")
async def get_config(request: Request) -> dict[str, Any]:
    """Return the full SiglabConfig as JSON."""
    state = request.app.state.dashboard
    if state.config is None:
        raise HTTPException(status_code=503, detail="Config not loaded")
    return _config_to_dict(state.config)


# ---------------------------------------------------------------------------
# Ops Board
# ---------------------------------------------------------------------------


def _load_artifact(root_dir: Path, relative_path: str) -> dict[str, Any]:
    """Load an ops artifact from a JSON file, returning status metadata."""
    path = (root_dir / relative_path).resolve()
    root = root_dir.resolve()
    if root not in path.parents and path != root:
        return {
            "status": "blocked",
            "path": relative_path,
            "error": "artifact path escapes repo root",
        }
    if not path.exists():
        return {
            "status": "missing",
            "path": relative_path,
            "error": "artifact missing",
        }
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return {
            "status": "malformed",
            "path": relative_path,
            "error": str(exc),
        }
    if not isinstance(payload, dict):
        return {
            "status": "malformed",
            "path": relative_path,
            "error": "artifact root must be a JSON object",
        }
    mtime = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
    age_seconds = max(0.0, (datetime.now(UTC) - mtime.astimezone(UTC)).total_seconds())
    return {
        "status": "present",
        "path": relative_path,
        "mtime": mtime.isoformat(),
        "age_seconds": round(age_seconds, 3),
        "freshness": (
            "fresh"
            if age_seconds <= 15 * 60
            else "stale"
            if age_seconds <= 24 * 60 * 60
            else "expired"
        ),
    }


def _summarize_ops_artifacts(
    artifacts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build the ops summary from loaded artifact payloads."""
    return {
        "buildathon_demo": {
            "demo_manifest": artifacts.get("demo_manifest", {}).get("status"),
            "telemetry_report": artifacts.get("telemetry", {}).get("status"),
            "market_report": artifacts.get("market_report", {}).get("status"),
            "sodex_preflight": artifacts.get("sodex_preflight", {}).get("status"),
            "wave_status": artifacts.get("wave_status", {}).get("status"),
        },
        "note": "Artifact summaries provide a high-level buildathon status overview.",
    }


@router.get("/ops-board")
async def ops_board(request: Request) -> dict[str, Any]:
    """Return consolidated ops-board data with artifact_status, summary, and service_health."""
    state = request.app.state.dashboard
    config = state.config
    if config is None:
        raise HTTPException(status_code=503, detail="Config not loaded")

    runs_dir = config.artifact_dir
    artifacts = {
        "demo_manifest": _load_artifact(runs_dir, "demo_manifest_latest.json"),
        "telemetry": _load_artifact(runs_dir, "latest_telemetry_report.json"),
        "market_report": _load_artifact(runs_dir, "market_report_latest.json"),
        "sodex_preflight": _load_artifact(runs_dir, "sodex_preflight_latest.json"),
        "wave_status": _load_artifact(runs_dir, "wave_status_latest.json"),
    }

    # Service health probe: check external dependencies
    service_health = {
        "dashboard": {"status": "running", "port": 3100},
        "siglab_db": {
            "status": "ok" if Path(str(config.ancestry_db_path)).exists() else "missing",
        },
        "sodex_api": {"status": "external", "note": "SoDEX public REST API (no auth)"},
        "sosovalue_api": {
            "status": "external",
            "note": "SoSoValue OpenAPI (requires API key)",
        },
    }

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "artifact_status": {
            name: {
                "status": art.get("status"),
                "path": art.get("path"),
                "mtime": art.get("mtime"),
                "age_seconds": art.get("age_seconds"),
                "freshness": art.get("freshness"),
                "error": art.get("error"),
            }
            for name, art in artifacts.items()
        },
        "summary": _summarize_ops_artifacts(artifacts),
        "service_health": service_health,
    }


# ---------------------------------------------------------------------------
# Evidence Graph
# ---------------------------------------------------------------------------


def _build_evidence_graph(state: Any) -> dict[str, Any] | None:
    """Build an evidence graph from the latest evidence store summary."""
    config = state.config
    if config is None:
        return None

    # Find the latest evidence summary
    evidence_dir = config.artifact_dir / "evidence"
    if not evidence_dir.exists():
        return None

    summaries = sorted(
        evidence_dir.glob("*.summary.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not summaries:
        return None

    try:
        summary = json.loads(summaries[0].read_text())
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None

    source_counts = dict(summary.get("source_counts") or {})
    entity_counts = dict(summary.get("entity_counts") or {})
    links = list(summary.get("top_links") or summary.get("links") or [])

    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []

    for source, count in source_counts.items():
        node_id = f"source:{source}"
        nodes[node_id] = {
            "id": node_id,
            "label": str(source),
            "kind": "source",
            "count": int(count),
            "spec_hash": None,
            "family": None,
            "score": None,
        }

    for entity, count in entity_counts.items():
        node_id = f"entity:{entity}"
        nodes[node_id] = {
            "id": node_id,
            "label": str(entity),
            "kind": "entity",
            "count": int(count),
            "spec_hash": None,
            "family": None,
            "score": None,
        }

    for link in links:
        if not isinstance(link, dict):
            continue
        entities = [str(item) for item in link.get("entities") or [] if item]
        relation = str(link.get("relation") or "linked")
        source = str(link.get("source") or "cross-module")
        for entity in entities:
            entity_id = f"entity:{entity}"
            source_id = f"source:{source}"
            nodes.setdefault(
                entity_id,
                {
                    "id": entity_id,
                    "label": entity,
                    "kind": "entity",
                    "count": 0,
                    "spec_hash": None,
                    "family": None,
                    "score": None,
                },
            )
            nodes.setdefault(
                source_id,
                {
                    "id": source_id,
                    "label": source,
                    "kind": "source",
                    "count": 0,
                    "spec_hash": None,
                    "family": None,
                    "score": None,
                },
            )
            edges.append({
                "source": source_id,
                "target": entity_id,
                "label": relation,
            })

    return {"nodes": list(nodes.values()), "edges": edges}


@router.get("/evidence-graph")
async def evidence_graph(request: Request) -> dict[str, Any]:
    """Return evidence graph nodes and edges with metadata."""
    state = request.app.state.dashboard
    graph = _build_evidence_graph(state)
    if graph is None:
        return {"nodes": [], "edges": [], "note": "No evidence data available"}
    return graph


# ---------------------------------------------------------------------------
# Skill Report
# ---------------------------------------------------------------------------


def _build_skill_report(state: Any) -> list[dict[str, Any]]:
    """Aggregate per-skill metrics from experiment tool traces."""
    if state.lineage is None:
        return []
    try:
        rows = state.lineage.dashboard_rows()
    except Exception:
        return []

    # Aggregate tool call data from experiment tool traces
    skill_usage: dict[str, dict[str, Any]] = {}

    for row in rows:
        research_summary = row.get("research_summary") or {}
        if isinstance(research_summary, str):
            try:
                research_summary = json.loads(research_summary)
            except (json.JSONDecodeError, TypeError, ValueError):
                research_summary = {}
        if not isinstance(research_summary, dict):
            continue

        tool_trace = research_summary.get("llm_tool_trace") or {}
        trace = tool_trace.get("trace") or {}
        tool_calls = list(trace.get("tool_calls") or [])

        # Also check workspace traces
        workspace = research_summary.get("workspace") or {}
        for stage_key in ("planner_trace_path", "writer_trace_path", "reflector_trace_path"):
            trace_path = workspace.get(stage_key)
            if trace_path:
                try:
                    stage_payload = json.loads(Path(trace_path).read_text())
                except (OSError, json.JSONDecodeError, TypeError, ValueError):
                    continue
                stage_trace = stage_payload.get("claude_trace") or {}
                for call in list(stage_trace.get("tool_calls") or []):
                    tool_calls.append(call)

        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            name = str(call.get("name") or "").strip()
            if not name:
                continue
            entry = skill_usage.setdefault(
                name,
                {
                    "skill_name": name,
                    "usage_count": 0,
                    "total_latency_ms": 0.0,
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                    "error_count": 0,
                    "stages": set(),
                    "classification": "unknown",
                },
            )
            entry["usage_count"] += 1
            entry["total_latency_ms"] += float(
                call.get("latency_ms") or call.get("duration_ms") or 0.0
            )
            entry["total_input_tokens"] += int(
                call.get("input_tokens") or call.get("context_tokens") or 0
            )
            entry["total_output_tokens"] += int(call.get("output_tokens") or 0)
            if call.get("is_error") or call.get("error"):
                entry["error_count"] += 1
            stage = str(call.get("stage") or "").strip()
            if stage:
                entry["stages"].add(stage)

    # Determine classification based on usage patterns
    report = []
    for name, entry in sorted(skill_usage.items()):
        stages = sorted(entry["stages"])
        classification = _classify_skill(name, entry["usage_count"])
        avg_latency = (
            round(entry["total_latency_ms"] / entry["usage_count"], 2)
            if entry["usage_count"] > 0
            else 0.0
        )
        report.append({
            "skill_name": name,
            "usage_count": entry["usage_count"],
            "average_latency_ms": avg_latency,
            "total_input_tokens": entry["total_input_tokens"],
            "total_output_tokens": entry["total_output_tokens"],
            "error_count": entry["error_count"],
            "stages": stages,
            "classification": classification,
        })

    return report


def _classify_skill(name: str, usage_count: int) -> str:
    """Classify a skill based on its name and usage patterns."""
    high_value_patterns = {
        "probe_", "compare_intended_vs_frozen_spec",
        "search_features", "suggest_feature_set", "inspect_feature",
    }
    medium_value_patterns = {
        "search_workspace", "search_workspace_text", "open_file",
    }
    if any(name.startswith(p) or name == p for p in high_value_patterns):
        return "HIGH_VALUE"
    if any(name.startswith(p) or name == p for p in medium_value_patterns):
        return "MEDIUM_VALUE"
    if name == "think":
        return "LOW_VALUE"
    if usage_count > 8:
        return "NOISY"
    return "MEDIUM_VALUE"


@router.get("/skill-report")
async def skill_report(request: Request) -> dict[str, Any]:
    """Return per-skill metrics with classification."""
    state = request.app.state.dashboard
    report = _build_skill_report(state)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "skills": report,
        "total_skills": len(report),
        "total_invocations": sum(s["usage_count"] for s in report),
    }


# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------


def _compute_risk_metrics(state: Any) -> dict[str, Any]:
    """Compute risk metrics from available data in the dashboard state.

    Returns a dict with composite_score, max_drawdown, correlation_matrix,
    and other risk-related data. Returns empty structure if no data available.
    """
    # Attempt to load paper session data for risk computation
    config = state.config
    if config is None:
        return {"composite_score": None, "max_drawdown": None, "correlation_matrix": None}

    sessions_dir = config.live_dir / "paper_sessions"
    if not sessions_dir.exists():
        return {"composite_score": None, "max_drawdown": None, "correlation_matrix": None}

    # Look for .npy session files and extract metrics from them
    # This is a best-effort computation; returns empty values if data insufficient
    try:
        import numpy as np

        from siglab.risk.guardian import (
            compute_composite_score,
            correlation_matrix,
            max_drawdown,
        )

        npy_files = sorted(sessions_dir.glob("*.npy"))
        if not npy_files:
            return {"composite_score": None, "max_drawdown": None, "correlation_matrix": None}

        # Build equity curves from session data
        equity_curves: list[np.ndarray] = []
        for npy_file in npy_files:
            try:
                data = np.load(npy_file, allow_pickle=True)
                if isinstance(data, np.ndarray) and data.size > 0:
                    # If data is a structured array or object, try to extract equity
                    if data.dtype.names is not None and "equity" in data.dtype.names:
                        eq = data["equity"]
                        if isinstance(eq, np.ndarray) and eq.size > 0:
                            equity_curves.append(eq.astype(float))
                    elif data.dtype == np.float64 or data.dtype == np.float32:
                        equity_curves.append(data)
            except Exception:
                continue

        # Compute max drawdown from the first available equity curve
        max_dd: float | None = None
        if equity_curves:
            max_dd = float(max_drawdown(equity_curves[0]))

        # Compute correlation matrix if multiple strategies
        corr_matrix: list[list[float]] | None = None
        if len(equity_curves) >= 2:
            # Convert equity curves to daily returns for correlation
            returns_list = []
            for eq in equity_curves:
                if eq.size >= 2:
                    rets = np.diff(eq) / eq[:-1]
                    returns_list.append(rets)
            if len(returns_list) >= 2:
                matrix = correlation_matrix(returns_list)
                if matrix.size > 0:
                    corr_matrix = matrix.tolist()

        # Compute composite score from available metrics
        composite: float | None = None
        if max_dd is not None:
            composite = float(compute_composite_score(
                sharpe=0.0,  # Will be filled when available
                drawdown=max_dd,
                concentration=0.0,
                correlation_risk=(
                    float(np.mean(matrix[np.triu_indices_from(matrix, k=1)]))
                    if corr_matrix is not None and len(corr_matrix) >= 2
                    else 0.0
                ),
            ))

        return {
            "composite_score": composite,
            "max_drawdown": max_dd,
            "correlation_matrix": corr_matrix,
            "strategy_count": len(equity_curves),
        }

    except ImportError:
        return {"composite_score": None, "max_drawdown": None, "correlation_matrix": None, "note": "numpy not available"}
    except Exception as exc:
        return {"composite_score": None, "max_drawdown": None, "correlation_matrix": None, "note": f"Error: {exc}"}


@router.get("/risk")
async def risk(request: Request) -> dict[str, Any]:
    """Return portfolio risk metrics: composite score, drawdown, correlation.

    Returns data computed from available paper trading sessions.
    Returns None-valued fields when no data is available.
    """
    state = request.app.state.dashboard
    metrics = _compute_risk_metrics(state)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        **metrics,
    }



