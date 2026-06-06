from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from siglab.data.sodex_rate_limit import SoDEXWeightScheduler


class SoDEXError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, payload: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class SoDEXRateLimitError(SoDEXError):
    """SoDEX rejected the request due to rate limits."""


class SoDEXTransportError(SoDEXError):
    """Network, DNS, TLS, timeout, or socket failure."""


class SoDEXUpstreamError(SoDEXError):
    """SoDEX returned a non-success HTTP or business response."""


class SoDEXFormatError(SoDEXError):
    """SoDEX returned a malformed response envelope."""


@dataclass
class _Metrics:
    latencies_ms: list[float] = field(default_factory=list)
    attempts: int = 0
    successes: int = 0
    retries: int = 0
    rate_limits: int = 0
    transport_failures: int = 0


class SoDEXPublicPerpsClient:
    def __init__(
        self,
        *,
        base_url: str = "https://mainnet-gw.sodex.dev/api/v1/perps",
        timeout_s: float = 10.0,
        retries: int = 1,
        client: httpx.AsyncClient | None = None,
        weight_scheduler: SoDEXWeightScheduler | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = float(timeout_s)
        self.retries = max(0, int(retries))
        self._client = client
        self._owns_client = client is None
        self._metrics: dict[str, _Metrics] = {}
        self.weight_scheduler = weight_scheduler or SoDEXWeightScheduler()

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def symbols(self, *, symbol: str | None = None) -> list[dict[str, Any]]:
        params = {"symbol": symbol} if symbol else None
        return self._rows(
            await self._request(
                "GET",
                "/markets/symbols",
                endpoint="perps.symbols",
                params=params,
                weight=SoDEXWeightScheduler.documented_weight("perps.symbols"),
            ),
            "perps.symbols",
        )

    async def coins(self, *, coin: str | None = None) -> list[dict[str, Any]]:
        params = {"coin": coin} if coin else None
        return self._rows(
            await self._request(
                "GET",
                "/markets/coins",
                endpoint="perps.coins",
                params=params,
                weight=SoDEXWeightScheduler.documented_weight("perps.coins"),
            ),
            "perps.coins",
        )

    async def tickers(self, *, symbol: str | None = None) -> list[dict[str, Any]]:
        params = {"symbol": symbol} if symbol else None
        return self._rows(
            await self._request(
                "GET",
                "/markets/tickers",
                endpoint="perps.tickers",
                params=params,
                weight=SoDEXWeightScheduler.documented_weight("perps.tickers"),
            ),
            "perps.tickers",
        )

    async def mini_tickers(self, *, symbol: str | None = None) -> list[dict[str, Any]]:
        params = {"symbol": symbol} if symbol else None
        return self._rows(
            await self._request(
                "GET",
                "/markets/miniTickers",
                endpoint="perps.mini_tickers",
                params=params,
                weight=SoDEXWeightScheduler.documented_weight("perps.mini_tickers"),
            ),
            "perps.mini_tickers",
        )

    async def mark_prices(self, *, symbol: str | None = None) -> list[dict[str, Any]]:
        params = {"symbol": symbol} if symbol else None
        return self._rows(
            await self._request(
                "GET",
                "/markets/mark-prices",
                endpoint="perps.mark_prices",
                params=params,
                weight=SoDEXWeightScheduler.documented_weight("perps.mark_prices"),
            ),
            "perps.mark_prices",
        )

    async def book_tickers(self, *, symbol: str | None = None) -> list[dict[str, Any]]:
        params = {"symbol": symbol} if symbol else None
        return self._rows(
            await self._request(
                "GET",
                "/markets/bookTickers",
                endpoint="perps.book_tickers",
                params=params,
                weight=SoDEXWeightScheduler.documented_weight("perps.book_tickers"),
            ),
            "perps.book_tickers",
        )

    async def orderbook(self, *, symbol: str, limit: int | None = None) -> dict[str, Any]:
        params = {"limit": int(limit)} if limit is not None else None
        payload = await self._request(
            "GET",
            f"/markets/{symbol}/orderbook",
            endpoint="perps.orderbook",
            params=params,
            weight=SoDEXWeightScheduler.documented_weight("perps.orderbook"),
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise SoDEXFormatError("perps.orderbook data was not an object", payload=payload)
        bids = data.get("bids", [])
        asks = data.get("asks", [])
        if bids and asks:
            best_bid = float(bids[0][0])
            best_ask = float(asks[0][0])
            mid = (best_bid + best_ask) / 2.0
            if mid > 0:
                spread_pct = (best_ask - best_bid) / mid
                if spread_pct > 0.05:
                    raise SoDEXFormatError(f"orderbook spread {spread_pct:.2%} exceeds 5% threshold")
        return dict(data)

    async def klines(
        self,
        *,
        symbol: str,
        interval: str,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"interval": interval}
        if start_time is not None:
            params["startTime"] = int(start_time)
        if end_time is not None:
            params["endTime"] = int(end_time)
        if limit is not None:
            params["limit"] = int(limit)
        return self._rows(
            await self._request(
                "GET",
                f"/markets/{symbol}/klines",
                endpoint="perps.klines",
                params=params,
                weight=SoDEXWeightScheduler.documented_weight("perps.klines"),
            ),
            "perps.klines",
        )

    async def trades(self, *, symbol: str, limit: int | None = None) -> list[dict[str, Any]]:
        params = {"limit": int(limit)} if limit is not None else None
        return self._rows(
            await self._request(
                "GET",
                f"/markets/{symbol}/trades",
                endpoint="perps.trades",
                params=params,
                weight=SoDEXWeightScheduler.documented_weight("perps.trades"),
            ),
            "perps.trades",
        )

    async def account_balances(self, *, user_address: str, account_id: int | None = None) -> dict[str, Any]:
        return await self._account_object(
            endpoint="perps.account_balances",
            path=f"/accounts/{_validate_evm_address(user_address)}/balances",
            account_id=account_id,
        )

    async def account_orders(
        self,
        *,
        user_address: str,
        symbol: str | None = None,
        account_id: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol
        if account_id is not None:
            params["accountID"] = int(account_id)
        payload = await self._request(
            "GET",
            f"/accounts/{_validate_evm_address(user_address)}/orders",
            endpoint="perps.account_orders",
            params=params or None,
            weight=SoDEXWeightScheduler.documented_weight("perps.account_orders"),
        )
        data = payload.get("data")
        if not isinstance(data, (dict, list)):
            raise SoDEXFormatError("perps.account_orders data was not an object or list", payload=payload)
        return {"data": data}

    async def account_positions(self, *, user_address: str, account_id: int | None = None) -> dict[str, Any]:
        return await self._account_object(
            endpoint="perps.account_positions",
            path=f"/accounts/{_validate_evm_address(user_address)}/positions",
            account_id=account_id,
        )

    async def account_state(self, *, user_address: str, account_id: int | None = None) -> dict[str, Any]:
        return await self._account_object(
            endpoint="perps.account_state",
            path=f"/accounts/{_validate_evm_address(user_address)}/state",
            account_id=account_id,
        )

    async def _account_object(self, *, endpoint: str, path: str, account_id: int | None = None) -> dict[str, Any]:
        params = {"accountID": int(account_id)} if account_id is not None else None
        payload = await self._request(
            "GET",
            path,
            endpoint=endpoint,
            params=params,
            weight=SoDEXWeightScheduler.documented_weight(endpoint),
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise SoDEXFormatError(f"{endpoint} data was not an object", payload=payload)
        return dict(data)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        endpoint: str,
        params: dict[str, Any] | None = None,
        weight: int = 20,
    ) -> dict[str, Any]:
        metrics = self._metrics_for(endpoint)
        last_error: SoDEXError | None = None
        await self.weight_scheduler.acquire(weight)
        for attempt in range(self.retries + 1):
            metrics.attempts += 1
            started = time.perf_counter()
            try:
                response = await self._http().request(
                    method,
                    f"{self.base_url}/{path.lstrip('/')}",
                    params=params,
                    headers={"Accept": "application/json"},
                    timeout=self.timeout_s,
                )
            except (httpx.HTTPError, OSError, TimeoutError) as exc:
                metrics.transport_failures += 1
                last_error = SoDEXTransportError(f"{endpoint} transport failure: {exc}")
            else:
                metrics.latencies_ms.append((time.perf_counter() - started) * 1000.0)
                status = int(response.status_code)
                if status == 429:
                    metrics.rate_limits += 1
                    last_error = SoDEXRateLimitError(f"{endpoint} rate limited", status_code=status)
                elif status >= 500 or status == 408:
                    last_error = SoDEXUpstreamError(f"{endpoint} retryable HTTP {status}", status_code=status)
                elif status >= 400:
                    raise SoDEXUpstreamError(f"{endpoint} HTTP {status}", status_code=status)
                else:
                    payload = self._checked_payload(response, endpoint)
                    metrics.successes += 1
                    return payload
            if attempt >= self.retries:
                break
            metrics.retries += 1
            await asyncio.sleep(0.25 * (2**attempt))
        raise last_error or SoDEXTransportError(f"{endpoint} failed")

    def metrics_snapshot(self) -> dict[str, Any]:
        endpoints: dict[str, Any] = {}
        for name, metrics in self._metrics.items():
            latencies = sorted(metrics.latencies_ms)
            attempts = max(1, metrics.attempts)
            endpoints[name] = {
                "p50_ms": _percentile(latencies, 50),
                "p95_ms": _percentile(latencies, 95),
                "attempts": metrics.attempts,
                "success_rate": metrics.successes / attempts,
                "retry_count": metrics.retries,
                "429_count": metrics.rate_limits,
                "transport_failures": metrics.transport_failures,
            }
        return {"provider": "sodex", "endpoints": endpoints, "weight_scheduler": self.weight_scheduler.snapshot()}

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(limits=httpx.Limits(max_connections=8, max_keepalive_connections=4))
        return self._client

    def _parse_json(self, response: httpx.Response, endpoint: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise SoDEXFormatError(f"{endpoint} returned malformed JSON", status_code=response.status_code) from exc
        if not isinstance(payload, dict):
            raise SoDEXFormatError(f"{endpoint} response was not an object", status_code=response.status_code, payload=payload)
        if "code" not in payload or "timestamp" not in payload:
            raise SoDEXFormatError(f"{endpoint} response envelope missing code/timestamp", status_code=response.status_code, payload=payload)
        return payload

    def _checked_payload(self, response: httpx.Response, endpoint: str) -> dict[str, Any]:
        status = int(response.status_code)
        if status == 429:
            raise SoDEXRateLimitError(f"{endpoint} rate limited", status_code=status)
        if status >= 400:
            raise SoDEXUpstreamError(f"{endpoint} HTTP {status}", status_code=status)
        payload = self._parse_json(response, endpoint)
        code = payload.get("code")
        if code not in (0, "0"):
            raise SoDEXUpstreamError(
                f"{endpoint} business error: {payload.get('error') or payload.get('message') or code}",
                status_code=status,
                payload=payload,
            )
        return payload

    def _rows(self, payload: dict[str, Any], endpoint: str) -> list[dict[str, Any]]:
        data = payload.get("data")
        if not isinstance(data, list):
            raise SoDEXFormatError(f"{endpoint} data was not a list", payload=payload)
        return [dict(item) for item in data if isinstance(item, dict)]

    def _metrics_for(self, endpoint: str) -> _Metrics:
        if endpoint not in self._metrics:
            self._metrics[endpoint] = _Metrics()
        return self._metrics[endpoint]


def _validate_evm_address(value: str) -> str:
    text = str(value or "").strip()
    if len(text) != 42 or not text.startswith("0x"):
        raise SoDEXFormatError("SoDEX userAddress must be a 0x-prefixed EVM address")
    try:
        int(text[2:], 16)
    except ValueError as exc:
        raise SoDEXFormatError("SoDEX userAddress must contain only hex characters") from exc
    return text


def _percentile(values: list[float], percentile: int) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    n = len(ordered)
    rank = (percentile / 100.0) * (n - 1)
    lower_idx = max(0, min(n - 1, int(math.floor(rank))))
    upper_idx = max(0, min(n - 1, int(math.ceil(rank))))
    if lower_idx == upper_idx:
        return ordered[lower_idx]
    frac = rank - math.floor(rank)
    return ordered[lower_idx] + frac * (ordered[upper_idx] - ordered[lower_idx])


def _batch_order_weight(order_count: int) -> int:
    if order_count < 1:
        return 1
    return 1 + (int(order_count) // 40)
