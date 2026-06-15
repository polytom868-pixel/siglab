from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, cast

from siglab.data import MarketDataProvider, ParquetLake
from siglab.evaluator.compile import compile_spec
from siglab.schemas import SignalSpec
from siglab.config import load_settings

StatusDict = dict[str, Any]
StatusTuple = tuple[bool, str]


class Strategy:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.config = kwargs.get("config", {})
        self.name = kwargs.get("name", self.__class__.__name__)

    async def strategy_wallet_signing_callback(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("SoDEX signing is not configured in this build")


class SoDEXExecutionAdapter:
    coin_to_asset: dict[str, int] = {}

    def __init__(self, config: dict[str, Any] | None = None, **kwargs: Any) -> None:
        self.config = dict(config or {})
        self.client = self.config.get("sodex_client")
        self.sign_callback = kwargs.get("sign_callback")
        self.wallet_address = kwargs.get("wallet_address")
        self.coin_to_asset = dict(
            self.config.get("coin_to_asset")
            or getattr(self.client, "coin_to_asset", {})
            or {}
        )

    async def update_leverage(self, **kwargs: Any) -> None:
        client = self._require_client()
        method = getattr(client, "update_leverage", None)
        if method is None:
            raise RuntimeError("Configured SoDEX client does not provide update_leverage()")
        result = method(**kwargs)
        if hasattr(result, "__await__"):
            await result

    async def place_market_order(self, **kwargs: Any) -> tuple[bool, str]:
        client = self._require_client()
        method = getattr(client, "place_market_order", None)
        if method is None:
            raise RuntimeError("Configured SoDEX client does not provide place_market_order()")
        result = method(**kwargs)
        if hasattr(result, "__await__"):
            result = await result
        if isinstance(result, tuple) and len(result) == 2:
            return bool(result[0]), str(result[1])
        if isinstance(result, dict):
            ok = bool(result.get("ok", result.get("success", False)))
            return ok, str(result.get("message") or result.get("result") or result)
        return bool(result), str(result)

    async def get_state(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        ok, state = await self.get_user_state(*_args, **_kwargs)
        if not ok or not isinstance(state, dict):
            raise RuntimeError(f"Unable to fetch SoDEX state: {state}")
        return state

    async def get_user_state(self, *args: Any, **kwargs: Any) -> tuple[bool, Any]:
        client = self._require_client()
        method = getattr(client, "get_user_state", None) or getattr(client, "get_state", None)
        if method is None:
            raise RuntimeError("Configured SoDEX client does not provide get_user_state() or get_state()")
        result = method(*args, **kwargs)
        if hasattr(result, "__await__"):
            result = await result
        if isinstance(result, tuple) and len(result) == 2:
            return bool(result[0]), result[1]
        return True, result

    async def all_mids(self) -> dict[str, float]:
        client = self._require_client()
        method = getattr(client, "all_mids", None)
        if method is None:
            raise RuntimeError("Configured SoDEX client does not provide all_mids()")
        result = method()
        if hasattr(result, "__await__"):
            result = await result
        return {str(symbol).upper(): float(price) for symbol, price in dict(result or {}).items()}

    def get_valid_order_size(self, _asset_id: int, size: float) -> float:
        method = getattr(self.client, "get_valid_order_size", None) if self.client is not None else None
        if method is None:
            return float(size)
        return float(method(_asset_id, size))

    def _require_client(self) -> Any:
        if self.client is None:
            raise RuntimeError("A real SoDEX client must be provided in runtime config before live execution")
        return self.client

    def dependency_report(self) -> dict[str, Any]:
        client = self.client
        signing = dict(self.config.get("sodex_signing") or {})
        api_key_name = signing.get("api_key_name") or self.config.get("sodex_api_key_name")
        account_id = signing.get("accountID") or signing.get("account_id") or self.config.get("sodex_account_id")
        environment = signing.get("environment") or self.config.get("sodex_environment") or "mainnet"
        nonce_store = signing.get("nonce_store_path") or self.config.get("sodex_nonce_store_path")
        signer = signing.get("signer") or self.sign_callback
        required = {
            "get_user_state": bool(getattr(client, "get_user_state", None) or getattr(client, "get_state", None)),
            "update_leverage": bool(getattr(client, "update_leverage", None)),
            "place_market_order": bool(getattr(client, "place_market_order", None)),
            "all_mids": bool(getattr(client, "all_mids", None)),
        }
        signed_ready = all(
            [
                client is not None,
                signer is not None,
                bool(api_key_name),
                account_id is not None,
                bool(nonce_store),
                not [name for name, present in required.items() if not present],
            ]
        )
        missing_signed: list[str] = []
        if signer is None:
            missing_signed.append("signer")
        if not api_key_name:
            missing_signed.append("api_key_name")
        if account_id is None:
            missing_signed.append("accountID")
        if not nonce_store:
            missing_signed.append("nonce_store_path")
        missing_signed.extend([f"client.{name}" for name, present in required.items() if not present])
        return {
            "client_configured": client is not None,
            "required_methods": required,
            "missing_methods": [name for name, present in required.items() if not present],
            "sign_callback_configured": self.sign_callback is not None,
            "wallet_address_configured": bool(self.wallet_address),
            "signed_path": {
                "ready": signed_ready,
                "environment": str(environment),
                "signer_available": signer is not None,
                "signer_type": getattr(signer, "signer_type", None) if signer is not None else None,
                "accountID_present": account_id is not None,
                "api_key_name_present": bool(api_key_name),
                "nonce_store_ready": bool(nonce_store),
                "missing_prerequisites": missing_signed,
            },
            "rate_limit_scope": {
                "budget_per_minute": 1200,
                "scope": "per_ip",
                "local_scheduler_only": True,
                "operator_warning": (
                    "SigLab's built-in SoDEX weight scheduler is process-local. "
                    "Use an external shared limiter when multiple processes share one egress IP."
                ),
            },
        }


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


class DirectionalPerpsSigLabStrategy(Strategy):
    SPEC_PATH: Path | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.live_spec: dict[str, Any] = {}
        self.spec: SignalSpec | None = None
        self.sodex_adapter: SoDEXExecutionAdapter | None = None

    def _get_strategy_wallet_address(self) -> str:
        runtime = dict(self.live_spec.get("runtime") or {})
        address = runtime.get("wallet_address") or runtime.get("address") or ""
        return str(address)

    async def setup(self) -> None:
        self.live_spec = self._load_live_spec()
        self.spec = SignalSpec.from_dict(dict(self.live_spec["spec"]))
        self.name = str(self.live_spec.get("strategy_name") or self.spec.strategy_hash())
        self.sodex_adapter = SoDEXExecutionAdapter(
            self.config if isinstance(self.config, dict) else {},
            sign_callback=self.strategy_wallet_signing_callback,
            wallet_address=self._get_strategy_wallet_address(),
        )
        runtime = dict(self.live_spec.get("runtime") or {})
        if not bool(runtime.get("dry_run", True)):
            report = self.sodex_adapter.dependency_report()
            if not report["client_configured"] or report["missing_methods"]:
                raise RuntimeError(f"Live SoDEX dependencies are incomplete: {report}")

    async def deposit(self, **kwargs: Any) -> StatusTuple:
        return (
            False,
            "Deposit is not automated for siglab-generated perp strategies. "
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

        adapter = self._require_sodex_adapter()
        address = self._get_strategy_wallet_address()
        leverage = max(1, int(math.ceil(_finite_float(runtime.get("live_leverage"), 1.0))))
        executed = 0
        for order in plan:
            symbol = str(order["symbol"])
            asset_id = adapter.coin_to_asset.get(symbol)
            if asset_id is None:
                raise ValueError(f"SoDEX asset id not found for {symbol}")
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

        adapter = self._require_sodex_adapter()
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

    async def dependency_report(self) -> dict[str, Any]:
        if self.sodex_adapter is None:
            await self.setup()
        adapter_report = self._require_sodex_adapter().dependency_report()
        return {
            "strategy_name": self.name,
            "spec_hash": self.live_spec.get("spec_hash"),
            "runtime": dict(self.live_spec.get("runtime") or {}),
            "sodex_adapter": adapter_report,
        }

    async def _build_live_status(self, *, include_trade_plan: bool) -> StatusDict:
        if self.spec is None:
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
                "spec_hash": self.live_spec.get("spec_hash"),
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
        spec = self._require_spec()
        runtime = dict(self.live_spec.get("runtime") or {})
        provider = MarketDataProvider(
            settings,
            ParquetLake(settings.data_lake_dir),
            config_path=runtime.get("sosovalue_config_path"),
        )
        try:
            compiled = await compile_spec(settings, provider, spec)
            prices = compiled.prices.sort_index()
            if len(prices.index) > 1:
                prices = prices.iloc[:-1]
            if prices.empty:
                raise ValueError("No live price history available for deployd strategy")
            targets = (
                compiled.target_positions.reindex(prices.index)
                .ffill()
                .fillna(0.0)
                .shift(1)
                .fillna(0.0)
            )
            if targets.empty:
                raise ValueError("No target history available for deployd strategy")
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
        adapter = self._require_sodex_adapter()
        ok, state = await adapter.get_user_state(address)
        if not ok or not isinstance(state, dict):
            raise ValueError(f"Unable to fetch SoDEX state for {address}: {state}")
        return state

    async def _net_deposit(self, address: str, *, fallback: float) -> float:
        ledger_adapter = self.config.get("ledger_adapter") if isinstance(self.config, dict) else None
        if ledger_adapter is None:
            return fallback
        ok, net_deposit = await ledger_adapter.get_strategy_net_deposit(
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
        return cast(dict[str, Any], json.loads(spec_path.read_text()))

    def _spec_path(self) -> Path:
        if isinstance(self.SPEC_PATH, Path):
            return self.SPEC_PATH
        if isinstance(self.SPEC_PATH, str):
            return Path(self.SPEC_PATH)
        path = self.config.get("siglab_live_spec_path") if isinstance(self.config, dict) else None
        if path:
            return Path(str(path))
        raise ValueError("Generated siglab strategy is missing SPEC_PATH")

    def _require_sodex_adapter(self) -> SoDEXExecutionAdapter:
        if self.sodex_adapter is None:
            raise ValueError("SoDEX adapter not initialized")
        return self.sodex_adapter

    def _require_spec(self) -> SignalSpec:
        if self.spec is None:
            raise ValueError("Spec not initialized")
        return self.spec



