"""Shared utility functions for CLI subcommand modules."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast
from collections.abc import Callable

from siglab.config import SiglabConfig
from siglab.path_utils import resolve_path_from_root
from siglab.schemas import SignalSpec


class _ResearchProvider(Protocol):
    is_configured: bool


class _MarketDataProvider(Protocol):
    def current_bundle_context(self) -> dict[str, Any]: ...


class _AncestryStore(Protocol):
    def best(
        self,
        track: str,
        run_session_id: str | None = ...,
    ) -> dict[str, Any] | None: ...

    def experiment_detail(self, spec_hash: str) -> dict[str, Any] | None: ...

    def dashboard_rows(
        self,
        track: str = ...,
        family: str = ...,
        run_session_id: str | None = ...,
    ) -> list[dict[str, Any]]: ...

    def recent(self, track: str, limit: int = ...) -> list[dict[str, Any]]: ...


class _Mutator(Protocol):
    def canonical_dict(self) -> dict[str, Any]: ...


def latest_path(directory: Path, pattern: str) -> Path | None:
    """Find the most recently modified file matching a glob pattern."""
    matches = sorted(directory.glob(pattern), key=lambda item: item.stat().st_mtime)
    return matches[-1] if matches else None


def read_jsonl(path: Path | None) -> list[dict[str, Any]]:
    rows, _stats = read_jsonl_with_stats(path)
    return rows


def read_jsonl_with_stats(
    path: Path | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if path is None or not path.exists():
        return (
            [],
            {
                "path": str(path) if path else None,
                "line_count": 0,
                "record_count": 0,
                "malformed_count": 0,
                "non_object_count": 0,
            },
        )
    rows: list[dict[str, Any]] = []
    malformed_count = 0
    non_object_count = 0
    lines = path.read_text(encoding="utf-8").splitlines()
    for line in lines:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            malformed_count += 1
            continue
        if isinstance(payload, dict):
            rows.append(payload)
        else:
            non_object_count += 1
    return (
        rows,
        {
            "path": str(path),
            "line_count": len(lines),
            "record_count": len(rows),
            "malformed_count": malformed_count,
            "non_object_count": non_object_count,
        },
    )


def latest_record(
    rows: list[dict[str, Any]],
    *,
    required_value: str | None = None,
) -> dict[str, Any] | None:
    rows = [row for row in rows if _record_has_required_value(row, required_value)]
    if not rows:
        return None
    return sorted(rows, key=_record_sort_key, reverse=True)[0]


def _record_has_required_value(row: dict[str, Any], required_value: str | None) -> bool:
    if required_value is None:
        return True
    if _record_timestamp(row) is None:
        return False
    if required_value == "numeric":
        return float_or_none(row.get("value")) is not None
    if required_value == "quote":
        attrs = dict(row.get("attributes") or {})
        return bool(attrs.get("bid") or row.get("value")) and bool(attrs.get("ask"))
    return True


def _record_sort_key(row: dict[str, Any]) -> tuple[int, float, str]:
    timestamp = _record_timestamp(row)
    if timestamp is None:
        return (0, 0.0, str(row.get("evidence_path") or ""))
    return (1, timestamp.timestamp(), str(row.get("evidence_path") or ""))


def _record_timestamp(row: dict[str, Any]) -> datetime | None:
    value = row.get("timestamp") or row.get("observed_at")
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def float_or_none(value: float | str | None) -> float | None:
    from siglab.utils import safe_float

    return safe_float(value)


def load_json_if_exists(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def sosovalue_currency_id(rows: list[dict[str, Any]], symbol: str) -> int | None:
    needle = str(symbol or "").strip().lower()
    for row in rows:
        if str(row.get("currencyName") or "").strip().lower() == needle:
            return int(row["currencyId"])
        if str(row.get("fullName") or "").strip().lower() == needle:
            return int(row["currencyId"])
    return None


def deployment_eligible(
    *,
    summary: dict[str, Any],
    trial_context: dict[str, Any] | None,
) -> bool:
    return not deployment_ineligible_reasons(
        summary=summary,
        trial_context=trial_context,
    )


def deployment_ineligible_reasons(
    *,
    summary: dict[str, Any],
    trial_context: dict[str, Any] | None,
) -> list[str]:
    summary = dict(summary or {})
    trial_context = dict(trial_context or {})
    reasons: list[str] = []
    if not bool(summary.get("passed")):
        reasons.append("summary_not_passed")
    if str(trial_context.get("fragility_label") or "").strip().lower() == "fragile":
        reasons.append("fragility_label_fragile")
    audit_total_return = summary.get("audit_total_return")
    if audit_total_return is not None and float(audit_total_return) < -0.02:
        reasons.append("audit_total_return_below_minus_2pct")
    fragility_pack = dict(trial_context.get("fragility_pack") or {})
    active_bar_count = fragility_pack.get("active_bar_count")
    if active_bar_count is not None and int(active_bar_count) < 72:
        reasons.append("active_bar_count_below_72")
    return reasons


def sodex_preflight_report(env: dict[str, str] | None = None) -> dict[str, Any]:
    """Check SoDEX signed-path prerequisites and return a readiness report."""
    from siglab.data.sodex_rate_limit import (
        SODEX_ENDPOINT_WEIGHTS,
        SODEX_WEIGHT_BUDGET_PER_MINUTE,
    )
    from siglab.live.sodex_client import (
        SUPPORTED_SODEX_SIGNED_ACTIONS,
        UNSUPPORTED_SODEX_SIGNED_ACTIONS,
    )

    source = env if env is not None else os.environ
    api_key_name = str(source.get("SODEX_API_KEY_NAME") or "").strip()
    account_id = str(source.get("SODEX_ACCOUNT_ID") or "").strip()
    nonce_store = str(source.get("SODEX_NONCE_STORE_PATH") or "").strip()
    environment = str(source.get("SODEX_ENVIRONMENT") or "testnet").strip().lower()
    private_key_present = bool(str(source.get("SODEX_PRIVATE_KEY") or "").strip())
    missing: list[str] = []
    if not api_key_name:
        missing.append("SODEX_API_KEY_NAME")
    if not account_id:
        missing.append("SODEX_ACCOUNT_ID")
    else:
        try:
            if int(account_id) < 0:
                missing.append("SODEX_ACCOUNT_ID must be an unsigned integer")
        except ValueError:
            missing.append("SODEX_ACCOUNT_ID must be an unsigned integer")
    if not nonce_store:
        missing.append("SODEX_NONCE_STORE_PATH")
        nonce_store_status = {
            "ready": False,
            "path_present": False,
            "parent_writable": False,
            "file_writable": False,
            "parseable": False,
            "error": "SODEX_NONCE_STORE_PATH is required",
        }
    else:
        nonce_path = Path(nonce_store).expanduser()
        if not nonce_path.is_absolute():
            nonce_path = (Path.cwd() / nonce_path).resolve()
        parent = nonce_path.parent
        parent_exists = parent.exists()
        parent_writable = bool(parent_exists and os.access(parent, os.W_OK))
        file_writable = bool(
            (not nonce_path.exists() and parent_writable)
            or os.access(nonce_path, os.W_OK),
        )
        parseable = True
        nonce_error = None
        if nonce_path.exists():
            try:
                parsed = json.loads(nonce_path.read_text())
                if not isinstance(parsed, dict):
                    parseable = False
                    nonce_error = "nonce store must be a JSON object"
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                parseable = False
                nonce_error = f"nonce store is not parseable JSON: {exc}"
        if not parent_exists:
            nonce_error = "nonce store parent directory does not exist"
        elif not parent_writable:
            nonce_error = "nonce store parent directory is not writable"
        elif not file_writable:
            nonce_error = "nonce store file is not writable"
        nonce_store_status = {
            "ready": bool(
                parent_exists and parent_writable and file_writable and parseable,
            ),
            "path_present": True,
            "parent_writable": parent_writable,
            "file_writable": file_writable,
            "parseable": parseable,
            "error": nonce_error,
        }
        if not nonce_store_status["ready"]:
            missing.append(
                f"SODEX_NONCE_STORE_PATH not ready: {nonce_store_status['error']}",
            )
    if not private_key_present:
        missing.append("SODEX_PRIVATE_KEY")
    if environment not in {"mainnet", "testnet"}:
        missing.append("SODEX_ENVIRONMENT must be mainnet or testnet")
    mainnet_confirmation = str(
        source.get("SODEX_MAINNET_LIVE_WRITE_CONFIRMATION") or "",
    ).strip()
    testnet_passed = (
        str(source.get("SODEX_TESTNET_PREFLIGHT_PASSED") or "").strip().lower()
    )
    if environment == "mainnet":
        if testnet_passed not in {"1", "true", "yes"}:
            missing.append("SODEX_TESTNET_PREFLIGHT_PASSED must be true before mainnet")
        if mainnet_confirmation != "I_UNDERSTAND_MAINNET_RISK":
            missing.append(
                "SODEX_MAINNET_LIVE_WRITE_CONFIRMATION must equal I_UNDERSTAND_MAINNET_RISK",
            )
    return {
        "public_read_ready": True,
        "schema_pinned": True,
        "signed_path": {
            "ready": not missing,
            "environment": environment,
            "signer_ready": private_key_present,
            "signer_type": "evm-private-key" if private_key_present else None,
            "accountID_present": bool(account_id),
            "api_key_name_present": bool(api_key_name),
            "nonce_store_ready": bool(nonce_store_status["ready"]),
            "nonce_store": nonce_store_status,
            "testnet_preflight_passed": testnet_passed in {"1", "true", "yes"},
            "mainnet_confirmation_present": mainnet_confirmation
            == "I_UNDERSTAND_MAINNET_RISK",
            "missing_prerequisites": missing,
        },
        "live_write_allowed": not missing,
        "live_write_refusal_reason": None
        if not missing
        else "missing signed-path prerequisites",
        "access_plan": {
            "preferred_validation_environment": "testnet",
            "mainnet_warning": "Do not attempt signed mainnet writes until testnet/account preflight passes and operator confirms supported chain/deposit requirements.",
            "buildathon_access_request": "Request SoSoValue/SoDEX buildathon access early when missing API/account prerequisites block live validation.",
            "required_operator_inputs": [
                "SoDEX environment: testnet first, mainnet only after explicit operator confirmation",
                "SoDEX API key name",
                "SoDEX accountID",
                "nonce store path",
                "isolated EVM signer private key",
                "user address for private/account WebSocket probes",
            ],
        },
        "next_actions": [
            "prefer SODEX_ENVIRONMENT=testnet for first signed validation",
            *[f"set {name}" for name in missing if name.startswith("SODEX_")],
            "run siglab sodex-preflight --json again before any deploy/export/live attempt",
        ],
        "request_weight_budget_per_minute": SODEX_WEIGHT_BUDGET_PER_MINUTE,
        "documented_endpoint_weights": dict(sorted(SODEX_ENDPOINT_WEIGHTS.items())),
        "rate_limit_scope": {
            "scope": "per_ip",
            "local_scheduler_only": True,
            "operator_warning": "SigLab's built-in SoDEX weight scheduler is process-local. Use an external shared limiter when multiple processes share one egress IP.",
        },
        "supported_signed_actions": sorted(SUPPORTED_SODEX_SIGNED_ACTIONS),
        "unsupported_signed_actions": dict(UNSUPPORTED_SODEX_SIGNED_ACTIONS),
    }


def parse_sodex_enum(value: str, aliases: dict[str, int], field_name: str) -> int:
    raw = str(value).strip()
    if raw.isdigit():
        parsed = int(raw)
        if parsed in set(aliases.values()):
            return parsed
    normalized = raw.upper().replace("-", "_")
    if normalized in aliases:
        return aliases[normalized]
    accepted = ", ".join(
        [*aliases.keys(), *[str(v) for v in sorted(set(aliases.values()))]],
    )
    print(
        f"--{field_name.replace('_', '-')} must be one of: {accepted}",
        file=sys.stderr,
    )
    raise SystemExit(1)


def require_sosovalue_config(settings: SiglabConfig) -> Path:
    config_path = resolve_path_from_root(
        settings.sosovalue_config_path,
        root_dir=settings.root_dir,
    )
    if not config_path.exists():
        print(
            f"SOSOVALUE_CONFIG_PATH is required for this command and must point to an existing file. Tried: {config_path}. Create it with `cp config.example.json config.json` or point SOSOVALUE_CONFIG_PATH at an existing SoSoValue config.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    settings.sosovalue_config_path = config_path
    return config_path


def display_deployment_record(
    *,
    settings: SiglabConfig,
    record: dict[str, Any],
) -> dict[str, Any]:
    normalized = dict(record)
    for key in [
        "strategy_dir",
        "spec_path",
        "manifest_path",
        "readme_path",
        "config_path",
    ]:
        normalized[key] = display_path_static(
            normalized.get(key),
            root_dir=settings.root_dir,
        )
    return normalized


def display_path_static(value: str | Path | None, root_dir: Path) -> str:
    """Resolve a path for display, relative to root_dir if possible."""
    from siglab.path_utils import display_path as _dp

    return cast(str, _dp(value, root_dir=root_dir))


def strip_audit_fields(
    payload: dict[str, Any] | list[Any],
) -> dict[str, Any] | list[Any]:
    if isinstance(payload, dict):
        cleaned: dict[str, Any] = {}
        for key, value in payload.items():
            key_str = str(key)
            if key_str.startswith("audit_"):
                continue
            cleaned[key_str] = strip_audit_fields(value)
        return cleaned
    if isinstance(payload, list):
        return [strip_audit_fields(item) for item in payload]
    return payload


def agent_safe_recent_results(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned_rows: list[dict[str, Any]] = []
    for row in rows:
        cleaned = dict(row)
        cleaned["summary"] = strip_audit_fields(dict(row.get("summary") or {}))
        cleaned_rows.append(cleaned)
    return cleaned_rows


def agent_safe_memory_packet(packet: dict[str, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], strip_audit_fields(dict(packet or {})))


def tool_only_external_research(*, web_researcher: _ResearchProvider) -> dict[str, Any]:
    return {
        "enabled": bool(web_researcher.is_configured),
        "provider": "tool_only",
        "queries": [],
        "reports": [],
    }


def minimal_research_summary(
    *,
    track: str,
    parent: SignalSpec,
    provider: _MarketDataProvider,
    web_researcher: _ResearchProvider,
    run_context: dict[str, Any],
) -> dict[str, Any]:
    return {
        "track": track,
        "parent_family": parent.family,
        "parent_hash": parent.strategy_hash(),
        "market_bundle": dict(provider.current_bundle_context() or {}),
        "external_research": tool_only_external_research(web_researcher=web_researcher),
        "run_context": dict(run_context),
        "memory_packet": {},
    }


def incumbent_detail(
    *,
    ancestry: _AncestryStore,
    track: str,
    run_session_id: str | None = None,
) -> dict[str, Any] | None:
    best = ancestry.best(track, run_session_id=run_session_id)
    if best is None:
        return None
    spec_hash = str(best.get("spec_hash") or "")
    if not spec_hash:
        return None
    return cast(dict[str, Any] | None, ancestry.experiment_detail(spec_hash))




def pick_deterministic_parent(
    *,
    track: str,
    ancestry: _AncestryStore,
    seed_specs: list[SignalSpec],
    iteration_number: int,
) -> SignalSpec:
    from collections import Counter

    recent_rows = ancestry.recent(track, limit=500)
    deterministic_rows = [row for row in recent_rows if row_is_deterministic(row)]
    family_counts: Counter[str] = Counter(
        str(row.get("family") or "") for row in deterministic_rows
    )
    seed_order = list(seed_specs)
    min_count = min(
        (family_counts.get(seed.family, 0) for seed in seed_order),
        default=0,
    )
    least_used = [
        seed for seed in seed_order if family_counts.get(seed.family, 0) == min_count
    ]
    if not least_used:
        return seed_order[0]
    return least_used[(iteration_number - 1) % len(least_used)]


def row_is_deterministic(row: dict[str, Any]) -> bool:
    research_summary = dict(row.get("research_summary") or {})
    run_context = dict(research_summary.get("run_context") or {})
    if "deterministic" in run_context:
        return bool(run_context.get("deterministic"))
    return str(run_context.get("phase_label") or "").strip().lower() == "burn_in"


def spec_trade_style(spec: dict[str, Any]) -> str:
    params = dict(spec.get("params") or {})
    trade_style = str(params.get("trade_style") or "").strip().lower()
    return trade_style or "unspecified"


def write_artifact(
    settings: SiglabConfig,
    track: str,
    evaluation: dict[str, Any],
) -> Path:
    from siglab.utils import write_json

    target_dir = settings.artifact_dir / track
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = target_dir / f"{timestamp}_{evaluation['spec_hash']}.json"
    write_json(target, evaluation)
    return cast(Path, target)


def parse_family_scope(
    family: str | None,
    families: str | None,
) -> str | list[str] | None:
    if family and families:
        print("Use either --family or --families, not both", file=sys.stderr)
        raise SystemExit(1)
    if family:
        return family
    if not families:
        return None
    parsed = [item.strip() for item in str(families).split(",") if item.strip()]
    if not parsed:
        print("--families must contain at least one family", file=sys.stderr)
        raise SystemExit(1)
    return parsed


def _format_optional_pct(value: float | str | None) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.2%}"
    except (TypeError, ValueError):
        return "n/a"


def _format_optional_number(value: float | str | None) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "n/a"


def external_research_from_llm_trace(
    *,
    llm_trace: dict[str, Any] | None,
    web_researcher: _ResearchProvider,
) -> dict[str, Any]:
    payload = tool_only_external_research(web_researcher=web_researcher)
    trace = dict((llm_trace or {}).get("trace") or {})
    tool_calls = list(trace.get("tool_calls") or [])
    reports: list[dict[str, Any]] = []
    queries: list[str] = []
    for tool_call in tool_calls:
        if str(tool_call.get("name") or "") != "tavily_search":
            continue
        result = dict(tool_call.get("result") or {})
        if not bool(result.get("ok")):
            continue
        query = str(result.get("query") or "").strip()
        if not query:
            continue
        queries.append(query)
        reports.append(
            {
                "query": query,
                "answer": result.get("answer"),
                "insights": list(result.get("insights") or []),
                "sources": list(result.get("sources") or []),
            },
        )
    if reports:
        payload["provider"] = "tavily_tool_calls"
        payload["queries"] = queries
        payload["reports"] = reports
    return payload


_HTML_CACHE: dict[str, str] = {}


def _render_html_template(name: str, **kwargs: Any) -> str:
    if name not in _HTML_CACHE:
        template_path = Path(__file__).parent.parent / "assets" / f"{name}.html"
        _HTML_CACHE[name] = template_path.read_text(encoding="utf-8")
    return _HTML_CACHE[name].format(**kwargs)


def add_json_flag(
    parser: argparse.ArgumentParser,
    *,
    dest: str = "as_json",
    default: bool = False,
    help_text: str | None = None,
) -> None:
    parser.add_argument(
        "--json",
        action="store_true",
        dest=dest,
        default=default,
        help=help_text or "Emit machine-readable JSON to stdout.",
    )


def maybe_print_json(payload: object, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, default=str))


def write_json_and_maybe_print(
    path: Path,
    payload: object,
    *,
    as_json: bool,
    writer: Callable[..., Any] | None = None,
) -> Path:
    if writer is None:
        from siglab.utils import write_json as _write_json

        writer = _write_json
    written = writer(path, payload)
    maybe_print_json(payload, as_json=as_json)
    return written if isinstance(written, Path) else path


def display_paths(
    values: str | Path | list[str | Path] | None,
    *,
    root_dir: Path | None,
) -> list[str | None]:
    from siglab.path_utils import display_path as _dp

    if values is None:
        return []
    if not isinstance(values, list):
        return [_dp(values, root_dir=root_dir)]
    return [_dp(item, root_dir=root_dir) for item in values]
