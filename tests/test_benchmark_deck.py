from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import yaml

from wayfinder_autolab.benchmark import (
    DEFAULT_BENCHMARK_DECK,
    benchmark_paths,
    benchmark_status,
    evaluate_benchmark_deck,
    init_benchmark_deck,
)
from wayfinder_autolab.models import CandidateGraph
from wayfinder_autolab.search.lineage import LineageStore


def _candidate(
    *,
    family: str = "perp_multi_asset_carry",
    features: list[str] | None = None,
    hypothesis: str = "carry seed",
    symbols: list[str] | None = None,
    params: dict | None = None,
) -> CandidateGraph:
    chosen_symbols = list(symbols or ["BTC", "ETH", "SOL", "HYPE"])
    return CandidateGraph.from_dict(
        {
            "track": "directional_perps",
            "family": family,
            "hypothesis": hypothesis,
            "neutrality_basis": "market",
            "features": list(features or ["funding_72h_mean", "funding_carry_to_vol"]),
            "universe": {
                "basis_groups": chosen_symbols,
                "max_symbols": len(chosen_symbols),
                "lookback_days": 365,
                "interval": "1h",
            },
            "risk": {"max_leverage": 1.0, "rebalance_threshold": 0.03},
            "regime_gates": {},
            "params": dict(params or {"long_count": 2, "short_count": 2, "gross_target": 1.0}),
        }
    )


def _summary(
    *,
    aggregate_score: float,
    validation_total_return: float,
    pre_audit_canonical_total_return: float,
    passed: bool,
) -> dict:
    return {
        "aggregate_score": aggregate_score,
        "validation_total_return": validation_total_return,
        "pre_audit_canonical_total_return": pre_audit_canonical_total_return,
        "median_total_return": validation_total_return,
        "median_sharpe": 1.0,
        "passed": passed,
    }


class _FakeMutator:
    def __init__(self, seed: CandidateGraph) -> None:
        self._seed = seed

    def load_seed_candidates(self, track: str, family: str | None = None) -> list[CandidateGraph]:
        return [self._seed]

    def _allowed_families(self, track: str, family: str | None = None) -> list[str]:
        return ["perp_multi_asset_carry", "perp_basket_neutral_levered"]

    def _allowed_features_by_family(self, track: str, family: str | None = None) -> dict[str, list[str]]:
        return {
            "perp_multi_asset_carry": ["funding_72h_mean", "funding_carry_to_vol", "funding_dispersion_72h"],
            "perp_basket_neutral_levered": ["price_return_24h", "relative_carry_z_72h"],
        }

    def _family_defaults(self, track: str, family: str | None = None) -> dict[str, dict]:
        return {}

    def _validate_candidate(
        self,
        *,
        candidate: CandidateGraph,
        track: str,
        allowed_families: list[str],
        allowed_features_by_family: dict[str, list[str]],
        family_defaults: dict[str, dict],
    ) -> CandidateGraph:
        if candidate.family not in allowed_families:
            raise ValueError(f"unsupported family: {candidate.family}")
        return candidate

    def _historical_seedworthy(self, summary: dict) -> bool:
        return bool(summary.get("passed"))

    def _historical_seed_quality(self, summary: dict) -> float:
        return float(summary.get("aggregate_score") or 0.0)


class _FakeProvider:
    def __init__(self) -> None:
        self._bundle = {}

    def begin_iteration_bundle(self, *, track: str, parent: CandidateGraph) -> None:
        self._bundle = {
            "bundle_id": f"bundle::{parent.strategy_hash()}",
            "symbols": list(parent.universe.basis_groups),
        }

    def current_bundle_context(self) -> dict:
        return dict(self._bundle)

    def clear_iteration_bundle(self) -> None:
        self._bundle = {}


class _FakeEvaluator:
    def __init__(self, summaries: list[dict]) -> None:
        self._summaries = list(summaries)

    async def evaluate(self, candidate: CandidateGraph, fast_mode: bool = False) -> dict:
        summary = dict(self._summaries.pop(0))
        return {
            "candidate_hash": candidate.strategy_hash(),
            "candidate": candidate.canonical_dict(),
            "summary": summary,
        }


class BenchmarkDeckTests(unittest.IsolatedAsyncioTestCase):
    def _settings(self, tmp: str) -> SimpleNamespace:
        root = Path(tmp)
        return SimpleNamespace(
            root_dir=root,
            artifact_dir=root / "artifacts",
            lineage_db_path=root / "lineage.db",
        )

    def _record_experiment(
        self,
        *,
        lineage: LineageStore,
        candidate: CandidateGraph,
        summary: dict,
        artifact_dir: Path,
        promoted: bool = False,
    ) -> str:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / f"{candidate.strategy_hash()}.json"
        artifact_path.write_text(
            json.dumps(
                {
                    "candidate_hash": candidate.strategy_hash(),
                    "candidate": candidate.canonical_dict(),
                    "summary": summary,
                },
                indent=2,
            )
        )
        lineage.record(
            evaluation={
                "candidate_hash": candidate.strategy_hash(),
                "candidate": candidate.canonical_dict(),
                "summary": summary,
            },
            parent_hash=None,
            research_summary={"run_context": {"phase_label": "main", "deterministic": False}},
            artifact_path=str(artifact_path),
        )
        if promoted:
            lineage.promote(candidate.strategy_hash())
        return str(artifact_path)

    def test_benchmark_init_prefers_promoted_carry_seed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._settings(tmp)
            lineage = LineageStore(settings.lineage_db_path)
            promoted = _candidate(hypothesis="promoted carry anchor")
            fallback = _candidate(
                family="perp_basket_neutral_levered",
                features=["price_return_24h", "relative_carry_z_72h"],
                hypothesis="fallback basket",
            )
            self._record_experiment(
                lineage=lineage,
                candidate=promoted,
                summary=_summary(
                    aggregate_score=4.8,
                    validation_total_return=0.042,
                    pre_audit_canonical_total_return=0.015,
                    passed=True,
                ),
                artifact_dir=settings.artifact_dir / "directional_perps",
                promoted=True,
            )
            self._record_experiment(
                lineage=lineage,
                candidate=fallback,
                summary=_summary(
                    aggregate_score=2.0,
                    validation_total_return=0.01,
                    pre_audit_canonical_total_return=0.0,
                    passed=True,
                ),
                artifact_dir=settings.artifact_dir / "directional_perps",
                promoted=False,
            )

            mutator = _FakeMutator(promoted)
            payload = init_benchmark_deck(
                settings=settings,
                lineage=lineage,
                mutator=mutator,
                deck_name=DEFAULT_BENCHMARK_DECK,
                agent_label="claude_code",
                run_label="claude-benchmark-1",
                force=False,
            )

            paths = benchmark_paths(settings=settings, deck_name=DEFAULT_BENCHMARK_DECK)
            seeded_payload = yaml.safe_load(paths.best_candidate_path.read_text())
            self.assertEqual(payload["seed_source"], "promoted_db")
            self.assertEqual(seeded_payload["hypothesis"], "promoted carry anchor")
            self.assertEqual(payload["state"]["agent_label"], "claude_code")
            self.assertEqual(payload["state"]["run_label"], "claude-benchmark-1")
            self.assertTrue(str(payload["state"]["benchmark_run_id"]).startswith("benchmark::directional_perps_external::claude_code::"))
            self.assertEqual(
                payload["state"]["incumbent_candidate_hash"],
                CandidateGraph.from_dict(seeded_payload).strategy_hash(),
            )
            self.assertIn("poetry run autolab benchmark-eval", paths.program_path.read_text())

    async def test_benchmark_eval_keeps_better_candidate_and_reverts_discard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._settings(tmp)
            lineage = LineageStore(settings.lineage_db_path)
            incumbent = _candidate(hypothesis="incumbent carry")
            mutator = _FakeMutator(incumbent)
            init_benchmark_deck(
                settings=settings,
                lineage=lineage,
                mutator=mutator,
                deck_name=DEFAULT_BENCHMARK_DECK,
                agent_label="claude_code",
                run_label="claude-benchmark-2",
                force=False,
            )

            better = _candidate(
                features=["funding_72h_mean", "funding_carry_to_vol", "funding_dispersion_72h"],
                hypothesis="improved carry candidate",
            )
            worse = _candidate(
                features=["funding_72h_mean"],
                hypothesis="worse carry candidate",
            )
            evaluator = _FakeEvaluator(
                [
                    _summary(
                        aggregate_score=5.0,
                        validation_total_return=0.05,
                        pre_audit_canonical_total_return=0.02,
                        passed=True,
                    ),
                    _summary(
                        aggregate_score=1.0,
                        validation_total_return=0.01,
                        pre_audit_canonical_total_return=0.0,
                        passed=True,
                    ),
                ]
            )
            provider = _FakeProvider()
            paths = benchmark_paths(settings=settings, deck_name=DEFAULT_BENCHMARK_DECK)

            paths.candidate_path.write_text(yaml.safe_dump(better.canonical_dict(), sort_keys=False))
            keep_result = await evaluate_benchmark_deck(
                settings=settings,
                lineage=lineage,
                mutator=mutator,
                evaluator=evaluator,
                provider=provider,
                deck_name=DEFAULT_BENCHMARK_DECK,
            )
            self.assertEqual(keep_result["status"], "keep")
            self.assertEqual(
                CandidateGraph.from_dict(yaml.safe_load(paths.best_candidate_path.read_text())).strategy_hash(),
                better.strategy_hash(),
            )
            self.assertEqual(
                CandidateGraph.from_dict(yaml.safe_load(paths.candidate_path.read_text())).strategy_hash(),
                better.strategy_hash(),
            )
            detail = lineage.experiment_detail(better.strategy_hash())
            self.assertIsNotNone(detail)
            self.assertTrue(detail["research_summary"]["run_context"]["benchmark_mode"])
            self.assertEqual(detail["research_summary"]["run_context"]["benchmark_deck"], DEFAULT_BENCHMARK_DECK)
            self.assertEqual(detail["research_summary"]["run_context"]["agent_label"], "claude_code")
            self.assertEqual(detail["research_summary"]["run_context"]["run_label"], "claude-benchmark-2")
            self.assertTrue(
                str(detail["research_summary"]["run_context"]["run_session_id"]).startswith(
                    "benchmark::directional_perps_external::claude_code::"
                )
            )

            paths.candidate_path.write_text(yaml.safe_dump(worse.canonical_dict(), sort_keys=False))
            discard_result = await evaluate_benchmark_deck(
                settings=settings,
                lineage=lineage,
                mutator=mutator,
                evaluator=evaluator,
                provider=provider,
                deck_name=DEFAULT_BENCHMARK_DECK,
            )
            self.assertEqual(discard_result["status"], "discard")
            self.assertEqual(
                CandidateGraph.from_dict(yaml.safe_load(paths.best_candidate_path.read_text())).strategy_hash(),
                better.strategy_hash(),
            )
            self.assertEqual(
                CandidateGraph.from_dict(yaml.safe_load(paths.candidate_path.read_text())).strategy_hash(),
                better.strategy_hash(),
            )

            status_payload = benchmark_status(
                settings=settings,
                deck_name=DEFAULT_BENCHMARK_DECK,
            )
            self.assertEqual(status_payload["state"]["incumbent_candidate_hash"], better.strategy_hash())
            self.assertEqual(len(status_payload["recent_results"]), 2)
            self.assertEqual(status_payload["recent_results"][0]["status"], "keep")
            self.assertEqual(status_payload["recent_results"][1]["status"], "discard")


if __name__ == "__main__":
    unittest.main()
