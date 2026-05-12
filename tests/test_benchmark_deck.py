from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import yaml

from siglab.benchmark import (
    DEFAULT_BENCHMARK_DECK,
    benchmark_paths,
    benchmark_status,
    evaluate_benchmark_deck,
    init_benchmark_deck,
)
from siglab.models import SignalSpec
from siglab.search.ancestry import LineageStore


def _spec(
    *,
    family: str = "perp_multi_asset_carry",
    features: list[str] | None = None,
    hypothesis: str = "carry seed",
    symbols: list[str] | None = None,
    params: dict | None = None,
) -> SignalSpec:
    chosen_symbols = list(symbols or ["BTC", "ETH", "SOL", "HYPE"])
    return SignalSpec.from_dict(
        {
            "track": "trend_signals",
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
    def __init__(self, seed: SignalSpec) -> None:
        self._seed = seed

    def load_seed_specs(self, track: str, family: str | None = None) -> list[SignalSpec]:
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

    def _validate_spec(
        self,
        *,
        spec: SignalSpec,
        track: str,
        allowed_families: list[str],
        allowed_features_by_family: dict[str, list[str]],
        family_defaults: dict[str, dict],
    ) -> SignalSpec:
        if spec.family not in allowed_families:
            raise ValueError(f"unsupported family: {spec.family}")
        return spec

    def _historical_seedworthy(self, summary: dict) -> bool:
        return bool(summary.get("passed"))

    def _historical_seed_quality(self, summary: dict) -> float:
        return float(summary.get("aggregate_score") or 0.0)


class _FakeProvider:
    def __init__(self) -> None:
        self._bundle = {}

    def begin_iteration_bundle(self, *, track: str, parent: SignalSpec) -> None:
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

    async def evaluate(self, spec: SignalSpec, fast_mode: bool = False) -> dict:
        summary = dict(self._summaries.pop(0))
        return {
            "spec_hash": spec.strategy_hash(),
            "spec": spec.canonical_dict(),
            "summary": summary,
        }


class BenchmarkDeckTests(unittest.IsolatedAsyncioTestCase):
    def _settings(self, tmp: str) -> SimpleNamespace:
        root = Path(tmp)
        return SimpleNamespace(
            root_dir=root,
            artifact_dir=root / "runs",
            ancestry_db_path=root / "ancestry.db",
        )

    def _record_experiment(
        self,
        *,
        ancestry: LineageStore,
        spec: SignalSpec,
        summary: dict,
        artifact_dir: Path,
        deployd: bool = False,
    ) -> str:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / f"{spec.strategy_hash()}.json"
        artifact_path.write_text(
            json.dumps(
                {
                    "spec_hash": spec.strategy_hash(),
                    "spec": spec.canonical_dict(),
                    "summary": summary,
                },
                indent=2,
            )
        )
        ancestry.record(
            evaluation={
                "spec_hash": spec.strategy_hash(),
                "spec": spec.canonical_dict(),
                "summary": summary,
            },
            parent_hash=None,
            research_summary={"run_context": {"phase_label": "main", "deterministic": False}},
            artifact_path=str(artifact_path),
        )
        if deployd:
            ancestry.deploy(spec.strategy_hash())
        return str(artifact_path)

    def test_benchmark_init_prefers_deployd_carry_seed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._settings(tmp)
            ancestry = LineageStore(settings.ancestry_db_path)
            deployd = _spec(hypothesis="deployd carry anchor")
            fallback = _spec(
                family="perp_basket_neutral_levered",
                features=["price_return_24h", "relative_carry_z_72h"],
                hypothesis="fallback basket",
            )
            self._record_experiment(
                ancestry=ancestry,
                spec=deployd,
                summary=_summary(
                    aggregate_score=4.8,
                    validation_total_return=0.042,
                    pre_audit_canonical_total_return=0.015,
                    passed=True,
                ),
                artifact_dir=settings.artifact_dir / "trend_signals",
                deployd=True,
            )
            self._record_experiment(
                ancestry=ancestry,
                spec=fallback,
                summary=_summary(
                    aggregate_score=2.0,
                    validation_total_return=0.01,
                    pre_audit_canonical_total_return=0.0,
                    passed=True,
                ),
                artifact_dir=settings.artifact_dir / "trend_signals",
                deployd=False,
            )

            mutator = _FakeMutator(deployd)
            payload = init_benchmark_deck(
                settings=settings,
                ancestry=ancestry,
                mutator=mutator,
                deck_name=DEFAULT_BENCHMARK_DECK,
                runner_label="claude_code",
                run_label="claude-benchmark-1",
                force=False,
            )

            paths = benchmark_paths(settings=settings, deck_name=DEFAULT_BENCHMARK_DECK)
            seeded_payload = yaml.safe_load(paths.best_spec_path.read_text())
            self.assertEqual(payload["seed_source"], "deployd_db")
            self.assertEqual(seeded_payload["hypothesis"], "deployd carry anchor")
            self.assertEqual(payload["state"]["runner_label"], "claude_code")
            self.assertEqual(payload["state"]["run_label"], "claude-benchmark-1")
            self.assertTrue(str(payload["state"]["benchmark_run_id"]).startswith("benchmark::trend_signals_external::claude_code::"))
            self.assertEqual(
                payload["state"]["incumbent_spec_hash"],
                SignalSpec.from_dict(seeded_payload).strategy_hash(),
            )
            self.assertIn("poetry run siglab benchmark-eval", paths.program_path.read_text())

    async def test_benchmark_eval_keeps_better_spec_and_reverts_discard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._settings(tmp)
            ancestry = LineageStore(settings.ancestry_db_path)
            incumbent = _spec(hypothesis="incumbent carry")
            mutator = _FakeMutator(incumbent)
            init_benchmark_deck(
                settings=settings,
                ancestry=ancestry,
                mutator=mutator,
                deck_name=DEFAULT_BENCHMARK_DECK,
                runner_label="claude_code",
                run_label="claude-benchmark-2",
                force=False,
            )

            better = _spec(
                features=["funding_72h_mean", "funding_carry_to_vol", "funding_dispersion_72h"],
                hypothesis="improved carry spec",
            )
            worse = _spec(
                features=["funding_72h_mean"],
                hypothesis="worse carry spec",
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

            paths.spec_path.write_text(yaml.safe_dump(better.canonical_dict(), sort_keys=False))
            keep_result = await evaluate_benchmark_deck(
                settings=settings,
                ancestry=ancestry,
                mutator=mutator,
                evaluator=evaluator,
                provider=provider,
                deck_name=DEFAULT_BENCHMARK_DECK,
            )
            self.assertEqual(keep_result["status"], "keep")
            self.assertEqual(
                SignalSpec.from_dict(yaml.safe_load(paths.best_spec_path.read_text())).strategy_hash(),
                better.strategy_hash(),
            )
            self.assertEqual(
                SignalSpec.from_dict(yaml.safe_load(paths.spec_path.read_text())).strategy_hash(),
                better.strategy_hash(),
            )
            detail = ancestry.experiment_detail(better.strategy_hash())
            self.assertIsNotNone(detail)
            self.assertTrue(detail["research_summary"]["run_context"]["benchmark_mode"])
            self.assertEqual(detail["research_summary"]["run_context"]["benchmark_deck"], DEFAULT_BENCHMARK_DECK)
            self.assertEqual(detail["research_summary"]["run_context"]["runner_label"], "claude_code")
            self.assertEqual(detail["research_summary"]["run_context"]["run_label"], "claude-benchmark-2")
            self.assertTrue(
                str(detail["research_summary"]["run_context"]["run_session_id"]).startswith(
                    "benchmark::trend_signals_external::claude_code::"
                )
            )

            paths.spec_path.write_text(yaml.safe_dump(worse.canonical_dict(), sort_keys=False))
            discard_result = await evaluate_benchmark_deck(
                settings=settings,
                ancestry=ancestry,
                mutator=mutator,
                evaluator=evaluator,
                provider=provider,
                deck_name=DEFAULT_BENCHMARK_DECK,
            )
            self.assertEqual(discard_result["status"], "discard")
            self.assertEqual(
                SignalSpec.from_dict(yaml.safe_load(paths.best_spec_path.read_text())).strategy_hash(),
                better.strategy_hash(),
            )
            self.assertEqual(
                SignalSpec.from_dict(yaml.safe_load(paths.spec_path.read_text())).strategy_hash(),
                better.strategy_hash(),
            )

            status_payload = benchmark_status(
                settings=settings,
                deck_name=DEFAULT_BENCHMARK_DECK,
            )
            self.assertEqual(status_payload["state"]["incumbent_spec_hash"], better.strategy_hash())
            self.assertEqual(len(status_payload["recent_results"]), 2)
            self.assertEqual(status_payload["recent_results"][0]["status"], "keep")
            self.assertEqual(status_payload["recent_results"][1]["status"], "discard")


if __name__ == "__main__":
    unittest.main()



