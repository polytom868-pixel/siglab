"""DeploymentStore — minimal SQLite store for deployment records."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from siglab.track_registry import resolve_track
from siglab.utils import feature_hash


def _deployment_from_row(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "spec_hash": row[0],
        "created_at": row[1],
        "strategy_name": row[2],
        "strategy_dir": row[3],
        "spec_path": row[4],
        "manifest_path": row[5],
        "readme_path": row[6],
        "job_name": row[7],
        "interval_seconds": row[8],
        "wallet_label": row[9],
        "config_path": row[10],
        "scheduled": bool(row[11]),
        "dry_run": bool(row[12]),
        "llm_finalized": bool(row[13]),
        "support_status": row[14],
        "support_reason": row[15],
        "metadata": json.loads(row[16]) if row[16] else {},
    }


def _spec_payload(raw_json: str) -> dict[str, Any]:
    payload = json.loads(raw_json)
    payload["track"] = resolve_track(payload.get("track"))
    return payload


class DeploymentStore:
    """Minimal SQLite deployment store, extracted from LineageStore."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.path))

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS experiments ( spec_hash TEXT PRIMARY KEY, created_at TEXT NOT NULL, track TEXT NOT NULL, family TEXT NOT NULL, parent_hash TEXT, spec_json TEXT NOT NULL, research_summary TEXT, aggregate_score REAL NOT NULL, passed INTEGER NOT NULL, deployd INTEGER NOT NULL DEFAULT 0, summary_json TEXT NOT NULL, artifact_path TEXT )"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS deployments ( spec_hash TEXT PRIMARY KEY, created_at TEXT NOT NULL, strategy_name TEXT NOT NULL, strategy_dir TEXT NOT NULL, spec_path TEXT NOT NULL, manifest_path TEXT NOT NULL, readme_path TEXT NOT NULL, job_name TEXT, interval_seconds INTEGER, wallet_label TEXT, config_path TEXT NOT NULL, scheduled INTEGER NOT NULL DEFAULT 0, dry_run INTEGER NOT NULL DEFAULT 1, llm_finalized INTEGER NOT NULL DEFAULT 0, support_status TEXT NOT NULL, support_reason TEXT, metadata_json TEXT, FOREIGN KEY(spec_hash) REFERENCES experiments(spec_hash) )"
            )
            connection.commit()

    def deployment(self, spec_hash: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT spec_hash, created_at, strategy_name, strategy_dir, spec_path, manifest_path, readme_path, job_name, interval_seconds, wallet_label, config_path, scheduled, dry_run, llm_finalized, support_status, support_reason, metadata_json FROM deployments WHERE spec_hash = ? LIMIT 1",
                (spec_hash,),
            ).fetchone()
        if row is None:
            return None
        return _deployment_from_row(row)

    def list_deployments(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT spec_hash, created_at, strategy_name, strategy_dir, spec_path, manifest_path, readme_path, job_name, interval_seconds, wallet_label, config_path, scheduled, dry_run, llm_finalized, support_status, support_reason, metadata_json FROM deployments ORDER BY created_at DESC"
            ).fetchall()
        return [_deployment_from_row(row) for row in rows]

    def record_deployment(self, payload: dict[str, Any]) -> None:
        metadata = dict(payload.get("metadata") or {})
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO deployments ( spec_hash, created_at, strategy_name, strategy_dir, spec_path, manifest_path, readme_path, job_name, interval_seconds, wallet_label, config_path, scheduled, dry_run, llm_finalized, support_status, support_reason, metadata_json ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(payload["spec_hash"]),
                    datetime.now(UTC).isoformat(),
                    str(payload["strategy_name"]),
                    str(payload["strategy_dir"]),
                    str(payload["spec_path"]),
                    str(payload["manifest_path"]),
                    str(payload["readme_path"]),
                    payload.get("job_name"),
                    payload.get("interval_seconds"),
                    payload.get("wallet_label"),
                    str(payload["config_path"]),
                    int(bool(payload.get("scheduled"))),
                    int(bool(payload.get("dry_run", True))),
                    int(bool(payload.get("llm_finalized"))),
                    str(payload.get("support_status") or "supported"),
                    payload.get("support_reason"),
                    json.dumps(metadata, sort_keys=True),
                ),
            )
            connection.commit()

    def experiment_detail(self, spec_hash: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT created_at, track, family, spec_hash, parent_hash, aggregate_score, passed, deployd, spec_json, research_summary, summary_json, artifact_path FROM experiments WHERE spec_hash = ? LIMIT 1",
                (spec_hash,),
            ).fetchone()
        if row is None:
            return None
        artifact_payload = None
        artifact_path = row[11]
        if artifact_path and Path(artifact_path).exists():
            try:
                artifact_payload = json.loads(Path(artifact_path).read_text())
            except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                artifact_payload = None
        spec = _spec_payload(row[8])
        return {
            "created_at": row[0],
            "track": resolve_track(row[1]),
            "family": row[2],
            "spec_hash": row[3],
            "parent_hash": row[4],
            "aggregate_score": row[5],
            "passed": bool(row[6]),
            "deployd": bool(row[7]),
            "spec": spec,
            "research_summary": json.loads(row[9]) if row[9] else {},
            "summary": json.loads(row[10]),
            "artifact_path": artifact_path,
            "artifact": artifact_payload,
            "feature_hash": feature_hash(spec.get("features") or []),
            "deployment": self.deployment(spec_hash),
        }
