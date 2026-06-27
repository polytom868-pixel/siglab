"""ExperimentRepo — SQL and file I/O layer for the dashboard."""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from siglab.config import SiglabConfig
from siglab.data.deployment_store import DeploymentStore
from siglab.utils import load_json_path
from siglab.track_registry import canonical_track_name, resolve_track

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


@dataclass
class ExperimentRepo:
    """Data access layer: SQL queries, JSON file I/O, and artifact loading."""

    config: SiglabConfig | None = None
    deployment_store: DeploymentStore | None = None
    _json_cache: dict[str, Any] = field(default_factory=dict)

    @property
    def db_path(self) -> Path | None:
        return None if self.config is None else self.config.ancestry_db_path

    def load_json(self, value: str | Path | None) -> dict[str, Any] | None:
        """Load a JSON file with caching. Accepts relative (to root) or absolute paths."""
        if not value:
            return None
        path = Path(value).expanduser()
        if not path.is_absolute() and self.config is not None:
            path = (self.config.root_dir / path).resolve()
        cached = self._json_cache.get(str(path))
        if cached is not None:
            return cast(dict[str, Any] | None, cached)
        payload: dict[str, Any] | None = load_json_path(path)
        self._json_cache[str(path)] = payload
        return payload

    def load_artifact(self, relative_path: str) -> dict[str, Any]:
        """Load an artifact JSON file relative to root_dir with status metadata."""
        if self.config is None:
            return {
                "status": "blocked",
                "path": relative_path,
                "error": "config not loaded",
                "payload": None,
            }
        path = (self.config.root_dir / relative_path).resolve()
        root = self.config.root_dir.resolve()
        if root not in path.parents and path != root:
            return {
                "status": "blocked",
                "path": relative_path,
                "error": "artifact path escapes repo root",
                "payload": None,
            }
        if not path.exists():
            return {
                "status": "missing",
                "path": relative_path,
                "error": "artifact missing",
                "payload": None,
            }
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            return {
                "status": "malformed",
                "path": relative_path,
                "error": str(exc),
                "payload": None,
            }
        if not isinstance(payload, dict):
            return {
                "status": "malformed",
                "path": relative_path,
                "error": "artifact root must be a JSON object",
                "payload": None,
            }
        mtime = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
        age_seconds = max(
            0.0,
            (datetime.now(UTC) - mtime.astimezone(UTC)).total_seconds(),
        )
        return {
            "status": "present",
            "path": relative_path,
            "mtime": mtime.isoformat(),
            "age_seconds": round(age_seconds, 3),
            "freshness": "fresh"
            if age_seconds <= 15 * 60
            else "stale"
            if age_seconds <= 24 * 60 * 60
            else "expired",
            "payload": payload,
        }

    def raw_runs(
        self,
        db_path: str | Path,
        track: str | None = None,
        family: str | None = None,
    ) -> list[dict[str, Any]]:
        """Query and group experiments into run summaries."""
        from siglab.dashboard.experiment_repo import raw_runs as _rr
        return _rr(db_path, track=track, family=family)

    def experiment_detail(self, spec_hash: str) -> dict[str, Any] | None:
        """Fetch a single experiment by spec_hash.

        Prefers the SQL query over DeploymentStore for raw experiment data,
        and falls back to DeploymentStore for deployment-backed lookups.
        """
        if (db_path := self.db_path) is not None:
            result = next(
                (
                    row
                    for row in raw_experiments(str(db_path))
                    if row.get("spec_hash") == spec_hash
                ),
                None,
            )
            if result is not None:
                return result
        if self.deployment_store is not None:
            return self.deployment_store.experiment_detail(spec_hash)
        return None

    def workspace_placeholders(
        self,
        *,
        track: str | None = None,
        family: str | None = None,
        existing_run_ids: set[str],
    ) -> list[dict[str, Any]]:
        """Discover active workspace sessions not yet in the DB."""
        placeholders: list[dict[str, Any]] = []
        if self.config is None:
            return placeholders
        runs_root = getattr(self.config, "artifact_dir", self.config.root_dir / "runs")
        if not runs_root.exists():
            return placeholders
        for state_path in sorted(
            runs_root.glob("*/workspaces/*/current/SESSION_STATE.json"),
        ):
            try:
                payload = json.loads(state_path.read_text())
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                continue
            run_session_id = str(
                payload.get("run_session_id") or state_path.parents[1].name,
            )
            if not run_session_id or run_session_id in existing_run_ids:
                continue
            track_name = resolve_track(state_path.parents[3].name)
            if track and canonical_track_name(track) != track_name:
                continue
            families = sorted({
                str(value)
                for value in [
                    payload.get("current_parent_family"),
                    payload.get("best_family"),
                ]
                if str(value or "").strip()
            })
            if family and family not in families:
                continue
            created_at = (
                datetime.fromtimestamp(state_path.stat().st_mtime)
                .astimezone()
                .isoformat()
            )
            placeholders.append({
                "run_session_id": run_session_id,
                "run_label": run_session_id,
                "track": track_name,
                "runner_label": "siglab_harness",
                "run_kind": "harness",
                "memory_scope": str(payload.get("memory_scope") or "track_shared"),
                "benchmark_mode": False,
                "benchmark_deck": None,
                "phase_labels": [str(payload.get("phase_label") or "starting")],
                "families": families,
                "experiment_count": 0,
                "llm_experiment_count": 0,
                "deterministic_experiment_count": 0,
                "tool_call_count": 0,
                "passed_count": 0,
                "deployd_count": 0,
                "first_created_at": created_at,
                "last_created_at": created_at,
                "best_spec_hash": None,
                "best_family": str(payload.get("best_family") or "") or None,
                "best_aggregate_score": None,
                "best_validation_total_return": None,
                "best_pre_audit_canonical_total_return": None,
                "llm_provider": "unknown",
                "llm_model": "unknown",
                "status": "running",
                "series_points": [],
            })
        placeholders.sort(
            key=lambda row: str(row.get("last_created_at") or ""),
            reverse=True,
        )
        return placeholders
