"""Market data provider for SigLab research pipelines.

Centralises access to SoSoValue market data, SoDEX perp klines/funding,
Pendle PT markets, and Delta Lab lending markets.  All live HTTP/WS calls
are made through the ``SoSoValueClient`` and ``SoDEXFeeds`` adapters;
``MarketDataProvider`` adds caching (warm + per-bundle), bundle manifests,
and a ``metrics_snapshot`` / ``close`` lifecycle for observability.
"""

from __future__ import annotations

import asyncio
import atexit
import copy
import json
import logging
import math
import re
from hashlib import sha256
from datetime import UTC, datetime
from typing import Any, TYPE_CHECKING

import pandas as pd

from siglab.data.store import ParquetLake
from siglab.data.sosovalue_client import SoSoValueClient, SoSoValueEndpoints
from siglab.schemas import SignalSpec, AssetUniverse
from siglab.config import SiglabConfig
from siglab.track_registry import canonical_track_name

if TYPE_CHECKING:
    from siglab.data.sodex_feeds import SoDEXFeeds

logger = logging.getLogger(__name__)

MAJOR_PERP_SYMBOLS = ["BTC", "ETH", "SOL", "HYPE", "DOGE", "BNB", "XRP", "SUI"]
CHAIN_NAME_TO_ID = {
    "ethereum": 1,
    "arbitrum": 42161,
    "base": 8453,
    "plasma": 9745,
    "hyperevm": 999,
    "unichain": 130,
}
STABLE_PT_PATTERN = re.compile(
    r"(?:^|[^A-Za-z])(usd|usdc|usdt|usde|usds|dai|fdusd|usdai|susde|upusdc|yoUSD)",
    re.IGNORECASE,
)


def _frame_column_or_default(
    frame: pd.DataFrame,
    column: str,
    *,
    default: float = 0.0,
) -> pd.Series:
    """Return *column* from *frame* as a numeric Series, or a constant default.

    Coerces the column to numeric (NaN on failure) and fills remaining
    NaNs with *default*.  If the column is missing entirely, returns a
    Series of *default* values aligned to *frame*'s index.
    """
    if column in frame.columns:
        return pd.to_numeric(frame[column], errors="coerce").fillna(default)
    return pd.Series(default, index=frame.index, dtype=float)


def _sanitize_perp_symbols(symbols: list[str]) -> list[str]:
    """Normalise, deduplicate, and filter perp symbol strings.

    Strips whitespace, uppercases, removes blanks and the literal ``"USD"``,
    and preserves first-occurrence order.
    """
    cleaned: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        normalized = str(symbol or "").strip().upper()
        if not normalized or normalized == "USD" or normalized in seen:
            continue
        cleaned.append(normalized)
        seen.add(normalized)
    return cleaned


def _dedupe_time_index(frame: pd.DataFrame) -> pd.DataFrame:
    """Deduplicate rows sharing the same index value, keeping the last.

    Also sorts the resulting frame by index.
    """
    if frame.empty:
        return frame
    return frame.groupby(level=0).last().sort_index()


def _safe_float(value: Any, digits: int = 8) -> float | None:
    """Convert *value* to a finite float rounded to *digits* decimal places.

    Returns ``None`` on ``TypeError``, ``ValueError``, or non-finite
    results (NaN, ±Inf).
    """
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return round(numeric, digits)


def _percentile_map(series: pd.Series, percentiles: list[float]) -> dict[str, float | None]:
    """Compute percentile values for *series*, returning a ``{pN: value}`` map.

    Non-finite values are dropped before calculation.  Returns ``None``
    for each percentile if the cleaned series is empty.
    """
    clean = pd.to_numeric(series, errors="coerce").replace([float("inf"), float("-inf")], pd.NA).dropna()
    if clean.empty:
        return {f"p{int(percentile)}": None for percentile in percentiles}
    return {
        f"p{int(percentile)}": _safe_float(clean.quantile(percentile / 100.0))
        for percentile in percentiles
    }


def _pair_calibration_snapshot(
    *,
    prices: pd.DataFrame,
    funding: pd.DataFrame,
    symbols: list[str],
) -> dict[str, Any]:
    """Compute pair-trading calibration metrics for the first two *symbols*.

    Returns a dict containing funding-spread percentiles, 72 h rolling
    pair volatility/correlation, 24 h return spread, residual z-score,
    and observed directional fractions.  Returns ``{}`` when fewer than
    two symbols are available or prices are missing.
    """
    if len(symbols) < 2:
        return {}
    asset_1_symbol, asset_2_symbol = symbols[:2]
    if asset_1_symbol not in prices.columns or asset_2_symbol not in prices.columns:
        return {}

    asset_1_price = pd.to_numeric(prices[asset_1_symbol], errors="coerce")
    asset_2_price = pd.to_numeric(prices[asset_2_symbol], errors="coerce")
    asset_1_funding = (
        pd.to_numeric(
            funding[asset_1_symbol]
            if asset_1_symbol in funding.columns
            else pd.Series(0.0, index=prices.index, dtype=float),
            errors="coerce",
        )
        .reindex(prices.index)
        .fillna(0.0)
    )
    asset_2_funding = (
        pd.to_numeric(
            funding[asset_2_symbol]
            if asset_2_symbol in funding.columns
            else pd.Series(0.0, index=prices.index, dtype=float),
            errors="coerce",
        )
        .reindex(prices.index)
        .fillna(0.0)
    )
    pair_ratio = asset_1_price.div(asset_2_price.replace(0.0, pd.NA))
    funding_spread = asset_1_funding.sub(asset_2_funding, fill_value=0.0)
    pair_volatility_72h = pair_ratio.pct_change().rolling(72).std()
    pair_correlation_72h = asset_1_price.pct_change().rolling(72).corr(asset_2_price.pct_change())
    return_spread_24h = asset_1_price.pct_change(24).sub(asset_2_price.pct_change(24))
    residual_z_60 = (
        pair_ratio.sub(pair_ratio.rolling(60).mean())
        .div(pair_ratio.rolling(60).std().replace(0.0, pd.NA))
    )

    return {
        "pair": [asset_1_symbol, asset_2_symbol],
        "sample_bars": int(prices.index.shape[0]),
        "funding_spread_percentiles": _percentile_map(
            funding_spread,
            [5, 25, 50, 75, 95],
        ),
        "pair_volatility_72h_percentiles": _percentile_map(
            pair_volatility_72h,
            [25, 50, 75, 95],
        ),
        "pair_correlation_72h_percentiles": _percentile_map(
            pair_correlation_72h,
            [10, 25, 50, 75, 90],
        ),
        "return_spread_24h_percentiles": _percentile_map(
            return_spread_24h,
            [5, 25, 50, 75, 95],
        ),
        "residual_z_60_percentiles": _percentile_map(
            residual_z_60,
            [10, 25, 50, 75, 90],
        ),
        "observed_fractions": {
            "funding_spread_positive_fraction": _safe_float((funding_spread > 0.0).mean()),
            "funding_spread_negative_fraction": _safe_float((funding_spread < 0.0).mean()),
            "pair_correlation_non_negative_fraction": _safe_float(
                (pair_correlation_72h >= 0.0).mean()
            ),
        },
    }


def _align_perp_bundle_frames(
    prices: pd.DataFrame,
    funding: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Deduplicate and align perp price and funding frames to a common index.

    Drops rows with any NaN prices, then forward-fills and zero-fills
    the funding frame to match.  Raises ``ValueError`` if no non-null
    price rows remain after alignment.
    """
    prices = _dedupe_time_index(prices)
    funding = _dedupe_time_index(funding)
    prices = prices.dropna(how="any")
    if prices.empty:
        raise ValueError("Perp bundle has no common non-null price coverage across requested symbols")
    funding = funding.reindex(prices.index).ffill().fillna(0.0).astype(float)
    return prices, funding


def _interval_to_hours(interval: str) -> float:
    """Convert a kline interval string to hours.

    Supports minute (``m``), hour (``h``), day (``d``), week (``w``),
    and month (``M``) suffixes.
    """
    interval = interval.strip().lower()
    if interval.endswith("m"):
        return float(interval[:-1]) / 60.0
    elif interval.endswith("h"):
        return float(interval[:-1])
    elif interval.endswith("d"):
        return float(interval[:-1]) * 24.0
    elif interval.endswith("w"):
        return float(interval[:-1]) * 168.0
    elif interval.endswith("M"):
        return float(interval[:-1]) * 720.0
    return 1.0


class MarketDataProvider:
    def __init__(
        self,
        settings: SiglabConfig,
        lake: ParquetLake,
        *,
        config_path: str | None = None,
        sodex_feeds: SoDEXFeeds | None = None,
    ) -> None:
        """Initialise the market data provider.

        Sets up the ``SoSoValueClient`` from *settings*, stores the
        ``ParquetLake`` reference, and prepares the internal warm/bundle
        caches.  ``sodex_feeds`` may be provided up-front or lazily
        assigned when SoDEX data is first requested.
        """
        self.settings = settings
        self.lake = lake
        self.sosovalue = SoSoValueClient(
            api_key=settings.sosovalue_api_key_override,
            endpoints=SoSoValueEndpoints(
                openapi_base_url=settings.sosovalue_openapi_base_url,
                etf_base_url=settings.sosovalue_etf_base_url,
                news_base_url=settings.sosovalue_news_base_url,
            ),
            timeout_s=settings.sosovalue_timeout_s,
            retries=settings.sosovalue_retries,
        )
        self.sodex_feeds = sodex_feeds  # May be set later via lazy import
        self._active_bundle_id: str | None = None
        self._active_as_of: datetime | None = None
        self._bundle_cache: dict[str, Any] = {}
        self._warm_cache: dict[str, Any] = {}
        self._bundle_components: list[dict[str, Any]] = []
        self._bundle_manifest: dict[str, Any] = {}
        self._atexit_handler = atexit.register(self._close_sync)

    def _close_sync(self) -> None:
        """Synchronous atexit hook to release HTTP client resources."""
        atexit.unregister(self._close_sync)
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                return
            loop.run_until_complete(self.close())
        except (RuntimeError, OSError):
            pass

    def metrics_snapshot(self) -> dict[str, Any]:
        """Aggregate provider-level metrics from all data clients."""
        snap: dict[str, Any] = {
            "sosovalue": self.sosovalue.metrics_snapshot(),
        }
        if self.sodex_feeds is not None:
            snap["sodex"] = self.sodex_feeds.metrics_snapshot()
        return snap

    async def close(self) -> None:
        """Shut down the provider and log final metrics.

        Emits a ``data_pipeline_metrics`` log line containing the full
        ``metrics_snapshot`` (SoSoValue request counts, latencies, error
        rates, and SoDEX metrics if present), then closes the underlying
        HTTP clients to release connection pools.
        """
        atexit.unregister(self._close_sync)
        logger.info("data_pipeline_metrics %s", json.dumps(self.metrics_snapshot(), default=str))
        await self.sosovalue.close()
        if self.sodex_feeds is not None:
            await self.sodex_feeds.close()

    def begin_iteration_bundle(
        self,
        *,
        track: str,
        parent: SignalSpec,
    ) -> dict[str, Any]:
        """Start a new iteration bundle and reset caches.

        Computes a deterministic ``bundle_id`` from the track name,
        parent strategy hash, and timestamp, then clears the warm and
        per-bundle caches so all subsequent fetches are fresh.

        HTTP: none (local state only)
        """
        as_of = datetime.now(UTC).replace(microsecond=0)
        payload = jsonable_iteration_payload(
            track=canonical_track_name(track) or track,
            parent_hash=parent.strategy_hash(),
            as_of=as_of,
        )
        bundle_id = sha256(payload.encode("utf-8")).hexdigest()[:16]
        self._active_bundle_id = bundle_id
        self._active_as_of = as_of
        self._bundle_cache = {}
        self._bundle_components = []
        metadata = {
            "bundle_id": bundle_id,
            "as_of": as_of.isoformat(),
            "track": canonical_track_name(track) or track,
            "parent_hash": parent.strategy_hash(),
            "components": [],
        }
        self._bundle_manifest = dict(metadata)
        self._write_bundle_manifest(metadata)
        return metadata

    def current_bundle_context(self) -> dict[str, Any] | None:
        """Return the active bundle ID and as-of timestamp, or ``None``.

        HTTP: none (local state only)
        """
        if self._active_bundle_id is None or self._active_as_of is None:
            return None
        return {
            "bundle_id": self._active_bundle_id,
            "as_of": self._active_as_of.isoformat(),
        }

    def clear_iteration_bundle(self) -> None:
        """Tear down the active bundle and release all caches.

        HTTP: none (local state only)
        """
        self._active_bundle_id = None
        self._active_as_of = None
        self._bundle_cache = {}
        self._bundle_components = []
        self._bundle_manifest = {}

    async def build_research_summary(
        self,
        track: str,
        parent: SignalSpec,
    ) -> dict[str, Any]:
        """Build a full research summary for a track/parent pair.

        Fetches perp bundle data and, for ``yield_flows`` tracks, also
        discovers stable-PT, rotation-PT, and lending markets.

        HTTP: multiple calls via ``discover_perp_symbols``,
        ``fetch_perp_bundle``, ``discover_stable_pt_markets``,
        ``discover_pt_markets``, and ``discover_lending_markets``.
        """
        track = canonical_track_name(track) or track
        summary: dict[str, Any] = {
            "track": track,
            "parent_family": parent.family,
            "parent_hash": parent.strategy_hash(),
        }
        if bundle_context := self.current_bundle_context():
            summary["market_bundle"] = bundle_context

        preferred_perp_symbols = list(parent.universe.basis_groups)
        preferred_perp_symbols = _sanitize_perp_symbols(preferred_perp_symbols)
        if track == "yield_flows" and (
            not preferred_perp_symbols
        ):
            preferred_perp_symbols = ["BTC", "ETH", "SOL", "HYPE", "DOGE"]

        symbols = await self.discover_perp_symbols(
            preferred_perp_symbols,
            limit=min(max(parent.universe.max_symbols, 5), 5),
        )
        perp_bundle = await self.fetch_perp_bundle(
            symbols=symbols,
            lookback_days=21,
            interval="1h",
        )
        if not perp_bundle["prices"].empty:
            prices = perp_bundle["prices"]
            funding = perp_bundle["funding"]
            summary["perp_symbols"] = symbols
            summary["perp_data_source"] = perp_bundle["source"]
            summary["market_bundle"] = {
                **dict(summary.get("market_bundle") or {}),
                "bundle_id": perp_bundle.get("bundle_id") or summary.get("market_bundle", {}).get("bundle_id"),
                "as_of": perp_bundle.get("bundle_as_of") or summary.get("market_bundle", {}).get("as_of"),
            }
            summary["perp_snapshot"] = [
                {
                    "symbol": symbol,
                    "return_7d": _safe_float(prices[symbol].pct_change(24 * 7).iloc[-1]),
                    "funding_72h_mean": _safe_float(funding[symbol].tail(72).mean()),
                }
                for symbol in prices.columns[:5]
            ]
            pair_calibration = _pair_calibration_snapshot(
                prices=prices,
                funding=funding,
                symbols=symbols,
            )
            if pair_calibration:
                summary["pair_calibration"] = pair_calibration

        if track == "yield_flows":
            stable_universe = AssetUniverse(
                basis_groups=["USD"],
                chains=["arbitrum", "base", "plasma"],
                max_symbols=5,
                lookback_days=120,
                interval="1d",
                min_liquidity_usd=250_000.0,
                min_volume_usd_24h=25_000.0,
                min_days_to_expiry=10,
                max_days_to_expiry=180,
            )
            rotation_universe = parent.universe
            if parent.family not in {"pt_yield_rotation", "stable_pt_ladder"}:
                rotation_universe = AssetUniverse(
                    basis_groups=["BTC", "ETH", "SOL"],
                    chains=["arbitrum", "base"],
                    max_symbols=5,
                    lookback_days=120,
                    interval="1d",
                    min_liquidity_usd=250_000.0,
                    min_volume_usd_24h=25_000.0,
                    min_days_to_expiry=10,
                    max_days_to_expiry=180,
                )

            stable_markets = await self.discover_stable_pt_markets(
                stable_universe,
                limit=min(stable_universe.max_symbols, 5),
            )
            rotation_markets = await self.discover_pt_markets(
                rotation_universe,
                limit=min(rotation_universe.max_symbols, 5),
            )
            summary["stable_pt_markets"] = [
                {
                    "market": market["marketName"],
                    "chain_id": market["chainId"],
                    "fixed_apy": market["fixedApy"],
                    "underlying_apy": market["underlyingApy"],
                    "days_to_expiry": market["daysToExpiry"],
                }
                for market in stable_markets[:5]
            ]
            summary["pt_rotation_markets"] = [
                {
                    "market": market["marketName"],
                    "chain_id": market["chainId"],
                    "fixed_apy": market["fixedApy"],
                    "underlying_apy": market["underlyingApy"],
                    "days_to_expiry": market["daysToExpiry"],
                    "hedge_symbol": market.get("hedgeSymbol"),
                }
                for market in rotation_markets[:5]
            ]
            lending_universe = parent.universe
            if parent.family != "lending_carry_rotation":
                lending_universe = AssetUniverse(
                    basis_groups=["ETH", "BTC", "SOL"],
                    chains=["arbitrum", "base", "unichain"],
                    max_symbols=5,
                    lookback_days=90,
                    interval="1h",
                    min_liquidity_usd=250_000.0,
                    min_volume_usd_24h=25_000.0,
                    min_days_to_expiry=7,
                    max_days_to_expiry=180,
                )
            lending_markets = await self.discover_lending_markets(
                lending_universe,
                limit=min(parent.universe.max_symbols, 5),
            )
            summary["lending_markets"] = [
                {
                    "market": market["marketLabel"],
                    "basis_symbol": market["basisSymbol"],
                    "venue": market["venue_name"],
                    "symbol": market["symbol"],
                    "net_supply_apr_now": market.get("net_supply_apr_now"),
                    "combined_net_supply_apr_now": market.get("combined_net_supply_apr_now"),
                    "util_now": market.get("util_now"),
                    "hedge_symbol": market.get("hedgeSymbol"),
                }
                for market in lending_markets[:5]
            ]
        return summary

    async def discover_perp_symbols(
        self,
        preferred_symbols: list[str],
        *,
        limit: int,
    ) -> list[str]:
        """Resolve perp symbols from preferred list, falling back to ``["BTC", "ETH"]``.

        Cached: warm cache (survives across bundles within a session).
        HTTP: optional ``delta_lab.get_basis_symbols`` call if no cache hit.
        """
        preferred_symbols = _sanitize_perp_symbols(preferred_symbols)
        warm_key = self._warm_cache_key(
            "perp_symbols",
            preferred_symbols=preferred_symbols,
            limit=limit,
        )
        if warm_key in self._warm_cache:
            return list(self._warm_cache[warm_key])[: max(1, int(limit))]
        if hasattr(self, "delta_lab") and hasattr(self.delta_lab, "get_basis_symbols"):
            payload = await self.delta_lab.get_basis_symbols()
            rows = list((payload or {}).get("symbols") or [])
            discovered = _sanitize_perp_symbols([str(row.get("symbol") or "") for row in rows])
            if discovered:
                resolved = (preferred_symbols or discovered)[: max(1, int(limit))]
                self._warm_cache[warm_key] = list(resolved)
                self._bundle_cache[warm_key] = list(resolved)
                return resolved
        resolved = preferred_symbols or ["BTC", "ETH"]
        resolved = resolved[: max(1, int(limit))]
        self._warm_cache[warm_key] = list(resolved)
        self._bundle_cache[warm_key] = list(resolved)
        return resolved

    async def fetch_perp_bundle(
        self,
        *,
        symbols: list[str],
        lookback_days: int,
        interval: str,
    ) -> dict[str, Any]:
        """Fetch perp klines and funding rates for *symbols*.

        Returns a dict with keys ``prices``, ``funding``, ``source``,
        ``bundle_as_of``, and ``bundle_id``.

        Cached: warm cache + per-bundle cache.  Frames are also persisted
        to the ``ParquetLake``.
        HTTP: ``SoDEXFeeds.fetch_klines`` + ``SoDEXFeeds.fetch_mark_prices``
        (or ``delta_lab`` variant) on cache miss.
        """
        symbols = _sanitize_perp_symbols(symbols)
        if not symbols:
            raise ValueError("No supported perp symbols after filtering synthetic stable labels")
        cache_key = self._bundle_cache_key(
            "perp_bundle",
            symbols=symbols,
            lookback_days=lookback_days,
            interval=interval,
        )
        warm_key = self._warm_cache_key(
            "perp_bundle",
            symbols=symbols,
            lookback_days=lookback_days,
            interval=interval,
        )
        if cache_key in self._bundle_cache:
            return copy.deepcopy(self._bundle_cache[cache_key])
        if warm_key in self._warm_cache:
            bundle = self._bind_bundle_to_active_context(self._warm_cache[warm_key])
            self._bundle_cache[cache_key] = copy.deepcopy(bundle)
            self._persist_bundle_frames(
                cache_key,
                prices=bundle["prices"],
                funding=bundle["funding"],
            )
            return bundle
        if hasattr(self, "_fetch_perp_bundle_delta_lab"):
            bundle = await self._fetch_perp_bundle_delta_lab(
                symbols=symbols,
                lookback_days=lookback_days,
                interval=interval,
            )
        else:
            # Use real SoDEX perp klines instead of synthetic ETF-proxy data
            bundle = await self._fetch_perp_bundle_sodex(
                symbols=symbols,
                lookback_days=lookback_days,
                interval=interval,
            )
        self._warm_cache[warm_key] = copy.deepcopy(bundle)
        bundle = self._bind_bundle_to_active_context(bundle)
        self._bundle_cache[cache_key] = copy.deepcopy(bundle)
        self._persist_bundle_frames(
            cache_key,
            prices=bundle["prices"],
            funding=bundle["funding"],
        )
        return bundle

    async def discover_stable_pt_markets(
        self,
        universe: AssetUniverse,
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Discover stable-PT markets by delegating to ``discover_pt_markets``.

        HTTP: same as ``discover_pt_markets`` (cached via lake JSON).
        """
        return await self.discover_pt_markets(
            universe,
            limit=limit,
            stable_only=True,
        )

    async def discover_pt_markets(
        self,
        universe: AssetUniverse,
        *,
        limit: int,
        stable_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Discover Pendle PT markets matching *universe* criteria.

        Cached: per-bundle cache, plus lake JSON (12 h TTL when no active
        bundle).  Currently returns ``[]`` as the Pendle data source is
        not wired.

        HTTP: none at present (Pendle API not integrated).
        """
        bundle_cache_key = self._bundle_cache_key(
            "pt_markets",
            groups=list(universe.basis_groups),
            chains=list(universe.chains),
            stable_only=int(stable_only),
            limit=limit,
            min_days=universe.min_days_to_expiry,
            max_days=universe.max_days_to_expiry,
        )
        if bundle_cache_key in self._bundle_cache:
            return list(self._bundle_cache[bundle_cache_key])[:limit]
        cache_key = (
            f"pt_markets__{int(stable_only)}__"
            f"{','.join(universe.chains or ['all'])}__"
            f"{','.join(universe.basis_groups or ['all'])}"
        )
        cached = None
        if self._active_bundle_id is None:
            cached = self.lake.latest_json("pendle", cache_key, max_age_hours=12)
        if cached:
            return list(cached)[:limit]

        # Pendle PT market data source is not available; return empty
        return []

    async def fetch_pt_histories(
        self,
        markets: list[dict[str, Any]],
        *,
        lookback_days: int,
    ) -> dict[str, pd.DataFrame]:
        """Fetch historical price frames for Pendle PT markets.

        Cached: per-bundle cache, plus lake parquet (24 h TTL).
        HTTP: none at present (Pendle API not integrated).
        """
        if not markets:
            return {}
        bundle_cache_key = self._bundle_cache_key(
            "pt_histories",
            markets=[self.market_label(row) for row in markets],
            lookback_days=lookback_days,
        )
        if bundle_cache_key in self._bundle_cache:
            cached = self._bundle_cache[bundle_cache_key]
            return {key: value.copy() for key, value in cached.items()}

        async def _fetch_one(row: dict[str, Any]) -> tuple[str, pd.DataFrame]:
            label = self.market_label(row)
            cached = None
            if self._active_bundle_id is None:
                cached = self.lake.latest_frame("pendle_history", label, max_age_hours=24)
            if cached is not None:
                return label, cached

            return label, pd.DataFrame()

        pairs = await asyncio.gather(*[_fetch_one(row) for row in markets])
        histories = {label: frame for label, frame in pairs if not frame.empty}
        self._bundle_cache[bundle_cache_key] = {key: value.copy() for key, value in histories.items()}
        for label, frame in histories.items():
            self._persist_bundle_frames(f"{bundle_cache_key}__{label}", prices=frame)
        return histories

    async def discover_lending_markets(
        self,
        universe: AssetUniverse,
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Discover lending markets via ``delta_lab.screen_lending``.

        Enriches each row with ``basisSymbol``, ``marketLabel``, and
        ``hedgeSymbol``, deduplicates by best combined APR, and persists
        results to the lake (6 h TTL).

        Cached: per-bundle cache, plus lake JSON.
        HTTP: ``delta_lab.screen_lending`` per basis group on cache miss.
        """
        bundle_cache_key = self._bundle_cache_key(
            "lending_markets",
            groups=list(universe.basis_groups),
            chains=list(universe.chains),
            limit=limit,
            min_liquidity=universe.min_liquidity_usd,
        )
        if bundle_cache_key in self._bundle_cache:
            return list(self._bundle_cache[bundle_cache_key])[:limit]
        cache_key = (
            f"lending_markets__{','.join(universe.basis_groups or ['all'])}__"
            f"{','.join(universe.chains or ['all'])}"
        )
        cached = None
        if self._active_bundle_id is None:
            cached = self.lake.latest_json("delta_lab", cache_key, max_age_hours=6)
        if cached:
            return list(cached)[:limit]

        basis_groups = universe.basis_groups or ["BTC", "ETH", "SOL", "USD"]
        allowed_chain_ids = {
            CHAIN_NAME_TO_ID[name.lower()]
            for name in universe.chains
            if name.lower() in CHAIN_NAME_TO_ID
        }
        discovered: dict[str, dict[str, Any]] = {}
        for basis in basis_groups:
            try:
                payload = await self.delta_lab.screen_lending(
                    basis=None if basis.upper() == "ALL" else basis.upper(),
                    limit=max(limit * 5, 20),
                    min_tvl=universe.min_liquidity_usd,
                    exclude_frozen=True,
                )
            except Exception:
                logger.warning("delta_lab.screen_lending failed for basis=%s, skipping", basis)
                continue
            for row in payload.get("data") or []:
                if allowed_chain_ids and int(row.get("chain_id") or 0) not in allowed_chain_ids:
                    continue
                if float(row.get("supply_tvl_usd") or 0.0) < universe.min_liquidity_usd:
                    continue
                enriched = dict(row)
                enriched["basisSymbol"] = basis.upper()
                enriched["marketLabel"] = self.lending_market_label(enriched)
                enriched["hedgeSymbol"] = (
                    basis.upper() if basis.upper() != "USD" else "USD"
                )
                key = enriched["marketLabel"]
                if key not in discovered or float(
                    enriched.get("combined_net_supply_apr_now") or 0.0
                ) > float(discovered[key].get("combined_net_supply_apr_now") or 0.0):
                    discovered[key] = enriched

        ordered = sorted(
            discovered.values(),
            key=lambda row: float(row.get("combined_net_supply_apr_now") or 0.0),
            reverse=True,
        )
        self.lake.write_json("delta_lab", cache_key, ordered)
        self._bundle_cache[bundle_cache_key] = list(ordered)
        self._persist_bundle_json(bundle_cache_key, ordered)
        return ordered[:limit]

    async def fetch_lending_bundle(
        self,
        markets: list[dict[str, Any]],
        *,
        lookback_days: int,
    ) -> dict[str, Any]:
        """Fetch lending time-series (prices, supply APR, utilization, etc.).

        Returns a dict with DataFrames keyed by market label, plus
        ``hedge_symbols``, ``source``, ``bundle_as_of``, and ``bundle_id``.

        Cached: per-bundle cache.  Price frames are persisted to the lake.
        HTTP: ``delta_lab.get_asset_timeseries`` per basis symbol on cache miss.
        """
        bundle_cache_key = self._bundle_cache_key(
            "lending_bundle",
            markets=[self.lending_market_label(row) for row in markets],
            lookback_days=lookback_days,
        )
        if bundle_cache_key in self._bundle_cache:
            return copy.deepcopy(self._bundle_cache[bundle_cache_key])
        if not markets:
            empty = pd.DataFrame()
            return {
                "prices": empty,
                "combined_supply_apy": empty,
                "supply_apr": empty,
                "supply_reward_apr": empty,
                "base_yield_apy": empty,
                "utilization": empty,
                "supply_tvl_usd": empty,
                "borrow_apr": empty,
                "borrow_tvl_usd": empty,
                "hedge_symbols": {},
                "source": "sosovalue_lending_empty",
                "bundle_as_of": (self._active_as_of or datetime.now(UTC)).isoformat(),
                "bundle_id": self._active_bundle_id,
            }
        grouped: dict[str, list[dict[str, Any]]] = {}
        for market in markets:
            grouped.setdefault(str(market["basisSymbol"]), []).append(market)

        price_series: list[pd.Series] = []
        combined_supply_series: list[pd.Series] = []
        supply_apr_series: list[pd.Series] = []
        reward_series: list[pd.Series] = []
        base_yield_series: list[pd.Series] = []
        util_series: list[pd.Series] = []
        supply_tvl_series: list[pd.Series] = []
        borrow_apr_series: list[pd.Series] = []
        borrow_tvl_series: list[pd.Series] = []
        hedge_symbols: dict[str, str] = {}

        for basis_symbol, basis_markets in grouped.items():
            try:
                if not hasattr(self, "delta_lab"):
                    continue
                payload = await self.delta_lab.get_asset_timeseries(
                    symbol=basis_symbol,
                    lookback_days=lookback_days,
                    limit=10_000,
                    as_of=self._active_as_of,
                    series="price,lending",
                )
            except Exception:
                logger.warning("delta_lab.get_asset_timeseries failed for %s, skipping", basis_symbol)
                continue
            price_df = payload.get("price")
            lending_df = payload.get("lending")
            if price_df is None or lending_df is None or price_df.empty or lending_df.empty:
                continue

            price_df = price_df.copy()
            lending_df = lending_df.copy()
            price_df.index = pd.to_datetime(price_df.index, utc=True).tz_convert(None)
            lending_df.index = pd.to_datetime(lending_df.index, utc=True).tz_convert(None)
            root_price = price_df["price_usd"].astype(float)

            for market in basis_markets:
                label = self.lending_market_label(market)
                market_df = lending_df[
                    (lending_df["market_id"].astype(int) == int(market["market_id"]))
                    & (lending_df["venue"].astype(str) == str(market["venue_name"]))
                    & (lending_df["asset_symbol"].astype(str) == str(market["symbol"]))
                ].copy()
                if market_df.empty:
                    continue
                market_df = market_df.groupby(level=0).last().sort_index()

                supply_apr = _frame_column_or_default(market_df, "supply_apr").rename(label)
                reward_apr = _frame_column_or_default(
                    market_df,
                    "supply_reward_apr",
                ).rename(label)
                base_yield = _frame_column_or_default(
                    market_df,
                    "base_yield_apy",
                ).rename(label)
                combined_supply = (
                    _frame_column_or_default(market_df, "combined_supply_apy")
                    .fillna(_frame_column_or_default(market_df, "supply_apr"))
                    .fillna(0.0)
                    .rename(label)
                )
                combined_supply = combined_supply.fillna(
                    supply_apr.add(reward_apr, fill_value=0.0).add(base_yield, fill_value=0.0)
                )

                price_series.append(root_price.rename(label))
                combined_supply_series.append(combined_supply)
                supply_apr_series.append(supply_apr)
                reward_series.append(reward_apr)
                base_yield_series.append(base_yield)
                util_series.append(_frame_column_or_default(market_df, "utilization").rename(label))
                supply_tvl_series.append(
                    _frame_column_or_default(market_df, "supply_tvl_usd").rename(label)
                )
                borrow_apr_series.append(
                    _frame_column_or_default(market_df, "borrow_apr").rename(label)
                )
                borrow_tvl_series.append(
                    _frame_column_or_default(market_df, "borrow_tvl_usd").rename(label)
                )
                hedge_symbols[label] = str(market.get("hedgeSymbol") or "")

        if not price_series:
            empty = pd.DataFrame()
            bundle = {
                "prices": empty,
                "combined_supply_apy": empty,
                "supply_apr": empty,
                "supply_reward_apr": empty,
                "base_yield_apy": empty,
                "utilization": empty,
                "supply_tvl_usd": empty,
                "borrow_apr": empty,
                "borrow_tvl_usd": empty,
                "hedge_symbols": hedge_symbols,
                "source": "delta_lab_lending",
                "bundle_as_of": (self._active_as_of or datetime.now(UTC)).isoformat(),
                "bundle_id": self._active_bundle_id,
            }
            self._bundle_cache[bundle_cache_key] = copy.deepcopy(bundle)
            return bundle

        prices = pd.concat(price_series, axis=1).sort_index().ffill().dropna(how="all")

        def _align(rows: list[pd.Series]) -> pd.DataFrame:
            return (
                pd.concat(rows, axis=1)
                .sort_index()
                .reindex(prices.index)
                .ffill()
                .dropna(how="all")
            )

        bundle = {
            "prices": prices,
            "combined_supply_apy": _align(combined_supply_series),
            "supply_apr": _align(supply_apr_series),
            "supply_reward_apr": _align(reward_series).fillna(0.0),
            "base_yield_apy": _align(base_yield_series).fillna(0.0),
            "utilization": _align(util_series),
            "supply_tvl_usd": _align(supply_tvl_series),
            "borrow_apr": _align(borrow_apr_series),
            "borrow_tvl_usd": _align(borrow_tvl_series),
            "hedge_symbols": hedge_symbols,
            "source": "delta_lab_lending",
            "bundle_as_of": (self._active_as_of or datetime.now(UTC)).isoformat(),
            "bundle_id": self._active_bundle_id,
        }
        self._bundle_cache[bundle_cache_key] = copy.deepcopy(bundle)
        self._persist_bundle_frames(f"{bundle_cache_key}__prices", prices=bundle["prices"])
        return bundle

    def market_label(self, row: dict[str, Any]) -> str:
        """Return a canonical label for a Pendle PT market row.

        Format: ``<sanitised_marketName>_<chainId>``.
        HTTP: none (pure string transform).
        """
        name = str(row.get("marketName") or "pt")
        compact_name = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")
        return f"{compact_name}_{row.get('chainId')}"

    def lending_market_label(self, row: dict[str, Any]) -> str:
        """Return a canonical label for a lending market row.

        Format: ``<basisSymbol>_<symbol>_<venue_name>_<market_id>``.
        HTTP: none (pure string transform).
        """
        venue = re.sub(r"[^A-Za-z0-9]+", "_", str(row.get("venue_name") or "lending")).strip("_")
        symbol = re.sub(r"[^A-Za-z0-9]+", "_", str(row.get("symbol") or "asset")).strip("_")
        market_id = str(row.get("market_id") or "0")
        basis = str(row.get("basisSymbol") or "basis")
        return f"{basis}_{symbol}_{venue}_{market_id}"

    def market_hedge_symbol(
        self,
        row: dict[str, Any],
        *,
        preferred_symbols: list[str] | None = None,
    ) -> str | None:
        """Guess a hedge symbol for a PT market from its name.

        Returns ``"USD"`` for stable-PT markets, a major perp symbol if
        one appears in the market name, or ``None`` if no match is found.
        HTTP: none (pure string matching).
        """
        market_name = str(row.get("marketName") or "").upper()
        symbol_pool = preferred_symbols or MAJOR_PERP_SYMBOLS
        for symbol in symbol_pool:
            if symbol.upper() in market_name:
                return symbol.upper()
        if STABLE_PT_PATTERN.search(str(row.get("marketName") or "")):
            return "USD"
        return None

    def _market_matches_group(self, market_name: str, group: str) -> bool:
        """Check whether *market_name* belongs to the given *group*.

        For ``"USD"`` group, matches against the stable-PT regex pattern;
        otherwise performs a case-insensitive substring check.
        """
        if group.upper() == "USD":
            return STABLE_PT_PATTERN.search(market_name) is not None
        return group.upper() in market_name.upper()

    async def _fetch_perp_bundle_sodex(
        self,
        *,
        symbols: list[str],
        lookback_days: int,
        interval: str,
    ) -> dict[str, Any]:
        """Fetch real perp klines and funding rates from SoDEX.

        Uses ``SoDEXFeeds.fetch_klines()`` for historical price data and
        ``SoDEXFeeds.fetch_mark_prices()`` for the latest funding rates.

        Returns
        -------
        dict
            With keys ``prices``, ``funding``, ``source`` (``"sodex_perp_klines"``),
            ``bundle_as_of``, and ``bundle_id``.
        """
        # Lazy import to avoid circular dependency (live.runtime -> siglab.data)
        from siglab.data.sodex_feeds import SoDEXFeeds  # noqa: PLC0415

        if self.sodex_feeds is None:
            self.sodex_feeds = SoDEXFeeds(lake=self.lake)

        as_of = self._active_as_of or datetime.now(UTC)

        # Estimate how many bars to request given the lookback period
        interval_hours = _interval_to_hours(interval)
        num_bars = max(100, min(1000, int(lookback_days * 24.0 / max(interval_hours, 1.0))))

        # Fetch klines for each symbol
        price_series_list: list[pd.Series] = []
        valid_symbols: list[str] = []
        for base_symbol in symbols:
            sodex_symbol = f"{base_symbol}-USD"
            try:
                klines = await self.sodex_feeds.fetch_klines(
                    symbol=sodex_symbol,
                    interval=interval,
                    limit=num_bars,
                )
            except Exception:
                logger.warning("SoDEX klines fetch failed for %s, skipping", sodex_symbol)
                continue
            if klines is not None and not klines.empty:
                series = klines["close"].rename(base_symbol)
                price_series_list.append(series)
                valid_symbols.append(base_symbol)

        if not price_series_list:
            raise ValueError(
                "SoDEX returned no kline data for any requested symbol; "
                "cannot build perp bundle"
            )

        # Align all price series to a common timestamp index
        prices = pd.concat(price_series_list, axis=1).sort_index()
        prices = prices.ffill().dropna(how="any")
        if prices.empty:
            raise ValueError("No common non-null price coverage after aligning SoDEX klines")

        # Fetch the latest funding rates from SoDEX mark-prices snapshot
        funding = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
        try:
            mark_prices = await self.sodex_feeds.fetch_mark_prices()
            funding_rate_map: dict[str, float] = {}
            for mp in mark_prices:
                mp_symbol = str(mp.get("symbol", ""))
                # SoDEX symbols are "BTC-USD", extract base "BTC"
                if mp_symbol.endswith("-USD"):
                    base = mp_symbol[:-4]
                    if base in symbols:
                        funding_rate_map[base] = float(mp.get("fundingRate") or 0.0)
            for base_sym in prices.columns:
                if base_sym in funding_rate_map:
                    funding[base_sym] = funding_rate_map[base_sym]
        except Exception:
            # If funding rate fetch fails, default to zero (no silent ETF proxy)
            logger.warning(
                "SoDEX mark_prices fetch failed; funding rates default to zero"
            )

        source = "sodex_perp_klines"

        self.lake.write_frame(
            "sodex_perp",
            f"prices_{sha256(str(as_of).encode()).hexdigest()[:16]}",
            prices,
        )
        self.lake.write_frame(
            "sodex_perp",
            f"funding_{sha256(str(as_of).encode()).hexdigest()[:16]}",
            funding,
        )

        return {
            "prices": prices,
            "funding": funding,
            "source": source,
            "bundle_as_of": as_of.isoformat(),
            "bundle_id": self._active_bundle_id,
        }

    async def fetch_etf_historical_inflow(self, *, etf_type: str = "us-btc-spot") -> list[dict[str, Any]]:
        """Fetch historical ETF inflow data from SoSoValue.

        Cached: lake JSON (6 h TTL).
        HTTP: ``SoSoValueClient.etf_historical_inflow`` on cache miss.
        """
        cache_key = f"historical_inflow_{etf_type}"
        cached = None
        if hasattr(self.lake, "latest_json"):
            cached = self.lake.latest_json("sosovalue_etf", cache_key, max_age_hours=6)
        if cached:
            return list(cached)
        rows = await self.sosovalue.etf_historical_inflow(etf_type=etf_type)
        if hasattr(self.lake, "write_json"):
            self.lake.write_json("sosovalue_etf", cache_key, rows)
        return rows

    async def fetch_featured_news(
        self,
        *,
        page_num: int = 1,
        page_size: int = 10,
        currency_id: int | None = None,
        category_list: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch featured news articles from SoSoValue and persist to the lake.

        Always-fresh: no cache, always hits the API.
        HTTP: ``SoSoValueClient.featured_news_by_currency``.
        """
        rows = await self.sosovalue.featured_news_by_currency(
            page_num=page_num,
            page_size=page_size,
            currency_id=currency_id,
            category_list=category_list,
        )
        normalized = [self._normalize_news_item(row) for row in rows]
        self.lake.write_json("sosovalue_news", f"featured_{page_num}_{page_size}", normalized)
        return normalized

    def _normalize_news_item(self, row: dict[str, Any]) -> dict[str, Any]:
        """Normalise a SoSoValue news item into a flat dict.

        Extracts the first multilingual content entry for ``title`` and
        ``summary``, and flattens tags, matched currencies, and metadata.
        """
        multilingual = list(row.get("multilanguageContent") or [])
        first_content = dict(multilingual[0] or {}) if multilingual else {}
        return {
            "id": row.get("id"),
            "title": first_content.get("title") or row.get("title"),
            "summary": first_content.get("content") or row.get("content"),
            "source_link": row.get("sourceLink"),
            "release_time": row.get("releaseTime"),
            "category": row.get("category"),
            "tags": list(row.get("tags") or []),
            "matched_currencies": list(row.get("matchedCurrencies") or []),
        }

    def _bundle_cache_key(self, kind: str, **params: Any) -> str:
        """Derive a deterministic cache key scoped to the active bundle.

        When no bundle is active the key depends only on *kind* and
        *params*; otherwise the active ``bundle_id`` is included so that
        different bundles never share cache entries.
        """
        if self._active_bundle_id is None:
            base = {"kind": kind, **params}
        else:
            base = {"bundle_id": self._active_bundle_id, "kind": kind, **params}
        return sha256(jsonable_dict(base).encode("utf-8")).hexdigest()[:20]

    def _warm_cache_key(self, kind: str, **params: Any) -> str:
        """Derive a deterministic cache key for the warm (cross-bundle) cache.

        Unlike ``_bundle_cache_key``, the bundle ID is never included so
        that warm-cache entries survive across bundles within a session.
        """
        base = {"kind": kind, **params}
        return sha256(jsonable_dict(base).encode("utf-8")).hexdigest()[:20]

    def _bind_bundle_to_active_context(self, bundle: dict[str, Any]) -> dict[str, Any]:
        """Stamp the active ``bundle_id`` and ``bundle_as_of`` onto *bundle*.

        Returns a deep copy so the warm-cache original is not mutated.
        """
        rebound = copy.deepcopy(bundle)
        rebound["bundle_id"] = self._active_bundle_id
        rebound["bundle_as_of"] = (self._active_as_of or datetime.now(UTC)).isoformat()
        return rebound

    def _persist_bundle_frames(
        self,
        cache_key: str,
        *,
        prices: pd.DataFrame,
        funding: pd.DataFrame | None = None,
    ) -> None:
        """Persist price (and optionally funding) DataFrames to the lake.

        Also records each frame as a bundle component for manifest
        tracking.
        """
        self.lake.write_frame("market_bundle_prices", cache_key, prices)
        self._record_bundle_component(
            namespace="market_bundle_prices",
            cache_key=cache_key,
            kind="frame",
        )
        if funding is not None:
            self.lake.write_frame("market_bundle_funding", cache_key, funding)
            self._record_bundle_component(
                namespace="market_bundle_funding",
                cache_key=cache_key,
                kind="frame",
            )

    def _persist_bundle_json(self, cache_key: str, payload: Any) -> None:
        """Persist a JSON-serialisable payload to the lake and record it as a bundle component."""
        self.lake.write_json("market_bundle_json", cache_key, payload)
        self._record_bundle_component(
            namespace="market_bundle_json",
            cache_key=cache_key,
            kind="json",
        )

    def _record_bundle_component(
        self,
        *,
        namespace: str,
        cache_key: str,
        kind: str,
    ) -> None:
        """Register a persisted artifact as a bundle manifest component.

        No-op when no bundle is active.  Deduplicates identical
        components and updates the on-disk manifest.
        """
        if self._active_bundle_id is None:
            return
        component = {
            "namespace": namespace,
            "cache_key": cache_key,
            "kind": kind,
        }
        if component in self._bundle_components:
            return
        self._bundle_components.append(component)
        self._bundle_manifest["components"] = list(self._bundle_components)
        self._write_bundle_manifest(self._bundle_manifest)

    def _write_bundle_manifest(self, payload: dict[str, Any]) -> None:
        """Merge *payload* into the bundle manifest and persist to the lake.

        No-op when neither an active bundle ID nor a ``bundle_id`` key
        in *payload* is present.
        """
        if self._active_bundle_id is None and not payload.get("bundle_id"):
            return
        self._bundle_manifest = {
            **dict(self._bundle_manifest),
            **dict(payload),
        }
        self.lake.write_json(
            "market_bundles",
            str(self._bundle_manifest.get("bundle_id") or self._active_bundle_id),
            self._bundle_manifest,
        )



def jsonable_iteration_payload(*, track: str, parent_hash: str, as_of: datetime) -> str:
    """Serialise iteration bundle identity fields to a sorted JSON string.

    Used to derive a deterministic bundle ID via SHA-256.
    """
    return json.dumps(
        {
            "track": track,
            "parent_hash": parent_hash,
            "as_of": as_of.isoformat(),
        },
        sort_keys=True,
    )


def jsonable_dict(payload: dict[str, Any]) -> str:
    """Serialise a dict to a sorted JSON string, coercing non-serialisable values via ``str``."""
    return json.dumps(payload, sort_keys=True, default=str)

