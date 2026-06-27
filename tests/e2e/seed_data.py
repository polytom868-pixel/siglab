"""Seed test data for Playwright E2E tests.

Creates a temporary SQLite database (and optional ops-artifact JSON files)
so the dashboard API returns realistic data for the E2E test flows.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any

from siglab.dashboard.routes import DashboardState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now(offset_minutes: int = 0) -> str:
    """Return ISO-8601 UTC string with optional offset."""
    dt = datetime.now(timezone.utc) + timedelta(minutes=offset_minutes)
    return dt.isoformat()


def _make_research_summary(
    run_session_id: str,
    run_label: str,
    runner_label: str = "siglab-agent",
    benchmark_mode: bool = False,
    memory_scope: str = "track_shared",
    phase_label: str = "Phase 1",
    deterministic: bool = False,
    iteration_number: int = 1,
    llm_tool_trace: bool = True,
) -> str:
    """Build a realistic research_summary JSON blob."""
    rs: dict[str, Any] = {
        "run_context": {
            "run_session_id": run_session_id,
            "run_label": run_label,
            "runner_label": runner_label,
            "benchmark_mode": benchmark_mode,
            "memory_scope": memory_scope,
            "phase_label": phase_label,
            "deterministic": deterministic,
            "iteration_number": iteration_number,
        },
        "workspace": {
            "planner_trace_path": None,
            "writer_trace_path": None,
            "reflector_trace_path": None,
        },
    }
    if llm_tool_trace:
        rs["llm_tool_trace"] = {
            "trace": {
                "provider": "anthropic",
                "model": "claude-sonnet-4",
                "tool_calls": [
                    {
                        "name": "think",
                        "latency_ms": 1200,
                        "input_tokens": 1500,
                        "output_tokens": 200,
                        "stage": "proposal",
                    },
                    {
                        "name": "search_features",
                        "latency_ms": 3400,
                        "input_tokens": 800,
                        "output_tokens": 600,
                        "stage": "proposal",
                    },
                ],
                "tool_rounds_used": 2,
                "tool_count_available": 25,
                "final_content_preview": "Selected features based on signal analysis.",
                "response_finish_reason": "end_turn",
            },
            "parent_family": None,
            "parent_hash": None,
            "spec_count": 1,
        }
    return json.dumps(rs)


def _make_summary_json(
    aggregate_score: float,
    median_sharpe: float = 1.0,
    median_total_return: float = 0.05,
    median_cagr: float = 0.03,
    median_calmar: float = 0.4,
    validation_total_return: float | None = None,
    pre_audit_canonical_total_return: float | None = None,
    holdout_available: bool = False,
) -> str:
    """Build a realistic summary_json blob."""
    summary: dict[str, Any] = {
        "aggregate_score": aggregate_score,
        "median_sharpe": median_sharpe,
        "median_total_return": median_total_return,
        "median_cagr": median_cagr,
        "median_calmar": median_calmar,
    }
    if validation_total_return is not None:
        summary["validation_total_return"] = validation_total_return
    if pre_audit_canonical_total_return is not None:
        summary["pre_audit_canonical_total_return"] = pre_audit_canonical_total_return
    if holdout_available:
        summary["holdout_available"] = True
        summary["holdout_sharpe"] = median_sharpe * 0.9
        summary["holdout_total_return"] = validation_total_return or (
            median_total_return * 0.8
        )
        summary["holdout_cagr"] = median_cagr * 0.85
        summary["holdout_calmar"] = median_calmar * 0.85
        summary["holdout_max_drawdown"] = -0.05
        summary["holdout_liquidated"] = False
    return json.dumps(summary)


def _make_spec_json(track: str, features: list[str] | None = None) -> str:
    """Build a realistic spec_json blob."""
    return json.dumps(
        {
            "track": track,
            "params": {
                "long_enabled": True,
                "short_enabled": False,
                "hedge_mode": "none",
                "hedge_ratio": 0.0,
            },
            "features": features or ["rsi_14", "ma_cross_50_200", "volume_spike"],
        }
    )


# ---------------------------------------------------------------------------
# Experiment definitions
# ---------------------------------------------------------------------------

# Run 1: Directional Perps (trend_signals track)
RUN_1_ID = "test-run-e2e-001"
RUN_1_LABEL = "Directional Perps Run"

RUN_1_EXPERIMENTS: list[dict[str, Any]] = [
    {
        "spec_hash": "spec-e2e-001-001",
        "track": "trend_signals",
        "family": "perp_multi_asset_decision",
        "aggregate_score": 0.50,
        "passed": True,
        "deployd": False,
        "parent_hash": None,
        "iteration": 1,
        "created_offset_minutes": -300,
        "features": ["rsi_14", "ma_cross_50_200", "volume_spike"],
    },
    {
        "spec_hash": "spec-e2e-001-002",
        "track": "trend_signals",
        "family": "perp_multi_asset_decision",
        "aggregate_score": 0.70,
        "passed": True,
        "deployd": False,
        "parent_hash": "spec-e2e-001-001",
        "iteration": 2,
        "created_offset_minutes": -240,
        "features": ["rsi_14", "ma_cross_50_200", "adx_20"],
    },
    {
        "spec_hash": "spec-e2e-001-003",
        "track": "trend_signals",
        "family": "perp_pair_trade_unlevered",
        "aggregate_score": 0.88,
        "passed": True,
        "deployd": True,
        "parent_hash": None,
        "iteration": 1,
        "created_offset_minutes": -180,
        "features": ["zscore_bb", "correlation_30d", "half_life_mean_reversion"],
    },
    {
        "spec_hash": "spec-e2e-001-004",
        "track": "trend_signals",
        "family": "perp_multi_asset_decision",
        "aggregate_score": 0.82,
        "passed": False,
        "deployd": False,
        "parent_hash": "spec-e2e-001-002",
        "iteration": 3,
        "created_offset_minutes": -120,
        "features": ["rsi_14", "ma_cross_50_200", "adx_20", "bb_width"],
    },
    {
        "spec_hash": "spec-e2e-001-005",
        "track": "trend_signals",
        "family": "perp_pair_trade_unlevered",
        "aggregate_score": 0.86,
        "passed": True,
        "deployd": False,
        "parent_hash": "spec-e2e-001-003",
        "iteration": 2,
        "created_offset_minutes": -60,
        "features": [
            "zscore_bb",
            "correlation_30d",
            "half_life_mean_reversion",
            "spread_volatility",
        ],
    },
]

# Run 2: Systematic Carry (yield_flows track)
RUN_2_ID = "test-run-e2e-002"
RUN_2_LABEL = "Systematic Carry Run"

RUN_2_EXPERIMENTS: list[dict[str, Any]] = [
    {
        "spec_hash": "spec-e2e-002-001",
        "track": "yield_flows",
        "family": "basis_spread",
        "aggregate_score": 0.40,
        "passed": True,
        "deployd": False,
        "parent_hash": None,
        "iteration": 1,
        "created_offset_minutes": -200,
        "features": ["funding_rate_8h", "basis_annualized", "open_interest_ratio"],
    },
    {
        "spec_hash": "spec-e2e-002-002",
        "track": "yield_flows",
        "family": "basis_spread",
        "aggregate_score": 0.65,
        "passed": True,
        "deployd": False,
        "parent_hash": "spec-e2e-002-001",
        "iteration": 2,
        "created_offset_minutes": -120,
        "features": [
            "funding_rate_8h",
            "basis_annualized",
            "open_interest_ratio",
            "premium_discount",
        ],
    },
    {
        "spec_hash": "spec-e2e-002-003",
        "track": "yield_flows",
        "family": "stable_pt_ladder",
        "aggregate_score": 0.72,
        "passed": True,
        "deployd": False,
        "parent_hash": None,
        "iteration": 1,
        "created_offset_minutes": -40,
        "features": ["yield_curve_slope", "stable_pt_apy", "ladder_utilization"],
    },
]


def _make_experiment(
    exp_def: dict[str, Any],
    run_session_id: str,
    run_label: str,
) -> tuple[list[str], list[Any]]:
    """Convert an experiment definition dict into SQL columns + values."""
    created_at = _now(offset_minutes=exp_def["created_offset_minutes"])
    spec_hash = exp_def["spec_hash"]
    track = exp_def["track"]
    family = exp_def["family"]
    parent_hash = exp_def["parent_hash"]
    aggregate_score = exp_def["aggregate_score"]
    passed = 1 if exp_def["passed"] else 0
    deployd = 1 if exp_def["deployd"] else 0
    spec_json = _make_spec_json(track, features=exp_def["features"])

    # Derive summary metrics from aggregate score
    sharpe = round(1.0 + (aggregate_score - 0.5) * 2.0, 2)
    total_return = round((aggregate_score - 0.5) * 0.4, 4)
    cagr = round(total_return * 0.7, 4)
    calmar = round(sharpe * 0.5, 2)

    is_best = aggregate_score >= 0.86
    summary_json = _make_summary_json(
        aggregate_score=aggregate_score,
        median_sharpe=sharpe,
        median_total_return=total_return,
        median_cagr=cagr,
        median_calmar=calmar,
        validation_total_return=total_return * 0.8,
        pre_audit_canonical_total_return=total_return * 1.1,
        holdout_available=is_best,
    )

    is_llm = exp_def.get("iteration", 1) > 0
    research_summary = _make_research_summary(
        run_session_id=run_session_id,
        run_label=run_label,
        runner_label="siglab-agent",
        benchmark_mode=False,
        memory_scope="track_shared",
        phase_label=f"Iteration {exp_def['iteration']}",
        deterministic=not is_llm,
        iteration_number=exp_def["iteration"],
        llm_tool_trace=is_llm,
    )

    artifact_path = None

    columns = [
        "spec_hash",
        "created_at",
        "track",
        "family",
        "parent_hash",
        "spec_json",
        "research_summary",
        "aggregate_score",
        "passed",
        "deployd",
        "summary_json",
        "artifact_path",
    ]
    values = [
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
        artifact_path,
    ]
    return columns, values


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_seeded_db() -> str:
    """Create a temporary SQLite database with seeded test data.

    Returns the absolute path to the database file.
    """
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Create experiments table matching DeploymentStore schema
    cursor.execute("""
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
    """)

    # Create deployments table (may be referenced by deployment store)
    cursor.execute("""
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
    """)

    # Insert Run 1 experiments
    for exp_def in RUN_1_EXPERIMENTS:
        columns, values = _make_experiment(exp_def, RUN_1_ID, RUN_1_LABEL)
        placeholders = ",".join(["?"] * len(columns))
        cursor.execute(
            f"INSERT OR REPLACE INTO experiments ({','.join(columns)}) "
            f"VALUES ({placeholders})",
            values,
        )

    # Insert Run 2 experiments
    for exp_def in RUN_2_EXPERIMENTS:
        columns, values = _make_experiment(exp_def, RUN_2_ID, RUN_2_LABEL)
        placeholders = ",".join(["?"] * len(columns))
        cursor.execute(
            f"INSERT OR REPLACE INTO experiments ({','.join(columns)}) "
            f"VALUES ({placeholders})",
            values,
        )

    conn.commit()
    conn.close()
    return db_path


def create_ops_artifacts(runs_dir: str) -> None:
    """Create minimal ops artifact JSON files in runs_dir.

    The ops board loads these from ``config.artifact_dir`` (typically
    ``root_dir / 'runs'``).  Each file matches what the buildathon
    proof chain produces.
    """
    artifacts = {
        "demo_manifest_latest.json": {
            "status": "complete",
            "readiness": {
                "sosovalue_input_to_output": True,
                "sodex_public_market_data": True,
                "provider_metrics_present": True,
            },
            "market_report_status": "generated",
            "red_flags": [],
            "artifacts": [
                {
                    "name": "Market Report",
                    "path": "runs/market_report_latest.json",
                    "status": "present",
                },
                {
                    "name": "Telemetry",
                    "path": "runs/latest_telemetry_report.json",
                    "status": "present",
                },
            ],
            "market_report_headline": "BTC showing momentum, ETH neutral",
        },
        "latest_telemetry_report.json": {
            "generated_at": _now(),
            "confidence": 0.85,
            "trace_count": 12,
            "tool_invocation_count": 48,
            "tool_error_count": 2,
            "provider_metrics_status": "complete",
            "provider_metrics": {
                "request_count": 24,
                "estimated_credits": 15.5,
                "returned_input_tokens": 45000,
                "returned_output_tokens": 8000,
                "context_pressure_events": 0,
                "credit_pressure_events": 0,
            },
            "model_counts": {"claude-sonnet-4": 8, "claude-k2.5": 4},
            "stage_counts": {"planner": 6, "writer": 4, "reflector": 2},
        },
        "market_report_latest.json": {
            "status": "complete",
            "entity": "BTC-PERP",
            "signal_summary": {
                "headline": "BTC showing momentum, ETH neutral",
                "flow_direction": "positive",
                "quote_bid": "84520.50",
                "quote_ask": "84521.00",
            },
            "decision_support": {
                "stance": "bullish",
            },
            "warnings": [],
        },
        "sodex_preflight_latest.json": {
            "public_read_ready": True,
            "schema_pinned": True,
            "live_write_allowed": False,
            "live_write_refusal_reason": "No API key configured for live trading",
            "signed_path": {"ready": False},
            "request_weight_budget_per_minute": 100,
            "next_actions": ["Configure API key for live trading"],
        },
        "wave_status_latest.json": {
            "wave_number": 3,
            "phase": "execution",
            "status": "active",
            "goal": "Validate trend signal pipeline end-to-end",
            "agents": ["strategist", "developer", "analyst"],
            "outputs": ["market_report_latest.json", "demo_manifest_latest.json"],
            "blockers": [],
            "validation_status": "in_progress",
            "next_decision": "Review market report",
            "stop_allowed": True,
            "unsafe_claims": [],
        },
    }

    os.makedirs(runs_dir, exist_ok=True)
    for filename, payload in artifacts.items():
        filepath = os.path.join(runs_dir, filename)
        with open(filepath, "w") as f:
            json.dump(payload, f, indent=2)


def seed_dashboard_state(state: DashboardState, db_path: str) -> None:
    """Patch a DashboardState instance to use a given database path.

    This is used by the conftest fixture to swap in the seeded database.
    """
    from pathlib import Path

    if state.config is not None:
        state.config.ancestry_db_path = Path(db_path)
