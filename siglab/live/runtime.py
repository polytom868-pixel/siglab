from __future__ import annotations

import json
import math
import uuid
from pathlib import Path
from typing import Any, cast

from siglab.data import MarketDataProvider, ParquetLake
from siglab.live.signal_compile import compile_spec
from siglab.risk.guardian import (
    CircuitBreakerState,
    check_concentration,
    compute_position_size,
)
from siglab.schemas import SignalSpec
from siglab.config import load_settings
from siglab.live.sodex_client import SoDEXSignedPerpsClient
from siglab.live.sodex_signing import (
    SoDEXNonceManager,
    SoDEXPrivateKeySigner,
    SoDEXSigner,
    perps_order_item,
    validate_account_id,
)

StatusDict = dict[str, Any]
StatusTuple = tuple[bool, str]


class Strategy:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.config = kwargs.get("config", {})
        self.name = kwargs.get("name", self.__class__.__name__)



class SoDEXExecutionAdapter:
    coin_to_asset: dict[str, int] = {}

    def __init__(self, config: dict[str, Any] | None = None, **kwargs: Any) -> None:
        self.config = dict(config or {})
        self.client = self.config.get("sodex_client")
        self.wallet_address = kwargs.get("wallet_address")
        self.coin_to_asset = dict(
            self.config.get("coin_to_asset")
            or getattr(self.client, "coin_to_asset", {})
            or {}
        )

    def setup(self) -> dict[str, Any]:
        """Wire a real ``SoDEXSignedPerpsClient`` from signing config.

        When signing credentials are present in ``config["sodex_signing"]`` and no
        client was pre-injected, construct a ``SoDEXSignedPerpsClient`` with a
        matching nonce manager and assign it to ``self.client``. When credentials
        are absent, leave the client unset so ``_require_client`` gates live use.
        Returns the post-setup ``dependency_report()``.
        """
        if self.client is not None:
            return self.dependency_report()

        signing = dict(self.config.get("sodex_signing") or {})
        api_key_name = signing.get("api_key_name") or self.config.get("sodex_api_key_name")
        account_id_raw = (
            signing.get("accountID")
            or signing.get("account_id")
            or self.config.get("sodex_account_id")
        )
        environment = signing.get("environment") or self.config.get("sodex_environment") or "testnet"
        nonce_store_path = signing.get("nonce_store_path") or self.config.get("sodex_nonce_store_path")
        private_key = signing.get("private_key") or self.config.get("sodex_private_key")
        signer: SoDEXSigner | None = signing.get("signer")
        if signer is None and private_key:
            signer = SoDEXPrivateKeySigner(private_key=private_key, environment=environment)

        has_credentials = bool(api_key_name) and account_id_raw is not None and signer is not None
        if not has_credentials:
            return self.dependency_report()

        account_id = validate_account_id(account_id_raw)
        store_path = Path(nonce_store_path) if nonce_store_path else None
        nonce_manager = SoDEXNonceManager(store_path=store_path, environment=environment)
        signing["signer"] = signer
        self.config["sodex_signing"] = signing
        self.client = SoDEXSignedPerpsClient(
            api_key_name=str(api_key_name),
            account_id=account_id,
            signer=signer,
            nonce_manager=nonce_manager,
            environment=str(environment),
            dry_run=bool(signing.get("dry_run", self.config.get("sodex_dry_run", True))),
        )
        if not self.coin_to_asset:
            self.coin_to_asset = dict(getattr(self.client, "coin_to_asset", {}) or {})
        return self.dependency_report()

    async def update_leverage(self, **kwargs: Any) -> None:
        method = self._resolve_client_method("update_leverage", fallback="update_leverage_request")
        await _await_if_needed(method(**kwargs))

    async def place_market_order(self, **kwargs: Any) -> tuple[bool, str]:
        client = self._require_client()
        asset_id = int(kwargs.get("asset_id", 0))
        is_buy = bool(kwargs.get("is_buy", True))
        size = float(kwargs.get("size", 0))
        reduce_only = bool(kwargs.get("reduce_only", False))
        order = perps_order_item(
            cl_ord_id=f"siglab_{uuid.uuid4().hex[:12]}",
            modifier=1,
            side=1 if is_buy else 2,
            order_type=2,
            time_in_force=1,
            quantity=str(size),
            reduce_only=reduce_only,
        )
        request = client.new_order_request(symbol_id=asset_id, orders=[order])
        result = await client.send_signed_request(request)
        if isinstance(result, dict):
            if result.get("dry_run"):
                return True, "dry-run market order submitted"
            ok = bool(result.get("ok", result.get("success", False)))
            return ok, str(result.get("message") or result.get("result") or result)
        return bool(result), str(result)

    async def get_state(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        ok, state = await self.get_user_state(*_args, **_kwargs)
        if not ok or not isinstance(state, dict):
            raise RuntimeError(f"Unable to fetch SoDEX state: {state}")
        return state

    async def get_user_state(self, *args: Any, **kwargs: Any) -> tuple[bool, Any]:
        method = self._resolve_client_method("get_user_state", fallback="get_state")
        result = await _await_if_needed(method(*args, **kwargs))
        if isinstance(result, tuple) and len(result) == 2:
            return bool(result[0]), result[1]
        return True, result

    async def all_mids(self) -> dict[str, float]:
        method = self._resolve_client_method("all_mids")
        result = await _await_if_needed(method())
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

    def _resolve_client_method(self, name: str, *, fallback: str | None = None) -> Any:
        client = self._require_client()
        method = getattr(client, name, None)
        if method is None and fallback is not None:
            method = getattr(client, fallback, None)
            missing = f"{name}() or {fallback}()"
        else:
            missing = f"{name}()"
        if method is None:
            raise RuntimeError(f"Configured SoDEX client does not provide {missing}")
        return method

    def dependency_report(self) -> dict[str, Any]:
        client = self.client
        signing = dict(self.config.get("sodex_signing") or {})
        api_key_name = signing.get("api_key_name") or self.config.get("sodex_api_key_name")
        account_id = signing.get("accountID") or signing.get("account_id") or self.config.get("sodex_account_id")
        environment = signing.get("environment") or self.config.get("sodex_environment") or "testnet"
        nonce_store = signing.get("nonce_store_path") or self.config.get("sodex_nonce_store_path")
        signer = signing.get("signer")
        required = {
            "get_user_state": bool(
                getattr(client, "get_user_state", None)
                or getattr(client, "get_state", None)
                or getattr(client, "account_state", None)
            ),
            "update_leverage": bool(
                getattr(client, "update_leverage", None)
                or getattr(client, "update_leverage_request", None)
            ),
            "place_market_order": bool(
                getattr(client, "new_order_request", None)
                and getattr(client, "send_signed_request", None)
            ),
            "all_mids": bool(
                getattr(client, "all_mids", None)
                or getattr(client, "mark_prices", None)
                or getattr(client, "tickers", None)
            ),
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
            "wallet_address_configured": bool(self.wallet_address),
            "dry_run": getattr(client, "dry_run", True) if client is not None else True,
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


async def _await_if_needed(result: Any) -> Any:
    """Await a value if it is awaitable; pass through otherwise."""
    if hasattr(result, "__await__"):
        return await result
    return result


class DirectionalPerpsSigLabStrategy(Strategy):
    SPEC_PATH: Path | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.live_spec: dict[str, Any] = {}
        self.spec: SignalSpec | None = None
        self.sodex_adapter: SoDEXExecutionAdapter | None = None
        self._circuit_breaker = CircuitBreakerState()

    def _get_strategy_wallet_address(self) -> str:
        runtime = dict(self.live_spec.get("runtime") or {})
        address = runtime.get("wallet_address") or runtime.get("address") or ""
        return str(address)

    def _adapter_dry_run(self) -> bool:
        """Read dry_run from the single source at SoDEXSignedPerpsClient."""
        if self.sodex_adapter is None or self.sodex_adapter.client is None:
            return True
        return bool(getattr(self.sodex_adapter.client, "dry_run", True))

    async def setup(self) -> None:
        self.live_spec = self._load_live_spec()
        self.spec = SignalSpec.from_dict(dict(self.live_spec["spec"]))
        self.name = str(self.live_spec.get("strategy_name") or self.spec.strategy_hash())
        self.sodex_adapter = SoDEXExecutionAdapter(
            self.config if isinstance(self.config, dict) else {},
            wallet_address=self._get_strategy_wallet_address(),
        )
        report = self.sodex_adapter.setup()
        if not self._adapter_dry_run():
            signed_path = report.get("signed_path") or {}
            if not report["client_configured"] or report["missing_methods"] or not signed_path.get("ready"):
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
        account_value = float(status.get("portfolio_value", 0.0))
        dry_run = self._adapter_dry_run()

        # ---- Circuit breaker state update ----
        self._circuit_breaker.equity = account_value
        if self._circuit_breaker.daily_start_equity <= 0.0:
            self._circuit_breaker.daily_start_equity = account_value
        if (
            self._circuit_breaker.peak_equity <= 0.0
            or account_value > self._circuit_breaker.peak_equity
        ):
            self._circuit_breaker.peak_equity = account_value

        # ---- Circuit breaker check (daily drawdown / consecutive losses) ----
        cb_passed, cb_reason = self._circuit_breaker.check_circuit_breakers()
        if not cb_passed:
            return False, f"Circuit breaker tripped: {cb_reason}"

        if not plan:
            return True, "No rebalance needed"
        if dry_run:
            return True, f"Dry run generated {len(plan)} rebalance orders"

        # ---- Drawdown stop (from peak) ----
        peak_dd = (
            (account_value - self._circuit_breaker.peak_equity)
            / self._circuit_breaker.peak_equity
            if self._circuit_breaker.peak_equity > 0
            else 0.0
        )
        dd_ratio = abs(peak_dd)
        if dd_ratio >= 0.20:
            return False, f"20% drawdown stop triggered: {peak_dd:.1%}"
        if dd_ratio >= 0.15:
            for order in plan:
                order["size"] = _finite_float(order["size"]) * 0.5
        elif dd_ratio >= 0.10:
            for order in plan:
                order["size"] = _finite_float(order["size"]) * 0.75

        adapter = self._require_sodex_adapter()
        address = self._get_strategy_wallet_address()
        runtime = dict(self.live_spec.get("runtime") or {})
        leverage = max(1, int(math.ceil(_finite_float(runtime.get("live_leverage"), 1.0))))
        executed = 0
        for order in plan:
            symbol = str(order["symbol"])
            # ---- Concentration check ----
            order_value = _finite_float(order.get("delta_usd", 0.0))
            if order_value > account_value * self._circuit_breaker.max_position_pct:
                continue  # skip orders exceeding concentration limit
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

        if self._adapter_dry_run():
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
                circuit_breaker=self._circuit_breaker,
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
                "dry_run": self._adapter_dry_run(),
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
        circuit_breaker: CircuitBreakerState | None = None,
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

            # ---- Risk: position sizing cap via compute_position_size() ----
            if circuit_breaker is not None and account_value > 0.0:
                max_pos_frac = compute_position_size(
                    risk_budget=circuit_breaker.max_risk_per_trade_pct,
                    volatility=0.05,
                    max_size=circuit_breaker.max_position_pct,
                )
                max_pos_value = account_value * max_pos_frac
                max_qty = max_pos_value / mid
                total_qty_after = current_qty + delta_qty
                if abs(total_qty_after) > max_qty:
                    if current_qty == 0.0:
                        sign = 1 if delta_qty >= 0 else -1
                        delta_qty = sign * max_qty
                    else:
                        direction = 1 if delta_qty >= 0 else -1
                        delta_qty = direction * max(0.0, max_qty - abs(current_qty))
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



