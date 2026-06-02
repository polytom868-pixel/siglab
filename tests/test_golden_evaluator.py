"""
Golden-file regression tests for the evaluator.

Verifies numerical reproducibility: same spec + same data → same hash → same summary.

Assertions fulfilled:
- VAL-EVAL-004: Golden-file regression test passes (byte-identical hash across runs)
- VAL-EVAL-008: Backward compat after refactoring (same spec → same results)
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from siglab.evaluator.core import ResearchEvaluator
from siglab.schemas import SignalSpec

from conftest import REPO_ROOT, DeterministicMockProvider, compute_evaluation_hash

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
GOLDEN_HASH_PATH = GOLDEN_DIR / "evaluator_golden.txt"


def _read_golden_hash() -> str | None:
    """Read the stored golden hash, or return None if absent."""
    if GOLDEN_HASH_PATH.exists():
        return GOLDEN_HASH_PATH.read_text(encoding="utf-8").strip()
    return None


def _write_golden_hash(h: str) -> None:
    """Persist a new golden hash (record mode)."""
    GOLDEN_HASH_PATH.write_text(h + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSpecHashDeterminism:
    """
    Same spec → same strategy_hash (trivially deterministic).
    """

    def test_same_instance_returns_same_hash(self, sample_spec: SignalSpec) -> None:
        h1 = sample_spec.strategy_hash()
        h2 = sample_spec.strategy_hash()
        assert h1 == h2
        assert len(h1) == 16  # hexdigest[:16]

    def test_identical_specs_return_same_hash(self) -> None:
        spec_a = SignalSpec(
            track="trend_signals",
            family="perp_multi_asset_decision",
            hypothesis="test",
            neutrality_basis=None,
            features=["price_return_24h"],
        )
        spec_b = SignalSpec(
            track="trend_signals",
            family="perp_multi_asset_decision",
            hypothesis="test",
            neutrality_basis=None,
            features=["price_return_24h"],
        )
        assert spec_a.strategy_hash() == spec_b.strategy_hash()

    def test_different_specs_different_hash(self) -> None:
        spec_a = SignalSpec(
            track="trend_signals",
            family="perp_multi_asset_decision",
            hypothesis="hyp A",
            neutrality_basis=None,
            features=["price_return_24h"],
        )
        spec_b = SignalSpec(
            track="trend_signals",
            family="perp_multi_asset_decision",
            hypothesis="hyp B",
            neutrality_basis=None,
            features=["price_return_24h"],
        )
        assert spec_a.strategy_hash() != spec_b.strategy_hash()


class TestEvaluationReproducibility:
    """
    Same spec + same deterministic data → byte-identical evaluation hash.
    """

    @pytest.mark.asyncio
    async def test_evaluate_returns_expected_keys(
        self,
        sample_spec: SignalSpec,
        mock_settings: pytest.MagicMock,  # type: ignore[type-arg]
    ) -> None:
        provider = DeterministicMockProvider()
        evaluator = ResearchEvaluator(settings=mock_settings, provider=provider)
        result = await evaluator.evaluate(sample_spec)
        assert "spec_hash" in result
        assert "summary" in result
        assert "spec" in result
        assert result["spec_hash"] == sample_spec.strategy_hash()
        assert isinstance(result["summary"], dict)

    @pytest.mark.asyncio
    async def test_first_and_second_run_byte_identical(
        self,
        sample_spec: SignalSpec,
        mock_settings: pytest.MagicMock,  # type: ignore[type-arg]
    ) -> None:
        provider = DeterministicMockProvider()
        evaluator = ResearchEvaluator(settings=mock_settings, provider=provider)
        result_a = await evaluator.evaluate(sample_spec)
        # Fresh evaluator with new provider for identical starting conditions
        provider_b = DeterministicMockProvider()
        evaluator_b = ResearchEvaluator(settings=mock_settings, provider=provider_b)
        result_b = await evaluator_b.evaluate(sample_spec)
        # spec_hash must match
        assert result_a["spec_hash"] == result_b["spec_hash"]
        # Full evaluation hash must match
        ha = compute_evaluation_hash(result_a)
        hb = compute_evaluation_hash(result_b)
        assert ha == hb, (
            f"Evaluation hash differs between runs: {ha} != {hb}\n"
            "This indicates non-determinism in the evaluator pipeline."
        )

    @pytest.mark.asyncio
    async def test_different_specs_different_evaluation_hash(
        self,
        sample_spec: SignalSpec,
        sample_spec_minimal: SignalSpec,
        mock_settings: pytest.MagicMock,  # type: ignore[type-arg]
    ) -> None:
        provider_a = DeterministicMockProvider()
        evaluator_a = ResearchEvaluator(settings=mock_settings, provider=provider_a)
        result_a = await evaluator_a.evaluate(sample_spec)
        provider_b = DeterministicMockProvider()
        evaluator_b = ResearchEvaluator(settings=mock_settings, provider=provider_b)
        result_b = await evaluator_b.evaluate(sample_spec_minimal)
        assert compute_evaluation_hash(result_a) != compute_evaluation_hash(
            result_b
        ), "Different specs should produce different evaluation hashes"


class TestGoldenFile:
    """
    Golden file hash comparison.

    If the golden file does not exist (first run, or after intentional changes),
    the test generates and stores it. Otherwise it verifies the current hash
    matches the stored value.
    """

    @pytest.mark.asyncio
    async def test_evaluator_golden_hash(
        self,
        sample_spec: SignalSpec,
        mock_settings: pytest.MagicMock,  # type: ignore[type-arg]
    ) -> None:
        provider = DeterministicMockProvider()
        evaluator = ResearchEvaluator(settings=mock_settings, provider=provider)
        result = await evaluator.evaluate(sample_spec)
        current_hash = compute_evaluation_hash(result)

        stored_hash = _read_golden_hash()
        if stored_hash is None:
            # Record mode — first run, persist the golden hash
            _write_golden_hash(current_hash)
            pytest.skip(
                f"Golden hash did not exist — recorded new hash: {current_hash}"
            )
        else:
            assert (
                current_hash == stored_hash
            ), (
                f"Golden hash mismatch!\n"
                f"  Current: {current_hash}\n"
                f"  Stored:  {stored_hash}\n"
                "This means the evaluator output has changed. "
                "If the change is intentional, delete the golden file and re-run to update it."
            )


class TestSpecCanonicalDict:
    """
    The canonical_dict / strategy_hash round-trip is stable.
    """

    def test_canonical_dict_round_trip(self, sample_spec: SignalSpec) -> None:
        canonical = sample_spec.canonical_dict()
        restored = SignalSpec.from_dict(canonical)
        assert sample_spec.strategy_hash() == restored.strategy_hash()

    def test_canonical_dict_feature_sorting(self) -> None:
        """Features must be sorted in canonical form so same set == same hash."""
        spec_a = SignalSpec(
            track="trend_signals",
            family="perp_multi_asset_decision",
            hypothesis="test",
            neutrality_basis=None,
            features=["z_last", "a_first", "m_mid"],
        )
        spec_b = SignalSpec(
            track="trend_signals",
            family="perp_multi_asset_decision",
            hypothesis="test",
            neutrality_basis=None,
            features=["a_first", "m_mid", "z_last"],
        )
        assert spec_a.strategy_hash() == spec_b.strategy_hash()
