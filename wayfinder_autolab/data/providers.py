from __future__ import annotations

import asyncio
import copy
import json
import math
import re
from hashlib import sha256
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd

from wayfinder_autolab.data.lake import ParquetLake
from wayfinder_autolab.models import CandidateGraph, UniverseSpec
from wayfinder_autolab.settings import AutolabSettings
from wayfinder_autolab.track_registry import canonical_track_name
from wayfinder_paths.adapters.pendle_adapter import PendleAdapter
from wayfinder_paths.core.clients.DeltaLabClient import DeltaLabClient
from wayfinder_paths.core.config import CONFIG, load_config, set_config

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
    if column in frame.columns:
        return pd.to_numeric(frame[column], errors="coerce").fillna(default)
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


def _safe_float(value: Any, digits: int = 8) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return round(numeric, digits)


def _percentile_map(series: pd.Series, percentiles: list[float]) -> dict[str, float | None]:
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
    prices = _dedupe_time_index(prices)
    funding = _dedupe_time_index(funding)
    prices = prices.dropna(how="any")
    if prices.empty:
        raise ValueError("Perp bundle has no common non-null price coverage across requested symbols")
    funding = funding.reindex(prices.index).ffill().fillna(0.0).astype(float)
    return prices, funding


class MarketDataProvider:
    def __init__(
        self,
        settings: AutolabSettings,
        lake: ParquetLake,
        *,
        config_path: str | None = None,
    ) -> None:
        self.settings = settings
        self.lake = lake
        load_config(config_path or settings.wayfinder_config_path, require_exists=True)
        if settings.wayfinder_api_key_override:
            config = dict(CONFIG)
            system = dict(config.get("system") or {})
            system["api_key"] = settings.wayfinder_api_key_override
            config["system"] = system
            set_config(config)
        self.delta_lab = DeltaLabClient()
        self.pendle = PendleAdapter(timeout=30.0)
        self._active_bundle_id: str | None = None
        self._active_as_of: datetime | None = None
        self._bundle_cache: dict[str, Any] = {}
        self._warm_cache: dict[str, Any] = {}
        self._bundle_components: list[dict[str, Any]] = []
        self._bundle_manifest: dict[str, Any] = {}

    async def close(self) -> None:
        await self.pendle.close()
        await self.delta_lab.client.aclose()

    def begin_iteration_bundle(
        self,
        *,
        track: str,
        parent: CandidateGraph,
    ) -> dict[str, Any]:
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
        parent: CandidateGraph,
    ) -> dict[str, Any]:
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
        if track == "systematic_carry" and (
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
                    "return_7d": float(prices[symbol].pct_change(24 * 7).iloc[-1]),
                    "funding_72h_mean": float(funding[symbol].tail(72).mean()),
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

        if track == "systematic_carry":
            stable_universe = UniverseSpec(
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
                rotation_universe = UniverseSpec(
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
                lending_universe = UniverseSpec(
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
        preferred_symbols = _sanitize_perp_symbols(preferred_symbols)
        warm_key = self._warm_cache_key(
            "perp_symbols",
            preferred_symbols=preferred_symbols,
            limit=limit,
        )
        if warm_key in self._warm_cache:
            return list(self._warm_cache[warm_key])[:limit]
        payload = await self.delta_lab.get_basis_symbols(get_all=True)
        available = [
            str(row.get("symbol"))
            for row in payload.get("symbols") or []
            if row.get("symbol")
        ]

        if not available:
            raise ValueError("Delta Lab returned no perp basis symbols")

        if preferred_symbols:
            filtered = [symbol for symbol in preferred_symbols if symbol in available]
            if filtered:
                self._warm_cache[warm_key] = list(filtered)
                return filtered[:limit]

        majors = [
            symbol
            for symbol in ["BTC", "ETH", "SOL", "HYPE", "DOGE", "BNB", "XRP", "SUI"]
            if symbol in available
        ]
        resolved = (majors or available)[:limit]
        self._warm_cache[warm_key] = list(resolved)
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
        bundle = await self._fetch_perp_bundle_delta_lab(
            symbols=symbols,
            lookback_days=lookback_days,
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
        universe: UniverseSpec,
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        return await self.discover_pt_markets(
            universe,
            limit=limit,
            stable_only=True,
        )

    async def discover_pt_markets(
        self,
        universe: UniverseSpec,
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

        rows = await self.pendle.list_active_pt_yt_markets(
            chains=universe.chains or ["arbitrum", "base", "plasma", "hyperevm"],
            min_liquidity_usd=universe.min_liquidity_usd,
            min_volume_usd_24h=universe.min_volume_usd_24h,
            min_days_to_expiry=universe.min_days_to_expiry,
            sort_by="fixed_apy",
            descending=True,
        )

        filtered: list[dict[str, Any]] = []
        basis_groups = [str(group).upper() for group in (universe.basis_groups or [])]
        hedge_candidates = [
            symbol for symbol in basis_groups if symbol != "USD"
        ] or MAJOR_PERP_SYMBOLS
        for row in rows:
            market_name = str(row.get("marketName") or "")
            days = float(row.get("daysToExpiry") or 0.0)
            if days > universe.max_days_to_expiry:
                continue
            is_stable = STABLE_PT_PATTERN.search(market_name) is not None
            if stable_only and not is_stable:
                continue

            if basis_groups and not stable_only:
                if not any(
                    self._market_matches_group(market_name, group)
                    for group in basis_groups
                ):
                    continue

            enriched = dict(row)
            enriched["hedgeSymbol"] = self.market_hedge_symbol(
                enriched,
                preferred_symbols=hedge_candidates,
            )
            filtered.append(enriched)

        self.lake.write_json("pendle", cache_key, filtered)
        self._bundle_cache[bundle_cache_key] = list(filtered)
        self._persist_bundle_json(bundle_cache_key, filtered)
        return filtered[:limit]

    async def fetch_pt_histories(
        self,
        markets: list[dict[str, Any]],
        *,
        lookback_days: int,
    ) -> dict[str, pd.DataFrame]:
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

            end = self._active_as_of or datetime.now(UTC)
            start = end - timedelta(days=lookback_days)
            payload = await self.pendle.fetch_market_history(
                int(row["chainId"]),
                str(row["marketAddress"]),
                time_frame="day",
                timestamp_start=start.isoformat().replace("+00:00", "Z"),
                timestamp_end=end.isoformat().replace("+00:00", "Z"),
                fields="ptPrice,impliedApy,underlyingApy,tvl,totalTvl,lpPrice,syPrice",
            )
            result_rows = payload.get("results") or []
            df = pd.DataFrame(result_rows)
            if df.empty:
                return label, df
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(
                None
            )
            df = df.set_index("timestamp").sort_index()
            self.lake.write_frame("pendle_history", label, df)
            return label, df

        pairs = await asyncio.gather(*[_fetch_one(row) for row in markets])
        histories = {label: frame for label, frame in pairs if not frame.empty}
        self._bundle_cache[bundle_cache_key] = {key: value.copy() for key, value in histories.items()}
        for label, frame in histories.items():
            self._persist_bundle_frames(f"{bundle_cache_key}__{label}", prices=frame)
        return histories

    async def discover_lending_markets(
        self,
        universe: UniverseSpec,
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
        bundle_cache_key = self._bundle_cache_key(
            "lending_bundle",
            markets=[self.lending_market_label(row) for row in markets],
            lookback_days=lookback_days,
        )
        if bundle_cache_key in self._bundle_cache:
            return copy.deepcopy(self._bundle_cache[bundle_cache_key])
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
                payload = await self.delta_lab.get_asset_timeseries(
                    symbol=basis_symbol,
                    lookback_days=lookback_days,
                    limit=10_000,
                    as_of=self._active_as_of,
                    series="price,lending",
                )
            except Exception:
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
        name = str(row.get("marketName") or "pt")
        compact_name = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")
        return f"{compact_name}_{row.get('chainId')}"

    def lending_market_label(self, row: dict[str, Any]) -> str:
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

    async def _fetch_perp_bundle_delta_lab(
        self,
        *,
        symbols: list[str],
        lookback_days: int,
    ) -> dict[str, Any]:
        all_prices: list[pd.Series] = []
        all_funding: list[pd.Series] = []
        as_of = self._active_as_of or datetime.now(UTC)

        for symbol in symbols:
            payload = await self.delta_lab.get_asset_timeseries(
                symbol=symbol,
                lookback_days=lookback_days,
                limit=10_000,
                as_of=as_of,
                series="price,funding",
            )
            price_df = payload.get("price")
            funding_df = payload.get("funding")
            if price_df is None or funding_df is None:
                raise ValueError(f"Delta Lab did not return price and funding for {symbol}")

            price_df = price_df.copy()
            funding_df = funding_df.copy()
            price_df.index = pd.to_datetime(price_df.index, utc=True).tz_convert(None)
            funding_df.index = pd.to_datetime(funding_df.index, utc=True).tz_convert(
                None
            )

            all_prices.append(price_df["price_usd"].rename(symbol))
            all_funding.append(funding_df["funding_rate"].rename(symbol))

        prices, funding = _align_perp_bundle_frames(
            pd.concat(all_prices, axis=1),
            pd.concat(all_funding, axis=1),
        )
        self.lake.write_frame("delta_lab", "perp_prices", prices)
        self.lake.write_frame("delta_lab", "perp_funding", funding)
        return {
            "prices": prices,
            "funding": funding,
            "source": "delta_lab",
            "bundle_as_of": as_of.isoformat(),
            "bundle_id": self._active_bundle_id,
        }

    def _bundle_cache_key(self, kind: str, **params: Any) -> str:
        if self._active_bundle_id is None:
            base = {"kind": kind, **params}
        else:
            base = {"bundle_id": self._active_bundle_id, "kind": kind, **params}
        return sha256(jsonable_dict(base).encode("utf-8")).hexdigest()[:20]

    def _warm_cache_key(self, kind: str, **params: Any) -> str:
        base = {"kind": kind, **params}
        return sha256(jsonable_dict(base).encode("utf-8")).hexdigest()[:20]

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

    def _persist_bundle_json(self, cache_key: str, payload: Any) -> None:
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
    return json.dumps(
        {
            "track": track,
            "parent_hash": parent_hash,
            "as_of": as_of.isoformat(),
        },
        sort_keys=True,
    )


def jsonable_dict(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, default=str)
