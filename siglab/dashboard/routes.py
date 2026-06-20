"""FastAPI REST routes for SigLab Dashboard.

Merged from the legacy server.py (Track 2.3).  Provides experiments,
runs, ops, evidence graph, skill report, risk, and market-data
endpoints.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from siglab.config import SiglabConfig

logger = logging.getLogger(__name__)

router = APIRouter()

SIGLAB_VERSION = "0.1.0"

_DEFAULT_PORT = int(os.environ.get("PORT", "8080"))


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(UTC).isoformat()


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
            "openapi_base_url": config.sosovalue_base_url,
            "etf_base_url": config.sosovalue_base_url,
            "news_base_url": config.sosovalue_base_url,
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
# Experiments (legacy server.py compat)
# ---------------------------------------------------------------------------


@router.get("/api/experiments")
async def api_experiments(
    request: Request,
    track: str | None = None,
    family: str | None = None,
) -> dict[str, Any]:
    """Return experiments payload matching legacy server.py shape."""
    from siglab.track_registry import canonical_track_name

    state = request.app.state.dashboard
    return state.experiments_payload(
        track=canonical_track_name(track) if track else None,
        family=family,
    )


@router.get("/api/runs")
async def api_runs(
    request: Request,
    track: str | None = None,
    family: str | None = None,
) -> dict[str, Any]:
    """Return runs payload matching legacy server.py shape."""
    from siglab.track_registry import canonical_track_name

    state = request.app.state.dashboard
    return state.runs_payload(
        track=canonical_track_name(track) if track else None,
        family=family,
    )


@router.get("/api/experiments/{spec_hash}")
async def api_experiment_detail(request: Request, spec_hash: str) -> dict[str, Any]:
    """Return a single experiment detail (legacy compat)."""
    state = request.app.state.dashboard
    payload = state.experiment_detail_payload(spec_hash)
    if payload is None:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return payload


@router.get("/api/experiments/{spec_hash}/series")
async def api_experiment_series(request: Request, spec_hash: str) -> dict[str, Any]:
    """Return experiment series including canonical run (legacy compat)."""
    state = request.app.state.dashboard
    payload = state.experiment_series_payload(spec_hash)
    if payload is None:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return payload


@router.post("/api/experiments/{spec_hash}/deploy")
async def api_experiment_deploy(
    request: Request,
    spec_hash: str,
) -> dict[str, Any]:
    """Deploy an experiment by spec hash (legacy compat)."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    state = request.app.state.dashboard
    return await state.deploy_experiment(spec_hash=spec_hash, payload=body)


# ---------------------------------------------------------------------------
# Ops Board / API Ops
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
        "dashboard": {"status": "running", "port": _DEFAULT_PORT},
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
        "generated_at": _now_iso(),
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


@router.get("/api/ops")
async def api_ops(request: Request) -> dict[str, Any]:
    """Return full ops payload with artifact details (legacy server.py compat)."""
    state = request.app.state.dashboard
    return state.ops_payload()


# ---------------------------------------------------------------------------
# Evidence Graph
# ---------------------------------------------------------------------------


def _build_evidence_graph(state: Any) -> dict[str, Any] | None:
    """Build an evidence graph from the latest evidence store summary."""
    config = state.config
    if config is None:
        return None

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
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("Failed to load evidence summary: %s", exc)
        return None

    source_counts = dict(summary.get("source_counts") or {})
    entity_counts = dict(summary.get("entity_counts") or {})
    links = list(summary.get("top_links") or summary.get("links") or [])

    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str, str]] = set()

    def _node(node_id: str, label: str, kind: str, count: int) -> dict[str, Any]:
        return {
            "id": node_id,
            "label": label,
            "kind": kind,
            "count": count,
            "spec_hash": None,
            "family": None,
            "score": None,
        }

    for source, count in source_counts.items():
        nodes[f"source:{source}"] = _node(f"source:{source}", str(source), "source", int(count))

    for entity, count in entity_counts.items():
        nodes[f"entity:{entity}"] = _node(f"entity:{entity}", str(entity), "entity", int(count))

    for link in links:
        if not isinstance(link, dict):
            continue
        relation = str(link.get("relation") or "linked")
        source_name = str(link.get("source") or "cross-module")
        entities_raw = link.get("entities")
        if isinstance(entities_raw, list) and entities_raw:
            entities = [str(item) for item in entities_raw if item]
        else:
            entities = []
            for key in ("entity", "feed_entity"):
                val = link.get(key)
                if val:
                    entities.append(str(val))
        for entity in entities:
            entity_id = f"entity:{entity}"
            source_id = f"source:{source_name}"
            nodes.setdefault(entity_id, _node(entity_id, entity, "entity", 0))
            nodes.setdefault(source_id, _node(source_id, source_name, "source", 0))
            edge_key = (source_id, entity_id, relation)
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)
            edges.append({
                "source": source_id,
                "target": entity_id,
                "label": relation,
                "confidence": link.get("confidence"),
                "warning": link.get("warning"),
                "day_gap": link.get("day_gap"),
            })
    return {"nodes": list(nodes.values()), "edges": edges}


@router.get("/evidence-graph")
async def evidence_graph(request: Request) -> dict[str, Any]:
    """Return evidence graph nodes and edges with metadata."""
    logger.debug("Evidence graph requested")
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
    except Exception as exc:
        logger.warning("Failed to load skill report rows: %s", exc)
        return []

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
    logger.debug("Skill report requested")
    state = request.app.state.dashboard
    report = _build_skill_report(state)
    return {
        "generated_at": _now_iso(),
        "skills": report,
        "total_skills": len(report),
        "total_invocations": sum(s["usage_count"] for s in report),
    }


# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------


def _compute_risk_metrics(state: Any) -> dict[str, Any]:
    """Compute risk metrics from available data in the dashboard state."""
    from siglab.dashboard.risk_utils import compute_risk_metrics, empty_risk_response

    logger.debug("Computing risk metrics")
    config = state.config
    if config is None:
        return empty_risk_response()

    sessions_dir = config.root_dir / "sessions"
    if not sessions_dir.exists():
        return empty_risk_response()

    try:
        return compute_risk_metrics(sessions_dir)
    except ImportError:
        return {**empty_risk_response(), "note": "numpy not available"}
    except Exception as exc:
        logger.warning("Risk computation failed: %s", exc)
        return {**empty_risk_response(), "note": "Error computing risk metrics"}


@router.get("/risk")
async def risk(request: Request) -> dict[str, Any]:
    """Return portfolio risk metrics: composite score, drawdown, correlation.

    Returns data computed from available paper trading sessions.
    Returns None-valued fields when no data is available.
    """
    logger.debug("Risk metrics requested")
    state = request.app.state.dashboard
    metrics = _compute_risk_metrics(state)
    return {
        "generated_at": _now_iso(),
        **metrics,
    }


# ---------------------------------------------------------------------------
# Market Data (SoDEX perps)
# ---------------------------------------------------------------------------


@router.get("/market/symbols")
async def market_symbols(request: Request) -> dict[str, Any]:
    """Return all tradable SoDEX perp symbols with metadata."""
    logger.debug("Market symbols requested")
    state = request.app.state.dashboard
    feeds = state.get_sodex_feeds()
    if feeds is None:
        return {"symbols": [], "note": "SoDEXFeeds not available"}
    try:
        symbols = await feeds.fetch_symbols()
        return {"symbols": symbols, "count": len(symbols)}
    except Exception as exc:
        logger.warning("Market symbols error: %s", exc)
        return {"symbols": [], "error": "Internal error"}


@router.get("/market/tickers")
async def market_tickers(request: Request) -> dict[str, Any]:
    """Return 24-hour ticker data for all SoDEX perp symbols."""
    logger.debug("Market tickers requested")
    state = request.app.state.dashboard
    feeds = state.get_sodex_feeds()
    if feeds is None:
        return {"tickers": [], "note": "SoDEXFeeds not available"}
    try:
        tickers = await feeds.fetch_tickers()
        return {"tickers": tickers, "count": len(tickers)}
    except Exception as exc:
        logger.warning("Market tickers error: %s", exc)
        return {"tickers": [], "error": "Internal error"}


@router.get("/market/klines/{symbol}")
async def market_klines(
    request: Request,
    symbol: str,
    interval: str = "1h",
    limit: int = 60,
) -> dict[str, Any]:
    """Return kline/candlestick data for a perp symbol."""
    logger.debug("Market klines requested: %s", symbol)
    state = request.app.state.dashboard
    feeds = state.get_sodex_feeds()
    if feeds is None:
        return {"klines": [], "symbol": symbol, "note": "SoDEXFeeds not available"}
    try:
        frame = await feeds.fetch_klines(symbol, interval, limit=limit)
        records = frame.reset_index().to_dict(orient="records") if not frame.empty else []
        for rec in records:
            ts = rec.get("timestamp")
            if ts is not None and hasattr(ts, "isoformat"):
                rec["timestamp"] = ts.isoformat()
        return {"klines": records, "symbol": symbol, "interval": interval, "count": len(records)}
    except Exception as exc:
        logger.warning("Market klines error (%s): %s", symbol, exc)
        return {"klines": [], "symbol": symbol, "error": "Internal error"}


@router.get("/market/orderbook/{symbol}")
async def market_orderbook(
    request: Request,
    symbol: str,
    limit: int = 20,
) -> dict[str, Any]:
    """Return order book depth for a perp symbol."""
    logger.debug("Market orderbook requested: %s", symbol)
    state = request.app.state.dashboard
    feeds = state.get_sodex_feeds()
    if feeds is None:
        return {"bids": [], "asks": [], "symbol": symbol, "note": "SoDEXFeeds not available"}
    try:
        data = await feeds.fetch_orderbook(symbol, limit=limit)
        return {
            "bids": data.get("bids", []),
            "asks": data.get("asks", []),
            "symbol": symbol,
        }
    except Exception as exc:
        logger.warning("Market orderbook error (%s): %s", symbol, exc)
        return {"bids": [], "asks": [], "symbol": symbol, "error": "Internal error"}
