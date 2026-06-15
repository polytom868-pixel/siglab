"""
Shared test fixtures, fakes, and path resolution for all SigLab tests.

Provides:
- REPO_ROOT: absolute path to the repository root
- sample_spec: a minimal deterministic SignalSpec fixture
- mock_settings: a SiglabConfig-style mock pointing to REPO_ROOT
- deterministic_mock_provider: a MarketDataProvider mock returning canned data
"""

from __future__ import annotations
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest
from siglab.schemas import AssetUniverse, RiskBounds, SignalSpec
from siglab.tui import api_client as _tui_api_client_module

# ---------------------------------------------------------------------------
# Repository root – single source of truth for all test files
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Sample spec fixture
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _seed_global_random() -> None:
    import random
    random.seed(0)

@pytest.fixture(scope="module")
def event_loop():
    import asyncio
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()

@pytest.fixture
def sample_spec() -> SignalSpec:
    """A minimal, deterministic SignalSpec for use in golden-file tests."""
    return SignalSpec(
        track="trend_signals",
        family="perp_multi_asset_decision",
        hypothesis="Momentum + carry on top perp assets",
        neutrality_basis="USD",
        features=["price_return_24h", "price_return_72h", "ema_gap_12_26", "funding_72h_mean"],
        universe=AssetUniverse(max_symbols=2, lookback_days=21, interval="1h"),
        risk=RiskBounds(max_leverage=1.0),
    )


@pytest.fixture
def sample_spec_minimal() -> SignalSpec:
    """Even simpler spec – useful for path-level tests."""
    return SignalSpec(
        track="trend_signals",
        family="perp_multi_asset_decision",
        hypothesis="Quick test",
        neutrality_basis=None,
        features=["price_return_24h"],
    )


# ---------------------------------------------------------------------------
# Mock settings fixture
# ---------------------------------------------------------------------------
@pytest.fixture
def mock_settings() -> MagicMock:
    """SiglabConfig mock with root_dir pointing to the real repository root."""
    settings = MagicMock()
    settings.root_dir = REPO_ROOT
    settings.sosovalue_config_path = str(REPO_ROOT / "tests" / "_data" / "soso.json")
    settings.generated_strategy_dir = str(REPO_ROOT / "tests" / "_data" / "strategies")
    settings.data_lake_dir = str(REPO_ROOT / "tests" / "_data" / "lake")
    settings.artifact_dir = str(REPO_ROOT / "tests" / "_data" / "artifacts")
    settings.live_dir = str(REPO_ROOT / "tests" / "_data" / "live")
    settings.ancestry_db_path = str(REPO_ROOT / "tests" / "_data" / "ancestry.db")
    settings.sosovalue_api_key_override = None
    return settings


# ---------------------------------------------------------------------------
# Deterministic mock MarketDataProvider
# ---------------------------------------------------------------------------
def _candledates(n: int = 200) -> pd.DatetimeIndex:
    """Create a deterministic DatetimeIndex with hourly candles."""
    return pd.date_range(end="2026-06-01 00:00", periods=n, freq="h")


def _price_series(
    base: float, volatility: float, n: int = 200, seed: int = 42
) -> np.ndarray:
    """Deterministic price series based on seeded random walk."""
    rng = np.random.default_rng(seed)
    returns = rng.normal(0, volatility, n)
    prices = base * np.cumprod(1 + returns)
    return prices


class DeterministicMockProvider:
    """
    A MarketDataProvider stand-in that returns canned deterministic data.

    Only the methods used by ``compile_spec`` are implemented; all others
    raise ``NotImplementedError`` so tests fail explicilty if an unexpected
    code path is exercised.
    """

    def __init__(self) -> None:
        self._call_count: dict[str, int] = {}

    # ---- async methods used by compile_spec ---------------------------------

    async def discover_perp_symbols(
        self,
        symbols: list[str],
        *,
        limit: int = 10,
    ) -> list[str]:
        self._call_count["discover_perp_symbols"] = (
            self._call_count.get("discover_perp_symbols", 0) + 1
        )
        # Return deterministic top symbols
        return ["BTC", "ETH"]

    async def fetch_perp_bundle(
        self,
        *,
        symbols: list[str],
        lookback_days: int,
        interval: str,
    ) -> dict[str, Any]:
        self._call_count["fetch_perp_bundle"] = (
            self._call_count.get("fetch_perp_bundle", 0) + 1
        )
        n = max(100, lookback_days * 24)
        dates = _candledates(n)
        btc_prices = _price_series(50000.0, 0.005, n, seed=42)
        eth_prices = _price_series(3000.0, 0.006, n, seed=99)
        prices = pd.DataFrame({"BTC": btc_prices, "ETH": eth_prices}, index=dates)
        funding = pd.DataFrame(
            {"BTC": np.full(n, 0.0001), "ETH": np.full(n, 0.00015)},
            index=dates,
        )
        return {
            "prices": prices,
            "funding": funding,
            "open_interest": None,
            "source": "golden_test_deterministic",
            "bundle_as_of": "2026-06-01T00:00:00",
        }

    # ---- non-async stubs ----------------------------------------------------
    def begin_iteration_bundle(self, **kwargs: Any) -> dict[str, Any]:
        return {"bundle_id": "golden", "track": "trend_signals", "components": []}

    def current_bundle_context(self) -> dict[str, Any] | None:
        return None

    def clear_iteration_bundle(self) -> None:
        pass

    async def build_research_summary(
        self,
        track: str,
        parent: SignalSpec,
    ) -> dict[str, Any]:
        return {"track": track}

    async def close(self) -> None:
        pass

    def __getattr__(self, name: str) -> Any:
        # Anything else called unexpectedly — raise immediately
        raise NotImplementedError(
            f"DeterministicMockProvider.{name} is not implemented"
        )


@pytest.fixture
def deterministic_provider() -> DeterministicMockProvider:
    """Fixture returning a DeterministicMockProvider (not wrapped as MagicMock)."""
    return DeterministicMockProvider()


# ---------------------------------------------------------------------------
# Helpers for golden-file hashing
# ---------------------------------------------------------------------------
def compute_evaluation_hash(result: dict[str, Any]) -> str:
    """
    Compute a deterministic hash of an evaluate() result dict.

    Only the ``spec_hash`` and ``summary`` fields are hashed, because those
    are what the VAL-EVAL-004 contract targets.
    """
    import hashlib
    import json

    payload = {
        "spec_hash": result.get("spec_hash", ""),
        "summary": _make_json_safe(result.get("summary", {})),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _make_json_safe(obj: Any) -> Any:
    """Recursively convert a dict to a JSON-safe (and deterministically ordered) form."""
    if isinstance(obj, dict):
        return {str(k): _make_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_json_safe(item) for item in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        val = float(obj)
        return None if np.isnan(val) or np.isinf(val) else val
    if isinstance(obj, float):
        return None if np.isnan(obj) or np.isinf(obj) else obj
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return obj


# ---------------------------------------------------------------------------
# Fast-TUI API stub (pilot test speedup)
# ---------------------------------------------------------------------------
_FAST_TUI_API_FILES = frozenset(
    {
        "test_tui_validation_contract.py",
        "test_tui_foundation.py",
        "test_tui_market.py",
        "test_tui_paper_trading.py",
        "test_tui_risk_screen.py",
        "test_tui_strategy.py",
        "test_tui_evidence.py",
        "test_tui_telemetry.py",
    }
)


@pytest.fixture(autouse=True)
def _fast_tui_api(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Stub TuiApiClient HTTP retry for pilot-bound TUI tests.

    Pilot tests in the listed files spin up the full Textual app; the
    screen ``on_mount`` fires 3 HTTP calls that retry-then-fail with a
    half-second sleep, costing about 2.5s per pilot test. This fixture
    patches :meth:`TuiApiClient._request_with_retry` with a no-op that
    returns an empty dict, cutting the pilot suite by roughly 25s.
    Opted out for ``TestApiClientMarketMethods`` (real retry path),
    ``test_tui_api_client.py`` (real retry path), and
    ``test_tui_tmux_hardening.py`` (opt-in tmux harness).
    """
    nodeid = request.node.nodeid
    if "/test_tui_api_client.py" in nodeid or "/test_tui_tmux_hardening.py" in nodeid:
        yield
        return
    if "TestApiClientMarketMethods" in nodeid:
        yield
        return
    if not any(name in nodeid for name in _FAST_TUI_API_FILES):
        yield
        return

    async def _stub_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(
        _tui_api_client_module.TuiApiClient,
        "_request_with_retry",
        _stub_request,
    )
    yield
