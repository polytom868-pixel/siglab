from __future__ import annotations

import asyncio
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from siglab.live.exporter import LiveDeploymentManager, deployment_readiness
from siglab.search.lineage import LineageStore
from siglab.config import SiglabConfig


class LiveExporterTest(unittest.TestCase):
    def test_deployment_readiness_rejects_unsupported_family(self) -> None:
        readiness = deployment_readiness(
            {
                "track": "yield_flows",
                "family": "stable_pt_ladder",
                "spec_hash": "deadbeefdeadbeef",
                "spec": {
                    "track": "yield_flows",
                    "family": "stable_pt_ladder",
                },
                "summary": {
                    "strict_holdout": True,
                    "holdout_available": True,
                    "liquidation_count": 0,
                },
                "artifact": {
                    "canonical_run": {"equity_curve": {"index": ["2026-01-01"], "values": [1.0]}},
                    "compiled_metadata": {"signal_timing": "next_bar"},
                },
            }
        )
        self.assertFalse(readiness["supported"])
        self.assertTrue(readiness["reasons"])

    def test_deploy_writes_strategy_package_and_records_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            strategy_root = root / "siglab" / "live" / "deployed_agents"
            strategy_root.mkdir(parents=True)

            config_path = root / "config.json"
            config_path.write_text(json.dumps({"wallets": []}))
            db_path = root / "ancestry.db"
            artifact_path = root / "artifact.json"

            spec = {
                "track": "trend_signals",
                "family": "perp_multi_asset_decision",
                "hypothesis": "Simple long-short perp basket.",
                "neutrality_basis": "none",
                "features": ["price_return_24h", "rsi_centered_14"],
                "universe": {
                    "basis_groups": ["BTC", "ETH"],
                    "chains": ["hyperevm"],
                    "max_symbols": 2,
                    "lookback_days": 90,
                    "interval": "1h",
                },
                "risk": {
                    "max_asset_weight": 0.35,
                    "max_chain_weight": 1.0,
                    "rebalance_threshold": 0.01,
                    "roll_days_before_expiry": 5,
                    "max_leverage": 2.0,
                },
                "params": {
                    "gross_target": 1.0,
                    "long_count": 1,
                    "long_enabled": True,
                    "short_count": 1,
                    "short_enabled": True,
                },
            }
            summary = {
                "aggregate_score": 1.25,
                "median_sharpe": 0.6,
                "median_cagr": 0.08,
                "median_total_return": 0.02,
                "liquidation_count": 0,
                "holdout_available": True,
                "holdout_total_return": 0.01,
                "holdout_sharpe": 0.3,
                "strict_holdout": True,
                "passed": True,
            }
            artifact = {
                "spec": spec,
                "spec_hash": "abcd1234abcd1234",
                "summary": summary,
                "compiled_metadata": {
                    "signal_timing": "next_bar",
                    "source": "delta_lab",
                    "bundle_as_of": "2026-03-13T00:00:00+00:00",
                },
                "canonical_run": {
                    "equity_curve": {
                        "index": ["2026-01-01T00:00:00", "2026-01-02T00:00:00"],
                        "values": [1.0, 1.01],
                    }
                },
            }
            artifact_path.write_text(json.dumps(artifact))

            settings = SiglabConfig(
                root_dir=root,
                sosovalue_config_path=config_path,
                generated_strategy_dir=strategy_root,
                data_lake_dir=root / "lake",
                artifact_dir=root / "runs",
                live_dir=root / "live",
                ancestry_db_path=db_path,
                sosovalue_api_key_override=None,
                claude_api_key=None,
                claude_model="claude-k2.5",
                claude_base_url="https://api.moonshot.ai/v1",
                claude_max_tokens=32768,
                claude_temperature=1.0,
                claude_top_p=0.95,
                claude_timeout_s=300,
                population_size=1,
            )
            settings.ensure_runtime_directories()

            ancestry = LineageStore(db_path)
            ancestry.record(
                evaluation={
                    "spec": spec,
                    "spec_hash": "abcd1234abcd1234",
                    "summary": summary,
                },
                parent_hash=None,
                research_summary={},
                artifact_path=str(artifact_path),
            )

            manager = LiveDeploymentManager(settings, ancestry, claude=None)
            record = asyncio.run(
                manager.deploy(
                    spec_hash="abcd1234abcd1234",
                    wallet_label=None,
                    config_path=str(config_path),
                    interval_seconds=None,
                    job_name=None,
                    dry_run=True,
                    llm_finalize=False,
                    schedule=False,
                )
            )

            self.assertEqual(record.strategy_name, "siglab_perp_multi_asset_decision_abcd1234abcd1234")
            self.assertTrue((strategy_root / record.strategy_name / "strategy.py").exists())
            self.assertTrue((strategy_root / record.strategy_name / "live_spec.json").exists())
            manifest_text = (strategy_root / record.strategy_name / "manifest.yaml").read_text()
            self.assertIn("SODEX_PERPS", manifest_text)
            self.assertIn("sodex_perps_order", manifest_text)
            self.assertNotIn("HYPERLIQUID", manifest_text)
            self.assertIn(
                'entrypoint: "siglab.live.deployed_agents.'
                'siglab_perp_multi_asset_decision_abcd1234abcd1234.strategy.'
                'SigLabPerpMultiAssetDecisionAbcd1234abcd1234Strategy"',
                manifest_text,
            )

            strategy_path = strategy_root / record.strategy_name / "strategy.py"
            spec = importlib.util.spec_from_file_location("siglab_generated_test_strategy", strategy_path)
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.assertTrue(hasattr(module, "SigLabPerpMultiAssetDecisionAbcd1234abcd1234Strategy"))
            readme_text = (strategy_root / record.strategy_name / "README.md").read_text()
            self.assertIn("Live Dependency Boundary", readme_text)
            self.assertIn("dependency_report()", readme_text)

            stored = ancestry.deployment("abcd1234abcd1234")
            self.assertIsNotNone(stored)
            self.assertEqual(stored["strategy_name"], record.strategy_name)
            self.assertFalse(stored["scheduled"])

    def test_live_deploy_refuses_before_writing_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            strategy_root = root / "siglab" / "live" / "deployed_agents"
            strategy_root.mkdir(parents=True)
            config_path = root / "config.json"
            config_path.write_text(json.dumps({"wallets": []}))
            db_path = root / "ancestry.db"
            artifact_path = root / "artifact.json"
            spec = {
                "track": "trend_signals",
                "family": "perp_pair_trade_unlevered",
                "features": ["pair_spread_z_72h"],
                "universe": {"basis_groups": ["BTC", "ETH"]},
                "risk": {},
                "params": {},
            }
            summary = {
                "aggregate_score": 1.0,
                "liquidation_count": 0,
                "holdout_available": True,
                "holdout_total_return": 0.01,
                "holdout_sharpe": 0.3,
                "strict_holdout": True,
                "passed": True,
            }
            artifact_path.write_text(json.dumps({"spec": spec, "summary": summary, "canonical_run": {"ok": True}, "compiled_metadata": {"signal_timing": "next_bar"}}))
            settings = SiglabConfig(
                root_dir=root,
                sosovalue_config_path=config_path,
                generated_strategy_dir=strategy_root,
                data_lake_dir=root / "lake",
                artifact_dir=root / "runs",
                live_dir=root / "live",
                ancestry_db_path=db_path,
                sosovalue_api_key_override=None,
                population_size=1,
            )
            settings.ensure_runtime_directories()
            ancestry = LineageStore(db_path)
            ancestry.record(
                evaluation={"spec": spec, "spec_hash": "feedfacefeedface", "summary": summary},
                parent_hash=None,
                research_summary={},
                artifact_path=str(artifact_path),
            )
            manager = LiveDeploymentManager(settings, ancestry, claude=None)

            with self.assertRaisesRegex(ValueError, "Live SoDEX deployment requires"):
                asyncio.run(
                    manager.deploy(
                        spec_hash="feedfacefeedface",
                        wallet_label="operator",
                        config_path=str(config_path),
                        interval_seconds=None,
                        job_name=None,
                        dry_run=False,
                        llm_finalize=False,
                        schedule=False,
                    )
                )

            self.assertEqual(list(strategy_root.iterdir()), [])



