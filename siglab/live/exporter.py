from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from siglab.cli.helpers import sodex_preflight_report
from siglab.config import SiglabConfig
from siglab.data.deployment_store import DeploymentStore as LineageStore
from siglab.evaluation.events import evaluate_gates
from siglab.llm import ClaudeClient, LLMProviderError
from siglab.path_utils import resolve_path_from_root
from siglab.track_registry import track_label

SUPPORTED_DIRECTIONAL_FAMILIES = {
    "perp_multi_asset_decision",
    "perp_pair_trade_unlevered",
    "perp_pair_trade_levered",
}


@dataclass
class DeploymentRecord:
    spec_hash: str
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
            "spec_hash": self.spec_hash,
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


def deployment_readiness(detail: dict[str, Any]) -> dict[str, Any]:
    spec = dict(detail.get("spec") or {})
    summary = dict(detail.get("summary") or {})
    compiled = dict((detail.get("artifact") or {}).get("compiled_metadata") or {})
    reasons: list[str] = []
    warnings: list[str] = []
    family = str(spec.get("family") or "")
    track = str(spec.get("track") or "")
    strict_holdout = bool(summary.get("strict_holdout"))
    holdout_available = bool(summary.get("holdout_available"))
    liquidated = bool(summary.get("liquidation_count", 0))
    series_available = bool((detail.get("artifact") or {}).get("canonical_run"))
    supported = track == "trend_signals" and family in SUPPORTED_DIRECTIONAL_FAMILIES
    if not supported:
        reasons.append(f"Live export is not implemented for {track}/{family} yet.")
    if not strict_holdout:
        reasons.append("Experiment does not have a strict holdout split.")
    if not holdout_available:
        reasons.append("Experiment does not have retained holdout metrics.")
    if liquidated:
        reasons.append("Experiment liquidated in backtest windows.")
    if not series_available:
        reasons.append("Experiment predates canonical retained series runs.")
    holdout_return = summary.get("holdout_total_return")
    if isinstance(holdout_return, (int, float)) and float(holdout_return) <= 0.0:
        warnings.append("Holdout return is non-positive.")
    if compiled.get("signal_timing") != "next_bar":
        warnings.append("Compiled signal timing is not next_bar.")
    return {
        "supported": supported and (not reasons),
        "support_status": "supported" if supported else "unsupported",
        "reasons": reasons,
        "warnings": warnings,
    }


class LiveDeploymentManager:
    """Live deployment orchestrator for SigLab experiments."""

    def __init__(
        self,
        settings: SiglabConfig,
        ancestry: LineageStore,
        claude: ClaudeClient | None = None,
    ) -> None:
        self.settings = settings
        self.ancestry = ancestry
        self.claude = claude

    async def deploy(
        self,
        *,
        spec_hash: str,
        wallet_label: str | None,
        config_path: str,
        interval_seconds: int | None,
        job_name: str | None,
        dry_run: bool,
        llm_finalize: bool,
        schedule: bool,
    ) -> DeploymentRecord:
        detail = self.ancestry.experiment_detail(spec_hash)
        if detail is None:
            raise ValueError(f"Unknown spec hash: {spec_hash}")
        track = str(detail.get("track") or detail.get("spec", {}).get("track", ""))
        summary = dict(detail.get("summary") or {})
        gates_passed, gate_reasons = evaluate_gates(track, summary)
        if not gates_passed:
            raise ValueError(
                f"Deployment refused — {len(gate_reasons)} gate(s) failed: {'; '.join(gate_reasons)}",
            )
        resolved_config_path = resolve_path_from_root(
            config_path, root_dir=self.settings.root_dir,
        )
        self._preflight_deploy_boundary(
            resolved_config_path=resolved_config_path,
            wallet_label=wallet_label,
            interval_seconds=interval_seconds,
            dry_run=dry_run,
            schedule=schedule,
        )
        readiness = deployment_readiness(detail)
        if not readiness["supported"]:
            raise ValueError(
                "; ".join(readiness["reasons"]) or "Experiment is not live-exportable",
            )
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
        record = DeploymentRecord(
            spec_hash=spec_hash,
            strategy_name=strategy_name,
            strategy_dir=str(package_dir),
            spec_path=str(package_dir / "live_spec.json"),
            manifest_path=str(package_dir / "manifest.yaml"),
            readme_path=str(package_dir / "README.md"),
            job_name=None,
            interval_seconds=None,
            wallet_label=wallet_label,
            config_path=str(resolved_config_path),
            scheduled=False,
            dry_run=bool(dry_run),
            llm_finalized=bool(llm_finalize and notes.get("source") == "claude"),
            support_status=readiness["support_status"],
            support_reason=None,
            metadata={
                "track_label": track_label(detail["track"]),
                "family": detail["family"],
                "warnings": readiness["warnings"],
                "runner_response": None,
                "llm_notes": notes,
            },
        )
        self.ancestry.record_deployment(record.to_dict())
        return record

    def _preflight_deploy_boundary(
        self,
        *,
        resolved_config_path: Path,
        wallet_label: str | None,
        interval_seconds: int | None,
        dry_run: bool,
        schedule: bool,
    ) -> None:
        if not resolved_config_path.exists():
            raise ValueError(f"SoDEX runtime config not found: {resolved_config_path}")
        if not dry_run:
            report = sodex_preflight_report()
            signed_path = report.get("signed_path", {})
            if not signed_path.get("ready", False):
                raise ValueError(
                    f"Live SoDEX deployment requires signing credentials. Missing: {', '.join(signed_path.get('missing_prerequisites', []))}",
                )
        if schedule:
            if not wallet_label:
                raise ValueError(
                    "wallet_label is required when scheduling a runner job",
                )
            if interval_seconds is None or interval_seconds <= 0:
                raise ValueError(
                    "interval_seconds must be positive when scheduling a runner job",
                )
            raise ValueError(
                "Scheduled SoDEX runner jobs require a configured runner client; refusing before writing artifacts",
            )

    async def _finalizer_notes(
        self, detail: dict[str, Any], *, llm_finalize: bool,
    ) -> dict[str, Any]:
        spec = dict(detail.get("spec") or {})
        summary = dict(detail.get("summary") or {})
        base_notes = {
            "source": "template",
            "strategy_doc": spec.get("hypothesis")
            or f"Live export for {spec.get('family')}.",
            "readme_summary": spec.get("hypothesis")
            or "Generated from a deployd SigLab experiment.",
            "operator_notes": "Run this from the siglab Poetry environment so the generated strategy can import siglab runtime helpers.",
            "risk_notes": f"Holdout return {summary.get('holdout_total_return')}, holdout Sharpe {summary.get('holdout_sharpe')}.",
        }
        if not llm_finalize or self.claude is None or (not self.claude.is_configured):
            return base_notes
        prompt = {
            "spec": spec,
            "summary": {
                "aggregate_score": summary.get("aggregate_score"),
                "holdout_total_return": summary.get("holdout_total_return"),
                "holdout_sharpe": summary.get("holdout_sharpe"),
                "median_cagr": summary.get("median_cagr"),
            },
        }
        try:
            payload = await self.claude.complete_json(
                system_prompt="You are preparing operator-facing notes for a generated live trading strategy. Return strict JSON with keys strategy_doc, readme_summary, operator_notes, risk_notes. Keep it concise, concrete, and do not invent unsupported capabilities.",
                user_prompt=json.dumps(prompt, indent=2),
                max_tokens=900,
            )
        except LLMProviderError:
            return base_notes
        return {
            "source": "claude",
            "strategy_doc": str(
                payload.get("strategy_doc") or base_notes["strategy_doc"],
            ).strip(),
            "readme_summary": str(
                payload.get("readme_summary") or base_notes["readme_summary"],
            ).strip(),
            "operator_notes": str(
                payload.get("operator_notes") or base_notes["operator_notes"],
            ).strip(),
            "risk_notes": str(
                payload.get("risk_notes") or base_notes["risk_notes"],
            ).strip(),
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
        spec = dict(detail.get("spec") or {})
        summary = dict(detail.get("summary") or {})
        sosovalue_config_path = str(
            resolve_path_from_root(config_path, root_dir=self.settings.root_dir),
        )
        return {
            "schema_version": "0.1",
            "spec_hash": detail["spec_hash"],
            "strategy_name": strategy_name,
            "track": detail["track"],
            "family": detail["family"],
            "spec": spec,
            "summary": {
                "aggregate_score": summary.get("aggregate_score"),
                "median_sharpe": summary.get("median_sharpe"),
                "median_cagr": summary.get("median_cagr"),
                "holdout_total_return": summary.get("holdout_total_return"),
                "holdout_sharpe": summary.get("holdout_sharpe"),
            },
            "compiled_metadata": compiled,
            "deployment": {
                "created_at": datetime.now(UTC).isoformat(),
                "wallet_label": wallet_label,
                "sosovalue_config_path": sosovalue_config_path,
                "operator_notes": llm_notes.get("operator_notes"),
                "risk_notes": llm_notes.get("risk_notes"),
            },
            "runtime": {
                "sosovalue_config_path": sosovalue_config_path,
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
        module_path = _module_path_from_root(
            package_dir=package_dir, root_dir=self.settings.root_dir,
        )
        strategy_py = f'from __future__ import annotations\nfrom pathlib import Path\nfrom siglab.live.runtime import DirectionalPerpsSigLabStrategy\n\nclass {class_name}(DirectionalPerpsSigLabStrategy):\n """{_escape_docstring(llm_notes.get("strategy_doc") or "Generated SigLab live strategy")}"""\n SPEC_PATH = Path(__file__).with_name("live_spec.json")'
        manifest = f"""schema_version: "0.1" entrypoint: "{module_path}.strategy.{class_name}" permissions: policy: | (wallet.id == 'FORMAT_WALLET_ID') AND ( (action.type == 'sodex_perps_order') OR (action.type == 'sodex_perps_cancel') ) adapters: - name: "LEDGER" capabilities: ["ledger.read", "ledger.write", "strategy.transactions"] - name: "SODEX_PERPS" capabilities: ["market.read", "perps.symbols", "perps.klines", "perps.state", "order.execute", "order.cancel", "position.manage"]"""
        readme = f"# {strategy_name} {llm_notes.get('readme_summary') or 'Generated from a deployd SigLab experiment.'} - Spec hash: `{live_spec['spec_hash']}` - Track / family: `{live_spec['track']}` / `{live_spec['family']}` - Holdout return: `{live_spec['summary'].get('holdout_total_return')}` - Holdout Sharpe: `{live_spec['summary'].get('holdout_sharpe')}` - Dry run default: `{live_spec['runtime']['dry_run']}` {llm_notes.get('operator_notes') or ''} {llm_notes.get('risk_notes') or ''} This package is generated in dry-run mode unless explicitly configured otherwise. Real SoDEX execution requires an operator-provided client that can fetch account state, update leverage, place market orders, and satisfy SoDEX signed REST requirements externally. The generated runtime exposes `dependency_report()` for preflight inspection before any live action."
        (package_dir / "__init__.py").write_text("")
        (package_dir / "strategy.py").write_text(strategy_py)
        (package_dir / "manifest.yaml").write_text(manifest)
        (package_dir / "README.md").write_text(readme)
        (package_dir / "live_spec.json").write_text(json.dumps(live_spec, indent=2))


def _strategy_name(detail: dict[str, Any]) -> str:
    family = re.sub(
        "[^a-z0-9]+", "_", str(detail.get("family") or "strategy").lower(),
    ).strip("_")
    return f"siglab_{family}_{detail['spec_hash']}"


def _strategy_class_name(strategy_name: str) -> str:
    pieces = [piece for piece in re.split("[^A-Za-z0-9]+", strategy_name) if piece]
    if not pieces:
        return "SigLabStrategy"
    normalized = []
    for index, piece in enumerate(pieces):
        if index == 0 and piece.lower() == "siglab":
            normalized.append("SigLab")
        else:
            normalized.append(piece[:1].upper() + piece[1:])
    return "".join(normalized) + "Strategy"


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
