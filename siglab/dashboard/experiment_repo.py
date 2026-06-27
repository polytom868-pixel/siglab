"""SQL and file I/O utilities for the dashboard."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from siglab.config import resolve_track

logger = logging.getLogger(__name__)


def raw_experiments(
    db_path: str | Path,
    track: str | None = None,
    family: str | None = None,
) -> list[dict[str, Any]]:
    """Query the ancestry DB and return raw experiment rows."""
    path = Path(db_path)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        q = (
            "SELECT created_at, track, family, spec_hash, parent_hash, "
            "aggregate_score, passed, deployd, spec_json, research_summary, "
            "summary_json, artifact_path FROM experiments"
        )
        params: list[Any] = []
        conditions: list[str] = []
        if track:
            conditions.append("track = ?")
            params.append(track)
        if family:
            conditions.append("family = ?")
            params.append(family)
        if conditions:
            q += " WHERE " + " AND ".join(conditions)
        q += " ORDER BY created_at ASC"
        cursor = conn.execute(q, params)
        for row in cursor.fetchall():
            spec = json.loads(row["spec_json"]) if row["spec_json"] else {}
            spec["track"] = resolve_track(spec.get("track"))
            rows.append({
                "created_at": row["created_at"],
                "track": resolve_track(row["track"]),
                "family": row["family"],
                "spec_hash": row["spec_hash"],
                "parent_hash": row["parent_hash"],
                "aggregate_score": row["aggregate_score"],
                "passed": bool(row["passed"]),
                "deployd": bool(row["deployd"]),
                "spec": spec,
                "research_summary": (
                    json.loads(row["research_summary"])
                    if row["research_summary"]
                    else {}
                ),
                "summary": (
                    json.loads(row["summary_json"])
                    if row["summary_json"]
                    else {}
                ),
                "artifact_path": row["artifact_path"],
            })
        conn.close()
    except (sqlite3.Error, OSError):
        pass
    return rows


def raw_runs(
    db_path: str | Path,
    track: str | None = None,
    family: str | None = None,
) -> list[dict[str, Any]]:
    """Group raw experiments into run summaries."""
    rows = raw_experiments(db_path, track=track, family=family)
    runs_map: dict[str, dict[str, Any]] = {}
    for row in rows:
        rs = row.get("research_summary") or {}
        rc = rs.get("run_context") or {}
        run_session_id = str(
            rc.get("run_session_id") or f"legacy::{row.get('spec_hash')}",
        )
        if run_session_id not in runs_map:
            runs_map[run_session_id] = {
                "run_session_id": run_session_id,
                "run_label": str(rc.get("run_label") or run_session_id),
                "track": row["track"],
                "runner_label": str(rc.get("runner_label") or "siglab_harness"),
                "run_kind": "benchmark" if rc.get("benchmark_mode") else "harness",
                "benchmark_mode": bool(rc.get("benchmark_mode")),
                "benchmark_deck": rc.get("benchmark_deck"),
                "memory_scope": str(rc.get("memory_scope") or "track_shared"),
                "phase_labels": [],
                "families": set(),
                "experiment_count": 0,
                "llm_experiment_count": 0,
                "deterministic_experiment_count": 0,
                "passed_count": 0,
                "deployd_count": 0,
                "first_created_at": row["created_at"],
                "last_created_at": row["created_at"],
                "best_spec_hash": None,
                "best_family": None,
                "best_aggregate_score": None,
                "best_validation_total_return": None,
                "best_pre_audit_canonical_total_return": None,
                "status": "pass" if row["passed"] else "fail",
            }
        entry = runs_map[run_session_id]
        entry["experiment_count"] += 1
        is_llm = bool(row.get("research_summary", {}).get("llm_tool_trace"))
        is_det = bool(rc.get("deterministic"))
        if is_llm:
            entry["llm_experiment_count"] += 1
        if is_det:
            entry["deterministic_experiment_count"] += 1
        if row["passed"]:
            entry["passed_count"] += 1
        if row["deployd"]:
            entry["deployd_count"] += 1
        phase = str(rc.get("phase_label") or "")
        if phase and phase not in entry["phase_labels"]:
            entry["phase_labels"].append(phase)
        fv = row.get("family")
        if fv:
            entry["families"].add(fv)
        score = float(row.get("aggregate_score") or 0.0)
        if (
            entry["best_aggregate_score"] is None
            or score > entry["best_aggregate_score"]
        ):
            entry["best_aggregate_score"] = score
            entry["best_spec_hash"] = row["spec_hash"]
            entry["best_family"] = fv
            entry["best_validation_total_return"] = row.get("summary", {}).get(
                "validation_total_return",
            )
            entry["best_pre_audit_canonical_total_return"] = row.get("summary", {}).get(
                "pre_audit_canonical_total_return",
            )
        entry["first_created_at"] = min(entry["first_created_at"], row["created_at"])
        entry["last_created_at"] = max(entry["last_created_at"], row["created_at"])
        if not row["passed"]:
            entry["status"] = "fail"
    for entry in runs_map.values():
        entry["phase_labels"] = sorted(set(entry["phase_labels"]))
        entry["families"] = sorted(entry["families"])
    return list(runs_map.values())


