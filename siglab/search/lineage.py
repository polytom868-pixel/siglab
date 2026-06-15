"""LineageStore — SQLite persistence layer for experiment lineage.

Analysis / ranking helpers live in :mod:`lineage_analysis`; shared types and
pure utility functions live in :mod:`lineage_types`.  This module owns the
database schema, CRUD operations, and deployment records.
"""

from __future__ import annotations

import json
import sqlite3

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from siglab.schemas import SignalSpec
from siglab.track_registry import matching_track_names, resolve_track

from siglab.search.lineage_types import (
    _maturity_bucket,
    _safe_float,
    _spec_assets,
)
from siglab.search.lineage_analysis import (
    assemble_memory_packet,
    build_run_summaries,
    coverage_summary as _coverage_summary,
    archetype_coverage as _archetype_coverage,
    novelty_pressure as _novelty_pressure,
    failure_pattern_summary as _failure_pattern_summary,
    behavior_pattern_summary as _behavior_pattern_summary,
    regime_pattern_summary as _regime_pattern_summary,
    drawdown_pattern_summary as _drawdown_pattern_summary,
    gate_pattern_summary as _gate_pattern_summary,
    equity_pattern_summary as _equity_pattern_summary,
    pareto_frontier as _pareto_frontier,
    validation_leaders as _validation_leaders,
    outstanding_runs as _outstanding_runs,
    last_five_runs as _last_five_runs,
    query_relevance as _query_relevance,
    experiment_row_payload,
    is_deterministic_experiment,
    feature_hash,
    filter_run_scope,
    row_diagnostic_snapshot as _row_diagnostic_snapshot,
    spec_payload,
    top_similar as _top_similar,
)

# Re-export for backward compatibility with ``from siglab.search.lineage import ...``
_safe_float = _safe_float
_median_value = __import__("siglab.search.lineage_types", fromlist=["_median_value"])._median_value
_delta = __import__("siglab.search.lineage_types", fromlist=["_delta"])._delta
_parse_timestamp = __import__("siglab.search.lineage_types", fromlist=["_parse_timestamp"])._parse_timestamp
_tokens = __import__("siglab.search.lineage_types", fromlist=["_tokens"])._tokens


class LineageStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._init_db()

    # ── DB primitives ────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS experiments (
                    spec_hash TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    track TEXT NOT NULL,
                    family TEXT NOT NULL,
                    parent_hash TEXT,
                    spec_json TEXT NOT NULL,
                    research_summary TEXT,
                    aggregate_score REAL NOT NULL,
                    passed INTEGER NOT NULL,
                    deployd INTEGER NOT NULL DEFAULT 0,
                    summary_json TEXT NOT NULL,
                    artifact_path TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS experiment_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    spec_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    track TEXT NOT NULL,
                    family TEXT NOT NULL,
                    parent_hash TEXT,
                    spec_json TEXT NOT NULL,
                    research_summary TEXT,
                    aggregate_score REAL NOT NULL,
                    passed INTEGER NOT NULL,
                    deployd INTEGER NOT NULL DEFAULT 0,
                    summary_json TEXT NOT NULL,
                    artifact_path TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_experiment_events_track_created_at
                ON experiment_events (track, created_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_experiment_events_spec_hash
                ON experiment_events (spec_hash)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS deployments (
                    spec_hash TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    strategy_name TEXT NOT NULL,
                    strategy_dir TEXT NOT NULL,
                    spec_path TEXT NOT NULL,
                    manifest_path TEXT NOT NULL,
                    readme_path TEXT NOT NULL,
                    job_name TEXT,
                    interval_seconds INTEGER,
                    wallet_label TEXT,
                    config_path TEXT NOT NULL,
                    scheduled INTEGER NOT NULL DEFAULT 0,
                    dry_run INTEGER NOT NULL DEFAULT 1,
                    llm_finalized INTEGER NOT NULL DEFAULT 0,
                    support_status TEXT NOT NULL,
                    support_reason TEXT,
                    metadata_json TEXT,
                    FOREIGN KEY(spec_hash) REFERENCES experiments(spec_hash)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS query_cards (
                    query_hash TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    track TEXT NOT NULL,
                    family TEXT,
                    parent_hash TEXT,
                    market_bundle_id TEXT,
                    as_of TEXT,
                    provider TEXT,
                    canonical_query TEXT NOT NULL,
                    report_json TEXT NOT NULL
                )
                """
            )
            event_count = connection.execute(
                "SELECT COUNT(*) FROM experiment_events"
            ).fetchone()[0]
            if int(event_count or 0) == 0:
                connection.execute(
                    """
                    INSERT INTO experiment_events (
                        spec_hash,
                        created_at,
                        track,
                        family,
                        parent_hash,
                        spec_json,
                        research_summary,
                        aggregate_score,
                        passed,
                        deployd,
                        summary_json,
                        artifact_path
                    )
                    SELECT
                        spec_hash,
                        created_at,
                        track,
                        family,
                        parent_hash,
                        spec_json,
                        research_summary,
                        aggregate_score,
                        passed,
                        deployd,
                        summary_json,
                        artifact_path
                    FROM experiments
                    """
                )
            connection.commit()

    # ── CRUD ─────────────────────────────────────────────────────────────────

    def record(
        self,
        *,
        evaluation: dict[str, Any],
        parent_hash: str | None,
        research_summary: dict[str, Any],
        artifact_path: str,
    ) -> None:
        spec_payload_dict = dict(evaluation["spec"])
        spec_payload_dict["track"] = resolve_track(spec_payload_dict.get("track"))
        research_payload = dict(research_summary)
        research_payload["track"] = resolve_track(research_payload.get("track"))
        recorded_at = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO experiment_events (
                    spec_hash,
                    created_at,
                    track,
                    family,
                    parent_hash,
                    spec_json,
                    research_summary,
                    aggregate_score,
                    passed,
                    deployd,
                    summary_json,
                    artifact_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    evaluation["spec_hash"],
                    recorded_at,
                    spec_payload_dict["track"],
                    spec_payload_dict["family"],
                    parent_hash,
                    json.dumps(spec_payload_dict, sort_keys=True),
                    json.dumps(research_payload, sort_keys=True),
                    float(evaluation["summary"]["aggregate_score"]),
                    int(bool(evaluation["summary"]["passed"])),
                    json.dumps(evaluation["summary"], sort_keys=True),
                    artifact_path,
                ),
            )
            connection.execute(
                """
                INSERT OR REPLACE INTO experiments (
                    spec_hash,
                    created_at,
                    track,
                    family,
                    parent_hash,
                    spec_json,
                    research_summary,
                    aggregate_score,
                    passed,
                    deployd,
                    summary_json,
                    artifact_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT deployd FROM experiments WHERE spec_hash = ?), 0), ?, ?)
                """,
                (
                    evaluation["spec_hash"],
                    recorded_at,
                    spec_payload_dict["track"],
                    spec_payload_dict["family"],
                    parent_hash,
                    json.dumps(spec_payload_dict, sort_keys=True),
                    json.dumps(research_payload, sort_keys=True),
                    float(evaluation["summary"]["aggregate_score"]),
                    int(bool(evaluation["summary"]["passed"])),
                    evaluation["spec_hash"],
                    json.dumps(evaluation["summary"], sort_keys=True),
                    artifact_path,
                ),
            )
            connection.commit()

    def deploy(self, spec_hash: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE experiments SET deployd = 1 WHERE spec_hash = ?",
                (spec_hash,),
            )
            connection.execute(
                """
                UPDATE experiment_events
                SET deployd = 1
                WHERE event_id = (
                    SELECT event_id
                    FROM experiment_events
                    WHERE spec_hash = ?
                    ORDER BY created_at DESC, event_id DESC
                    LIMIT 1
                )
                """,
                (spec_hash,),
            )
            connection.commit()

    def has_spec(self, spec_hash: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM experiments WHERE spec_hash = ? LIMIT 1",
                (spec_hash,),
            ).fetchone()
        return row is not None

    def clear_passed(self, *, track: str | None = None) -> dict[str, Any]:
        params: list[Any] = []
        clauses = ["passed = 1"]
        if track:
            track_names = matching_track_names(track)
            placeholders = ",".join("?" for _ in track_names)
            clauses.append(f"track IN ({placeholders})")
            params.extend(track_names)

        where_clause = " AND ".join(clauses)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT spec_hash, artifact_path, deployd
                FROM experiments
                WHERE {where_clause}
                ORDER BY created_at ASC
                """,
                tuple(params),
            ).fetchall()
            if not rows:
                return {
                    "experiments_deleted": 0,
                    "runs_deleted": 0,
                    "deployments_deleted": 0,
                    "event_rows_deleted": 0,
                    "query_cards_deleted": 0,
                    "spec_hashes": [],
                }

            spec_hashes = [str(row[0]) for row in rows]
            artifact_paths = [str(row[1]) for row in rows if row[1]]
            placeholders = ",".join("?" for _ in spec_hashes)
            deployments_deleted = connection.execute(
                f"DELETE FROM deployments WHERE spec_hash IN ({placeholders})",
                tuple(spec_hashes),
            ).rowcount
            event_rows_deleted = connection.execute(
                f"DELETE FROM experiment_events WHERE spec_hash IN ({placeholders})",
                tuple(spec_hashes),
            ).rowcount
            query_cards_deleted = connection.execute(
                f"DELETE FROM query_cards WHERE parent_hash IN ({placeholders})",
                tuple(spec_hashes),
            ).rowcount
            experiments_deleted = connection.execute(
                f"DELETE FROM experiments WHERE spec_hash IN ({placeholders})",
                tuple(spec_hashes),
            ).rowcount
            connection.commit()

        runs_deleted = 0
        for ap in artifact_paths:
            path = Path(ap)
            if not path.exists():
                continue
            try:
                path.unlink()
            except OSError:
                continue
            runs_deleted += 1

        return {
            "experiments_deleted": int(experiments_deleted),
            "runs_deleted": runs_deleted,
            "deployments_deleted": int(deployments_deleted),
            "event_rows_deleted": int(event_rows_deleted),
            "query_cards_deleted": int(query_cards_deleted),
            "spec_hashes": spec_hashes,
        }

    # ── Query cards ──────────────────────────────────────────────────────────

    def record_query_cards(
        self,
        *,
        track: str,
        family: str,
        parent_hash: str | None,
        market_bundle: dict[str, Any] | None,
        external_research: dict[str, Any],
    ) -> list[str]:
        reports = list(external_research.get("reports") or [])
        if not reports:
            return []

        canonical_track = resolve_track(track)
        bundle_id = None
        as_of = None
        if market_bundle:
            bundle_id = market_bundle.get("bundle_id")
            as_of = market_bundle.get("as_of")

        hashes: list[str] = []
        with self._connect() as connection:
            for report in reports:
                query = str(report.get("query") or "").strip()
                if not query:
                    continue
                query_hash = sha256(
                    json.dumps(
                        {
                            "track": canonical_track,
                            "family": family,
                            "query": query,
                            "market_bundle_id": bundle_id,
                            "as_of": as_of,
                        },
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest()[:16]
                connection.execute(
                    """
                    INSERT OR REPLACE INTO query_cards (
                        query_hash,
                        created_at,
                        track,
                        family,
                        parent_hash,
                        market_bundle_id,
                        as_of,
                        provider,
                        canonical_query,
                        report_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        query_hash,
                        datetime.now(UTC).isoformat(),
                        canonical_track,
                        family,
                        parent_hash,
                        bundle_id,
                        as_of,
                        str(external_research.get("provider") or "unknown"),
                        query,
                        json.dumps(report, sort_keys=True),
                    ),
                )
                hashes.append(query_hash)
            connection.commit()
        return hashes

    # ── Deployments ──────────────────────────────────────────────────────────

    def record_deployment(self, payload: dict[str, Any]) -> None:
        metadata = dict(payload.get("metadata") or {})
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO deployments (
                    spec_hash,
                    created_at,
                    strategy_name,
                    strategy_dir,
                    spec_path,
                    manifest_path,
                    readme_path,
                    job_name,
                    interval_seconds,
                    wallet_label,
                    config_path,
                    scheduled,
                    dry_run,
                    llm_finalized,
                    support_status,
                    support_reason,
                    metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
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

    def deployment(self, spec_hash: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    spec_hash,
                    created_at,
                    strategy_name,
                    strategy_dir,
                    spec_path,
                    manifest_path,
                    readme_path,
                    job_name,
                    interval_seconds,
                    wallet_label,
                    config_path,
                    scheduled,
                    dry_run,
                    llm_finalized,
                    support_status,
                    support_reason,
                    metadata_json
                FROM deployments
                WHERE spec_hash = ?
                LIMIT 1
                """,
                (spec_hash,),
            ).fetchone()
        if row is None:
            return None
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

    def list_deployments(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    spec_hash,
                    created_at,
                    strategy_name,
                    strategy_dir,
                    spec_path,
                    manifest_path,
                    readme_path,
                    job_name,
                    interval_seconds,
                    wallet_label,
                    config_path,
                    scheduled,
                    dry_run,
                    llm_finalized,
                    support_status,
                    support_reason,
                    metadata_json
                FROM deployments
                ORDER BY created_at DESC
                """,
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            result.append(
                {
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
            )
        return result

    # ── Queries ──────────────────────────────────────────────────────────────

    def recent(
        self,
        track: str,
        *,
        limit: int = 5,
        include_deterministic: bool = True,
        run_session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = self._track_experiments(track, run_session_id=run_session_id)
        if not include_deterministic:

            rows = [row for row in rows if not is_deterministic_experiment(row)]
        return rows[:limit]

    def best(self, track: str, *, run_session_id: str | None = None) -> dict[str, Any] | None:
        rows = [
            row
            for row in self._track_experiments(track, run_session_id=run_session_id)
            if bool(row.get("passed"))
        ]
        if not rows:
            return None
        rows.sort(
            key=lambda row: (
                int(bool(row.get("deployd"))),
                float(row.get("aggregate_score") or -1e18),
                str(row.get("created_at") or ""),
            ),
            reverse=True,
        )
        row = rows[0]
        return {
            "spec_hash": row.get("spec_hash"),
            "spec": dict(row.get("spec") or {}),
            "aggregate_score": row.get("aggregate_score"),
            "deployd": bool(row.get("deployd")),
        }

    def list_rows(self, *, track: str | None, limit: int) -> list[dict[str, Any]]:
        query = (
            "SELECT created_at, track, family, spec_hash, aggregate_score, passed, deployd, summary_json "
            "FROM experiments "
        )
        params: tuple[Any, ...]
        if track:
            track_names = matching_track_names(track)
            placeholders = ",".join("?" for _ in track_names)
            query += f"WHERE track IN ({placeholders}) "
            params = (*track_names, limit)
        else:
            params = (limit,)
        query += "ORDER BY created_at DESC LIMIT ?"

        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()

        result: list[dict[str, Any]] = []
        for row in rows:
            summary = json.loads(row[7]) if row[7] else {}
            result.append({
                "created_at": row[0],
                "track": resolve_track(row[1]),
                "family": row[2],
                "spec_hash": row[3],
                "aggregate_score": row[4],
                "passed": bool(row[5]),
                "deployd": bool(row[6]),
                "validation_total_return": summary.get("validation_total_return"),
                "sharpe": summary.get("median_sharpe") or summary.get("validation_sharpe"),
                "max_drawdown": summary.get("max_drawdown"),
                "equity_curve": summary.get("equity_curve", []),
            })
        return result

    def dashboard_rows(
        self,
        *,
        track: str | None = None,
        family: str | None = None,
        run_session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        query = (
            """
            SELECT
                event_id,
                created_at,
                track,
                family,
                spec_hash,
                parent_hash,
                aggregate_score,
                passed,
                deployd,
                spec_json,
                research_summary,
                summary_json,
                artifact_path
            FROM experiment_events
            """
        )
        params_list: list[Any] = []
        clauses: list[str] = []
        if track:
            track_names = matching_track_names(track)
            placeholders = ",".join("?" for _ in track_names)
            clauses.append(f"track IN ({placeholders})")
            params_list.extend(track_names)
        if family:
            clauses.append("family = ?")
            params_list.append(family)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at ASC"

        with self._connect() as connection:
            rows = connection.execute(query, tuple(params_list)).fetchall()

        payload_rows: list[dict[str, Any]] = []
        for row in rows:
            spec = spec_payload(row[9])
            payload_rows.append(
                {
                    "event_id": int(row[0]),
                    "created_at": row[1],
                    "track": resolve_track(row[2]),
                    "family": row[3],
                    "spec_hash": row[4],
                    "parent_hash": row[5],
                    "aggregate_score": row[6],
                    "passed": bool(row[7]),
                    "deployd": bool(row[8]),
                    "spec": spec,
                    "research_summary": json.loads(row[10]) if row[10] else {},
                    "summary": json.loads(row[11]),
                    "artifact_path": row[12],
                    "feature_hash": feature_hash(spec.get("features") or []),
                    "deployment": self.deployment(row[4]),
                }
            )
        if run_session_id:
            payload_rows = filter_run_scope(payload_rows, run_session_id=run_session_id)
        return payload_rows

    def experiment_detail(self, spec_hash: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    created_at,
                    track,
                    family,
                    spec_hash,
                    parent_hash,
                    aggregate_score,
                    passed,
                    deployd,
                    spec_json,
                    research_summary,
                    summary_json,
                    artifact_path
                FROM experiments
                WHERE spec_hash = ?
                LIMIT 1
                """,
                (spec_hash,),
            ).fetchone()

        if row is None:
            return None

        artifact_payload = None
        artifact_path = row[11]
        if artifact_path and Path(artifact_path).exists():
            try:
                artifact_payload = json.loads(Path(artifact_path).read_text())
            except Exception:
                artifact_payload = None

        spec = spec_payload(row[8])
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

    # ── Delegating analysis methods ──────────────────────────────────────────

    def memory_packet(
        self,
        *,
        track: str,
        parent: SignalSpec,
        market_bundle: dict[str, Any] | None,
        limit: int = 3,
        run_session_id: str | None = None,
    ) -> dict[str, Any]:

        experiments_all = self._track_experiments(track, run_session_id=run_session_id)
        query_cards = (
            []
            if run_session_id
            else self._relevant_query_cards(
                track=track,
                parent=parent.canonical_dict(),
                market_bundle=market_bundle,
                limit=limit,
            )
        )

        return assemble_memory_packet(
            track=track,
            parent=parent,
            market_bundle=market_bundle,
            limit=limit,
            run_session_id=run_session_id,
            experiments_all=experiments_all,
            query_cards=query_cards,
        )

    def run_summaries(
        self,
        *,
        track: str | None = None,
        family: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = self.dashboard_rows(track=track, family=family)
        return build_run_summaries(rows)

    # ── Backward-compatible internal helpers ──────────────────────────────────

    def _feature_hash(self, features: list[str]) -> str:
        return feature_hash(features)

    def _is_deterministic_experiment(self, row: dict[str, Any]) -> bool:

        return is_deterministic_experiment(row)

    def _row_tool_call_count(self, row: dict[str, Any]) -> int:
        from siglab.search.lineage_analysis import row_tool_call_count

        return row_tool_call_count(row)

    def _spec_payload(self, raw_json: str) -> dict[str, Any]:
        return spec_payload(raw_json)

    def _spec_assets(self, spec: dict[str, Any]) -> list[str]:
        return _spec_assets(spec)

    def _maturity_bucket(self, universe: dict[str, Any]) -> str:
        return _maturity_bucket(universe)

    def _objective_vector(self, row: dict[str, Any]) -> tuple[float, float, float]:
        from siglab.search.lineage_analysis import objective_vector

        return objective_vector(row)

    def _dominates(
        self,
        left: tuple[float, float, float],
        right: tuple[float, float, float],
    ) -> bool:
        from siglab.search.lineage_analysis import dominates

        return dominates(left, right)

    def _spec_similarity(
        self,
        *,
        parent_payload: dict[str, Any],
        parent_assets: set[str],
        parent_features: set[str],
        parent_maturity: str,
        other: dict[str, Any],
    ) -> float:
        from siglab.search.lineage_analysis import spec_similarity

        return spec_similarity(
            parent_payload=parent_payload,
            parent_assets=parent_assets,
            parent_features=parent_features,
            parent_maturity=parent_maturity,
            other=other,
        )

    def _row_diagnostic_snapshot(self, row: dict[str, Any]) -> dict[str, Any]:
        return _row_diagnostic_snapshot(row)

    def _parent_delta(
        self,
        *,
        row: dict[str, Any],
        rows_by_hash: dict[str, dict[str, Any]],
        diagnostics_by_hash: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        from siglab.search.lineage_analysis import parent_delta

        return parent_delta(
            row=row,
            rows_by_hash=rows_by_hash,
            diagnostics_by_hash=diagnostics_by_hash,
        )

    def _artifact_payload(self, row: dict[str, Any]) -> dict[str, Any] | None:
        from siglab.search.lineage_analysis import artifact_payload

        return artifact_payload(row)

    def _pre_audit_trade_episodes(
        self,
        ap: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        from siglab.search.lineage_analysis import pre_audit_trade_episodes

        return pre_audit_trade_episodes(ap)

    def _behavior_pack(self, trade_episodes: list[dict[str, Any]]) -> dict[str, Any]:
        from siglab.search.lineage_analysis import behavior_pack

        return behavior_pack(trade_episodes)

    def _regime_pack(self, trade_episodes: list[dict[str, Any]]) -> dict[str, Any]:
        from siglab.search.lineage_analysis import regime_pack

        return regime_pack(trade_episodes)

    def _policy_snapshot(self, spec: dict[str, Any]) -> dict[str, Any]:
        from siglab.search.lineage_analysis import policy_snapshot

        return policy_snapshot(spec)

    def _trade_style(self, spec: dict[str, Any]) -> str:
        from siglab.search.lineage_analysis import trade_style

        return trade_style(spec)

    def _diagnostic_tags(
        self,
        *,
        summary: dict[str, Any],
        behavior_pack: dict[str, Any],
        regime_pack: dict[str, Any],
    ) -> list[str]:
        from siglab.search.lineage_analysis import diagnostic_tags

        return diagnostic_tags(
            summary=summary,
            behavior=behavior_pack,
            regimes=regime_pack,
        )

    def _coverage_summary(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        return _coverage_summary(rows)

    def _archetype_coverage(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return _archetype_coverage(rows)

    def _novelty_pressure(
        self,
        rows: list[dict[str, Any]],
        *,
        diagnostics_by_hash: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        return _novelty_pressure(rows, diagnostics_by_hash=diagnostics_by_hash)

    def _failure_pattern_summary(
        self,
        rows: list[dict[str, Any]],
        *,
        diagnostics_by_hash: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        return _failure_pattern_summary(rows, diagnostics_by_hash=diagnostics_by_hash)

    def _behavior_pattern_summary(
        self,
        rows: list[dict[str, Any]],
        *,
        diagnostics_by_hash: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        return _behavior_pattern_summary(rows, diagnostics_by_hash=diagnostics_by_hash)

    def _regime_pattern_summary(
        self,
        rows: list[dict[str, Any]],
        *,
        diagnostics_by_hash: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        return _regime_pattern_summary(rows, diagnostics_by_hash=diagnostics_by_hash)

    def _drawdown_pattern_summary(
        self,
        rows: list[dict[str, Any]],
        *,
        diagnostics_by_hash: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        return _drawdown_pattern_summary(rows, diagnostics_by_hash=diagnostics_by_hash)

    def _gate_pattern_summary(
        self,
        rows: list[dict[str, Any]],
        *,
        diagnostics_by_hash: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        return _gate_pattern_summary(rows, diagnostics_by_hash=diagnostics_by_hash)

    def _equity_pattern_summary(
        self,
        rows: list[dict[str, Any]],
        *,
        diagnostics_by_hash: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        return _equity_pattern_summary(rows, diagnostics_by_hash=diagnostics_by_hash)

    def _pareto_frontier(
        self,
        rows: list[dict[str, Any]],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        return _pareto_frontier(rows, limit=limit)

    def _top_similar(
        self,
        *,
        rows: list[dict[str, Any]],
        parent_payload: dict[str, Any],
        parent_assets: set[str],
        parent_features: set[str],
        parent_maturity: str,
        limit: int,
        diagnostics_by_hash: dict[str, dict[str, Any]],
        rows_by_hash: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return _top_similar(
            rows=rows,
            parent_payload=parent_payload,
            parent_assets=parent_assets,
            parent_features=parent_features,
            parent_maturity=parent_maturity,
            limit=limit,
            diagnostics_by_hash=diagnostics_by_hash,
            rows_by_hash=rows_by_hash,
        )

    def _validation_leaders(
        self,
        rows: list[dict[str, Any]],
        *,
        limit: int,
        diagnostics_by_hash: dict[str, dict[str, Any]],
        rows_by_hash: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return _validation_leaders(
            rows,
            limit=limit,
            diagnostics_by_hash=diagnostics_by_hash,
            rows_by_hash=rows_by_hash,
        )

    def _outstanding_runs(
        self,
        rows: list[dict[str, Any]],
        *,
        limit: int,
        diagnostics_by_hash: dict[str, dict[str, Any]],
        rows_by_hash: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return _outstanding_runs(
            rows,
            limit=limit,
            diagnostics_by_hash=diagnostics_by_hash,
            rows_by_hash=rows_by_hash,
        )

    def _last_five_runs(
        self,
        rows: list[dict[str, Any]],
        *,
        diagnostics_by_hash: dict[str, dict[str, Any]],
        rows_by_hash: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return _last_five_runs(
            rows,
            diagnostics_by_hash=diagnostics_by_hash,
            rows_by_hash=rows_by_hash,
        )

    # ── DB-only helpers ──────────────────────────────────────────────────────

    def _track_experiments(
        self,
        track: str,
        *,
        run_session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        track_names = matching_track_names(track)
        placeholders = ",".join("?" for _ in track_names)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    created_at,
                    spec_hash,
                    family,
                    parent_hash,
                    aggregate_score,
                    passed,
                    deployd,
                    spec_json,
                    research_summary,
                    summary_json,
                    artifact_path
                FROM experiments
                WHERE track IN ({placeholders})
                ORDER BY created_at DESC
                """,
                tuple(track_names),
            ).fetchall()
        payloads = [experiment_row_payload(row) for row in rows]
        if run_session_id:
            payloads = filter_run_scope(payloads, run_session_id=run_session_id)
        return payloads

    def _row_run_session_id(self, row: dict[str, Any]) -> str:
        from siglab.search.lineage_analysis import row_run_session_id

        return row_run_session_id(row)

    def _filter_run_scope(
        self,
        rows: list[dict[str, Any]],
        *,
        run_session_id: str,
    ) -> list[dict[str, Any]]:
        return filter_run_scope(rows, run_session_id=run_session_id)

    def _experiment_row_payload(self, row: tuple[Any, ...]) -> dict[str, Any]:
        return experiment_row_payload(row)

    def _relevant_query_cards(
        self,
        *,
        track: str,
        parent: dict[str, Any],
        market_bundle: dict[str, Any] | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        track_names = matching_track_names(track)
        placeholders = ",".join("?" for _ in track_names)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    created_at,
                    family,
                    parent_hash,
                    market_bundle_id,
                    as_of,
                    provider,
                    canonical_query,
                    report_json
                FROM query_cards
                WHERE track IN ({placeholders})
                ORDER BY created_at DESC
                LIMIT 200
                """,
                tuple(track_names),
            ).fetchall()

        current_bundle_id = None
        if market_bundle:
            current_bundle_id = market_bundle.get("bundle_id")
        ranked: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            report = json.loads(row[7]) if row[7] else {}
            payload = {
                "created_at": row[0],
                "family": row[1],
                "parent_hash": row[2],
                "market_bundle_id": row[3],
                "as_of": row[4],
                "provider": row[5],
                "query": row[6],
                "answer": report.get("answer"),
                "insights": list(report.get("insights") or [])[:4],
                "sources": list(report.get("sources") or [])[:3],
            }
            ranked.append((_query_relevance(parent, payload, current_bundle_id), payload))

        ranked.sort(
            key=lambda item: (
                item[0],
                item[1].get("created_at") or "",
            ),
            reverse=True,
        )
        return [item[1] for item in ranked[:limit] if item[0] > 0.0]
