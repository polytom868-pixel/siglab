from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from wayfinder_autolab.data import MarketDataProvider, ParquetLake
from wayfinder_autolab.evaluator.compile import compile_candidate
from wayfinder_autolab.models import CandidateGraph
from wayfinder_autolab.settings import load_settings
from wayfinder_paths.adapters.hyperliquid_adapter.adapter import HyperliquidAdapter
from wayfinder_paths.core.strategies.Strategy import StatusDict, StatusTuple, Strategy


def _finite_float(value: Any, default: float = 0.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    return numeric if math.isfinite(numeric) else default


def _compact_weights(payload: dict[str, float], *, epsilon: float = 1e-9) -> dict[str, float]:
    return {
        str(symbol): round(float(weight), 6)
        for symbol, weight in payload.items()
        if abs(float(weight)) > epsilon
    }


class DirectionalPerpsAutolabStrategy(Strategy):
    SPEC_PATH: Path | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.live_spec: dict[str, Any] = {}
        self.candidate: CandidateGraph | None = None
        self.hyperliquid_adapter: HyperliquidAdapter | None = None

    async def setup(self) -> None:
        self.live_spec = self._load_live_spec()
        self.candidate = CandidateGraph.from_dict(dict(self.live_spec["candidate"]))
        self.name = str(self.live_spec.get("strategy_name") or self.candidate.strategy_hash())
        self.hyperliquid_adapter = HyperliquidAdapter(
            self.config if isinstance(self.config, dict) else {},
            sign_callback=self.strategy_wallet_signing_callback,
            wallet_address=self._get_strategy_wallet_address(),
        )

    async def deposit(self, **kwargs: Any) -> StatusTuple:
        return (
            False,
            "Deposit is not automated for autolab-generated perp strategies. "
            "Fund the configured strategy wallet / venue, then run update().",
        )

    async def update(self) -> StatusTuple:
        status = await self._build_live_status(include_trade_plan=True)
        strategy_status = dict(status["strategy_status"])
        plan = list(strategy_status.get("trade_plan") or [])
        runtime = dict(self.live_spec.get("runtime") or {})
        dry_run = bool(runtime.get("dry_run", True))
        if not plan:
            return True, "No rebalance needed"
        if dry_run:
            return True, f"Dry run generated {len(plan)} rebalance orders"

        adapter = self._require_hyperliquid_adapter()
        address = self._get_strategy_wallet_address()
        leverage = max(1, int(math.ceil(_finite_float(runtime.get("live_leverage"), 1.0))))
        executed = 0
        for order in plan:
            symbol = str(order["symbol"])
            asset_id = adapter.coin_to_asset.get(symbol)
            if asset_id is None:
                raise ValueError(f"Hyperliquid asset id not found for {symbol}")
            await adapter.update_leverage(
                asset_id=asset_id,
                leverage=leverage,
                is_cross=True,
                address=address,
            )
            ok, result = await adapter.place_market_order(
                asset_id=asset_id,
                is_buy=bool(order["is_buy"]),
                slippage=_finite_float(runtime.get("slippage"), 0.0035),
                size=adapter.get_valid_order_size(asset_id, _finite_float(order["size"])),
                address=address,
                reduce_only=bool(order.get("reduce_only", False)),
            )
            if not ok:
                return False, f"{symbol} order failed: {result}"
            executed += 1
        return True, f"Executed {executed} rebalance orders"

    async def withdraw(self, **kwargs: Any) -> StatusTuple:
        status = await self._build_live_status(include_trade_plan=False)
        current_positions = dict(status["strategy_status"].get("current_positions") or {})
        if not current_positions:
            return True, "No open perp positions to close"

        runtime = dict(self.live_spec.get("runtime") or {})
        if bool(runtime.get("dry_run", True)):
            return True, f"Dry run would close {len(current_positions)} perp positions"

        adapter = self._require_hyperliquid_adapter()
        address = self._get_strategy_wallet_address()
        closed = 0
        for symbol, qty in current_positions.items():
            asset_id = adapter.coin_to_asset.get(symbol)
            if asset_id is None:
                continue
            size = adapter.get_valid_order_size(asset_id, abs(_finite_float(qty)))
            if size <= 0:
                continue
            ok, result = await adapter.place_market_order(
                asset_id=asset_id,
                is_buy=_finite_float(qty) < 0.0,
                slippage=_finite_float(runtime.get("slippage"), 0.0035),
                size=size,
                address=address,
                reduce_only=True,
            )
            if not ok:
                return False, f"{symbol} close failed: {result}"
            closed += 1
        return True, f"Closed {closed} perp positions"

    async def exit(self, **kwargs: Any) -> StatusTuple:
        return await self.withdraw(**kwargs)

    @staticmethod
    async def policies() -> list[str]:
        return []

    async def _status(self) -> StatusDict:
        return await self._build_live_status(include_trade_plan=True)

    async def _build_live_status(self, *, include_trade_plan: bool) -> StatusDict:
        if self.candidate is None:
            await self.setup()

        address = self._get_strategy_wallet_address()
        target_snapshot = await self._latest_target_snapshot()
        state = await self._load_user_state(address)
        current_positions = self._extract_perp_positions(state)
        account_value = self._account_value(state)
        net_deposit = await self._net_deposit(address, fallback=account_value)
        runtime = dict(self.live_spec.get("runtime") or {})
        leverage = _finite_float(runtime.get("live_leverage"), 1.0)
        trade_plan = (
            self._build_trade_plan(
                target_weights=dict(target_snapshot["target_weights"]),
                current_positions=current_positions,
                mids=dict(target_snapshot["mid_prices"]),
                account_value=account_value,
                leverage=leverage,
                min_trade_usd=_finite_float(runtime.get("min_trade_usd"), 25.0),
            )
            if include_trade_plan
            else []
        )
        return {
            "portfolio_value": account_value,
            "net_deposit": net_deposit,
            "gas_available": 0.0,
            "gassed_up": True,
            "strategy_status": {
                "candidate_hash": self.live_spec.get("candidate_hash"),
                "strategy_name": self.live_spec.get("strategy_name"),
                "family": self.live_spec.get("family"),
                "source": target_snapshot.get("source"),
                "bundle_as_of": target_snapshot.get("bundle_as_of"),
                "latest_signal_timestamp": target_snapshot.get("timestamp"),
                "dry_run": bool(runtime.get("dry_run", True)),
                "current_account_value": round(account_value, 6),
                "target_weights": _compact_weights(
                    dict(target_snapshot["target_weights"])
                ),
                "current_positions": _compact_weights(current_positions),
                "trade_plan": trade_plan,
                "compiled_metadata": target_snapshot.get("compiled_metadata"),
            },
        }

    async def _latest_target_snapshot(self) -> dict[str, Any]:
        settings = load_settings()
        settings.ensure_runtime_directories()
        candidate = self._require_candidate()
        runtime = dict(self.live_spec.get("runtime") or {})
        provider = MarketDataProvider(
            settings,
            ParquetLake(settings.data_lake_dir),
            config_path=runtime.get("wayfinder_config_path"),
        )
        try:
            compiled = await compile_candidate(settings, provider, candidate)
            prices = compiled.prices.sort_index()
            if len(prices.index) > 1:
                prices = prices.iloc[:-1]
            if prices.empty:
                raise ValueError("No live price history available for promoted strategy")
            targets = (
                compiled.target_positions.reindex(prices.index)
                .ffill()
                .fillna(0.0)
                .shift(1)
                .fillna(0.0)
            )
            if targets.empty:
                raise ValueError("No target history available for promoted strategy")
            timestamp = prices.index[-1]
            return {
                "timestamp": timestamp.isoformat(),
                "target_weights": {
                    str(symbol): float(weight)
                    for symbol, weight in targets.iloc[-1].to_dict().items()
                },
                "mid_prices": {
                    str(symbol): float(price)
                    for symbol, price in prices.iloc[-1].to_dict().items()
                },
                "source": compiled.metadata.get("source"),
                "bundle_as_of": compiled.metadata.get("bundle_as_of"),
                "compiled_metadata": compiled.metadata,
            }
        finally:
            await provider.close()

    async def _load_user_state(self, address: str) -> dict[str, Any]:
        adapter = self._require_hyperliquid_adapter()
        ok, state = await adapter.get_user_state(address)
        if not ok or not isinstance(state, dict):
            raise ValueError(f"Unable to fetch Hyperliquid state for {address}: {state}")
        return state

    async def _net_deposit(self, address: str, *, fallback: float) -> float:
        ok, net_deposit = await self.ledger_adapter.get_strategy_net_deposit(
            wallet_address=address
        )
        return _finite_float(net_deposit, fallback) if ok else fallback

    def _account_value(self, state: dict[str, Any]) -> float:
        cross_value = _finite_float(
            (state.get("crossMarginSummary") or {}).get("accountValue"),
            0.0,
        )
        margin_value = _finite_float(
            (state.get("marginSummary") or {}).get("accountValue"),
            0.0,
        )
        return max(cross_value, margin_value, 0.0)

    def _extract_perp_positions(self, state: dict[str, Any]) -> dict[str, float]:
        positions: dict[str, float] = {}
        for wrapper in state.get("assetPositions", []) or []:
            position = wrapper.get("position") or {}
            coin = str(position.get("coin") or "").strip().upper()
            if not coin:
                continue
            positions[coin] = _finite_float(position.get("szi"), 0.0)
        return positions

    def _build_trade_plan(
        self,
        *,
        target_weights: dict[str, float],
        current_positions: dict[str, float],
        mids: dict[str, float],
        account_value: float,
        leverage: float,
        min_trade_usd: float,
    ) -> list[dict[str, Any]]:
        plan: list[dict[str, Any]] = []
        symbols = sorted(set(target_weights) | set(current_positions))
        for symbol in symbols:
            mid = _finite_float(mids.get(symbol), 0.0)
            if mid <= 0:
                continue
            target_notional = _finite_float(target_weights.get(symbol), 0.0) * account_value * leverage
            target_qty = target_notional / mid
            current_qty = _finite_float(current_positions.get(symbol), 0.0)
            delta_qty = target_qty - current_qty
            delta_usd = abs(delta_qty) * mid
            if delta_usd < min_trade_usd:
                continue
            plan.append(
                {
                    "symbol": symbol,
                    "target_weight": round(_finite_float(target_weights.get(symbol), 0.0), 6),
                    "target_qty": round(target_qty, 8),
                    "current_qty": round(current_qty, 8),
                    "delta_qty": round(delta_qty, 8),
                    "delta_usd": round(delta_usd, 4),
                    "size": abs(delta_qty),
                    "is_buy": delta_qty > 0.0,
                    "reduce_only": False,
                }
            )
        return plan

    def _load_live_spec(self) -> dict[str, Any]:
        spec_path = self._spec_path()
        if not spec_path.exists():
            raise FileNotFoundError(f"Live spec not found: {spec_path}")
        return json.loads(spec_path.read_text())

    def _spec_path(self) -> Path:
        if isinstance(self.SPEC_PATH, Path):
            return self.SPEC_PATH
        if isinstance(self.SPEC_PATH, str):
            return Path(self.SPEC_PATH)
        path = self.config.get("autolab_live_spec_path") if isinstance(self.config, dict) else None
        if path:
            return Path(str(path))
        raise ValueError("Generated autolab strategy is missing SPEC_PATH")

    def _require_hyperliquid_adapter(self) -> HyperliquidAdapter:
        if self.hyperliquid_adapter is None:
            raise ValueError("Hyperliquid adapter not initialized")
        return self.hyperliquid_adapter

    def _require_candidate(self) -> CandidateGraph:
        if self.candidate is None:
            raise ValueError("Candidate not initialized")
        return self.candidate
