"""Tests for siglab.cli.helpers — critical public API coverage."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from siglab.cli.helpers import (
    agent_safe_memory_packet,
    agent_safe_recent_results,
    deployment_eligible,
    deployment_ineligible_reasons,
    display_path_static,
    float_or_none,
    latest_path,
    load_json_if_exists,
    parse_family_scope,
    read_jsonl,
    read_jsonl_with_stats,
    split_cli_list,
    sosovalue_currency_id,
    spec_trade_style,
    strip_audit_fields,
)
from siglab.path_utils import resolve_path_from_root
from siglab.track_registry import canonical_track_name, track_label


class TestSplitCliList:
    def test_comma_separated(self):
        assert split_cli_list("BTC-USD,ETH-USD") == ["BTC-USD", "ETH-USD"]

    def test_whitespace_stripped(self):
        assert split_cli_list(" BTC , ETH ") == ["BTC", "ETH"]

    def test_empty_string(self):
        assert split_cli_list("") == []

    def test_none_input(self):
        assert split_cli_list(None) == []

    def test_trailing_comma(self):
        assert split_cli_list("BTC,") == ["BTC"]


class TestCanonicalTrackName:
    def test_known_track(self):
        assert canonical_track_name("trend_signals") == "trend_signals"

    def test_unknown_track_passthrough(self):
        assert canonical_track_name("custom_track") == "custom_track"

    def test_none_returns_none(self):
        assert canonical_track_name(None) is None


class TestTrackLabel:
    def test_trend_signals(self):
        assert track_label("trend_signals") == "Directional Perps"

    def test_yield_flows(self):
        assert track_label("yield_flows") == "Systematic Carry"

    def test_unknown_track(self):
        assert track_label("nonexistent_track") == "Nonexistent Track"

    def test_none(self):
        assert track_label(None) == "Unknown Track"


class TestDisplayPathStatic:
    def test_relative_path(self, tmp_path):
        sub = tmp_path / "foo" / "bar.txt"
        result = display_path_static(sub, root_dir=tmp_path)
        assert result == "foo/bar.txt"

    def test_external_path(self, tmp_path, monkeypatch):
        external = Path("/tmp/external.txt")
        result = display_path_static(external, root_dir=tmp_path)
        assert "external" in result


class TestResolvePathFromRoot:
    def test_relative_resolves(self, tmp_path):
        result = resolve_path_from_root("data/cache", root_dir=tmp_path)
        assert result == (tmp_path / "data/cache").resolve()

    def test_absolute_passthrough(self):
        result = resolve_path_from_root("/tmp/foo", root_dir=Path("/home"))
        assert result == Path("/tmp/foo")


class TestFloatOrNone:
    def test_valid_float(self):
        assert float_or_none("3.14") == pytest.approx(3.14)

    def test_integer_string(self):
        assert float_or_none("42") == pytest.approx(42.0)

    def test_none_returns_none(self):
        assert float_or_none(None) is None

    def test_non_numeric_string(self):
        assert float_or_none("abc") is None

    def test_already_float(self):
        assert float_or_none(1.5) == pytest.approx(1.5)


class TestLoadJsonIfExists:
    def test_valid_json(self, tmp_path):
        path = tmp_path / "data.json"
        path.write_text(json.dumps({"key": "value"}))
        assert load_json_if_exists(path) == {"key": "value"}

    def test_missing_file(self, tmp_path):
        assert load_json_if_exists(tmp_path / "missing.json") is None

    def test_none_input(self):
        assert load_json_if_exists(None) is None


class TestReadJsonl:
    def test_valid_jsonl(self, tmp_path):
        path = tmp_path / "test.jsonl"
        path.write_text('{"a":1}\n{"b":2}\n')
        rows = read_jsonl(path)
        assert rows == [{"a": 1}, {"b": 2}]

    def test_with_stats(self, tmp_path):
        path = tmp_path / "test.jsonl"
        path.write_text('{"a":1}\nBAD\n{"b":2}\n')
        rows, stats = read_jsonl_with_stats(path)
        assert len(rows) == 2
        assert stats["malformed_count"] == 1
        assert stats["record_count"] == 2

    def test_none_path(self):
        assert read_jsonl(None) == []


class TestStripAuditFields:
    def test_removes_audit_prefix(self):
        payload = {"audit_score": 0.9, "real_field": "ok"}
        result = strip_audit_fields(payload)
        assert "audit_score" not in result
        assert result["real_field"] == "ok"

    def test_nested(self):
        payload = {"summary": {"audit_total": 1.0, "passed": True}}
        result = strip_audit_fields(payload)
        assert "audit_total" not in result["summary"]
        assert result["summary"]["passed"] is True

    def test_list_handling(self):
        payload = [{"audit_x": 1, "y": 2}]
        result = strip_audit_fields(payload)
        assert "audit_x" not in result[0]
        assert result[0]["y"] == 2


class TestAgentSafeMemoryPacket:
    def test_strips_audit_fields(self):
        packet = {"audit_field": "gone", "kept": True}
        result = agent_safe_memory_packet(packet)
        assert "audit_field" not in result
        assert result["kept"] is True

    def test_none_packet(self):
        result = agent_safe_memory_packet(None)
        assert isinstance(result, dict)


class TestDeploymentIneligibleReasons:
    def test_eligible(self):
        summary = {"passed": True, "audit_total_return": 0.05}
        reasons = deployment_ineligible_reasons(summary=summary, trial_context=None)
        assert reasons == []

    def test_not_passed(self):
        summary = {"passed": False}
        reasons = deployment_ineligible_reasons(summary=summary, trial_context=None)
        assert "summary_not_passed" in reasons

    def test_fragile_label(self):
        summary = {"passed": True}
        trial_context = {"fragility_label": "fragile"}
        reasons = deployment_ineligible_reasons(summary=summary, trial_context=trial_context)
        assert "fragility_label_fragile" in reasons

    def test_audit_return_below_threshold(self):
        summary = {"passed": True, "audit_total_return": -0.05}
        reasons = deployment_ineligible_reasons(summary=summary, trial_context=None)
        assert "audit_total_return_below_minus_2pct" in reasons


class TestDeploymentEligible:
    def test_eligible(self):
        assert deployment_eligible(summary={"passed": True}, trial_context=None) is True

    def test_ineligible(self):
        assert deployment_eligible(summary={"passed": False}, trial_context=None) is False


class TestSosovalueCurrencyId:
    def test_found(self):
        rows = [{"currencyId": 1, "currencyName": "bitcoin"}, {"currencyId": 2, "currencyName": "ethereum"}]
        assert sosovalue_currency_id(rows, "bitcoin") == 1

    def test_not_found(self):
        rows = [{"currencyId": 1, "currencyName": "bitcoin"}]
        assert sosovalue_currency_id(rows, "solana") is None


class TestLatestPath:
    def test_finds_latest(self, tmp_path):
        (tmp_path / "a.json").write_text("{}")
        (tmp_path / "b.json").write_text("{}")
        result = latest_path(tmp_path, "*.json")
        assert result is not None

    def test_no_matches(self, tmp_path):
        assert latest_path(tmp_path, "*.json") is None


class TestSpecTradeStyle:
    def test_explicit_style(self):
        spec = {"params": {"trade_style": "reversion"}}
        assert spec_trade_style(spec) == "reversion"

    def test_missing_params(self):
        assert spec_trade_style({}) == "unspecified"

    def test_empty_style(self):
        spec = {"params": {"trade_style": ""}}
        assert spec_trade_style(spec) == "unspecified"


class TestParseFamilyScope:
    def test_single_family(self):
        assert parse_family_scope("momentum", None) == "momentum"

    def test_multiple_families(self):
        result = parse_family_scope(None, "momentum,reversion")
        assert result == ["momentum", "reversion"]

    def test_both_raises(self):
        with pytest.raises(SystemExit):
            parse_family_scope("momentum", "reversion")


class TestAgentSafeRecentResults:
    def test_strips_audit_from_summary(self):
        rows = [{"summary": {"audit_score": 0.9, "passed": True}, "id": 1}]
        result = agent_safe_recent_results(rows)
        assert "audit_score" not in result[0]["summary"]
        assert result[0]["summary"]["passed"] is True
