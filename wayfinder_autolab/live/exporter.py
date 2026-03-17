from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from wayfinder_autolab.llm import KimiClient
from wayfinder_autolab.path_utils import resolve_path_from_root
from wayfinder_autolab.search.lineage import LineageStore
from wayfinder_autolab.settings import AutolabSettings
from wayfinder_autolab.track_registry import track_label
from wayfinder_paths.runner.client import RunnerControlClient
from wayfinder_paths.runner.lifecycle import ensure_daemon_started
from wayfinder_paths.runner.paths import get_runner_paths

SUPPORTED_DIRECTIONAL_FAMILIES = {
    "perp_multi_asset_decision",
    "perp_pair_trade_unlevered",
    "perp_pair_trade_levered",
}


@dataclass
class PromotionRecord:
    candidate_hash: str
    strategy_name: str
    strategy_dir: str
    spec_path: str
    manifest_path: str
    readme_path: str
    job_name: str | None
    interval_seconds: int | None
    wallet_label: str | None
    config_path: str
    scheduled: bool
    dry_run: bool
    llm_finalized: bool
    support_status: str
    support_reason: str | None
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_hash": self.candidate_hash,
            "strategy_name": self.strategy_name,
            "strategy_dir": self.strategy_dir,
            "spec_path": self.spec_path,
            "manifest_path": self.manifest_path,
            "readme_path": self.readme_path,
            "job_name": self.job_name,
            "interval_seconds": self.interval_seconds,
            "wallet_label": self.wallet_label,
            "config_path": self.config_path,
            "scheduled": self.scheduled,
            "dry_run": self.dry_run,
            "llm_finalized": self.llm_finalized,
            "support_status": self.support_status,
            "support_reason": self.support_reason,
            "metadata": self.metadata,
        }


def promotion_readiness(detail: dict[str, Any]) -> dict[str, Any]:
    candidate = dict(detail.get("candidate") or {})
    summary = dict(detail.get("summary") or {})
    compiled = dict((detail.get("artifact") or {}).get("compiled_metadata") or {})

    reasons: list[str] = []
    warnings: list[str] = []
    family = str(candidate.get("family") or "")
    track = str(candidate.get("track") or "")
    strict_holdout = bool(summary.get("strict_holdout"))
    holdout_available = bool(summary.get("holdout_available"))
    liquidated = bool(summary.get("liquidation_count", 0))
    series_available = bool((detail.get("artifact") or {}).get("canonical_run"))

    supported = track == "directional_perps" and family in SUPPORTED_DIRECTIONAL_FAMILIES
    if not supported:
        reasons.append(f"Live export is not implemented for {track}/{family} yet.")
    if not strict_holdout:
        reasons.append("Experiment does not have a strict holdout split.")
    if not holdout_available:
        reasons.append("Experiment does not have retained holdout metrics.")
    if liquidated:
        reasons.append("Experiment liquidated in backtest windows.")
    if not series_available:
        reasons.append("Experiment predates canonical retained series artifacts.")

    holdout_return = summary.get("holdout_total_return")
    if isinstance(holdout_return, (int, float)) and float(holdout_return) <= 0.0:
        warnings.append("Holdout return is non-positive.")
    if compiled.get("signal_timing") != "next_bar":
        warnings.append("Compiled signal timing is not next_bar.")

    return {
        "supported": supported and not reasons,
        "support_status": "supported" if supported else "unsupported",
        "reasons": reasons,
        "warnings": warnings,
    }


class LivePromotionManager:
    def __init__(
        self,
        settings: AutolabSettings,
        lineage: LineageStore,
        kimi: KimiClient | None = None,
    ) -> None:
        self.settings = settings
        self.lineage = lineage
        self.kimi = kimi

    async def promote(
        self,
        *,
        candidate_hash: str,
        wallet_label: str | None,
        config_path: str,
        interval_seconds: int | None,
        job_name: str | None,
        dry_run: bool,
        llm_finalize: bool,
        schedule: bool,
    ) -> PromotionRecord:
        detail = self.lineage.experiment_detail(candidate_hash)
        if detail is None:
            raise ValueError(f"Unknown candidate hash: {candidate_hash}")
        resolved_config_path = resolve_path_from_root(
            config_path,
            root_dir=self.settings.root_dir,
        )

        readiness = promotion_readiness(detail)
        if not readiness["supported"]:
            raise ValueError("; ".join(readiness["reasons"]) or "Experiment is not live-exportable")

        strategy_name = _strategy_name(detail)
        package_dir = self.settings.generated_strategy_dir / strategy_name
        notes = await self._finalizer_notes(detail, llm_finalize=llm_finalize)
        live_spec = self._build_live_spec(
            detail=detail,
            strategy_name=strategy_name,
            wallet_label=wallet_label,
            config_path=str(resolved_config_path),
            dry_run=dry_run,
            llm_notes=notes,
        )
        self._write_strategy_package(
            package_dir=package_dir,
            strategy_name=strategy_name,
            live_spec=live_spec,
            llm_notes=notes,
        )

        runner_response: dict[str, Any] | None = None
        normalized_job_name = job_name or f"{strategy_name}-update"
        if schedule:
            if not wallet_label:
                raise ValueError("wallet_label is required when scheduling a runner job")
            if interval_seconds is None or interval_seconds <= 0:
                raise ValueError("interval_seconds must be positive when scheduling a runner job")
            runner_response = self._ensure_runner_job(
                strategy_name=strategy_name,
                job_name=normalized_job_name,
                interval_seconds=interval_seconds,
                wallet_label=wallet_label,
                config_path=str(resolved_config_path),
            )

        record = PromotionRecord(
            candidate_hash=candidate_hash,
            strategy_name=strategy_name,
            strategy_dir=str(package_dir),
            spec_path=str(package_dir / "live_spec.json"),
            manifest_path=str(package_dir / "manifest.yaml"),
            readme_path=str(package_dir / "README.md"),
            job_name=normalized_job_name if schedule else None,
            interval_seconds=interval_seconds if schedule else None,
            wallet_label=wallet_label,
            config_path=str(resolved_config_path),
            scheduled=bool(schedule),
            dry_run=bool(dry_run),
            llm_finalized=bool(llm_finalize and notes.get("source") == "kimi"),
            support_status=readiness["support_status"],
            support_reason=None,
            metadata={
                "track_label": track_label(detail["track"]),
                "family": detail["family"],
                "warnings": readiness["warnings"],
                "runner_response": runner_response,
                "llm_notes": notes,
            },
        )
        self.lineage.record_promotion(record.to_dict())
        return record

    async def _finalizer_notes(
        self,
        detail: dict[str, Any],
        *,
        llm_finalize: bool,
    ) -> dict[str, Any]:
        candidate = dict(detail.get("candidate") or {})
        summary = dict(detail.get("summary") or {})
        base_notes = {
            "source": "template",
            "strategy_doc": candidate.get("hypothesis") or f"Live export for {candidate.get('family')}.",
            "readme_summary": candidate.get("hypothesis") or "Generated from a promoted Autolab experiment.",
            "operator_notes": (
                "Run this from the autolab Poetry environment so the generated strategy can "
                "import wayfinder_autolab runtime helpers."
            ),
            "risk_notes": (
                f"Holdout return {summary.get('holdout_total_return')}, "
                f"holdout Sharpe {summary.get('holdout_sharpe')}."
            ),
        }
        if not llm_finalize or self.kimi is None or not self.kimi.is_configured:
            return base_notes

        prompt = {
            "candidate": candidate,
            "summary": {
                "aggregate_score": summary.get("aggregate_score"),
                "holdout_total_return": summary.get("holdout_total_return"),
                "holdout_sharpe": summary.get("holdout_sharpe"),
                "median_cagr": summary.get("median_cagr"),
            },
        }
        try:
            payload = await self.kimi.complete_json(
                system_prompt=(
                    "You are preparing operator-facing notes for a generated live trading "
                    "strategy. Return strict JSON with keys strategy_doc, readme_summary, "
                    "operator_notes, risk_notes. Keep it concise, concrete, and do not invent "
                    "unsupported capabilities."
                ),
                user_prompt=json.dumps(prompt, indent=2),
                max_tokens=900,
            )
        except Exception:
            return base_notes

        return {
            "source": "kimi",
            "strategy_doc": str(payload.get("strategy_doc") or base_notes["strategy_doc"]).strip(),
            "readme_summary": str(payload.get("readme_summary") or base_notes["readme_summary"]).strip(),
            "operator_notes": str(payload.get("operator_notes") or base_notes["operator_notes"]).strip(),
            "risk_notes": str(payload.get("risk_notes") or base_notes["risk_notes"]).strip(),
        }

    def _build_live_spec(
        self,
        *,
        detail: dict[str, Any],
        strategy_name: str,
        wallet_label: str | None,
        config_path: str,
        dry_run: bool,
        llm_notes: dict[str, Any],
    ) -> dict[str, Any]:
        artifact = dict(detail.get("artifact") or {})
        compiled = dict(artifact.get("compiled_metadata") or {})
        candidate = dict(detail.get("candidate") or {})
        summary = dict(detail.get("summary") or {})
        return {
            "schema_version": "0.1",
            "candidate_hash": detail["candidate_hash"],
            "strategy_name": strategy_name,
            "track": detail["track"],
            "family": detail["family"],
            "candidate": candidate,
            "summary": {
                "aggregate_score": summary.get("aggregate_score"),
                "median_sharpe": summary.get("median_sharpe"),
                "median_cagr": summary.get("median_cagr"),
                "holdout_total_return": summary.get("holdout_total_return"),
                "holdout_sharpe": summary.get("holdout_sharpe"),
            },
            "compiled_metadata": compiled,
            "promotion": {
                "created_at": datetime.now(UTC).isoformat(),
                "wallet_label": wallet_label,
                "wayfinder_config_path": str(
                    resolve_path_from_root(config_path, root_dir=self.settings.root_dir)
                ),
                "operator_notes": llm_notes.get("operator_notes"),
                "risk_notes": llm_notes.get("risk_notes"),
            },
            "runtime": {
                "wayfinder_config_path": str(
                    resolve_path_from_root(config_path, root_dir=self.settings.root_dir)
                ),
                "dry_run": bool(dry_run),
                "slippage": 0.0035,
                "min_trade_usd": 25.0,
                "live_leverage": 1.0,
            },
            "notes": llm_notes,
        }

    def _write_strategy_package(
        self,
        *,
        package_dir: Path,
        strategy_name: str,
        live_spec: dict[str, Any],
        llm_notes: dict[str, Any],
    ) -> None:
        _ensure_package_tree(package_dir=package_dir, root_dir=self.settings.root_dir)
        class_name = _strategy_class_name(strategy_name)
        module_path = _module_path_from_root(package_dir=package_dir, root_dir=self.settings.root_dir)
        strategy_py = f'''from __future__ import annotations

from pathlib import Path

from wayfinder_autolab.live.runtime import DirectionalPerpsAutolabStrategy


class {class_name}(DirectionalPerpsAutolabStrategy):
    """{_escape_docstring(llm_notes.get("strategy_doc") or "Generated Autolab live strategy")}"""

    SPEC_PATH = Path(__file__).with_name("live_spec.json")
'''
        manifest = f'''schema_version: "0.1"
entrypoint: "{module_path}.strategy.{class_name}"
permissions:
  policy: |
    (wallet.id == 'FORMAT_WALLET_ID') AND (
      (action.type == 'hyperliquid_order') OR
      (action.type == 'hyperliquid_cancel')
    )
adapters:
  - name: "LEDGER"
    capabilities: ["ledger.read", "ledger.write", "strategy.transactions"]
  - name: "HYPERLIQUID"
    capabilities: ["market.read", "market.meta", "market.funding", "market.candles", "order.execute", "order.cancel", "position.manage"]
'''
        readme = f"""# {strategy_name}

{llm_notes.get("readme_summary") or "Generated from a promoted Autolab experiment."}

- Candidate hash: `{live_spec["candidate_hash"]}`
- Track / family: `{live_spec["track"]}` / `{live_spec["family"]}`
- Holdout return: `{live_spec["summary"].get("holdout_total_return")}`
- Holdout Sharpe: `{live_spec["summary"].get("holdout_sharpe")}`
- Dry run default: `{live_spec["runtime"]["dry_run"]}`

## Operator Notes

{llm_notes.get("operator_notes") or ""}

## Risk Notes

{llm_notes.get("risk_notes") or ""}
"""
        (package_dir / "__init__.py").write_text("")
        (package_dir / "strategy.py").write_text(strategy_py)
        (package_dir / "manifest.yaml").write_text(manifest)
        (package_dir / "README.md").write_text(readme)
        (package_dir / "live_spec.json").write_text(json.dumps(live_spec, indent=2))

    def _ensure_runner_job(
        self,
        *,
        strategy_name: str,
        job_name: str,
        interval_seconds: int,
        wallet_label: str,
        config_path: str,
    ) -> dict[str, Any]:
        paths = get_runner_paths()
        ok, info = ensure_daemon_started(
            paths=paths,
            tick_seconds=1.0,
            max_workers=4,
            max_failures=5,
            default_timeout_seconds=20 * 60,
            log_level="INFO",
        )
        if not ok:
            raise RuntimeError(f"Runner failed to start: {info}")
        client = RunnerControlClient(sock_path=paths.sock_path)
        payload = {
            "strategy": strategy_name,
            "action": "update",
            "config": str(config_path),
            "wallet_label": str(wallet_label),
            "debug": False,
        }
        response = client.call(
            "add_job",
            {
                "name": str(job_name),
                "type": "strategy",
                "payload": payload,
                "interval_seconds": int(interval_seconds),
            },
        )
        if not response.get("ok"):
            response = client.call(
                "update_job",
                {
                    "name": str(job_name),
                    "interval_seconds": int(interval_seconds),
                    "payload": payload,
                },
            )
        if not response.get("ok"):
            raise RuntimeError(f"Runner job registration failed: {response}")
        return response


def _strategy_name(detail: dict[str, Any]) -> str:
    family = re.sub(r"[^a-z0-9]+", "_", str(detail.get("family") or "strategy").lower()).strip("_")
    return f"autolab_{family}_{detail['candidate_hash']}"


def _strategy_class_name(strategy_name: str) -> str:
    pieces = [piece for piece in re.split(r"[^A-Za-z0-9]+", strategy_name) if piece]
    return "".join(piece[:1].upper() + piece[1:] for piece in pieces) + "Strategy"


def _escape_docstring(value: str) -> str:
    return str(value).replace('"""', '\\"\\"\\"').strip()


def _module_path_from_root(*, package_dir: Path, root_dir: Path) -> str:
    relative = package_dir.resolve().relative_to(root_dir.resolve())
    return ".".join(relative.parts)


def _ensure_package_tree(*, package_dir: Path, root_dir: Path) -> None:
    package_dir.mkdir(parents=True, exist_ok=True)
    current = package_dir.resolve()
    root = root_dir.resolve()
    if current != root and root not in current.parents:
        raise ValueError(f"Generated strategy directory must live inside {root}")
    while True:
        init_path = current / "__init__.py"
        if not init_path.exists():
            init_path.write_text("")
        if current.parent == root:
            break
        current = current.parent
