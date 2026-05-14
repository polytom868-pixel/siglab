from __future__ import annotations

import ast
import importlib
import inspect
import json
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml


SEVERITY_ORDER = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}
REQUIRED_CAPABILITIES = {
    "signal_shape",
    "execution_profile",
    "diagnostic_adapter",
    "policy_schema",
    "prompt_module",
}
STUB_MARKERS = (
    "stubbed until",
    "placeholder",
    "notimplemented",
    "pass",
)


def build_profile(root_dir: Path) -> dict[str, Any]:
    root_dir = root_dir.resolve()
    package_dir = root_dir / "siglab"
    findings: list[dict[str, Any]] = []
    modules: list[dict[str, Any]] = []
    public_objects: list[dict[str, Any]] = []

    for path in sorted(package_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        module_name = _module_name(root_dir=root_dir, path=path)
        module_record: dict[str, Any] = {
            "module": module_name,
            "path": str(path.relative_to(root_dir)),
        }
        modules.append(module_record)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            findings.append(
                _finding(
                    "critical",
                    "syntax_error",
                    path,
                    f"{exc.msg} at line {exc.lineno}",
                    symbol=module_name,
                    line=exc.lineno,
                )
            )
            continue

        _scan_ast_for_static_findings(
            tree=tree,
            path=path,
            findings=findings,
        )

        try:
            module = importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001 - this is exactly what the probe must expose
            findings.append(
                _finding(
                    "critical",
                    "import_error",
                    path,
                    f"{type(exc).__name__}: {exc}",
                    symbol=module_name,
                )
            )
            continue

        for name, value in sorted(vars(module).items()):
            if name.startswith("_") or getattr(value, "__module__", None) != module_name:
                continue
            if inspect.isfunction(value) or inspect.isclass(value):
                public_objects.append(_describe_public_object(name=name, value=value, path=path))

    _scan_contract_files(root_dir=root_dir, findings=findings)

    findings.sort(key=lambda item: (SEVERITY_ORDER.get(item["severity"], 99), item["path"], item["kind"]))
    summary = {
        "module_count": len(modules),
        "public_object_count": len(public_objects),
        "finding_count": len(findings),
        "by_severity": _counts(findings, "severity"),
        "by_kind": _counts(findings, "kind"),
    }
    return {
        "summary": summary,
        "modules": modules,
        "public_objects": public_objects,
        "findings": findings,
    }


def profile_as_text(profile: dict[str, Any]) -> str:
    summary = dict(profile.get("summary") or {})
    lines = [
        "SigLab hardening profile",
        f"modules={summary.get('module_count', 0)} public_objects={summary.get('public_object_count', 0)} findings={summary.get('finding_count', 0)}",
        f"by_severity={json.dumps(summary.get('by_severity') or {}, sort_keys=True)}",
        f"by_kind={json.dumps(summary.get('by_kind') or {}, sort_keys=True)}",
        "",
        "Findings:",
    ]
    findings = list(profile.get("findings") or [])
    if not findings:
        lines.append("- none")
        return "\n".join(lines)
    for finding in findings:
        location = finding["path"]
        if finding.get("line"):
            location = f"{location}:{finding['line']}"
        symbol = f" {finding['symbol']}" if finding.get("symbol") else ""
        lines.append(
            f"- [{finding['severity']}] {finding['kind']} {location}{symbol}: {finding['message']}"
        )
    return "\n".join(lines)


def strict_failure_count(profile: dict[str, Any]) -> int:
    return sum(
        1
        for finding in list(profile.get("findings") or [])
        if finding.get("severity") in {"critical", "high"}
    )


def _module_name(*, root_dir: Path, path: Path) -> str:
    return ".".join(path.relative_to(root_dir).with_suffix("").parts)


def _describe_public_object(*, name: str, value: Any, path: Path) -> dict[str, Any]:
    record: dict[str, Any] = {
        "name": name,
        "kind": "class" if inspect.isclass(value) else "function",
        "path": str(path),
    }
    try:
        record["signature"] = str(inspect.signature(value))
    except (TypeError, ValueError):
        record["signature"] = "<uninspectable>"
    if inspect.isclass(value) and is_dataclass(value):
        record["dataclass_fields"] = [field.name for field in fields(value)]
    return record


def _scan_ast_for_static_findings(*, tree: ast.AST, path: Path, findings: list[dict[str, Any]]) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if _body_is_pass_only(node.body):
                findings.append(
                    _finding(
                        "high",
                        "pass_only_function",
                        path,
                        "Function body is only pass.",
                        symbol=node.name,
                        line=node.lineno,
                    )
                )
        if isinstance(node, ast.Raise):
            text = ast.unparse(node).lower() if hasattr(ast, "unparse") else ""
            if "stubbed until" in text or "notimplemented" in text:
                findings.append(
                    _finding(
                        "high",
                        "stubbed_runtime_path",
                        path,
                        "Runtime path raises a stub/not-implemented error.",
                        line=node.lineno,
                    )
                )
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if path.name == "hardening_profile.py":
                continue
            lowered = node.value.lower()
            if any(marker in lowered for marker in STUB_MARKERS[:3]):
                findings.append(
                    _finding(
                        "medium",
                        "stub_marker",
                        path,
                        node.value.strip()[:220],
                        line=getattr(node, "lineno", None),
                    )
                )


def _scan_contract_files(*, root_dir: Path, findings: list[dict[str, Any]]) -> None:
    family_path = root_dir / "mutable" / "family_lab.yaml"
    feature_path = root_dir / "mutable" / "feature_lab.yaml"
    try:
        families_payload = yaml.safe_load(family_path.read_text(encoding="utf-8")) or {}
        features_payload = yaml.safe_load(feature_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        findings.append(
            _finding(
                "critical",
                "contract_yaml_load_error",
                family_path,
                f"{type(exc).__name__}: {exc}",
            )
        )
        return

    family_tracks = dict(families_payload.get("tracks") or {})
    feature_tracks = dict(features_payload.get("tracks") or {})
    for track_name, track_payload in family_tracks.items():
        for family_name, family_payload in dict(track_payload.get("families") or {}).items():
            family_spec = dict(family_payload or {})
            capabilities = dict(family_spec.get("capabilities") or {})
            missing = sorted(REQUIRED_CAPABILITIES - set(capabilities))
            if missing:
                findings.append(
                    _finding(
                        "high",
                        "family_missing_capabilities",
                        family_path,
                        f"Missing capabilities: {', '.join(missing)}",
                        symbol=f"{track_name}.{family_name}",
                    )
                )

            feature_family = (
                dict(feature_tracks.get(track_name) or {})
                .get("families", {})
                .get(family_name)
                or {}
            )
            aliases = set(dict(feature_family.get("aliases") or {}).keys())
            raw_series = set(str(value) for value in list(feature_family.get("raw_series") or []))
            known_features = aliases | raw_series
            if not known_features:
                findings.append(
                    _finding(
                        "critical",
                        "family_missing_feature_contract",
                        feature_path,
                        "Family has no feature contract.",
                        symbol=f"{track_name}.{family_name}",
                    )
                )
                continue
            for feature_name in dict(family_spec.get("feature_weights") or {}):
                if feature_name not in known_features:
                    findings.append(
                        _finding(
                            "high",
                            "family_feature_weight_unknown",
                            family_path,
                            f"`{feature_name}` is weighted but not defined as raw series or alias.",
                            symbol=f"{track_name}.{family_name}",
                        )
                    )


def _body_is_pass_only(body: list[ast.stmt]) -> bool:
    meaningful = [node for node in body if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Constant)]
    return len(meaningful) == 1 and isinstance(meaningful[0], ast.Pass)


def _finding(
    severity: str,
    kind: str,
    path: Path,
    message: str,
    *,
    symbol: str | None = None,
    line: int | None = None,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "kind": kind,
        "path": str(path),
        "symbol": symbol,
        "line": line,
        "message": message,
    }


def _counts(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return counts
