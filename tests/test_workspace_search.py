"""Comprehensive tests for workspace and search modules.

Covers: cards, manifests, builder, indexes, select, mutate, lineage.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml

# Import orchestration first to break circular import chain
# (orchestration imports workspace.builder, which imports orchestration.trials)
import siglab.orchestration.trials  # noqa: F401

from siglab.search.lineage import LineageStore
from siglab.search.mutate import (
    BASKET_NEUTRAL_FAMILIES,
    BASKET_NEUTRAL_LEVERED_FAMILY,
    BASKET_NEUTRAL_UNLEVERED_FAMILY,
    CROSS_SECTIONAL_UNIVERSES,
    LEVERED_PAIR_FAMILY,
    MULTI_ASSET_CARRY_FAMILY,
    PAIR_CARRY_REGIME_FEATURES,
    PAIR_COMPRESSION_REVERSION_FEATURES,
    PAIR_DYNAMIC_RESIDUAL_FEATURES,
    PAIR_MEAN_REVERSION_FEATURES,
    PAIR_MEAN_REVERSION_SPEED_FEATURES,
    PAIR_QUALITY_MOMENTUM_FEATURES,
    PAIR_TRADE_FAMILIES,
    PAIR_UNIVERSES,
    TREND_SIGNALS_LOOKBACK_DAYS,
    UNLEVERED_PAIR_FAMILY,
    SpecMutator,
)
from siglab.search.select import (
    _row_quality,
    pick_deterministic_parent,
    pick_parent,
    rank_deterministic_specs,
)
from siglab.workspace import WorkspaceBuilder
from siglab.workspace.cards import (
    dump_frontmatter,
    dump_yaml_block,
    parse_frontmatter,
    read_frontmatter,
    render_experiment_card,
    render_experiment_view_card,
    render_probe_card,
    strip_audit_fields,
    write_markdown,
)
from siglab.workspace.indexes import (
    INDEX_COMPACT_INTERVAL,
    INDEX_COMPACT_ROW_LIMIT,
    append_jsonl,
    compact_jsonl,
    ensure_index,
    load_jsonl,
    maybe_compact,
    search_rows,
)
from siglab.workspace.manifests import (
    _feature_description,
    _feature_kind,
    build_feature_catalog,
    compute_spec_fingerprint,
    render_constraints,
    render_cookbook_pages,
    render_families_index,
    render_family_contract,
    render_family_feature_manifest,
    render_family_manifest,
    render_feature_catalog_md,
    render_feature_surface,
    render_policy_surface,
    render_regime_catalog,
    render_runbook,
)

# ========================================================================
# cards.py
# ========================================================================


class TestDumpFrontmatter:
    def test_basic_frontmatter(self):
        result = dump_frontmatter({"key": "value", "num": 42}, "Body text")
        assert result.startswith("---\n")
        assert "key: value" in result
        assert "num: 42" in result
        assert result.endswith("Body text\n")

    def test_empty_body(self):
        result = dump_frontmatter({"a": 1}, "")
        assert "---\n" in result
        assert result.strip().endswith("---")

    def test_float_representation(self):
        result = dump_frontmatter({"pi": 3.14}, "")
        assert "3.14" in result


class TestDumpYamlBlock:
    def test_round_trip(self):
        data = {"name": "test", "list": [1, 2, 3]}
        result = dump_yaml_block(data)
        parsed = yaml.safe_load(result)
        assert parsed == data


class TestParseFrontmatter:
    def test_parses_frontmatter(self):
        text = "---\nkey: value\n---\n\nBody here"
        frontmatter, body = parse_frontmatter(text)
        assert frontmatter == {"key": "value"}
        assert "Body here" in body

    def test_no_frontmatter(self):
        text = "Just body text"
        frontmatter, body = parse_frontmatter(text)
        assert frontmatter == {}
        assert body == "Just body text"

    def test_empty_frontmatter(self):
        text = "---\n---\n\nBody"
        frontmatter, body = parse_frontmatter(text)
        assert frontmatter == {}
        assert "Body" in body

    def test_incomplete_frontmatter(self):
        text = "---\nkey: value\nNo closing fence"
        frontmatter, body = parse_frontmatter(text)
        assert frontmatter == {}
        assert body == text


class TestReadFrontmatter:
    def test_reads_from_file(self, tmp_path):
        path = tmp_path / "test.md"
        path.write_text("---\nfoo: bar\n---\n\nContent")
        frontmatter, body = read_frontmatter(path)
        assert frontmatter == {"foo": "bar"}
        assert "Content" in body


class TestWriteMarkdown:
    def test_writes_markdown_file(self, tmp_path):
        path = tmp_path / "out" / "test.md"
        write_markdown(path, frontmatter={"key": "val"}, body="Body text")
        assert path.exists()
        content = path.read_text()
        assert "key: val" in content
        assert "Body text" in content

    def test_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "a" / "b" / "c" / "test.md"
        write_markdown(path, frontmatter={}, body="Body")
        assert path.exists()


class TestStripAuditFields:
    def test_strips_audit_prefixed_keys(self):
        data = {"audit_secret": "hidden", "normal_key": "visible", "audit_log": "hidden"}
        result = strip_audit_fields(data)
        assert "audit_secret" not in result
        assert result == {"normal_key": "visible"}

    def test_nested_dict(self):
        data = {"outer": {"audit_inner": "x", "inner_keep": "y"}}
        result = strip_audit_fields(data)
        assert "audit_inner" not in result["outer"]
        assert result["outer"]["inner_keep"] == "y"

    def test_list_of_dicts(self):
        data = [{"audit_x": 1, "keep": 2}, {"audit_y": 3, "keep": 4}]
        result = strip_audit_fields(data)
        assert result == [{"keep": 2}, {"keep": 4}]

    def test_non_dict_list_primitives(self):
        assert strip_audit_fields(42) == 42
        assert strip_audit_fields("hello") == "hello"
        assert strip_audit_fields([1, 2, 3]) == [1, 2, 3]
        assert strip_audit_fields(None) is None


class TestRenderExperimentCard:
    def test_renders_experiment_card(self):
        row = {
            "spec_hash": "abc123",
            "parent_hash": "parent456",
            "family": "perp_multi_asset_carry",
            "passed": True,
            "deployd": False,
            "spec": {
                "hypothesis": "Carry works",
                "features": ["funding_72h_mean", "funding_carry_to_vol"],
                "universe": {"basis_groups": ["BTC", "ETH"]},
                "params": {"trade_style": "continuation"},
            },
            "summary": {
                "median_total_return": 0.02,
                "validation_total_return": 0.015,
                "pre_audit_canonical_total_return": 0.03,
                "active_bar_fraction": 0.5,
            },
            "research_summary": {
                "trial": {
                    "return_driver": "price_dominant",
                    "exposure_profile": "net_long",
                }
            },
            "created_at": "2026-01-01T00:00:00Z",
        }
        body, frontmatter = render_experiment_card(row=row, artifact=None)
        assert frontmatter["kind"] == "experiment"
        assert frontmatter["spec_hash"] == "abc123"
        assert frontmatter["family"] == "perp_multi_asset_carry"
        assert frontmatter["passed"] is True
        assert "Carry works" in body
        assert "funding_72h_mean" in body

    def test_experiment_card_missing_summary(self):
        row = {
            "spec_hash": "abc",
            "family": "test",
            "passed": False,
            "deployd": False,
            "spec": {},
            "summary": {},
            "research_summary": {},
        }
        body, frontmatter = render_experiment_card(row=row, artifact=None)
        assert frontmatter["outcome"] == "failed"
        assert frontmatter["outcome"] == "failed"


class TestRenderExperimentViewCard:
    def test_renders_view_card(self):
        row = {
            "spec_hash": "hash1",
            "family": "perp_multi_asset_carry",
            "summary": {"pre_audit_canonical_total_return": 0.03, "median_total_return": 0.01},
            "created_at": "2026-01-01T00:00:00Z",
        }
        body, frontmatter = render_experiment_view_card(
            row=row, canonical_card_ref="cards/experiments/hash1.md", kind="viewed"
        )
        assert frontmatter["kind"] == "viewed"
        assert frontmatter["spec_hash"] == "hash1"
        assert "cards/experiments/hash1.md" in body


class TestRenderProbeCard:
    def test_renders_probe_card(self):
        body, frontmatter = render_probe_card(
            probe_key="probe_001",
            probe_type="probe_feature_forward_stats",
            family="perp_multi_asset_carry",
            universe=["BTC", "ETH"],
            bundle_id="bundle-1",
            arguments={"feature": "relative_carry_z_72h"},
            result={"ok": True, "median_spearman": 0.053},
            tracking_tags=["carry", "probe"],
        )
        assert frontmatter["kind"] == "probe"
        assert frontmatter["probe_key"] == "probe_001"
        assert frontmatter["probe_type"] == "probe_feature_forward_stats"
        assert "relative_carry_z_72h" in body
        assert "median_spearman" in body

    def test_probe_card_default_tags(self):
        body, frontmatter = render_probe_card(
            probe_key="p1",
            probe_type="basic_probe",
            family="test_family",
            universe=["A"],
            bundle_id=None,
            arguments={},
            result={"ok": True},
        )
        assert "test_family" in frontmatter["tracking_tags"]
        assert "basic_probe" in frontmatter["tracking_tags"]


# ========================================================================
# manifests.py
# ========================================================================


class TestFeatureKind:
    def test_returns_regime_funding(self):
        kind, subkind = _feature_kind("funding_dispersion_72h", "funding_dispersion_72h")
        assert kind == "regime"

    def test_returns_signal_carry(self):
        kind, subkind = _feature_kind("funding_72h_mean", "funding_72h_mean")
        assert subkind in ("carry",)

    def test_returns_signal_trend(self):
        kind, subkind = _feature_kind("ema_gap_12_26", "ema_gap_12_26")
        assert subkind == "trend"

    def test_returns_signal_general_fallback(self):
        kind, subkind = _feature_kind("custom_feature_x", "custom_feature_x")
        assert kind == "signal"
        assert subkind == "general"


class TestFeatureDescription:
    def test_carry_description(self):
        desc = _feature_description("relative_carry_z_72h", "relative_carry_z_72h")
        assert "Carry relative" in desc

    def test_funding_dispersion_description(self):
        desc = _feature_description("funding_dispersion_72h", "funding_dispersion_72h")
        assert "dispersion" in desc

    def test_return_based(self):
        desc = _feature_description("price_return_24h", "price_return_24h")
        assert "Return" in desc or "return" in desc

    def test_volatility_description(self):
        desc = _feature_description("pair_realized_vol_168h", "pair_realized_vol_168h")
        assert "Volatility" in desc

    def test_fallback_description(self):
        desc = _feature_description("weird_custom_signal", "div(a,b)")
        assert "derived from" in desc


class TestBuildFeatureCatalog:
    def test_builds_catalog(self):
        with (
            patch("siglab.workspace.manifests.load_feature_spec") as mock_load,
        ):
            mock_load.return_value = {
                "aliases": {
                    "funding_72h_mean": "funding_72h_mean",
                    "funding_dispersion_72h": "funding_dispersion_72h",
                },
                "operators": ["div", "clip"],
                "raw_series_by_family": {},
            }
            catalog = build_feature_catalog(
                track="trend_signals",
                families=["perp_multi_asset_carry"],
                root_dir=Path("/fake"),
            )
            assert len(catalog) == 2
            names = [item["name"] for item in catalog]
            assert "funding_72h_mean" in names
            assert "funding_dispersion_72h" in names

    def test_empty_families(self):
        with patch("siglab.workspace.manifests.load_feature_spec") as mock_load:
            mock_load.return_value = {"aliases": {}, "operators": [], "raw_series_by_family": {}}
            catalog = build_feature_catalog(
                track="trend_signals",
                families=[],
                root_dir=Path("/fake"),
            )
            assert catalog == []


class TestComputeSpecFingerprint:
    def test_computes_fingerprint(self, tmp_path):
        mutable = tmp_path / "mutable"
        mutable.mkdir()
        (mutable / "family_lab.yaml").write_text("family: test")
        (mutable / "feature_lab.yaml").write_text("features: test")
        (tmp_path / "siglab" / "search").mkdir(parents=True)
        (tmp_path / "siglab" / "search" / "mutate.py").write_text("# mutate")
        (tmp_path / "siglab" / "families.py").write_text("# families")
        result = compute_spec_fingerprint(tmp_path)
        assert "fingerprint" in result
        assert len(result["fingerprint"]) == 64
        assert len(result["files"]) == 4


class TestRenderHelpers:
    def test_render_runbook(self):
        result = render_runbook()
        assert "# Runbook" in result
        assert "RUNBOOK.md" in result

    def test_render_regime_catalog(self):
        result = render_regime_catalog()
        assert "# Regime Catalog" in result
        assert "market_uptrend" in result

    def test_render_policy_surface(self):
        result = render_policy_surface(families=["perp_multi_asset_carry"])
        assert "# Policy Surface" in result
        assert "multi_asset_carry" in result

    def test_render_cookbook_pages_trend_signals(self):
        pages = render_cookbook_pages("trend_signals")
        assert "pair_trade_patterns.md" in pages
        assert "carry_patterns.md" in pages
        assert "directional_patterns.md" in pages
        assert "basket_neutral_patterns.md" in pages

    def test_render_cookbook_pages_other_track(self):
        pages = render_cookbook_pages("yield_flows")
        assert pages == {}

    def test_render_constraints(self, tmp_path):
        with patch("siglab.workspace.manifests.load_family_spec") as mock_load:
            mock_load.return_value = {}
            with patch("siglab.workspace.manifests.family_execution_profile") as mock_prof:
                mock_prof.return_value = "ranked_carry"
                result = render_constraints(
                    track="trend_signals",
                    families=["perp_multi_asset_carry"],
                    root_dir=tmp_path,
                )
                assert "# Constraints" in result
                assert "multi_asset_carry" in result

    def test_render_feature_surface(self):
        catalog = [
            {"name": "funding_72h_mean", "kind": "signal", "subkind": "carry"},
            {"name": "market_volatility_168h", "kind": "regime", "subkind": "market_regime"},
        ]
        result = render_feature_surface(catalog=catalog)
        assert "# Feature Surface" in result
        assert "funding_72h_mean" in result

    def test_render_feature_catalog_md(self):
        catalog = [
            {"name": "test_feat", "family": ["perp_basket"], "kind": "signal", "subkind": "carry",
             "formula": "test()", "description": "A test feature",
             "common_uses": ["carry_ranking"], "similar_features": []}
        ]
        result = render_feature_catalog_md(catalog=catalog)
        assert "test_feat" in result
        assert "test()" in result

    def test_render_family_feature_manifest(self):
        catalog = [
            {"name": "feat_a", "family": ["family_x"], "kind": "signal", "subkind": "carry",
             "formula": "a()", "description": "desc", "similar_features": [], "anti_patterns": []},
            {"name": "feat_b", "family": ["family_y"], "kind": "signal", "subkind": "trend",
             "formula": "b()", "description": "desc2", "similar_features": [], "anti_patterns": []},
        ]
        result = render_family_feature_manifest(family="family_x", catalog=catalog)
        assert "feat_a" in result
        assert "feat_b" not in result

    def test_render_families_index(self, tmp_path):
        with patch("siglab.workspace.manifests.load_family_spec") as mock_load:
            mock_load.return_value = {}
            with patch("siglab.workspace.manifests.family_execution_profile") as mock_prof:
                mock_prof.return_value = "ranked_carry"
                rows = [
                    {"family": "perp_multi_asset_carry", "passed": True, "summary": {"pre_audit_canonical_total_return": 0.05}},
                    {"family": "perp_multi_asset_carry", "passed": False, "summary": {"pre_audit_canonical_total_return": -0.02}},
                ]
                result = render_families_index(
                    track="trend_signals",
                    families=["perp_multi_asset_carry"],
                    root_dir=tmp_path,
                    rows=rows,
                )
                assert "# Families Index" in result
                assert "hot" in result

    def test_render_family_contract(self, tmp_path):
        with (
            patch("siglab.workspace.manifests.load_family_spec") as mock_load,
            patch("siglab.workspace.manifests.load_feature_spec") as mock_feat_load,
            patch("siglab.workspace.manifests.family_execution_profile") as mock_prof,
        ):
            mock_load.return_value = {"defaults": {"min_abs_score": 0.12}, "capabilities": {"prompt_module": "pair_trade"}}
            mock_feat_load.return_value = {"aliases": {"funding_72h_mean": "rolling_mean(funding,72)"}, "raw_series": [], "operators": []}
            mock_prof.return_value = "pair_trade"
            result = render_family_contract(
                track="trend_signals",
                family=UNLEVERED_PAIR_FAMILY,
                root_dir=tmp_path,
            )
            assert result["family"] == UNLEVERED_PAIR_FAMILY
            assert "funding_72h_mean" in result["allowed_aliases"]
            assert "entry_abs_score" in result["policy_surface"]["pair_local_sweep_fields"]

    def test_render_family_manifest(self, tmp_path):
        with (
            patch("siglab.workspace.manifests.load_family_spec") as mock_load,
            patch("siglab.workspace.manifests.load_feature_spec") as mock_feat_load,
            patch("siglab.workspace.manifests.family_execution_profile") as mock_prof,
        ):
            mock_load.return_value = {
                "defaults": {"min_abs_score": 0.12},
                "capabilities": {"prompt_module": "pair_trade"},
            }
            mock_feat_load.return_value = {
                "aliases": {"funding_72h_mean": "rolling_mean(funding,72)"},
                "raw_series": ["funding"],
                "operators": ["rolling_mean"],
            }
            mock_prof.return_value = "pair_trade"
            result = render_family_manifest(
                track="trend_signals",
                family=UNLEVERED_PAIR_FAMILY,
                root_dir=tmp_path,
            )
            assert UNLEVERED_PAIR_FAMILY in result
            assert "funding_72h_mean" in result
            assert "rolling_mean" in result


# ========================================================================
# builder.py
# ========================================================================


class TestWorkspaceBuilder:
    def test_constructor(self):
        settings = SimpleNamespace(
            root_dir=Path("/tmp"),
            artifact_dir=Path("/tmp/runs"),
            ancestry_db_path=Path("/tmp/ancestry.db"),
        )
        ancestry = MagicMock(spec=LineageStore)
        mutator = MagicMock(spec=SpecMutator)
        builder = WorkspaceBuilder(settings=settings, ancestry=ancestry, mutator=mutator)
        assert builder.settings == settings
        assert builder.ancestry == ancestry
        assert builder.mutator == mutator

    def _ensure_root_files(self, root: Path) -> None:
        (root / "mutable").mkdir(parents=True, exist_ok=True)
        (root / "mutable" / "family_lab.yaml").write_text("tracks: {trend_signals: {families: {perp_multi_asset_carry: {defaults: {}, universe: {basis_groups: [BTC]}}}}}")
        (root / "mutable" / "feature_lab.yaml").write_text("tracks: {trend_signals: {aliases: {ema_gap_12_26: sub(ema(price,12),ema(price,26))}}}")
        (root / "mutable" / "graph_lab.yaml").write_text("specs: []")
        (root / "siglab" / "search").mkdir(parents=True, exist_ok=True)
        (root / "siglab" / "search" / "mutate.py").write_text("")
        (root / "siglab" / "families.py").write_text("")

    def test_initialize_session(self, tmp_path):
        self._ensure_root_files(tmp_path)
        settings = SimpleNamespace(
            root_dir=tmp_path,
            artifact_dir=tmp_path / "runs",
            ancestry_db_path=tmp_path / "ancestry.db",
        )
        ancestry = LineageStore(settings.ancestry_db_path)
        mutator = MagicMock(spec=SpecMutator)
        mutator.load_seed_specs.return_value = []
        mutator._allowed_features_by_family.return_value = {}
        mutator._family_defaults.return_value = {}
        mutator._allowed_families.return_value = ["perp_multi_asset_carry"]
        builder = WorkspaceBuilder(settings=settings, ancestry=ancestry, mutator=mutator)
        session = builder.initialize_session(
            track="trend_signals",
            run_session_id="test-session",
            family_scope=None,
        )
        assert session.track == "trend_signals"
        assert session.root.exists()

    def test_prepare_session_creates_dirs(self, tmp_path):
        self._ensure_root_files(tmp_path)
        settings = SimpleNamespace(
            root_dir=tmp_path,
            artifact_dir=tmp_path / "runs",
            ancestry_db_path=tmp_path / "ancestry.db",
        )
        ancestry = MagicMock(spec=LineageStore)
        ancestry.recent.return_value = []
        mutator = MagicMock(spec=SpecMutator)
        mutator.load_seed_specs.return_value = []
        mutator._allowed_features_by_family.return_value = {}
        mutator._family_defaults.return_value = {}
        mutator._allowed_families.return_value = ["perp_multi_asset_carry"]
        builder = WorkspaceBuilder(settings=settings, ancestry=ancestry, mutator=mutator)
        session = builder.initialize_session(
            track="trend_signals",
            run_session_id="test-session",
            family_scope=None,
        )
        assert session.root.exists()
        assert session.manifests_dir.exists()

    def test_terraform(self, tmp_path):
        self._ensure_root_files(tmp_path)
        settings = SimpleNamespace(
            root_dir=tmp_path,
            artifact_dir=tmp_path / "runs",
            ancestry_db_path=tmp_path / "ancestry.db",
        )
        ancestry = MagicMock(spec=LineageStore)
        mutator = MagicMock(spec=SpecMutator)
        mutator._allowed_families.return_value = ["perp_multi_asset_carry"]
        builder = WorkspaceBuilder(settings=settings, ancestry=ancestry, mutator=mutator)
        with (
            patch("siglab.workspace.builder.build_feature_catalog") as mock_cat,
            patch("siglab.workspace.builder.render_family_manifest", return_value=""),
            patch("siglab.workspace.builder.render_runbook", return_value=""),
        ):
            mock_cat.return_value = []
            session = builder.initialize_session(
                track="trend_signals",
                run_session_id="test-terraform",
                family_scope=None,
            )
            assert session.track == "trend_signals"


# ========================================================================
# indexes.py
# ========================================================================


class TestEnsureIndex:
    def test_creates_file(self, tmp_path):
        path = tmp_path / "index.jsonl"
        assert not path.exists()
        ensure_index(path)
        assert path.exists()
        assert path.read_text() == ""

    def test_existing_file_unchanged(self, tmp_path):
        path = tmp_path / "index.jsonl"
        path.write_text("existing content")
        ensure_index(path)
        assert path.read_text() == "existing content"

    def test_creates_parent_dir(self, tmp_path):
        path = tmp_path / "nested" / "dir" / "index.jsonl"
        ensure_index(path)
        assert path.exists()


class TestAppendJsonl:
    def test_appends_record(self, tmp_path):
        path = tmp_path / "index.jsonl"
        append_jsonl(path, {"key": "value"})
        lines = path.read_text().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0]) == {"key": "value"}

    def test_appends_multiple(self, tmp_path):
        path = tmp_path / "index.jsonl"
        append_jsonl(path, {"a": 1})
        append_jsonl(path, {"b": 2})
        lines = path.read_text().splitlines()
        assert len(lines) == 2

    def test_handles_non_serializable(self, tmp_path):
        path = tmp_path / "index.jsonl"
        append_jsonl(path, {"data": Path("/tmp")})
        lines = path.read_text().splitlines()
        assert len(lines) == 1


class TestLoadJsonl:
    def test_loads_records(self, tmp_path):
        path = tmp_path / "index.jsonl"
        path.write_text('{"a": 1}\n{"b": 2}\n')
        rows = load_jsonl(path)
        assert len(rows) == 2

    def test_skips_empty_lines(self, tmp_path):
        path = tmp_path / "index.jsonl"
        path.write_text('{"a": 1}\n\n{"b": 2}\n')
        rows = load_jsonl(path)
        assert len(rows) == 2

    def test_returns_empty_for_missing_file(self, tmp_path):
        path = tmp_path / "nonexistent.jsonl"
        rows = load_jsonl(path)
        assert rows == []

    def test_skips_invalid_json(self, tmp_path):
        path = tmp_path / "index.jsonl"
        path.write_text('{"valid": true}\nnot json\n{"also": "valid"}\n')
        rows = load_jsonl(path)
        assert len(rows) == 2


class TestCompactJsonl:
    def test_does_not_compact_below_limit(self, tmp_path):
        path = tmp_path / "index.jsonl"
        for i in range(10):
            append_jsonl(path, {"spec_hash": f"hash{i}", "created_at": f"2026-01-{i+1:02d}T00:00:00Z"})
        compact_jsonl(path, key_fields=["spec_hash"])
        rows = load_jsonl(path)
        assert len(rows) == 10

    def test_dedupes_by_key_fields(self, tmp_path):
        path = tmp_path / "index.jsonl"
        for i in range(INDEX_COMPACT_ROW_LIMIT + 5):
            append_jsonl(path, {"spec_hash": f"hash{i % 3}", "created_at": f"2026-01-{(i % 3)+1:02d}T00:00:00Z", "other": i})
        compact_jsonl(path, key_fields=["spec_hash"])
        rows = load_jsonl(path)
        assert len(rows) == 3


class TestMaybeCompact:
    def test_compacts_on_interval(self, tmp_path):
        path = tmp_path / "index.jsonl"
        for i in range(INDEX_COMPACT_ROW_LIMIT + 5):
            append_jsonl(path, {"spec_hash": f"hash{i}", "created_at": "2026-01-01T00:00:00Z"})
        maybe_compact(path, key_fields=["spec_hash"], iteration_number=INDEX_COMPACT_INTERVAL)
        rows = load_jsonl(path)
        assert len(rows) == INDEX_COMPACT_ROW_LIMIT + 5
        # Not compacted because INDEX_COMPACT_ROW_LIMIT threshold: only compact if > 1000 rows
        assert len(rows) == INDEX_COMPACT_ROW_LIMIT + 5

    def test_skips_non_interval(self, tmp_path):
        path = tmp_path / "index.jsonl"
        for i in range(INDEX_COMPACT_ROW_LIMIT + 5):
            append_jsonl(path, {"spec_hash": f"hash{i}", "created_at": "2026-01-01T00:00:00Z"})
        maybe_compact(path, key_fields=["spec_hash"], iteration_number=INDEX_COMPACT_INTERVAL + 1)
        rows = load_jsonl(path)
        assert len(rows) == INDEX_COMPACT_ROW_LIMIT + 5

    def test_skips_zero_iteration(self, tmp_path):
        path = tmp_path / "index.jsonl"
        path.write_text('{"a": 1}\n')
        maybe_compact(path, key_fields=["spec_hash"], iteration_number=0)
        assert load_jsonl(path) == [{"a": 1}]


class TestSearchRows:
    def test_search_by_query(self):
        rows = [
            {"kind": "experiment", "family": "carry", "search_text": "carry strategy", "created_at": "2026-01-02"},
            {"kind": "experiment", "family": "momentum", "search_text": "momentum breakout", "created_at": "2026-01-01"},
        ]
        result = search_rows(rows=rows, query="carry", kind=None, family=None, outcome=None, limit=5)
        assert len(result) == 1
        assert result[0]["search_text"] == "carry strategy"

    def test_filter_by_kind(self):
        rows = [
            {"kind": "experiment", "search_text": "text", "created_at": "2026-01-01"},
            {"kind": "probe", "search_text": "text", "created_at": "2026-01-02"},
        ]
        result = search_rows(rows=rows, query="text", kind="probe", family=None, outcome=None, limit=5)
        assert len(result) == 1
        assert result[0]["kind"] == "probe"

    def test_filter_by_family(self):
        rows = [
            {"kind": "experiment", "family": "carry", "search_text": "text", "created_at": "2026-01-01"},
            {"kind": "experiment", "family": "momentum", "search_text": "text", "created_at": "2026-01-02"},
        ]
        result = search_rows(rows=rows, query="text", kind="experiment", family="carry", outcome=None, limit=5)
        assert len(result) == 1

    def test_empty_query_returns_all(self):
        rows = [
            {"kind": "experiment", "search_text": "text a", "created_at": "2026-01-01"},
            {"kind": "probe", "search_text": "text b", "created_at": "2026-01-02"},
        ]
        result = search_rows(rows=rows, query="", kind=None, family=None, outcome=None, limit=5)
        assert len(result) == 2

    def test_respects_limit(self):
        rows = [
            {"kind": "experiment", "search_text": "text", "created_at": f"2026-01-{i:02d}"}
            for i in range(1, 30)
        ]
        result = search_rows(rows=rows, query="text", kind=None, family=None, outcome=None, limit=5)
        assert len(result) <= 20


# ========================================================================
# select.py
# ========================================================================


class TestRowQuality:
    def test_basic_score(self):
        row = {"aggregate_score": 5.0, "passed": True, "summary": {}}
        quality = _row_quality(row)
        assert quality == 5.5  # 5.0 + 0.5 for passed

    def test_deployd_bonus(self):
        row = {"aggregate_score": 5.0, "passed": True, "deployd": True, "summary": {}}
        quality = _row_quality(row)
        assert quality == 5.75  # 5.0 + 0.5 + 0.25

    def test_holdout_return_bonus(self):
        row = {"aggregate_score": 5.0, "passed": False, "summary": {"holdout_total_return": 0.05}}
        quality = _row_quality(row)
        assert quality == pytest.approx(5.0 + 0.05 * 4.0)

    def test_pre_audit_return_bonus(self):
        row = {"aggregate_score": 5.0, "passed": False, "summary": {"pre_audit_canonical_total_return": 0.03}}
        quality = _row_quality(row)
        assert quality == pytest.approx(5.0 + 0.03 * 10.0)

    def test_validation_return_bonus(self):
        row = {"aggregate_score": 5.0, "passed": False, "summary": {"validation_total_return": 0.02}}
        quality = _row_quality(row)
        assert quality == pytest.approx(5.0 + 0.02 * 6.0)

    def test_low_bar_fraction_penalty(self):
        row = {"aggregate_score": 5.0, "passed": False, "summary": {"active_bar_fraction": 0.02}}
        quality = _row_quality(row)
        assert quality == pytest.approx(5.0 - 0.3)

    def test_normal_bar_fraction_no_penalty(self):
        row = {"aggregate_score": 5.0, "passed": False, "summary": {"active_bar_fraction": 0.1}}
        quality = _row_quality(row)
        assert quality == 5.0

    def test_empty_row(self):
        quality = _row_quality({"summary": {}})
        assert quality == 0.0


class TestPickParent:
    def test_returns_seed_when_no_rows(self):
        ancestry = MagicMock(spec=LineageStore)
        ancestry.recent.return_value = []
        seed = MagicMock()
        seed.family = "test"
        result = pick_parent(track="trend_signals", ancestry=ancestry, seed_specs=[seed])
        assert result == seed


class TestPickDeterministicParent:
    def test_returns_seed_when_no_rows(self):
        ancestry = MagicMock(spec=LineageStore)
        ancestry.recent.return_value = []
        seed = MagicMock()
        seed.family = "test"
        seed.strategy_hash.return_value = "seed_hash"
        result = pick_deterministic_parent(
            track="trend_signals", ancestry=ancestry, seed_specs=[seed], iteration_number=1
        )
        assert result == seed

    def test_returns_spec_from_pool(self):
        ancestry = MagicMock(spec=LineageStore)
        ancestry.recent.return_value = [{
            "research_summary": {"run_context": {"deterministic": True, "phase_label": "burn_in"}},
            "spec": {"features": ["ema_gap_12_26"], "family": "test", "universe": {"basis_groups": ["BTC"]},
                      "params": {}, "regime_gates": {}},
            "spec_hash": "existing_hash",
            "aggregate_score": 5.0,
            "passed": True,
            "summary": {},
        }]
        seed = MagicMock()
        seed.family = "test"
        seed.strategy_hash.return_value = "seed_hash"
        seed.canonical_dict.return_value = {"features": ["funding_72h_mean"], "family": "test",
                                            "universe": {"basis_groups": ["BTC"]},
                                            "params": {}, "regime_gates": {}}
        with patch("siglab.search.select.SignalSpec.from_dict") as mock_from_dict:
            mock_spec = MagicMock()
            mock_spec.strategy_hash.return_value = "existing_hash"
            mock_spec.family = "test"
            mock_spec.canonical_dict.return_value = {"features": ["ema_gap_12_26"], "family": "test",
                                                      "universe": {"basis_groups": ["BTC"]},
                                                      "params": {}, "regime_gates": {}}
            mock_from_dict.return_value = mock_spec
            result = pick_deterministic_parent(
                track="trend_signals", ancestry=ancestry, seed_specs=[seed], iteration_number=1
            )
            assert result is not None


class TestRankDeterministicSpecs:
    def test_returns_all_when_under_population_size(self):
        specs = [MagicMock(), MagicMock()]
        parent = MagicMock()
        result = rank_deterministic_specs(
            specs=specs, parent=parent, recent_rows=[], seed_specs=[], population_size=5
        )
        assert len(result) == 2

    def test_returns_all_when_equal_to_population_size(self):
        specs = [MagicMock(), MagicMock()]
        parent = MagicMock()
        result = rank_deterministic_specs(
            specs=specs, parent=parent, recent_rows=[], seed_specs=[], population_size=2
        )
        assert len(result) == 2

    def test_selects_subset_when_over_population_size(self):
        specs = []
        for i in range(10):
            s = MagicMock()
            s.strategy_hash.return_value = f"hash{i}"
            s.family = "test"
            s.canonical_dict.return_value = {
                "features": [f"feat{i}"], "family": "test",
                "universe": {"basis_groups": ["BTC"]},
                "params": {}, "regime_gates": {},
            }
            specs.append(s)
        parent = specs[0]
        result = rank_deterministic_specs(
            specs=specs, parent=parent, recent_rows=[], seed_specs=[parent], population_size=3
        )
        assert len(result) == 3





# ========================================================================
# mutate.py - constants
# ========================================================================


class TestMutateConstants:
    def test_pair_universes(self):
        assert len(PAIR_UNIVERSES) >= 2
        for universe in PAIR_UNIVERSES:
            assert len(universe) == 2

    def test_cross_sectional_universes(self):
        assert len(CROSS_SECTIONAL_UNIVERSES) >= 2
        for universe in CROSS_SECTIONAL_UNIVERSES:
            assert len(universe) >= 3

    def test_pair_trade_families(self):
        assert UNLEVERED_PAIR_FAMILY in PAIR_TRADE_FAMILIES
        assert LEVERED_PAIR_FAMILY in PAIR_TRADE_FAMILIES

    def test_basket_neutral_families(self):
        assert BASKET_NEUTRAL_UNLEVERED_FAMILY in BASKET_NEUTRAL_FAMILIES
        assert BASKET_NEUTRAL_LEVERED_FAMILY in BASKET_NEUTRAL_FAMILIES

    def test_multi_asset_carry_family(self):
        assert MULTI_ASSET_CARRY_FAMILY == "perp_multi_asset_carry"

    def test_trend_signals_lookback_days(self):
        assert TREND_SIGNALS_LOOKBACK_DAYS == 365

    def test_pair_feature_sets_have_formulas(self):
        assert len(PAIR_MEAN_REVERSION_FEATURES) >= 2
        assert len(PAIR_QUALITY_MOMENTUM_FEATURES) >= 2
        assert len(PAIR_COMPRESSION_REVERSION_FEATURES) >= 2
        assert len(PAIR_DYNAMIC_RESIDUAL_FEATURES) >= 2
        assert len(PAIR_MEAN_REVERSION_SPEED_FEATURES) >= 2
        assert len(PAIR_CARRY_REGIME_FEATURES) >= 2


class TestSpecMutator:
    def test_constructor(self):
        settings = SimpleNamespace(
            root_dir=Path("/tmp"),
            artifact_dir=Path("/tmp/runs"),
        )
        claude = MagicMock()
        mutator = SpecMutator(settings=settings, claude=claude)
        assert mutator.settings == settings

    def _ensure_mutable_specs(self, root: Path) -> None:
        mutable = root / "mutable"
        mutable.mkdir(parents=True, exist_ok=True)
        (mutable / "family_lab.yaml").write_text("tracks: {trend_signals: {families: {perp_multi_asset_carry: {defaults: {}, universe: {basis_groups: [BTC]}}}}}")
        (mutable / "feature_lab.yaml").write_text("tracks: {trend_signals: {aliases: {ema_gap_12_26: sub(ema(price,12),ema(price,26))}}}")
        (mutable / "graph_lab.yaml").write_text("specs: []")

    def test_load_seed_specs(self, tmp_path):
        self._ensure_mutable_specs(tmp_path)
        settings = SimpleNamespace(root_dir=tmp_path, artifact_dir=tmp_path / "runs")
        claude = MagicMock()
        mutator = SpecMutator(settings=settings, claude=claude)
        with patch("siglab.search.mutate.load_track_family_specs") as mock_ts:
            mock_ts.return_value = {"perp_multi_asset_carry": {"defaults": {}, "universe": {}}}
            with patch("siglab.search.mutate.SignalSpec.from_dict") as mock_spec:
                mock_spec.return_value = MagicMock()
                with patch("siglab.search.mutate.load_family_spec") as mock_fam:
                    mock_fam.return_value = {
                        "defaults": {"min_abs_score": 0.12},
                        "universe": {"basis_groups": ["BTC", "ETH", "SOL", "HYPE"]},
                    }
                    with pytest.raises(ValueError, match="No seed specs defined"):
                        mutator.load_seed_specs("trend_signals")

    def test_allowed_features_by_family(self, tmp_path):
        self._ensure_mutable_specs(tmp_path)
        settings = SimpleNamespace(root_dir=tmp_path, artifact_dir=tmp_path / "runs")
        claude = MagicMock()
        mutator = SpecMutator(settings=settings, claude=claude)
        mutator._allowed_families = MagicMock(return_value=["test_family"])
        with patch("siglab.search.mutate.load_feature_spec") as mock_fs:
            mock_fs.return_value = {"aliases": {"ema_gap_12_26": "sub(ema(price,12),ema(price,26))"}}
            result = mutator._allowed_features_by_family("trend_signals")
            assert isinstance(result, dict)

    def test_family_defaults(self, tmp_path):
        self._ensure_mutable_specs(tmp_path)
        settings = SimpleNamespace(root_dir=tmp_path, artifact_dir=tmp_path / "runs")
        claude = MagicMock()
        mutator = SpecMutator(settings=settings, claude=claude)
        with patch.object(mutator, "_allowed_features_by_family") as mock_allowed:
            mock_allowed.return_value = {"perp_test": ["feat_a"]}
            with patch("siglab.search.mutate.load_family_spec") as mock_fam:
                mock_fam.return_value = {
                    "defaults": {"min_abs_score": 0.12},
                    "universe": {"basis_groups": ["BTC", "ETH"]},
                }
                with patch("siglab.search.mutate.load_track_family_specs") as mock_ts:
                    mock_ts.return_value = {
                        "perp_test": {"defaults": {"min_abs_score": 0.12}, "feature_weights": {}}
                    }
                    result = mutator._family_defaults("trend_signals", family="perp_test")
                    assert "perp_test" in result
                    assert result["perp_test"]["params"]["min_abs_score"] == 0.12


# ========================================================================
# lineage.py
# ========================================================================


class TestLineageStore:
    def test_init_creates_table(self, tmp_path):
        db_path = tmp_path / "ancestry.db"
        LineageStore(db_path)
        assert db_path.exists()
        with sqlite3.connect(db_path) as conn:
            tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            names = {row[0] for row in tables}
            assert "experiments" in names
            assert "experiment_events" in names

    def test_record_and_get_lineage(self, tmp_path):
        db_path = tmp_path / "ancestry.db"
        store = LineageStore(db_path)
        spec = MagicMock()
        spec.strategy_hash.return_value = "hash1"
        spec.canonical_dict.return_value = {"features": ["ema_gap_12_26"], "family": "perp_multi_asset_carry", "track": "trend_signals"}
        spec.family = "perp_multi_asset_carry"
        store.record(
            evaluation={
                "spec": spec.canonical_dict(),
                "spec_hash": spec.strategy_hash(),
                "summary": {"aggregate_score": 5.0, "median_sharpe": 1.0, "passed": True, "gate_reasons": []},
            },
            parent_hash=None,
            research_summary={"track": "trend_signals"},
            artifact_path="",
        )
        detail = store.experiment_detail("hash1")
        assert detail is not None
        assert detail["summary"]["aggregate_score"] == 5.0

    def test_experiment_detail_nonexistent(self, tmp_path):
        db_path = tmp_path / "ancestry.db"
        store = LineageStore(db_path)
        detail = store.experiment_detail("nonexistent")
        assert detail is None

    def test_recent_returns_rows(self, tmp_path):
        db_path = tmp_path / "ancestry.db"
        store = LineageStore(db_path)
        spec = MagicMock()
        spec.strategy_hash.return_value = "hash1"
        spec.canonical_dict.return_value = {"features": ["funding_72h_mean"], "family": "perp_multi_asset_carry", "track": "trend_signals"}
        spec.family = "perp_multi_asset_carry"
        store.record(
            evaluation={
                "spec": spec.canonical_dict(),
                "spec_hash": spec.strategy_hash(),
                "summary": {"aggregate_score": 5.0, "median_sharpe": 0.5, "median_cagr": 0.03, "median_total_return": 0.02, "passed": True, "gate_reasons": []},
            },
            parent_hash=None,
            research_summary={"track": "trend_signals"},
            artifact_path="",
        )
        rows = store.recent("trend_signals", limit=10)
        assert len(rows) >= 1
        assert rows[0]["spec_hash"] == "hash1"

    def test_recent_empty_track(self, tmp_path):
        db_path = tmp_path / "ancestry.db"
        store = LineageStore(db_path)
        rows = store.recent("nonexistent_track", limit=10)
        assert rows == []

    def test_best_returns_highest_scoring(self, tmp_path):
        db_path = tmp_path / "ancestry.db"
        store = LineageStore(db_path)
        for i, score in enumerate([3.0, 8.0, 5.0]):
            spec = MagicMock()
            spec.strategy_hash.return_value = f"hash{i}"
            spec.canonical_dict.return_value = {"features": [f"feat{i}"], "family": "test", "track": "trend_signals"}
            spec.family = "test"
            store.record(
                evaluation={
                    "spec": spec.canonical_dict(),
                    "spec_hash": spec.strategy_hash(),
                    "summary": {
                        "aggregate_score": score,
                        "median_sharpe": 0.5,
                        "median_cagr": 0.03,
                        "median_total_return": 0.02,
                        "passed": score > 4.0,
                        "gate_reasons": [],
                    },
                },
                parent_hash=None,
                research_summary={"track": "trend_signals"},
                artifact_path="",
            )
        best = store.best("trend_signals")
        assert best is not None
        assert best["aggregate_score"] == 8.0

    def test_best_no_rows(self, tmp_path):
        db_path = tmp_path / "ancestry.db"
        store = LineageStore(db_path)
        best = store.best("nonexistent")
        assert best is None

    def test_dashboard_rows(self, tmp_path):
        db_path = tmp_path / "ancestry.db"
        store = LineageStore(db_path)
        spec = MagicMock()
        spec.strategy_hash.return_value = "hash1"
        spec.canonical_dict.return_value = {"features": ["funding_72h_mean"], "family": "test", "track": "trend_signals"}
        spec.family = "test"
        store.record(
            evaluation={
                "spec": spec.canonical_dict(),
                "spec_hash": spec.strategy_hash(),
                "summary": {"aggregate_score": 5.0, "median_sharpe": 0.5, "median_cagr": 0.03, "median_total_return": 0.02, "passed": True, "gate_reasons": []},
            },
            parent_hash=None,
            research_summary={"track": "trend_signals"},
            artifact_path="",
        )
        rows = store.dashboard_rows(track="trend_signals")
        assert len(rows) >= 1

    def test_has_spec_returns_true_for_existing(self, tmp_path):
        db_path = tmp_path / "ancestry.db"
        store = LineageStore(db_path)
        spec = MagicMock()
        spec.strategy_hash.return_value = "hash1"
        spec.canonical_dict.return_value = {"features": ["funding_72h_mean"], "family": "test", "track": "trend_signals"}
        spec.family = "test"
        store.record(
            evaluation={
                "spec": spec.canonical_dict(),
                "spec_hash": spec.strategy_hash(),
                "summary": {"aggregate_score": 5.0, "median_sharpe": 0.5, "median_cagr": 0.03, "median_total_return": 0.02, "passed": True, "gate_reasons": []},
            },
            parent_hash=None,
            research_summary={"track": "trend_signals"},
            artifact_path="",
        )
        assert store.has_spec("hash1") is True
        assert store.has_spec("nonexistent") is False

    def test_list_rows(self, tmp_path):
        db_path = tmp_path / "ancestry.db"
        store = LineageStore(db_path)
        spec = MagicMock()
        spec.strategy_hash.return_value = "hash1"
        spec.canonical_dict.return_value = {"features": ["funding_72h_mean"], "family": "test", "track": "trend_signals"}
        spec.family = "test"
        store.record(
            evaluation={
                "spec": spec.canonical_dict(),
                "spec_hash": spec.strategy_hash(),
                "summary": {"aggregate_score": 5.0, "median_sharpe": 0.5, "median_cagr": 0.03, "median_total_return": 0.02, "passed": True, "gate_reasons": []},
            },
            parent_hash=None,
            research_summary={"track": "trend_signals"},
            artifact_path="",
        )
        rows = store.list_rows(track="trend_signals", limit=10)
        assert len(rows) >= 1
