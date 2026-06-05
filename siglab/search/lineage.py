from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter, defaultdict
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from siglab.schemas import SignalSpec
from siglab.strategy_semantics import inferred_trade_style
from siglab.track_registry import canonical_track_name, matching_track_names


class LineageStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._init_db()

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

    def record(
        self,
        *,
        evaluation: dict[str, Any],
        parent_hash: str | None,
        research_summary: dict[str, Any],
        artifact_path: str,
    ) -> None:
        spec_payload = dict(evaluation["spec"])
        spec_payload["track"] = (
            canonical_track_name(spec_payload.get("track")) or spec_payload.get("track")
        )
        research_payload = dict(research_summary)
        research_payload["track"] = (
            canonical_track_name(research_payload.get("track")) or research_payload.get("track")
        )
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
                    spec_payload["track"],
                    spec_payload["family"],
                    parent_hash,
                    json.dumps(spec_payload, sort_keys=True),
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
                    spec_payload["track"],
                    spec_payload["family"],
                    parent_hash,
                    json.dumps(spec_payload, sort_keys=True),
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

        canonical_track = canonical_track_name(track) or track
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
            rows = [row for row in rows if not self._is_deterministic_experiment(row)]
        return rows[:limit]

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
        llm_phase_rows = [
            row for row in experiments_all if not self._is_deterministic_experiment(row)
        ]
        experiments = llm_phase_rows or experiments_all
        parent_payload = parent.canonical_dict()
        parent_assets = set(self._spec_assets(parent_payload))
        parent_features = set(str(feature) for feature in parent.features)
        parent_maturity = self._maturity_bucket(parent_payload.get("universe") or {})
        rows_by_hash = {
            str(row.get("spec_hash")): row
            for row in experiments
        }
        winners = [row for row in experiments if bool(row.get("passed"))]
        failures = [row for row in experiments if not bool(row.get("passed"))]
        diagnostics_by_hash = {
            str(row.get("spec_hash")): self._row_diagnostic_snapshot(row)
            for row in experiments[: max(limit * 8, 24)]
        }

        nearest_winners = self._top_similar(
            rows=winners,
            parent_payload=parent_payload,
            parent_assets=parent_assets,
            parent_features=parent_features,
            parent_maturity=parent_maturity,
            limit=limit,
            diagnostics_by_hash=diagnostics_by_hash,
            rows_by_hash=rows_by_hash,
        )
        nearest_failures = self._top_similar(
            rows=failures,
            parent_payload=parent_payload,
            parent_assets=parent_assets,
            parent_features=parent_features,
            parent_maturity=parent_maturity,
            limit=limit,
            diagnostics_by_hash=diagnostics_by_hash,
            rows_by_hash=rows_by_hash,
        )
        query_cards = (
            []
            if run_session_id
            else self._relevant_query_cards(
                track=track,
                parent=parent_payload,
                market_bundle=market_bundle,
                limit=limit,
            )
        )

        return {
            "market_bundle": dict(market_bundle or {}),
            "pareto_frontier": self._pareto_frontier(experiments, limit=limit),
            "validation_leaders": self._validation_leaders(
                experiments,
                limit=limit,
                diagnostics_by_hash=diagnostics_by_hash,
                rows_by_hash=rows_by_hash,
            ),
            "nearest_winners": nearest_winners,
            "nearest_failures": nearest_failures,
            "outstanding_runs": self._outstanding_runs(
                experiments,
                limit=max(limit * 2, 6),
                diagnostics_by_hash=diagnostics_by_hash,
                rows_by_hash=rows_by_hash,
            ),
            "last_five_runs": self._last_five_runs(
                llm_phase_rows or experiments,
                diagnostics_by_hash=diagnostics_by_hash,
                rows_by_hash=rows_by_hash,
            ),
            "coverage_summary": self._coverage_summary(experiments),
            "archetype_coverage": self._archetype_coverage(experiments),
            "novelty_pressure": self._novelty_pressure(
                llm_phase_rows or experiments,
                diagnostics_by_hash=diagnostics_by_hash,
            ),
            "failure_pattern_summary": self._failure_pattern_summary(
                failures,
                diagnostics_by_hash=diagnostics_by_hash,
            ),
            "behavior_pattern_summary": self._behavior_pattern_summary(
                failures,
                diagnostics_by_hash=diagnostics_by_hash,
            ),
            "regime_pattern_summary": self._regime_pattern_summary(
                failures,
                diagnostics_by_hash=diagnostics_by_hash,
            ),
            "drawdown_pattern_summary": self._drawdown_pattern_summary(
                failures,
                diagnostics_by_hash=diagnostics_by_hash,
            ),
            "gate_pattern_summary": self._gate_pattern_summary(
                failures,
                diagnostics_by_hash=diagnostics_by_hash,
            ),
            "equity_pattern_summary": self._equity_pattern_summary(
                failures,
                diagnostics_by_hash=diagnostics_by_hash,
            ),
            "query_cards": query_cards,
        }

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
                "track": canonical_track_name(row[1]) or row[1],
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
        for artifact_path in artifact_paths:
            path = Path(artifact_path)
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
            spec = self._spec_payload(row[9])
            payload_rows.append(
                {
                    "event_id": int(row[0]),
                    "created_at": row[1],
                    "track": canonical_track_name(row[2]) or row[2],
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
                    "feature_hash": self._feature_hash(spec.get("features") or []),
                    "deployment": self.deployment(row[4]),
                }
            )
        if run_session_id:
            payload_rows = self._filter_run_scope(payload_rows, run_session_id=run_session_id)
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

        spec = self._spec_payload(row[8])
        return {
            "created_at": row[0],
            "track": canonical_track_name(row[1]) or row[1],
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
            "feature_hash": self._feature_hash(spec.get("features") or []),
            "deployment": self.deployment(spec_hash),
        }

    def run_summaries(
        self,
        *,
        track: str | None = None,
        family: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = self.dashboard_rows(track=track, family=family)
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        run_meta: dict[str, dict[str, Any]] = {}

        for row in rows:
            research_summary = dict(row.get("research_summary") or {})
            run_context = dict(research_summary.get("run_context") or {})
            run_session_id = str(run_context.get("run_session_id") or f"legacy::{row['spec_hash']}")
            grouped[run_session_id].append(row)
            if run_session_id not in run_meta:
                benchmark_mode = bool(run_context.get("benchmark_mode"))
                run_meta[run_session_id] = {
                    "run_session_id": run_session_id,
                    "track": canonical_track_name(row.get("track")) or row.get("track"),
                    "runner_label": str(
                        run_context.get("runner_label")
                        or ("external_agent" if benchmark_mode else "siglab_harness")
                    ),
                    "run_label": str(run_context.get("run_label") or run_session_id),
                    "memory_scope": str(run_context.get("memory_scope") or "track_shared"),
                    "run_kind": "benchmark" if benchmark_mode else "harness",
                    "benchmark_mode": benchmark_mode,
                    "benchmark_deck": run_context.get("benchmark_deck"),
                    "phase_labels": set(),
                    "families": set(),
                }
            run_meta[run_session_id]["phase_labels"].add(
                str(run_context.get("phase_label") or "unknown")
            )
            run_meta[run_session_id]["families"].add(str(row.get("family") or "unknown"))

        summaries: list[dict[str, Any]] = []
        for run_session_id, members in grouped.items():
            meta = run_meta[run_session_id]
            ordered = sorted(members, key=lambda row: str(row.get("created_at") or ""))
            best = max(
                ordered,
                key=lambda row: (
                    _safe_float((row.get("summary") or {}).get("aggregate_score"), default=-1e18),
                    str(row.get("created_at") or ""),
                ),
            )
            llm_count = 0
            deterministic_count = 0
            tool_call_count = 0
            passed_count = 0
            deployd_count = 0
            for row in ordered:
                if self._is_deterministic_experiment(row):
                    deterministic_count += 1
                else:
                    llm_count += 1
                tool_call_count += self._row_tool_call_count(row)
                passed_count += int(bool(row.get("passed")))
                deployd_count += int(bool(row.get("deployd")))
            best_summary = dict(best.get("summary") or {})
            summaries.append(
                {
                    "run_session_id": run_session_id,
                    "run_label": meta["run_label"],
                    "track": meta["track"],
                    "runner_label": meta["runner_label"],
                    "run_kind": meta["run_kind"],
                    "memory_scope": meta["memory_scope"],
                    "benchmark_mode": meta["benchmark_mode"],
                    "benchmark_deck": meta["benchmark_deck"],
                    "phase_labels": sorted(meta["phase_labels"]),
                    "families": sorted(meta["families"]),
                    "experiment_count": len(ordered),
                    "llm_experiment_count": llm_count,
                    "deterministic_experiment_count": deterministic_count,
                    "tool_call_count": tool_call_count,
                    "passed_count": passed_count,
                    "deployd_count": deployd_count,
                    "first_created_at": ordered[0].get("created_at"),
                    "last_created_at": ordered[-1].get("created_at"),
                    "best_spec_hash": best.get("spec_hash"),
                    "best_family": best.get("family"),
                    "best_aggregate_score": best_summary.get("aggregate_score"),
                    "best_validation_total_return": best_summary.get("validation_total_return"),
                    "best_pre_audit_canonical_total_return": best_summary.get(
                        "pre_audit_canonical_total_return"
                    ),
                    "status": (
                        "deployd"
                        if deployd_count > 0
                        else "pass"
                        if passed_count > 0
                        else "fail"
                    ),
                }
            )
        summaries.sort(
            key=lambda row: (
                str(row.get("last_created_at") or ""),
                _safe_float(row.get("best_aggregate_score"), default=-1e18),
            ),
            reverse=True,
        )
        return summaries

    def _feature_hash(self, features: list[str]) -> str:
        payload = "|".join(sorted(str(feature) for feature in features))
        return sha256(payload.encode("utf-8")).hexdigest()[:16]

    def _is_deterministic_experiment(self, row: dict[str, Any]) -> bool:
        research_summary = dict(row.get("research_summary") or {})
        run_context = dict(research_summary.get("run_context") or {})
        if "deterministic" in run_context:
            return bool(run_context.get("deterministic"))
        phase_label = str(run_context.get("phase_label") or "").strip().lower()
        if phase_label == "burn_in":
            return True
        return False

    def _row_tool_call_count(self, row: dict[str, Any]) -> int:
        research_summary = dict(row.get("research_summary") or {})
        llm_tool_trace = dict(research_summary.get("llm_tool_trace") or {})
        trace = dict(llm_tool_trace.get("trace") or {})
        return len(list(trace.get("tool_calls") or []))

    def _spec_payload(self, raw_json: str) -> dict[str, Any]:
        payload = json.loads(raw_json)
        payload["track"] = canonical_track_name(payload.get("track")) or payload.get("track")
        return payload

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
        payloads = [self._experiment_row_payload(row) for row in rows]
        if run_session_id:
            payloads = self._filter_run_scope(payloads, run_session_id=run_session_id)
        return payloads

    def _row_run_session_id(self, row: dict[str, Any]) -> str:
        research_summary = dict(row.get("research_summary") or {})
        run_context = dict(research_summary.get("run_context") or {})
        return str(run_context.get("run_session_id") or "").strip()

    def _filter_run_scope(
        self,
        rows: list[dict[str, Any]],
        *,
        run_session_id: str,
    ) -> list[dict[str, Any]]:
        target = str(run_session_id or "").strip()
        if not target:
            return list(rows)
        return [
            row for row in rows
            if self._row_run_session_id(row) == target
        ]

    def _experiment_row_payload(self, row: tuple[Any, ...]) -> dict[str, Any]:
        return {
            "created_at": row[0],
            "spec_hash": row[1],
            "family": row[2],
            "parent_hash": row[3],
            "aggregate_score": row[4],
            "passed": bool(row[5]),
            "deployd": bool(row[6]),
            "spec": self._spec_payload(row[7]),
            "research_summary": json.loads(row[8]) if row[8] else {},
            "summary": json.loads(row[9]),
            "artifact_path": row[10],
        }

    def _pareto_frontier(
        self,
        rows: list[dict[str, Any]],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        frontier: list[dict[str, Any]] = []
        for row in rows:
            vector = self._objective_vector(row)
            dominated = False
            for other in rows:
                if other["spec_hash"] == row["spec_hash"]:
                    continue
                if self._dominates(self._objective_vector(other), vector):
                    dominated = True
                    break
            if dominated:
                continue
            frontier.append(
                {
                    "spec_hash": row["spec_hash"],
                    "family": row["family"],
                    "aggregate_score": row["aggregate_score"],
                    "median_sharpe": _safe_float(row["summary"].get("median_sharpe")),
                    "median_cagr": _safe_float(row["summary"].get("median_cagr")),
                    "holdout_total_return": _safe_float(
                        row["summary"].get("holdout_total_return"),
                    ),
                    "assets": self._spec_assets(row["spec"]),
                }
            )
        frontier.sort(
            key=lambda row: (
                _safe_float(row.get("aggregate_score")),
                _safe_float(row.get("holdout_total_return"), default=-1e9),
                _safe_float(row.get("median_sharpe")),
            ),
            reverse=True,
        )
        return frontier[:limit]

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
        ranked = sorted(
            rows,
            key=lambda row: (
                self._spec_similarity(
                    parent_payload=parent_payload,
                    parent_assets=parent_assets,
                    parent_features=parent_features,
                    parent_maturity=parent_maturity,
                    other=row,
                ),
                _safe_float(row.get("aggregate_score")),
            ),
            reverse=True,
        )
        payloads: list[dict[str, Any]] = []
        for row in ranked[:limit]:
            diagnostic = diagnostics_by_hash.get(str(row.get("spec_hash")) or "", {})
            payloads.append(
                {
                    "spec_hash": row["spec_hash"],
                    "family": row["family"],
                    "aggregate_score": row["aggregate_score"],
                    "passed": bool(row["passed"]),
                    "deployd": bool(row["deployd"]),
                    "features": list(row["spec"].get("features") or [])[:8],
                    "assets": self._spec_assets(row["spec"]),
                    "maturity_bucket": self._maturity_bucket(row["spec"].get("universe") or {}),
                    "trade_style": diagnostic.get("trade_style"),
                    "diagnostic_tags": list(diagnostic.get("diagnostic_tags") or [])[:4],
                    "behavior_pack": dict(diagnostic.get("behavior_pack") or {}),
                    "regime_pack": dict(diagnostic.get("regime_pack") or {}),
                    "trade_regime_pack": dict(diagnostic.get("trade_regime_pack") or {}),
                    "drawdown_pack": dict(diagnostic.get("drawdown_pack") or {}),
                    "equity_shift_pack": dict(diagnostic.get("equity_shift_pack") or {}),
                    "time_bin_pack": dict(diagnostic.get("time_bin_pack") or {}),
                    "exemplar_trade_pack": dict(diagnostic.get("exemplar_trade_pack") or {}),
                    "gate_diagnostics": dict(diagnostic.get("gate_diagnostics") or {}),
                    "policy": dict(diagnostic.get("policy") or {}),
                    "parent_delta": self._parent_delta(
                        row=row,
                        rows_by_hash=rows_by_hash,
                        diagnostics_by_hash=diagnostics_by_hash,
                    ),
                    "gate_reasons": list(row["summary"].get("gate_reasons") or [])[:4],
                    "summary": {
                        "median_sharpe": row["summary"].get("median_sharpe"),
                        "median_cagr": row["summary"].get("median_cagr"),
                        "median_total_return": row["summary"].get("median_total_return"),
                        "pre_audit_canonical_total_return": row["summary"].get(
                            "pre_audit_canonical_total_return"
                        ),
                        "holdout_total_return": row["summary"].get("holdout_total_return"),
                    },
                }
            )
        return payloads

    def _validation_leaders(
        self,
        rows: list[dict[str, Any]],
        *,
        limit: int,
        diagnostics_by_hash: dict[str, dict[str, Any]],
        rows_by_hash: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        ranked = sorted(
            rows,
            key=lambda row: (
                _safe_float(row["summary"].get("validation_total_return"), default=-1e9),
                _safe_float(row["summary"].get("validation_sharpe"), default=-1e9),
                _safe_float(row.get("aggregate_score"), default=-1e9),
            ),
            reverse=True,
        )
        payloads: list[dict[str, Any]] = []
        for row in ranked[:limit]:
            diagnostic = diagnostics_by_hash.get(str(row.get("spec_hash")) or "", {})
            payloads.append(
                {
                    "spec_hash": row["spec_hash"],
                    "family": row["family"],
                    "aggregate_score": row["aggregate_score"],
                    "passed": bool(row["passed"]),
                    "deployd": bool(row["deployd"]),
                    "features": list(row["spec"].get("features") or [])[:8],
                    "assets": self._spec_assets(row["spec"]),
                    "trade_style": diagnostic.get("trade_style"),
                    "diagnostic_tags": list(diagnostic.get("diagnostic_tags") or [])[:4],
                    "behavior_pack": dict(diagnostic.get("behavior_pack") or {}),
                    "regime_pack": dict(diagnostic.get("regime_pack") or {}),
                    "trade_regime_pack": dict(diagnostic.get("trade_regime_pack") or {}),
                    "drawdown_pack": dict(diagnostic.get("drawdown_pack") or {}),
                    "equity_shift_pack": dict(diagnostic.get("equity_shift_pack") or {}),
                    "time_bin_pack": dict(diagnostic.get("time_bin_pack") or {}),
                    "exemplar_trade_pack": dict(diagnostic.get("exemplar_trade_pack") or {}),
                    "gate_diagnostics": dict(diagnostic.get("gate_diagnostics") or {}),
                    "policy": dict(diagnostic.get("policy") or {}),
                    "parent_delta": self._parent_delta(
                        row=row,
                        rows_by_hash=rows_by_hash,
                        diagnostics_by_hash=diagnostics_by_hash,
                    ),
                    "gate_reasons": list(row["summary"].get("gate_reasons") or [])[:4],
                    "summary": {
                        "validation_total_return": row["summary"].get("validation_total_return"),
                        "validation_sharpe": row["summary"].get("validation_sharpe"),
                        "median_total_return": row["summary"].get("median_total_return"),
                        "median_sharpe": row["summary"].get("median_sharpe"),
                        "pre_audit_canonical_total_return": row["summary"].get(
                            "pre_audit_canonical_total_return"
                        ),
                    },
                }
            )
        return payloads

    def _spec_similarity(
        self,
        *,
        parent_payload: dict[str, Any],
        parent_assets: set[str],
        parent_features: set[str],
        parent_maturity: str,
        other: dict[str, Any],
    ) -> float:
        spec = other["spec"]
        score = 0.0
        if spec.get("family") == parent_payload.get("family"):
            score += 5.0
        if spec.get("neutrality_basis") == parent_payload.get("neutrality_basis"):
            score += 1.0

        other_assets = set(self._spec_assets(spec))
        score += float(len(parent_assets & other_assets)) * 1.5

        other_features = set(str(feature) for feature in spec.get("features") or [])
        score += float(len(parent_features & other_features)) * 0.35

        if self._maturity_bucket(spec.get("universe") or {}) == parent_maturity:
            score += 1.0
        if (spec.get("params") or {}).get("hedge_mode") == (parent_payload.get("params") or {}).get("hedge_mode"):
            score += 0.75
        if bool(other.get("deployd")):
            score += 0.25
        return score

    def _coverage_summary(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        family_attempts: Counter[str] = Counter()
        family_passes: Counter[str] = Counter()
        asset_counts: Counter[str] = Counter()
        feature_counts: Counter[str] = Counter()
        failure_modes: Counter[str] = Counter()

        for row in rows:
            family = str(row.get("family") or "unknown")
            family_attempts[family] += 1
            if row.get("passed"):
                family_passes[family] += 1
            asset_counts.update(self._spec_assets(row["spec"]))
            feature_counts.update(str(feature) for feature in row["spec"].get("features") or [])
            failure_modes.update(str(reason) for reason in row["summary"].get("gate_reasons") or [])

        return {
            "experiments_total": len(rows),
            "passed_total": int(sum(1 for row in rows if row.get("passed"))),
            "families": [
                {
                    "family": family,
                    "attempted": family_attempts[family],
                    "passed": family_passes[family],
                }
                for family, _count in family_attempts.most_common(6)
            ],
            "assets": [{"asset": asset, "count": count} for asset, count in asset_counts.most_common(8)],
            "features": [
                {"feature": feature, "count": count}
                for feature, count in feature_counts.most_common(10)
            ],
            "failure_modes": [
                {"reason": reason, "count": count}
                for reason, count in failure_modes.most_common(6)
            ],
        }

    def _archetype_coverage(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        attempted: Counter[str] = Counter()
        passed: Counter[str] = Counter()
        for row in rows:
            trade_style = self._trade_style(row.get("spec") or {})
            attempted[trade_style] += 1
            if bool(row.get("passed")):
                passed[trade_style] += 1
        return [
            {
                "trade_style": trade_style,
                "attempted": attempted[trade_style],
                "passed": passed[trade_style],
            }
            for trade_style, _count in attempted.most_common(6)
        ]

    def _outstanding_runs(
        self,
        rows: list[dict[str, Any]],
        *,
        limit: int,
        diagnostics_by_hash: dict[str, dict[str, Any]],
        rows_by_hash: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        ranked = [
            row
            for row in rows
            if not bool(row.get("deployd"))
        ]
        payloads: list[dict[str, Any]] = []
        for row in ranked[:limit]:
            diagnostic = diagnostics_by_hash.get(str(row.get("spec_hash")) or "", {})
            payloads.append(
                {
                    "spec_hash": row["spec_hash"],
                    "family": row["family"],
                    "aggregate_score": row["aggregate_score"],
                    "passed": bool(row["passed"]),
                    "trade_style": diagnostic.get("trade_style"),
                    "diagnostic_tags": list(diagnostic.get("diagnostic_tags") or [])[:4],
                    "policy": dict(diagnostic.get("policy") or {}),
                    "gate_reasons": list(row["summary"].get("gate_reasons") or [])[:4],
                    "summary": {
                        "median_total_return": row["summary"].get("median_total_return"),
                        "median_sharpe": row["summary"].get("median_sharpe"),
                        "validation_total_return": row["summary"].get("validation_total_return"),
                        "validation_sharpe": row["summary"].get("validation_sharpe"),
                        "pre_audit_canonical_total_return": row["summary"].get(
                            "pre_audit_canonical_total_return"
                        ),
                    },
                    "parent_delta": self._parent_delta(
                        row=row,
                        rows_by_hash=rows_by_hash,
                        diagnostics_by_hash=diagnostics_by_hash,
                    ),
                }
            )
        return payloads

    def _last_five_runs(
        self,
        rows: list[dict[str, Any]],
        *,
        diagnostics_by_hash: dict[str, dict[str, Any]],
        rows_by_hash: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for row in rows[:5]:
            diagnostic = diagnostics_by_hash.get(str(row.get("spec_hash")) or "", {})
            gate_diagnostics = dict(diagnostic.get("gate_diagnostics") or {})
            summary = dict(row.get("summary") or {})
            payloads.append(
                {
                    "spec_hash": row["spec_hash"],
                    "parent_hash": row.get("parent_hash"),
                    "family": row["family"],
                    "hypothesis": str((row.get("spec") or {}).get("hypothesis") or ""),
                    "trade_style": diagnostic.get("trade_style"),
                    "features": list((row.get("spec") or {}).get("features") or [])[:6],
                    "summary": {
                        "median_total_return": summary.get("median_total_return"),
                        "validation_total_return": summary.get("validation_total_return"),
                        "pre_audit_canonical_total_return": summary.get(
                            "pre_audit_canonical_total_return"
                        ),
                    },
                    "active_bar_fraction": gate_diagnostics.get("active_bar_fraction"),
                    "gate_bottlenecks": list(gate_diagnostics.get("bottleneck_tags") or [])[:4],
                    "sweep_drift": {
                        "material_change": bool(summary.get("policy_sweep_material_change")),
                        "changed_keys": list(summary.get("policy_sweep_changed_keys") or [])[:6],
                        "changed_param_count": len(list(summary.get("policy_sweep_changed_keys") or [])),
                        "activity_penalty": summary.get("policy_sweep_activity_penalty"),
                        "proposed_policy": dict(summary.get("policy_sweep_proposed_policy") or {}),
                        "frozen_policy": dict(summary.get("policy_sweep_frozen_policy") or {}),
                    },
                    "policy": dict(diagnostic.get("policy") or {}),
                    "parent_delta": self._parent_delta(
                        row=row,
                        rows_by_hash=rows_by_hash,
                        diagnostics_by_hash=diagnostics_by_hash,
                    ),
                }
            )
        return payloads

    def _novelty_pressure(
        self,
        rows: list[dict[str, Any]],
        *,
        diagnostics_by_hash: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        recent_rows = list(rows[:5])
        if not recent_rows:
            return {
                "required": False,
                "reason": "insufficient_recent_runs",
                "recent_count": 0,
            }

        family_counts: Counter[str] = Counter()
        trade_style_counts: Counter[str] = Counter()
        feature_counts: Counter[str] = Counter()
        restrictive_count = 0
        low_activity_count = 0

        for row in recent_rows:
            family_counts[str(row.get("family") or "unknown")] += 1
            diagnostic = diagnostics_by_hash.get(str(row.get("spec_hash")) or "", {})
            trade_style = str(diagnostic.get("trade_style") or "unknown")
            trade_style_counts[trade_style] += 1
            feature_counts.update(str(feature) for feature in (row.get("spec") or {}).get("features") or [])
            gate_diagnostics = dict(diagnostic.get("gate_diagnostics") or {})
            active_fraction = _safe_float(
                gate_diagnostics.get("active_bar_fraction"),
                default=None,
            )
            if active_fraction is not None and active_fraction <= 0.02:
                low_activity_count += 1
            bottlenecks = set(str(tag) for tag in gate_diagnostics.get("bottleneck_tags") or [])
            if "restrictive_regime_gate" in bottlenecks or "weak_signal_coverage" in bottlenecks:
                restrictive_count += 1

        dominant_family = family_counts.most_common(1)[0] if family_counts else ("unknown", 0)
        dominant_trade_style = trade_style_counts.most_common(1)[0] if trade_style_counts else ("unknown", 0)
        dominant_family_rows = [
            row
            for row in recent_rows
            if str(row.get("family") or "unknown") == dominant_family[0]
        ]
        dominant_family_best_pre_audit: float
        scores: list[float] = []
        for row in dominant_family_rows:
            score = _safe_float(
                (row.get("summary") or {}).get("pre_audit_canonical_total_return"),
                default=float("-inf"),
            )
            if score is not None:
                scores.append(score)
        dominant_family_best_pre_audit = max(scores, default=float("-inf"))
        dominant_family_positive_anchor = dominant_family_best_pre_audit > 0.02
        family_concentration_requires_branch = (
            dominant_family[1] >= 4 and not dominant_family_positive_anchor
        )
        overused_features = [
            {"feature": feature, "count": count}
            for feature, count in feature_counts.most_common(6)
        ]
        required = len(recent_rows) >= 4 and (
            family_concentration_requires_branch
            or dominant_trade_style[1] >= 4
            or restrictive_count >= 3
            or low_activity_count >= 3
        )
        reason_bits: list[str] = []
        if family_concentration_requires_branch:
            reason_bits.append("family_concentration")
        if dominant_trade_style[1] >= 4:
            reason_bits.append("trade_style_concentration")
        if restrictive_count >= 3:
            reason_bits.append("restrictive_gating")
        if low_activity_count >= 3:
            reason_bits.append("low_activity")
        if not reason_bits:
            reason_bits.append("healthy_variation")
        return {
            "required": required,
            "reason": ",".join(reason_bits),
            "recent_count": len(recent_rows),
            "dominant_family": {
                "family": dominant_family[0],
                "count": dominant_family[1],
            },
            "dominant_family_positive_anchor": dominant_family_positive_anchor,
            "dominant_family_best_pre_audit": (
                None
                if not math.isfinite(dominant_family_best_pre_audit)
                else round(dominant_family_best_pre_audit, 6)
            ),
            "dominant_trade_style": {
                "trade_style": dominant_trade_style[0],
                "count": dominant_trade_style[1],
            },
            "restrictive_gate_share": round(restrictive_count / len(recent_rows), 4),
            "low_activity_share": round(low_activity_count / len(recent_rows), 4),
            "overused_features": overused_features,
        }

    def _failure_pattern_summary(
        self,
        rows: list[dict[str, Any]],
        *,
        diagnostics_by_hash: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        gate_reasons: Counter[str] = Counter()
        diagnostic_tags: Counter[str] = Counter()
        for row in rows:
            gate_reasons.update(str(reason) for reason in row["summary"].get("gate_reasons") or [])
            diagnostic = diagnostics_by_hash.get(str(row.get("spec_hash")) or "", {})
            diagnostic_tags.update(str(tag) for tag in diagnostic.get("diagnostic_tags") or [])
        return {
            "gate_reasons": [
                {"reason": reason, "count": count}
                for reason, count in gate_reasons.most_common(6)
            ],
            "diagnostic_tags": [
                {"tag": tag, "count": count}
                for tag, count in diagnostic_tags.most_common(6)
            ],
        }

    def _behavior_pattern_summary(
        self,
        rows: list[dict[str, Any]],
        *,
        diagnostics_by_hash: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        trade_counts: list[float] = []
        holding_bars: list[float] = []
        gap_hours: list[float] = []
        flip_rates: list[float] = []
        pattern_counts: Counter[str] = Counter()
        for row in rows:
            diagnostic = diagnostics_by_hash.get(str(row.get("spec_hash")) or "", {})
            behavior_pack = diagnostic.get("behavior_pack") or {}
            trade_count = _safe_float(behavior_pack.get("trade_count"), default=-1.0)
            if trade_count is not None and trade_count >= 0.0:
                trade_counts.append(trade_count)
            median_holding_bars = _safe_float(behavior_pack.get("median_holding_bars"), default=-1.0)
            if median_holding_bars is not None and median_holding_bars >= 0.0:
                holding_bars.append(median_holding_bars)
            median_gap_hours = _safe_float(behavior_pack.get("median_gap_hours"), default=-1.0)
            if median_gap_hours is not None and median_gap_hours >= 0.0:
                gap_hours.append(median_gap_hours)
            flip_rate = _safe_float(behavior_pack.get("flip_rate"), default=-1.0)
            if flip_rate is not None and flip_rate >= 0.0:
                flip_rates.append(flip_rate)
            for tag in diagnostic.get("diagnostic_tags") or []:
                if tag in {"few_trades", "overtrading", "sparse_entries", "very_short_holds"}:
                    pattern_counts[str(tag)] += 1
        return {
            "median_trade_count": _median_value(trade_counts),
            "median_holding_bars": _median_value(holding_bars),
            "median_gap_hours": _median_value(gap_hours),
            "median_flip_rate": _median_value(flip_rates),
            "patterns": [
                {"pattern": pattern, "count": count}
                for pattern, count in pattern_counts.most_common(6)
            ],
        }

    def _regime_pattern_summary(
        self,
        rows: list[dict[str, Any]],
        *,
        diagnostics_by_hash: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        dimension_counters: dict[str, Counter[str]] = defaultdict(Counter)
        for row in rows:
            diagnostic = diagnostics_by_hash.get(str(row.get("spec_hash")) or "", {})
            regime_pack = diagnostic.get("regime_pack") or {}
            for dimension, payload in regime_pack.items():
                worst_label = str((payload or {}).get("worst_label") or "").strip()
                if worst_label:
                    dimension_counters[str(dimension)][worst_label] += 1
        return {
            dimension: [
                {"label": label, "count": count}
                for label, count in counter.most_common(3)
            ]
            for dimension, counter in dimension_counters.items()
        }

    def _drawdown_pattern_summary(
        self,
        rows: list[dict[str, Any]],
        *,
        diagnostics_by_hash: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        drawdowns: list[float] = []
        signal_alignment: list[float] = []
        direction_counts: Counter[str] = Counter()
        feature_counts: Counter[str] = Counter()
        for row in rows:
            diagnostic = diagnostics_by_hash.get(str(row.get("spec_hash")) or "", {})
            drawdown_pack = diagnostic.get("drawdown_pack") or {}
            drawdown = _safe_float(drawdown_pack.get("drawdown"), default=None)
            if drawdown is not None:
                drawdowns.append(drawdown)
            direction = str(drawdown_pack.get("dominant_position_direction") or "").strip()
            if direction:
                direction_counts[direction] += 1
            signal_story = dict(drawdown_pack.get("signal_story") or {})
            aligned_fraction = _safe_float(
                signal_story.get("aligned_with_position_fraction"),
                default=None,
            )
            if aligned_fraction is not None:
                signal_alignment.append(aligned_fraction)
            for payload in list(drawdown_pack.get("top_feature_contributors") or [])[:4]:
                feature = str((payload or {}).get("feature") or "").strip()
                if feature:
                    feature_counts[feature] += 1
        return {
            "median_drawdown": _median_value(drawdowns),
            "median_signal_alignment": _median_value(signal_alignment),
            "dominant_position_directions": [
                {"direction": direction, "count": count}
                for direction, count in direction_counts.most_common(4)
            ],
            "common_feature_contributors": [
                {"feature": feature, "count": count}
                for feature, count in feature_counts.most_common(6)
            ],
        }

    def _gate_pattern_summary(
        self,
        rows: list[dict[str, Any]],
        *,
        diagnostics_by_hash: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        active_fractions: list[float] = []
        entry_fractions: list[float] = []
        alignment_fractions: list[float] = []
        position_flip_rates: list[float] = []
        bottleneck_tags: Counter[str] = Counter()
        for row in rows:
            diagnostic = diagnostics_by_hash.get(str(row.get("spec_hash")) or "", {})
            gate_diagnostics = diagnostic.get("gate_diagnostics") or {}
            active = _safe_float(gate_diagnostics.get("active_bar_fraction"), default=None)
            if active is not None:
                active_fractions.append(active)
            entry = _safe_float(gate_diagnostics.get("entry_signal_bar_fraction"), default=None)
            if entry is not None:
                entry_fractions.append(entry)
            alignment = _safe_float(gate_diagnostics.get("score_alignment_when_active"), default=None)
            if alignment is not None:
                alignment_fractions.append(alignment)
            position_flip = _safe_float(gate_diagnostics.get("position_flip_rate"), default=None)
            if position_flip is not None:
                position_flip_rates.append(position_flip)
            bottleneck_tags.update(str(tag) for tag in gate_diagnostics.get("bottleneck_tags") or [])
        return {
            "median_active_bar_fraction": _median_value(active_fractions),
            "median_entry_signal_bar_fraction": _median_value(entry_fractions),
            "median_score_alignment_when_active": _median_value(alignment_fractions),
            "median_position_flip_rate": _median_value(position_flip_rates),
            "bottleneck_tags": [
                {"tag": tag, "count": count}
                for tag, count in bottleneck_tags.most_common(6)
            ],
        }

    def _equity_pattern_summary(
        self,
        rows: list[dict[str, Any]],
        *,
        diagnostics_by_hash: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        max_drawdowns: list[float] = []
        post_peak_entries: list[float] = []
        drawdown_window_entries: list[float] = []
        regime_counts: dict[str, Counter[str]] = defaultdict(Counter)
        for row in rows:
            diagnostic = diagnostics_by_hash.get(str(row.get("spec_hash")) or "", {})
            equity_pack = diagnostic.get("equity_shift_pack") or {}
            max_drawdown = _safe_float(equity_pack.get("max_drawdown"), default=None)
            if max_drawdown is not None:
                max_drawdowns.append(max_drawdown)
            post_peak = _safe_float(
                ((equity_pack.get("post_peak") or {}).get("entries_per_day")),
                default=None,
            )
            if post_peak is not None:
                post_peak_entries.append(post_peak)
            drawdown_entries = _safe_float(
                ((equity_pack.get("drawdown_window") or {}).get("entries_per_day")),
                default=None,
            )
            if drawdown_entries is not None:
                drawdown_window_entries.append(drawdown_entries)
            regime = dict(((equity_pack.get("drawdown_window") or {}).get("regime")) or {})
            for dimension, value in regime.items():
                if not str(dimension).endswith("_label"):
                    continue
                label = str(value or "").strip()
                if label:
                    regime_counts[str(dimension)][label] += 1
        return {
            "median_max_drawdown": _median_value(max_drawdowns),
            "median_post_peak_entries_per_day": _median_value(post_peak_entries),
            "median_drawdown_window_entries_per_day": _median_value(drawdown_window_entries),
            "drawdown_window_regimes": {
                dimension: [
                    {"label": label, "count": count}
                    for label, count in counter.most_common(3)
                ]
                for dimension, counter in regime_counts.items()
            },
        }

    def _row_diagnostic_snapshot(self, row: dict[str, Any]) -> dict[str, Any]:
        spec = row.get("spec") or {}
        summary = row.get("summary") or {}
        artifact_payload = self._artifact_payload(row)
        trade_episodes = self._pre_audit_trade_episodes(artifact_payload)
        behavior_pack = self._behavior_pack(trade_episodes)
        regime_pack = self._regime_pack(trade_episodes)
        canonical_run = dict((artifact_payload or {}).get("canonical_run") or {})
        drawdown_pack = dict(canonical_run.get("pre_audit_drawdown_pack") or {})
        context_pack = dict(canonical_run.get("pre_audit_context_pack") or {})
        policy = {
            **dict(context_pack.get("policy_context") or {}),
            **self._policy_snapshot(spec),
        }
        diagnostic_tags = self._diagnostic_tags(
            summary=summary,
            behavior_pack=behavior_pack,
            regime_pack=regime_pack,
        )
        return {
            "trade_style": self._trade_style(spec),
            "policy": policy,
            "behavior_pack": behavior_pack,
            "regime_pack": regime_pack,
            "trade_regime_pack": dict(context_pack.get("trade_regime_pack") or {}),
            "drawdown_pack": drawdown_pack,
            "equity_shift_pack": dict(context_pack.get("equity_shift_pack") or {}),
            "time_bin_pack": dict(context_pack.get("time_bin_pack") or {}),
            "exemplar_trade_pack": dict(context_pack.get("exemplar_trades") or {}),
            "gate_diagnostics": dict(context_pack.get("gate_diagnostics") or {}),
            "diagnostic_tags": diagnostic_tags,
        }

    def _parent_delta(
        self,
        *,
        row: dict[str, Any],
        rows_by_hash: dict[str, dict[str, Any]],
        diagnostics_by_hash: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        parent_hash = str(row.get("parent_hash") or "").strip()
        if not parent_hash:
            return {}
        parent_row = rows_by_hash.get(parent_hash)
        if parent_row is None:
            return {}
        child_diag = diagnostics_by_hash.get(str(row.get("spec_hash")) or "", {})
        parent_diag = diagnostics_by_hash.get(parent_hash)
        if parent_diag is None:
            parent_diag = self._row_diagnostic_snapshot(parent_row)
        child_summary = dict(row.get("summary") or {})
        parent_summary = dict(parent_row.get("summary") or {})
        regime_changes: dict[str, dict[str, str]] = {}
        dimension_keys = sorted(
            set(child_diag.get("regime_pack") or {}).union(set(parent_diag.get("regime_pack") or {}))
        )
        for dimension in dimension_keys:
            child_label = str(((child_diag.get("regime_pack") or {}).get(dimension) or {}).get("worst_label") or "")
            parent_label = str(((parent_diag.get("regime_pack") or {}).get(dimension) or {}).get("worst_label") or "")
            if child_label and child_label != parent_label:
                regime_changes[dimension] = {"parent": parent_label, "child": child_label}
        delta = {
            "pre_audit_return_delta": _delta(
                child_summary.get("pre_audit_canonical_total_return"),
                parent_summary.get("pre_audit_canonical_total_return"),
            ),
            "validation_return_delta": _delta(
                child_summary.get("validation_total_return"),
                parent_summary.get("validation_total_return"),
            ),
            "trade_count_delta": _delta(
                (child_diag.get("behavior_pack") or {}).get("trade_count"),
                (parent_diag.get("behavior_pack") or {}).get("trade_count"),
            ),
            "median_holding_bars_delta": _delta(
                (child_diag.get("behavior_pack") or {}).get("median_holding_bars"),
                (parent_diag.get("behavior_pack") or {}).get("median_holding_bars"),
            ),
            "flip_rate_delta": _delta(
                (child_diag.get("behavior_pack") or {}).get("flip_rate"),
                (parent_diag.get("behavior_pack") or {}).get("flip_rate"),
            ),
            "drawdown_delta": _delta(
                (child_diag.get("drawdown_pack") or {}).get("drawdown"),
                (parent_diag.get("drawdown_pack") or {}).get("drawdown"),
            ),
            "regime_changes": regime_changes,
        }
        return {key: value for key, value in delta.items() if value not in ({}, None)}

    def _artifact_payload(self, row: dict[str, Any]) -> dict[str, Any] | None:
        artifact_path = str(row.get("artifact_path") or "").strip()
        if not artifact_path:
            return None
        path = Path(artifact_path)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text())
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    def _pre_audit_trade_episodes(
        self,
        artifact_payload: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        if not artifact_payload:
            return []
        canonical_run = dict(artifact_payload.get("canonical_run") or {})
        episodes = list(canonical_run.get("trade_episodes") or [])
        if not episodes:
            return []
        visual_split = dict(canonical_run.get("visual_split") or {})
        audit_start = None
        for window in list(visual_split.get("ranges") or []):
            if str(window.get("kind") or "") == "audit_holdout":
                audit_start = _parse_timestamp(window.get("start_timestamp"))
                break
        if audit_start is None:
            return [episode for episode in episodes if isinstance(episode, dict)]

        filtered: list[dict[str, Any]] = []
        for episode in episodes:
            if not isinstance(episode, dict):
                continue
            end_timestamp = _parse_timestamp(episode.get("end_timestamp"))
            if end_timestamp is None:
                end_timestamp = _parse_timestamp(episode.get("start_timestamp"))
            if end_timestamp is None or end_timestamp >= audit_start:
                continue
            filtered.append(episode)
        return filtered

    def _behavior_pack(self, trade_episodes: list[dict[str, Any]]) -> dict[str, Any]:
        if not trade_episodes:
            return {}
        bars: list[float] = []
        for episode in trade_episodes:
            bars_val = _safe_float(episode.get("bars"), default=-1.0)
            if bars_val is not None and bars_val >= 0.0:
                bars.append(bars_val)
        returns: list[float] = []
        for episode in trade_episodes:
            total_return = episode.get("total_return")
            if total_return is not None:
                returns.append(float(total_return))
        start_times = [
            timestamp
            for episode in trade_episodes
            if (timestamp := _parse_timestamp(episode.get("start_timestamp"))) is not None
        ]
        gaps_hours: list[float] = []
        for previous, current in zip(start_times, start_times[1:]):
            gaps_hours.append((current - previous).total_seconds() / 3600.0)

        directions = [str(episode.get("direction") or "") for episode in trade_episodes]
        flips = sum(
            1
            for previous, current in zip(directions, directions[1:])
            if previous and current and previous != current
        )
        direction_counts: Counter[str] = Counter(
            direction for direction in directions if direction and direction != "flat"
        )
        dominant_direction = direction_counts.most_common(1)[0][0] if direction_counts else None

        return {
            "trade_count": len(trade_episodes),
            "median_holding_bars": _median_value(bars),
            "median_total_return": _median_value(returns),
            "profitable_trade_fraction": _safe_float(
                sum(1 for value in returns if value > 0.0) / len(returns)
                if returns
                else None
            ),
            "median_gap_hours": _median_value(gaps_hours),
            "flip_rate": _safe_float(flips / max(1, len(directions) - 1) if len(directions) > 1 else 0.0),
            "dominant_direction": dominant_direction,
            "direction_counts": dict(direction_counts),
        }

    def _regime_pack(self, trade_episodes: list[dict[str, Any]]) -> dict[str, Any]:
        if not trade_episodes:
            return {}
        label_keys: set[str] = set()
        for episode in trade_episodes:
            entry_regime = dict(episode.get("entry_regime") or {})
            for key, value in entry_regime.items():
                if key.endswith("_label") and value:
                    label_keys.add(key)
        dimensions = {
            key.removesuffix("_label"): key
            for key in sorted(label_keys)
        }
        regime_pack: dict[str, Any] = {}
        for dimension, label_key in dimensions.items():
            returns_by_label: dict[str, list[float]] = defaultdict(list)
            for episode in trade_episodes:
                entry_regime = dict(episode.get("entry_regime") or {})
                label = str(entry_regime.get(label_key) or "").strip()
                total_return = _safe_float(episode.get("total_return"), default=None)
                if not label or total_return is None:
                    continue
                returns_by_label[label].append(total_return)
            if not returns_by_label:
                continue
            averaged = {
                label: sum(values) / len(values)
                for label, values in returns_by_label.items()
                if values
            }
            if not averaged:
                continue
            best_label = max(averaged.items(), key=lambda item: item[1])[0]
            worst_label = min(averaged.items(), key=lambda item: item[1])[0]
            regime_pack[dimension] = {
                "best_label": best_label,
                "worst_label": worst_label,
            }
        return regime_pack

    def _policy_snapshot(self, spec: dict[str, Any]) -> dict[str, Any]:
        params = dict(spec.get("params") or {})
        keys = [
            "trade_style",
            "gross_target",
            "max_gross_target",
            "signal_leverage_scale",
            "entry_abs_score",
            "exit_abs_score",
            "flip_abs_score",
            "max_holding_bars",
            "cooldown_bars",
            "min_abs_score",
        ]
        policy = {key: params.get(key) for key in keys if key in params}
        return {key: value for key, value in policy.items() if value is not None}

    def _trade_style(self, spec: dict[str, Any]) -> str:
        return inferred_trade_style(spec)

    def _diagnostic_tags(
        self,
        *,
        summary: dict[str, Any],
        behavior_pack: dict[str, Any],
        regime_pack: dict[str, Any],
    ) -> list[str]:
        tags: list[str] = []
        gate_reasons = {str(reason) for reason in summary.get("gate_reasons") or []}
        if gate_reasons & {
            "non_positive_validation_return",
            "non_positive_validation_sharpe",
            "non_positive_median_return",
            "non_positive_median_sharpe",
        }:
            tags.append("negative_validation")
        if "drawdown_limit" in gate_reasons:
            tags.append("drawdown_limited")

        trade_count = _safe_float(behavior_pack.get("trade_count"), default=-1.0)
        median_gap_hours = _safe_float(behavior_pack.get("median_gap_hours"), default=-1.0)
        median_holding_bars = _safe_float(behavior_pack.get("median_holding_bars"), default=-1.0)
        flip_rate = _safe_float(behavior_pack.get("flip_rate"), default=-1.0)
        if trade_count is not None and 0.0 <= trade_count <= 2.0:
            tags.append("few_trades")
        if (trade_count is not None and trade_count <= 3.0
                and median_gap_hours is not None and median_gap_hours >= 72.0):
            tags.append("sparse_entries")
        if flip_rate is not None and flip_rate >= 0.5:
            tags.append("overtrading")
        if median_holding_bars is not None and 0.0 <= median_holding_bars <= 6.0:
            tags.append("very_short_holds")

        regime_tag_map = {
            "market_trend": {
                "market_downtrend": "weak_market_downtrend",
            },
            "market_volatility": {
                "high_volatility": "weak_high_volatility",
            },
            "pair_volatility": {
                "high_volatility": "weak_high_volatility",
            },
            "funding_dispersion": {
                "funding_dispersed": "weak_funding_dispersed",
            },
            "co_movement": {
                "low_co_movement": "weak_low_co_movement",
            },
            "pair_correlation": {
                "low_correlation": "weak_low_correlation",
            },
            "breadth": {
                "weak_participation": "weak_narrow_breadth",
            },
            "pair_direction": {
                "asset_2_leading": "weak_asset_2_leading",
            },
        }
        for dimension, label_map in regime_tag_map.items():
            worst_label = str((regime_pack.get(dimension) or {}).get("worst_label") or "")
            if worst_label in label_map:
                tags.append(label_map[worst_label])
        return sorted(set(tags))

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
            ranked.append((self._query_relevance(parent, payload, current_bundle_id), payload))

        ranked.sort(
            key=lambda item: (
                item[0],
                item[1].get("created_at") or "",
            ),
            reverse=True,
        )
        return [item[1] for item in ranked[:limit] if item[0] > 0.0]

    def _query_relevance(
        self,
        parent: dict[str, Any],
        query_card: dict[str, Any],
        current_bundle_id: str | None,
    ) -> float:
        parent_tokens = set()
        parent_tokens.update(_tokens(parent.get("family")))
        parent_tokens.update(_tokens(parent.get("neutrality_basis")))
        parent_tokens.update(_tokens(" ".join(parent.get("features") or [])))
        parent_tokens.update(_tokens(" ".join(self._spec_assets(parent))))

        query_tokens = set()
        query_tokens.update(_tokens(query_card.get("family")))
        query_tokens.update(_tokens(query_card.get("query")))
        query_tokens.update(_tokens(query_card.get("answer")))
        for insight in query_card.get("insights") or []:
            query_tokens.update(_tokens(insight))

        score = float(len(parent_tokens & query_tokens))
        if current_bundle_id and query_card.get("market_bundle_id") == current_bundle_id:
            score += 3.0
        if query_card.get("family") == parent.get("family"):
            score += 2.0
        return score

    def _spec_assets(self, spec: dict[str, Any]) -> list[str]:
        universe = spec.get("universe") or {}
        return [str(asset).upper() for asset in universe.get("basis_groups") or []]

    def _maturity_bucket(self, universe: dict[str, Any]) -> str:
        min_days = int(universe.get("min_days_to_expiry", 0) or 0)
        max_days = int(universe.get("max_days_to_expiry", 0) or 0)
        if max_days <= 30:
            return "short"
        if min_days >= 60:
            return "long"
        return "medium"

    def _objective_vector(self, row: dict[str, Any]) -> tuple[float, float, float]:
        summary = row.get("summary") or {}
        aggregate = _safe_float(row.get("aggregate_score"))
        holdout = _safe_float(
            summary.get("holdout_total_return"),
            default=_safe_float(summary.get("median_total_return")),
        )
        sharpe = _safe_float(summary.get("median_sharpe"))
        return (
            aggregate if aggregate is not None else 0.0,
            holdout if holdout is not None else 0.0,
            sharpe if sharpe is not None else 0.0,
        )

    def _dominates(
        self,
        left: tuple[float, float, float],
        right: tuple[float, float, float],
    ) -> bool:
        return all(left_i >= right_i for left_i, right_i in zip(left, right)) and any(
            left_i > right_i for left_i, right_i in zip(left, right)
        )


def _safe_float(value: Any, *, default: float | None = 0.0) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:
        return default
    return number


def _median_value(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _delta(current: Any, previous: Any) -> float | None:
    current_value = _safe_float(current, default=None)
    previous_value = _safe_float(previous, default=None)
    if current_value is None or previous_value is None:
        return None
    return current_value - previous_value


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _tokens(value: Any) -> set[str]:
    text = str(value or "").lower()
    cleaned = "".join(char if char.isalnum() else " " for char in text)
    return {token for token in cleaned.split() if len(token) >= 3}



