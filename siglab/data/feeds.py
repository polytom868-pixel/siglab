from __future__ import annotations

import asyncio
import atexit
import copy
import json
import logging
import re
import time
from datetime import UTC, datetime
from typing import Any, cast

import httpx
import pandas as pd

from siglab.config import SiglabConfig
from siglab.config import AssetUniverse, SignalSpec
from siglab.config import resolve_track
from siglab.data.provider_base import _Metrics
from siglab.data.sodex_client import (
    SoDEXUpstreamError,
    SoDEXPublicPerpsClient,
    SoDEXWeightScheduler,
)
from siglab.data.sosovalue_client import SoSoValueClient, SoSoValueEndpoints, SoSoValueRequestSpec
from siglab.data.store import ParquetLake
from siglab.utils import dget, percentile as _percentile
from siglab.utils import safe_float as _safe_float
from siglab.utils import short_hash

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
    "(?:^|[^A-Za-z])(usd|usdc|usdt|usde|usds|dai|fdusd|usdai|susde|upusdc|yoUSD)",
    re.IGNORECASE,
)
MAX_FFILL_BARS = 5


def _frame_column_or_default(
    frame: pd.DataFrame,
    column: str,
    *,
    default: float = 0.0,
) -> pd.Series:
    if column in frame.columns:
        series = pd.to_numeric(frame[column], errors="coerce")
        n_missing = int(series.isna().sum())
        if n_missing:
            logger.warning(
                "data_nan_fill column=%s missing=%d default=%.2f",
                column,
                n_missing,
                default,
            )
        return series.fillna(default)
    return pd.Series(default, index=frame.index, dtype=float)


def _sanitize_perp_symbols(symbols: list[str]) -> list[str]:
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
    if frame.empty:
        return frame
    return frame.groupby(level=0).last().sort_index()


def _percentile_map(
    series: pd.Series,
    percentiles: list[float],
) -> dict[str, float | None]:
    clean = (
        pd.to_numeric(series, errors="coerce")
        .replace([float("inf"), float("-inf")], pd.NA)
        .dropna()
    )
    if clean.empty:
        return {f"p{int(percentile)}": None for percentile in percentiles}
    return {
        f"p{int(percentile)}": _safe_float(clean.quantile(percentile / 100.0))
        for percentile in percentiles
    }


def _aligned_funding_series(
    prices: pd.DataFrame,
    funding: pd.DataFrame,
    symbol: str,
) -> pd.Series:
    raw = (
        funding[symbol]
        if symbol in funding.columns
        else pd.Series(0.0, index=prices.index, dtype=float)
    )
    return pd.to_numeric(raw, errors="coerce").reindex(prices.index).fillna(0.0)


def _pair_calibration_snapshot(
    *,
    prices: pd.DataFrame,
    funding: pd.DataFrame,
    symbols: list[str],
) -> dict[str, Any]:
    if len(symbols) < 2:
        return {}
    asset_1_symbol, asset_2_symbol = symbols[:2]
    if asset_1_symbol not in prices.columns or asset_2_symbol not in prices.columns:
        return {}
    asset_1_price = pd.to_numeric(prices[asset_1_symbol], errors="coerce")
    asset_2_price = pd.to_numeric(prices[asset_2_symbol], errors="coerce")
    asset_1_funding = _aligned_funding_series(prices, funding, asset_1_symbol)
    asset_2_funding = _aligned_funding_series(prices, funding, asset_2_symbol)
    pair_ratio = asset_1_price.div(asset_2_price.replace(0.0, pd.NA))
    funding_spread = asset_1_funding.sub(asset_2_funding, fill_value=0.0)
    pair_volatility_72h = pair_ratio.pct_change().rolling(72).std()
    pair_correlation_72h = (
        asset_1_price.pct_change().rolling(72).corr(asset_2_price.pct_change())
    )
    return_spread_24h = asset_1_price.pct_change(24).sub(asset_2_price.pct_change(24))
    residual_z_60 = pair_ratio.sub(pair_ratio.rolling(60).mean()).div(
        pair_ratio.rolling(60).std().replace(0.0, pd.NA),
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
            "funding_spread_positive_fraction": _safe_float(
                (funding_spread > 0.0).mean(),
            ),
            "funding_spread_negative_fraction": _safe_float(
                (funding_spread < 0.0).mean(),
            ),
            "pair_correlation_non_negative_fraction": _safe_float(
                (pair_correlation_72h >= 0.0).mean(),
            ),
        },
    }


def _align_perp_bundle_frames(
    prices: pd.DataFrame,
    funding: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    prices = _dedupe_time_index(prices)
    funding = _dedupe_time_index(funding)
    prices = prices.dropna(how="any")
    if prices.empty:
        raise ValueError(
            "Perp bundle has no common non-null price coverage across requested symbols",
        )
    funding = (
        funding.reindex(prices.index)
        .ffill(limit=MAX_FFILL_BARS)
        .fillna(0.0)
        .astype(float)
    )
    return (prices, funding)


def _interval_to_hours(interval: str) -> float:
    i = interval.strip().lower()
    if not i:
        return 1.0
    try:
        mul = {"m": 1 / 60.0, "h": 1.0, "d": 24.0, "w": 168.0}[i[-1]]
    except KeyError:
        return 1.0
    return float(i[:-1]) * mul


class MarketDataProvider:
    def __init__(
        self,
        settings: SiglabConfig,
        lake: ParquetLake,
        *,
        sodex_feeds: SoDEXFeeds | None = None,
    ) -> None:
        self.settings = settings
        self.lake = lake
        self.sosovalue = SoSoValueClient(
            api_key=settings.sosovalue_api_key_override,
            endpoints=SoSoValueEndpoints(
                openapi_base_url=settings.sosovalue_base_url,
                etf_base_url=settings.etf_base_url,
                news_base_url=settings.news_base_url,
            ),
            timeout_s=settings.sosovalue_timeout_s,
            retries=settings.sosovalue_retries,
        )
        self.sodex_feeds = sodex_feeds
        self._active_bundle_id: str | None = None
        self._active_as_of: datetime | None = None
        self._bundle_cache: dict[str, Any] = {}
        self._warm_cache: dict[str, Any] = {}
        self._bundle_components: list[dict[str, Any]] = []
        self._bundle_manifest: dict[str, Any] = {}
        self.delta_lab: Any = None
        atexit.register(self._close_sync)

    def _close_sync(self) -> None:
        atexit.unregister(self._close_sync)
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                return
            loop.run_until_complete(self.close())
        except (RuntimeError, OSError):
            pass

    def metrics_snapshot(self) -> dict[str, Any]:
        return {"sosovalue": self.sosovalue.metrics_snapshot()} | (
            {"sodex": self.sodex_feeds.metrics_snapshot()}
            if self.sodex_feeds is not None
            else {}
        )

    async def close(self) -> None:
        atexit.unregister(self._close_sync)
        logger.info(
            "data_pipeline_metrics %s",
            json.dumps(self.metrics_snapshot(), default=str),
        )
        await self.sosovalue.close()
        if self.sodex_feeds is not None:
            await self.sodex_feeds.close()

    def begin_iteration_bundle(
        self,
        *,
        track: str,
        parent: SignalSpec,
    ) -> dict[str, Any]:
        as_of = datetime.now(UTC).replace(microsecond=0)
        resolved_track = cast(str, resolve_track(track))
        payload = jsonable_iteration_payload(
            track=resolved_track,
            parent_hash=parent.strategy_hash(),
            as_of=as_of,
        )
        bundle_id = short_hash(payload)
        self._active_bundle_id = bundle_id
        self._active_as_of = as_of
        self._bundle_cache = {}
        self._bundle_components = []
        metadata: dict[str, Any] = {
            "bundle_id": bundle_id,
            "as_of": as_of.isoformat(),
            "track": resolved_track,
            "parent_hash": parent.strategy_hash(),
            "components": [],
        }
        self._bundle_manifest = dict(metadata)
        self._write_bundle_manifest(metadata)
        return metadata

    def current_bundle_context(self) -> dict[str, Any] | None:
        if self._active_bundle_id is None or self._active_as_of is None:
            return None
        return {
            "bundle_id": self._active_bundle_id,
            "as_of": self._active_as_of.isoformat(),
        }

    def clear_iteration_bundle(self) -> None:
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
        track = cast(str, resolve_track(track) if track is not None else "default")
        summary: dict[str, Any] = {
            "track": track,
            "parent_family": parent.family,
            "parent_hash": parent.strategy_hash(),
        }
        if bc := self.current_bundle_context():
            summary["market_bundle"] = bc
        pps = _sanitize_perp_symbols(list(parent.universe.basis_groups))
        if track == "yield_flows" and (not pps):
            pps = ["BTC", "ETH", "SOL", "HYPE", "DOGE"]
        symbols = await self.discover_perp_symbols(
            pps,
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
                **summary.get("market_bundle", {}),
                "bundle_id": perp_bundle.get("bundle_id")
                or dget(summary, "market_bundle", "bundle_id"),
                "as_of": perp_bundle.get("bundle_as_of")
                or dget(summary, "market_bundle", "as_of"),
            }
            summary["perp_snapshot"] = [
                {
                    "symbol": symbol,
                    "return_7d": _safe_float(
                        prices[symbol].pct_change(24 * 7).iloc[-1],
                    ),
                    "funding_72h_mean": _safe_float(funding[symbol].tail(72).mean()),
                }
                for symbol in prices.columns[:5]
            ]
            if pc := _pair_calibration_snapshot(
                prices=prices,
                funding=funding,
                symbols=symbols,
            ):
                summary["pair_calibration"] = pc
        if track == "yield_flows":
            su = AssetUniverse(
                basis_groups=["USD"],
                chains=["arbitrum", "base", "plasma"],
                max_symbols=5,
                lookback_days=120,
                interval="1d",
                min_liquidity_usd=250000.0,
                min_volume_usd_24h=25000.0,
                min_days_to_expiry=10,
                max_days_to_expiry=180,
            )
            ru = (
                parent.universe
                if parent.family in {"pt_yield_rotation", "stable_pt_ladder"}
                else AssetUniverse(
                    basis_groups=["BTC", "ETH", "SOL"],
                    chains=["arbitrum", "base"],
                    max_symbols=5,
                    lookback_days=120,
                    interval="1d",
                    min_liquidity_usd=250000.0,
                    min_volume_usd_24h=25000.0,
                    min_days_to_expiry=10,
                    max_days_to_expiry=180,
                )
            )
            lu = (
                parent.universe
                if parent.family == "lending_carry_rotation"
                else AssetUniverse(
                    basis_groups=["ETH", "BTC", "SOL"],
                    chains=["arbitrum", "base", "unichain"],
                    max_symbols=5,
                    lookback_days=90,
                    interval="1h",
                    min_liquidity_usd=250000.0,
                    min_volume_usd_24h=25000.0,
                    min_days_to_expiry=7,
                    max_days_to_expiry=180,
                )
            )
            stable_markets, rotation_markets, lending_markets = await asyncio.gather(
                self.discover_stable_pt_markets(su, limit=min(su.max_symbols, 5)),
                self.discover_pt_markets(ru, limit=min(ru.max_symbols, 5)),
                self.discover_lending_markets(
                    lu,
                    limit=min(parent.universe.max_symbols, 5),
                ),
            )
            summary["stable_pt_markets"] = [
                {
                    "market": m["marketName"],
                    "chain_id": m["chainId"],
                    "fixed_apy": m["fixedApy"],
                    "underlying_apy": m["underlyingApy"],
                    "days_to_expiry": m["daysToExpiry"],
                }
                for m in stable_markets[:5]
            ]
            summary["pt_rotation_markets"] = [
                {
                    "market": m["marketName"],
                    "chain_id": m["chainId"],
                    "fixed_apy": m["fixedApy"],
                    "underlying_apy": m["underlyingApy"],
                    "days_to_expiry": m["daysToExpiry"],
                    "hedge_symbol": m.get("hedgeSymbol"),
                }
                for m in rotation_markets[:5]
            ]
            summary["lending_markets"] = [
                {
                    "market": m["marketLabel"],
                    "basis_symbol": m["basisSymbol"],
                    "venue": m["venue_name"],
                    "symbol": m["symbol"],
                    "net_supply_apr_now": m.get("net_supply_apr_now"),
                    "combined_net_supply_apr_now": m.get("combined_net_supply_apr_now"),
                    "util_now": m.get("util_now"),
                    "hedge_symbol": m.get("hedgeSymbol"),
                }
                for m in lending_markets[:5]
            ]
        sosovalue_etf, sosovalue_news = await asyncio.gather(
            self.fetch_etf_historical_inflow(etf_type="us-btc-spot"),
            self.fetch_featured_news(page_size=5),
            return_exceptions=True,
        )
        if isinstance(sosovalue_etf, list):
            summary["etf_inflow"] = [
                {
                    "date": r.get("date"),
                    "total_net_inflow": r.get("totalNetInflow"),
                    "total_net_assets": r.get("totalNetAssets"),
                }
                for r in sosovalue_etf[:10]
            ]
        if isinstance(sosovalue_news, list):
            summary["featured_news"] = [
                {
                    "title": r.get("title"),
                    "source_link": r.get("source_link"),
                    "published_at": r.get("published_at"),
                }
                for r in sosovalue_news[:5]
            ]
        summary["sosovalue_evidence_used"] = True
        return summary

    async def discover_perp_symbols(
        self,
        symbols: list[str],
        *,
        limit: int,
    ) -> list[str]:
        symbols = _sanitize_perp_symbols(symbols)
        wk = self._warm_cache_key(
            "perp_symbols",
            preferred_symbols=symbols,
            limit=limit,
        )
        if wk in self._warm_cache:
            return list(self._warm_cache[wk])[: max(1, int(limit))]
        if hasattr(self, "delta_lab") and hasattr(self.delta_lab, "get_basis_symbols"):
            rows = list(
                ((await self.delta_lab.get_basis_symbols()) or {}).get("symbols") or [],
            )
            discovered = _sanitize_perp_symbols(
                [str(r.get("symbol") or "") for r in rows],
            )
            if discovered:
                resolved = (symbols or discovered)[: max(1, int(limit))]
                self._warm_cache[wk] = self._bundle_cache[wk] = list(resolved)
                return resolved
        resolved = (symbols or ["BTC", "ETH"])[: max(1, int(limit))]
        self._warm_cache[wk] = self._bundle_cache[wk] = list(resolved)
        return resolved

    async def fetch_perp_bundle(
        self,
        *,
        symbols: list[str],
        lookback_days: int,
        interval: str,
    ) -> dict[str, Any]:
        symbols = _sanitize_perp_symbols(symbols)
        if not symbols:
            raise ValueError(
                "No supported perp symbols after filtering synthetic stable labels",
            )
        ck = self._bundle_cache_key(
            "perp_bundle",
            symbols=symbols,
            lookback_days=lookback_days,
            interval=interval,
        )
        wk = self._warm_cache_key(
            "perp_bundle",
            symbols=symbols,
            lookback_days=lookback_days,
            interval=interval,
        )
        if ck in self._bundle_cache:
            return copy.deepcopy(self._bundle_cache[ck])
        if wk in self._warm_cache:
            bundle = self._bind_bundle_to_active_context(self._warm_cache[wk])
            self._bundle_cache[ck] = copy.deepcopy(bundle)
            self._persist_bundle_frames(
                ck,
                prices=bundle["prices"],
                funding=bundle["funding"],
            )
            return bundle
        delta_lab_fn = getattr(self, "_fetch_perp_bundle_delta_lab", None)
        bundle = await (
            delta_lab_fn(
                symbols=symbols,
                lookback_days=lookback_days,
                interval=interval,
            )
            if delta_lab_fn is not None
            else self._fetch_perp_bundle_sodex(
                symbols=symbols,
                lookback_days=lookback_days,
                interval=interval,
            )
        )
        self._warm_cache[wk] = copy.deepcopy(bundle)
        bundle = self._bind_bundle_to_active_context(bundle)
        self._bundle_cache[ck] = copy.deepcopy(bundle)
        self._persist_bundle_frames(
            ck,
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
        return await self.discover_pt_markets(universe, limit=limit, stable_only=True)

    async def discover_pt_markets(
        self,
        universe: AssetUniverse,
        *,
        limit: int,
        stable_only: bool = False,
    ) -> list[dict[str, Any]]:
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
        cache_key = f"pt_markets__{int(stable_only)}__{','.join(universe.chains or ['all'])}__{','.join(universe.basis_groups or ['all'])}"
        cached = None
        if self._active_bundle_id is None:
            cached = self.lake.latest_json("pendle", cache_key, max_age_hours=12)
        if cached:
            return list(cached)[:limit]
        return []

    async def fetch_pt_histories(
        self,
        markets: list[dict[str, Any]],
        *,
        lookback_days: int,
    ) -> dict[str, pd.DataFrame]:
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
                cached = self.lake.latest_frame(
                    "pendle_history",
                    label,
                    max_age_hours=24,
                )
            if cached is not None:
                return (label, cached)
            return (label, pd.DataFrame())

        pairs = await asyncio.gather(*[_fetch_one(row) for row in markets])
        histories = {label: frame for label, frame in pairs if not frame.empty}
        self._bundle_cache[bundle_cache_key] = {
            key: value.copy() for key, value in histories.items()
        }
        for label, frame in histories.items():
            self._persist_bundle_frames(f"{bundle_cache_key}__{label}", prices=frame)
        return histories

    async def discover_lending_markets(
        self,
        universe: AssetUniverse,
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        bundle_cache_key = self._bundle_cache_key(
            "lending_markets",
            groups=list(universe.basis_groups),
            chains=list(universe.chains),
            limit=limit,
            min_liquidity=universe.min_liquidity_usd,
        )
        if bundle_cache_key in self._bundle_cache:
            return list(self._bundle_cache[bundle_cache_key])[:limit]
        cache_key = f"lending_markets__{','.join(universe.basis_groups or ['all'])}__{','.join(universe.chains or ['all'])}"
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
            if not hasattr(self, "delta_lab"):
                continue
            try:
                payload = await self.delta_lab.screen_lending(
                    basis=None if basis.upper() == "ALL" else basis.upper(),
                    limit=max(limit * 5, 20),
                    min_tvl=universe.min_liquidity_usd,
                    exclude_frozen=True,
                )
            except Exception:
                logger.exception(
                    "delta_lab.screen_lending failed for basis=%s, skipping",
                    basis,
                )
                continue
            for row in payload.get("data") or []:
                if (
                    allowed_chain_ids
                    and int(row.get("chain_id") or 0) not in allowed_chain_ids
                ):
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
                    enriched.get("combined_net_supply_apr_now") or 0.0,
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
                    limit=10000,
                    as_of=self._active_as_of,
                    series="price,lending",
                )
            except Exception:
                logger.exception(
                    "delta_lab.get_asset_timeseries failed for %s, skipping",
                    basis_symbol,
                )
                continue
            price_df = payload.get("price")
            lending_df = payload.get("lending")
            if (
                price_df is None
                or lending_df is None
                or price_df.empty
                or lending_df.empty
            ):
                continue
            price_df = price_df.copy()
            lending_df = lending_df.copy()
            price_df.index = pd.to_datetime(price_df.index, utc=True).tz_convert(None)
            lending_df.index = pd.to_datetime(lending_df.index, utc=True).tz_convert(
                None,
            )
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
                supply_apr = _frame_column_or_default(market_df, "supply_apr").rename(
                    label,
                )
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
                    supply_apr.add(reward_apr, fill_value=0.0).add(
                        base_yield,
                        fill_value=0.0,
                    ),
                )
                price_series.append(root_price.rename(label))
                combined_supply_series.append(combined_supply)
                supply_apr_series.append(supply_apr)
                reward_series.append(reward_apr)
                base_yield_series.append(base_yield)
                util_series.append(
                    _frame_column_or_default(market_df, "utilization").rename(label),
                )
                supply_tvl_series.append(
                    _frame_column_or_default(market_df, "supply_tvl_usd").rename(label),
                )
                borrow_apr_series.append(
                    _frame_column_or_default(market_df, "borrow_apr").rename(label),
                )
                borrow_tvl_series.append(
                    _frame_column_or_default(market_df, "borrow_tvl_usd").rename(label),
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
        prices = (
            pd.concat(price_series, axis=1)
            .sort_index()
            .ffill(limit=MAX_FFILL_BARS)
            .dropna(how="all")
        )

        def _align(rows: list[pd.Series]) -> pd.DataFrame:
            return (
                pd.concat(rows, axis=1)
                .sort_index()
                .reindex(prices.index)
                .ffill(limit=MAX_FFILL_BARS)
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
        self._persist_bundle_frames(
            f"{bundle_cache_key}__prices",
            prices=cast(pd.DataFrame, bundle["prices"]),
        )
        return bundle

    def market_label(self, row: dict[str, Any]) -> str:
        name = str(row.get("marketName") or "pt")
        compact_name = re.sub("[^A-Za-z0-9]+", "_", name).strip("_")
        return f"{compact_name}_{row.get('chainId')}"

    def lending_market_label(self, row: dict[str, Any]) -> str:
        venue = re.sub(
            "[^A-Za-z0-9]+",
            "_",
            str(row.get("venue_name") or "lending"),
        ).strip("_")
        symbol = re.sub("[^A-Za-z0-9]+", "_", str(row.get("symbol") or "asset")).strip(
            "_",
        )
        market_id = str(row.get("market_id") or "0")
        basis = str(row.get("basisSymbol") or "basis")
        return f"{basis}_{symbol}_{venue}_{market_id}"

    def market_hedge_symbol(
        self,
        row: dict[str, Any],
        *,
        preferred_symbols: list[str] | None = None,
    ) -> str | None:
        market_name = str(row.get("marketName") or "").upper()
        symbol_pool = preferred_symbols or MAJOR_PERP_SYMBOLS
        for symbol in symbol_pool:
            if symbol.upper() in market_name:
                return symbol.upper()
        if STABLE_PT_PATTERN.search(str(row.get("marketName") or "")):
            return "USD"
        return None

    def _market_matches_group(self, market_name: str, group: str) -> bool:
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

        if self.sodex_feeds is None:
            self.sodex_feeds = SoDEXFeeds(lake=self.lake)
        as_of = self._active_as_of or datetime.now(UTC)
        interval_hours = _interval_to_hours(interval)
        num_bars = max(
            100,
            min(1000, int(lookback_days * 24.0 / max(interval_hours, 1.0))),
        )
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
                logger.exception(
                    "SoDEX klines fetch failed for %s, skipping",
                    sodex_symbol,
                )
                continue
            if klines is not None and (not klines.empty):
                series = klines["close"].rename(base_symbol)
                price_series_list.append(series)
                valid_symbols.append(base_symbol)
        if not price_series_list:
            raise ValueError(
                "SoDEX returned no kline data for any requested symbol; cannot build perp bundle",
            )
        prices = pd.concat(price_series_list, axis=1).sort_index()
        prices = prices.ffill(limit=MAX_FFILL_BARS).dropna(how="any")
        if prices.empty:
            raise ValueError(
                "No common non-null price coverage after aligning SoDEX klines",
            )
        funding_rate_map: dict[str, float] = {}
        try:
            mark_prices = await self.sodex_feeds.fetch_mark_prices()
            for mp in mark_prices:
                mp_symbol = str(mp.get("symbol", ""))
                if mp_symbol.endswith("-USD"):
                    base = mp_symbol[:-4]
                    if base in symbols:
                        funding_rate_map[base] = float(mp.get("fundingRate") or 0.0)
        except Exception:
            logger.exception(
                "SoDEX mark_prices fetch failed; funding snapshots unavailable",
            )
        funding_series_list: list[pd.Series] = []
        start_ms = int(prices.index.min().timestamp() * 1000)
        end_ms = int(prices.index.max().timestamp() * 1000)
        for base_symbol in valid_symbols:
            sodex_symbol = f"{base_symbol}-USD"
            try:
                history = await self.sodex_feeds._client.funding_history(
                    sodex_symbol,
                    start_time=start_ms,
                    end_time=end_ms,
                )
                if history:
                    hist_df = pd.DataFrame(history)
                    time_col = (
                        "fundingTime"
                        if "fundingTime" in hist_df.columns
                        else "timestamp"
                    )
                    hist_df["timestamp"] = pd.to_datetime(
                        hist_df[time_col],
                        unit="ms",
                        utc=True,
                    )
                    hist_df = hist_df.set_index("timestamp").sort_index()
                    fs = hist_df["fundingRate"].astype(float).rename(base_symbol)
                    fs = fs.reindex(prices.index).ffill(limit=8).fillna(0.0)
                else:
                    fs = pd.Series(
                        funding_rate_map.get(base_symbol, 0.0),
                        index=prices.index,
                        name=base_symbol,
                    )
            except Exception:
                logger.exception(
                    "funding_history failed for %s, using latest snapshot",
                    sodex_symbol,
                )
                fs = pd.Series(
                    funding_rate_map.get(base_symbol, 0.0),
                    index=prices.index,
                    name=base_symbol,
                )
            funding_series_list.append(fs)
        funding = (
            pd.concat(funding_series_list, axis=1).astype(float)
            if funding_series_list
            else pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
        )
        source = "sodex_perp_klines"
        self.lake.write_frame("sodex_perp", f"prices_{short_hash(str(as_of))}", prices)
        self.lake.write_frame(
            "sodex_perp",
            f"funding_{short_hash(str(as_of))}",
            funding,
        )
        return {
            "prices": prices,
            "funding": funding,
            "source": source,
            "bundle_as_of": as_of.isoformat(),
            "bundle_id": self._active_bundle_id,
        }

    async def fetch_etf_historical_inflow(
        self,
        *,
        etf_type: str = "us-btc-spot",
    ) -> list[dict[str, Any]]:
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
        rows = await self.sosovalue.featured_news_by_currency(
            page_num=page_num,
            page_size=page_size,
            currency_id=currency_id,
            category_list=category_list,
        )
        normalized = [self._normalize_news_item(row) for row in rows]
        self.lake.write_json(
            "sosovalue_news",
            f"featured_{page_num}_{page_size}",
            normalized,
        )
        return normalized

    def _normalize_news_item(self, row: dict[str, Any]) -> dict[str, Any]:
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
        if self._active_bundle_id is None:
            base = {"kind": kind, **params}
        else:
            base = {"bundle_id": self._active_bundle_id, "kind": kind, **params}
        return short_hash(jsonable_dict(base), 20)

    def _warm_cache_key(self, kind: str, **params: Any) -> str:
        base = {"kind": kind, **params}
        return short_hash(jsonable_dict(base), 20)

    def _bind_bundle_to_active_context(self, bundle: dict[str, Any]) -> dict[str, Any]:
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

    def _persist_bundle_json(self, cache_key: str, payload: object) -> None:
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
        if self._active_bundle_id is None:
            return
        component = {"namespace": namespace, "cache_key": cache_key, "kind": kind}
        if component in self._bundle_components:
            return
        self._bundle_components.append(component)
        self._bundle_manifest["components"] = list(self._bundle_components)
        self._write_bundle_manifest(self._bundle_manifest)

    def _write_bundle_manifest(self, payload: dict[str, Any]) -> None:
        if self._active_bundle_id is None and (not payload.get("bundle_id")):
            return
        self._bundle_manifest = {**self._bundle_manifest, **payload}
        self.lake.write_json(
            "market_bundles",
            str(self._bundle_manifest.get("bundle_id") or self._active_bundle_id),
            self._bundle_manifest,
        )


def jsonable_iteration_payload(*, track: str, parent_hash: str, as_of: datetime) -> str:
    """Serialise iteration bundle identity fields to a sorted JSON string."""
    return json.dumps(
        {"track": track, "parent_hash": parent_hash, "as_of": as_of.isoformat()},
        sort_keys=True,
    )


def jsonable_dict(payload: dict[str, Any]) -> str:
    """Serialise a dict to a sorted JSON string."""
    return json.dumps(payload, sort_keys=True, default=str)


# ============================================================
# SoDEX Feeds (merged from data/sodex_feeds.py)
# ============================================================

KLINE_INTERVALS = frozenset({"1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w", "1M"})
_KLINE_FIELDS = {
    "t": "timestamp",
    "o": "open",
    "h": "high",
    "l": "low",
    "c": "close",
    "v": "volume",
    "q": "quote_volume",
}
_HOURLY_OR_LARGER_INTERVALS = frozenset({"1h", "4h", "1d", "1w", "1M"})


def _interval_rounds_to_hour(interval: str | None) -> bool:
    return interval is not None and interval in _HOURLY_OR_LARGER_INTERVALS


def _kline_to_row(kline: dict[str, Any]) -> dict[str, Any]:
    return {_KLINE_FIELDS.get(k, k): v for k, v in kline.items()}


DEFAULT_KLINES_CACHE_TTL_HOURS = 1.0
DEFAULT_SYMBOLS_CACHE_TTL_HOURS = 24.0
DEFAULT_TICKERS_CACHE_TTL_HOURS = 0.25
DEFAULT_MARK_PRICES_CACHE_TTL_HOURS = 0.25
DEFAULT_BOOK_TICKERS_CACHE_TTL_HOURS = 0.08
DEFAULT_ORDERBOOK_CACHE_TTL_HOURS = 0.03
DEFAULT_TRADES_CACHE_TTL_HOURS = 0.08


class SoDEXFeeds:
    """High-level SoDEX perp market data feed with ParquetLake caching."""

    def __init__(
        self,
        lake: ParquetLake,
        *,
        base_url: str = "https://mainnet-gw.sodex.dev/api/v1/perps",
        timeout_s: float = 10.0,
        retries: int = 1,
        klines_cache_ttl_hours: float = DEFAULT_KLINES_CACHE_TTL_HOURS,
        symbols_cache_ttl_hours: float = DEFAULT_SYMBOLS_CACHE_TTL_HOURS,
        tickers_cache_ttl_hours: float = DEFAULT_TICKERS_CACHE_TTL_HOURS,
        mark_prices_cache_ttl_hours: float = DEFAULT_MARK_PRICES_CACHE_TTL_HOURS,
        book_tickers_cache_ttl_hours: float = DEFAULT_BOOK_TICKERS_CACHE_TTL_HOURS,
        orderbook_cache_ttl_hours: float = DEFAULT_ORDERBOOK_CACHE_TTL_HOURS,
        trades_cache_ttl_hours: float = DEFAULT_TRADES_CACHE_TTL_HOURS,
        weight_scheduler: SoDEXWeightScheduler | None = None,
    ) -> None:
        self.lake = lake
        self._http_client = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
        )
        self._client = SoDEXPublicPerpsClient(
            base_url=base_url,
            timeout_s=timeout_s,
            retries=retries,
            weight_scheduler=weight_scheduler,
            client=self._http_client,
        )
        self._klines_cache_ttl_hours = klines_cache_ttl_hours
        self._symbols_cache_ttl_hours = symbols_cache_ttl_hours
        self._tickers_cache_ttl_hours = tickers_cache_ttl_hours
        self._mark_prices_cache_ttl_hours = mark_prices_cache_ttl_hours
        self._book_tickers_cache_ttl_hours = book_tickers_cache_ttl_hours
        self._orderbook_cache_ttl_hours = orderbook_cache_ttl_hours
        self._trades_cache_ttl_hours = trades_cache_ttl_hours

    async def close(self) -> None:
        """Release the underlying HTTP client resources."""
        await self._http_client.aclose()

    async def fetch_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 100,
        *,
        start_time: int | None = None,
        end_time: int | None = None,
        skip_cache: bool = False,
    ) -> pd.DataFrame:
        """Fetch kline / candlestick data for a perp symbol."""
        interval = str(interval).lower()
        if interval not in KLINE_INTERVALS:
            raise ValueError(
                f"Unsupported kline interval {interval!r}; expected one of {sorted(KLINE_INTERVALS)}",
            )
        if not symbol or not symbol.strip():
            return self._empty_klines_frame()
        cache_key = self._kline_cache_key(symbol, interval, limit, start_time, end_time)
        if not skip_cache:
            cached = self.lake.latest_frame(
                "sodex_klines",
                cache_key,
                max_age_hours=self._klines_cache_ttl_hours,
            )
            if cached is not None and (not cached.empty):
                return cached
        try:
            rows = await self._client.klines(
                symbol=symbol.strip(),
                interval=interval,
                start_time=start_time,
                end_time=end_time,
                limit=limit,
            )
        except SoDEXUpstreamError as exc:
            logger.warning("SoDEX klines upstream error for %s: %s", symbol, exc)
            empty = self._empty_klines_frame()
            self.lake.write_frame("sodex_klines", cache_key, empty)
            return empty
        frame = self._klines_to_frame(rows, interval=interval)
        self.lake.write_frame("sodex_klines", cache_key, frame)
        return frame

    def _kline_cache_key(
        self,
        symbol: str,
        interval: str,
        limit: int,
        start_time: int | None,
        end_time: int | None,
    ) -> str:
        parts = [symbol, interval, str(limit)]
        if start_time is not None:
            parts.append(f"st{start_time}")
        if end_time is not None:
            parts.append(f"et{end_time}")
        return "_".join(parts)

    @staticmethod
    def _empty_klines_frame() -> pd.DataFrame:
        frame = pd.DataFrame(
            {
                "open": pd.Series(dtype=float),
                "high": pd.Series(dtype=float),
                "low": pd.Series(dtype=float),
                "close": pd.Series(dtype=float),
                "volume": pd.Series(dtype=float),
                "quote_volume": pd.Series(dtype=float),
            },
        )
        frame.index = pd.DatetimeIndex([], name="timestamp")
        return frame

    @staticmethod
    def _klines_to_frame(
        rows: list[dict[str, Any]],
        *,
        interval: str | None = None,
    ) -> pd.DataFrame:
        if not rows:
            return SoDEXFeeds._empty_klines_frame()
        data = [_kline_to_row(k) for k in rows]
        frame = pd.DataFrame(data)
        expected = [
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "quote_volume",
        ]
        for col in expected:
            if col not in frame.columns:
                frame[col] = 0
        numeric_cols = ["open", "high", "low", "close", "volume", "quote_volume"]
        for col in numeric_cols:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        if "timestamp" in frame.columns:
            frame["timestamp"] = pd.to_numeric(frame["timestamp"], errors="coerce")
            frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
            frame = frame.set_index("timestamp").sort_index()
            if not frame.empty and _interval_rounds_to_hour(interval):
                frame.index = cast(pd.DatetimeIndex, frame.index).round("1h")
        return frame

    async def _fetch_and_cache_json_list(
        self,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        cache_path: tuple[str, str],
        ttl_hours: float | None = None,
        skip_cache: bool = False,
    ) -> list[dict[str, Any]]:
        namespace, cache_key = cache_path
        if not skip_cache:
            cached = self.lake.latest_json(
                namespace,
                cache_key,
                max_age_hours=ttl_hours,
            )
            if cached is not None:
                return list(cached)
        try:
            method = getattr(self._client, endpoint)
            rows = await method(**params or {})
        except SoDEXUpstreamError:
            return []
        self.lake.write_json(namespace, cache_key, cast(list[dict[str, Any]], rows))
        return cast(list[dict[str, Any]], rows)

    async def fetch_symbols(self, *, skip_cache: bool = False) -> list[dict[str, Any]]:
        """Fetch all tradable perp symbols with metadata."""
        return await self._fetch_and_cache_json_list(
            "symbols",
            cache_path=("sodex_symbols", "all_symbols"),
            ttl_hours=self._symbols_cache_ttl_hours,
            skip_cache=skip_cache,
        )

    async def fetch_tickers(
        self,
        *,
        symbol: str | None = None,
        skip_cache: bool = False,
    ) -> list[dict[str, Any]]:
        """Fetch 24-hour ticker statistics."""
        cache_key = f"tickers_{symbol}" if symbol else "tickers_all"
        return await self._fetch_and_cache_json_list(
            "tickers",
            params={"symbol": symbol},
            cache_path=("sodex_tickers", cache_key),
            ttl_hours=self._tickers_cache_ttl_hours,
            skip_cache=skip_cache,
        )

    async def fetch_mark_prices(
        self,
        *,
        symbol: str | None = None,
        skip_cache: bool = False,
    ) -> list[dict[str, Any]]:
        """Fetch current mark prices, index prices, and funding rates."""
        cache_key = f"mark_prices_{symbol}" if symbol else "mark_prices_all"
        return await self._fetch_and_cache_json_list(
            "mark_prices",
            params={"symbol": symbol},
            cache_path=("sodex_mark_prices", cache_key),
            ttl_hours=self._mark_prices_cache_ttl_hours,
            skip_cache=skip_cache,
        )

    async def fetch_book_tickers(
        self,
        *,
        symbol: str | None = None,
        skip_cache: bool = False,
    ) -> list[dict[str, Any]]:
        """Fetch best bid/ask for perp symbols."""
        cache_key = f"book_tickers_{symbol}" if symbol else "book_tickers_all"
        return await self._fetch_and_cache_json_list(
            "book_tickers",
            params={"symbol": symbol},
            cache_path=("sodex_book_tickers", cache_key),
            ttl_hours=self._book_tickers_cache_ttl_hours,
            skip_cache=skip_cache,
        )

    async def fetch_orderbook(
        self,
        symbol: str,
        limit: int = 100,
        *,
        skip_cache: bool = False,
    ) -> dict[str, Any]:
        """Fetch order book depth for a perp symbol."""
        if not symbol or not symbol.strip():
            return {"bids": [], "asks": [], "symbol": symbol}
        cache_key = f"orderbook_{symbol}_{limit}"
        if not skip_cache:
            cached = self.lake.latest_json(
                "sodex_orderbook",
                cache_key,
                max_age_hours=self._orderbook_cache_ttl_hours,
            )
            if cached is not None:
                return dict(cached)
        try:
            data = await self._client.orderbook(symbol=symbol.strip(), limit=limit)
        except SoDEXUpstreamError:
            empty: dict[str, Any] = {"bids": [], "asks": [], "symbol": symbol}
            self.lake.write_json("sodex_orderbook", cache_key, empty)
            return empty
        result = dict(data)
        result["symbol"] = symbol
        self.lake.write_json("sodex_orderbook", cache_key, result)
        return result

    async def fetch_trades(
        self,
        symbol: str,
        limit: int = 100,
        *,
        skip_cache: bool = False,
    ) -> list[dict[str, Any]]:
        """Fetch recent trades for a perp symbol."""
        if not symbol or not symbol.strip():
            return []
        return await self._fetch_and_cache_json_list(
            "trades",
            params={"symbol": symbol.strip(), "limit": limit},
            cache_path=("sodex_trades", f"trades_{symbol}_{limit}"),
            ttl_hours=self._trades_cache_ttl_hours,
            skip_cache=skip_cache,
        )

    def metrics_snapshot(self) -> dict[str, Any]:
        """Return client-level metrics for the underlying HTTP client."""
        return self._client.metrics_snapshot()

