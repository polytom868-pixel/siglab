"""FastAPI HTTP client for the SigLab TUI."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, cast
from collections.abc import Awaitable, Callable

import httpx

logger = logging.getLogger(__name__)
WsCallback = Callable[[dict[str, Any]], Awaitable[None]]


class TuiApiClient:
    """Async HTTP client for the SigLab FastAPI dashboard."""

    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        timeout: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None
        self._timeout = timeout

    async def __aenter__(self) -> TuiApiClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
            )
        return self._client

    async def _request_with_retry(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Single retry with 0.5s backoff on transient errors."""
        client = await self._ensure_client()
        try:
            return await self._do_request(client, method, path, **kwargs)
        except (
            httpx.ConnectError,
            httpx.TimeoutException,
            httpx.HTTPStatusError,
        ) as exc:
            if (
                isinstance(exc, httpx.HTTPStatusError)
                and exc.response.status_code < 500
            ):
                raise
            logger.warning(
                "Request %s %s failed (%s), retrying in 0.5s",
                method,
                path,
                exc,
            )
            await asyncio.sleep(0.5)
            client = await self._ensure_client()
            return await self._do_request(client, method, path, **kwargs)

    @staticmethod
    async def _do_request(
        client: httpx.AsyncClient,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        response = await getattr(client, method)(path, **kwargs)
        response.raise_for_status()
        return cast(dict[str, Any], response.json())

    async def _get(self, path: str, **kwargs: Any) -> dict[str, Any]:
        """GET request with retry. Thin wrapper around _request_with_retry."""
        return await self._request_with_retry("get", path, **kwargs)

    async def _post(self, path: str, **kwargs: Any) -> dict[str, Any]:
        """POST request with retry. Thin wrapper around _request_with_retry."""
        return await self._request_with_retry("post", path, **kwargs)

    async def close(self) -> None:
        """Close the underlying HTTP client session."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get_health(self) -> dict[str, Any]:
        """Fetch the /health endpoint."""
        return await self._get("/health")

    async def get_config(self) -> dict[str, Any]:
        """Fetch the /config endpoint."""
        return await self._get("/config")

    async def get_ops_board(self) -> dict[str, Any]:
        """Fetch the /ops-board endpoint."""
        return await self._get("/ops-board")

    async def get_evidence_graph(self) -> dict[str, Any]:
        """Fetch the /evidence-graph endpoint."""
        return await self._get("/evidence-graph")

    async def get_skill_report(self) -> dict[str, Any]:
        """Fetch the /skill-report endpoint."""
        return await self._get("/skill-report")

    async def get_telemetry_report(self) -> dict[str, Any]:
        """Fetch the /ops-board telemetry data."""
        return await self.get_ops_board()

    async def get_risk(self) -> dict[str, Any]:
        """Fetch the /risk endpoint."""
        return await self._get("/risk")

    async def get_strategies(
        self,
        track: str | None = None,
        family: str | None = None,
    ) -> dict[str, Any]:
        """Fetch strategy list from the ancestry/experiment database."""
        params: dict[str, str] = {}
        if track:
            params["track"] = track
        if family:
            params["family"] = family
        return await self._get("/strategies", params=params)

    async def get_strategy_detail(self, spec_hash: str) -> dict[str, Any]:
        """Fetch detailed results for a single strategy."""
        return await self._get(f"/strategies/{spec_hash}")

    async def get_benchmark_status(
        self,
        deck: str = "trend_signals_external",
    ) -> dict[str, Any]:
        """Fetch benchmark deck status."""
        return await self._get("/benchmark/status", params={"deck": deck})

    async def get_benchmark_results(
        self,
        deck: str = "trend_signals_external",
    ) -> dict[str, Any]:
        """Fetch benchmark evaluation results."""
        return await self._get("/benchmark/results", params={"deck": deck})

    async def get_market_symbols(self) -> dict[str, Any]:
        """Fetch all tradable SoDEX perp symbols."""
        return await self._get("/market/symbols")

    async def get_market_tickers(self) -> dict[str, Any]:
        """Fetch 24-hour ticker data for all perp symbols."""
        return await self._get("/market/tickers")

    async def get_market_klines(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 60,
    ) -> dict[str, Any]:
        """Fetch kline/candlestick data for a perp symbol."""
        return await self._get(
            f"/market/klines/{symbol}",
            params={"interval": interval, "limit": limit},
        )

    async def get_market_orderbook(
        self,
        symbol: str,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Fetch order book depth for a perp symbol."""
        return await self._get(f"/market/orderbook/{symbol}", params={"limit": limit})

    async def list_paper_sessions(self) -> dict[str, Any]:
        """List all paper trading sessions."""
        return await self._get("/paper/sessions")

    async def create_paper_session(self, name: str | None = None) -> dict[str, Any]:
        """Create a new paper trading session."""
        body: dict[str, Any] = {}
        if name:
            body["name"] = name
        return await self._post("/paper/sessions", json=body)

    async def get_paper_session(self, session_id: str) -> dict[str, Any]:
        """Get paper trading session status."""
        return await self._get(f"/paper/sessions/{session_id}")

    async def get_paper_positions(self, session_id: str) -> dict[str, Any]:
        """Get positions for a paper trading session."""
        return await self._get(f"/paper/sessions/{session_id}/positions")

    async def get_paper_orders(self, session_id: str) -> dict[str, Any]:
        """Get orders for a paper trading session."""
        return await self._get(f"/paper/sessions/{session_id}/orders")

    async def get_paper_pnl(self, session_id: str) -> dict[str, Any]:
        """Get PnL summary for a paper trading session."""
        return await self._get(f"/paper/sessions/{session_id}/pnl")

    async def place_paper_order(
        self,
        session_id: str,
        *,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "MARKET",
        price: float | None = None,
    ) -> dict[str, Any]:
        """Place a paper order."""
        body: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "order_type": order_type,
        }
        if price is not None:
            body["price"] = price
        return await self._post(f"/paper/sessions/{session_id}/orders", json=body)

    async def cancel_paper_order(
        self,
        session_id: str,
        order_id: str,
    ) -> dict[str, Any]:
        """Cancel a paper order."""
        return await self._request_with_retry(
            "delete",
            f"/paper/sessions/{session_id}/orders/{order_id}",
        )

    async def ws_connect(self) -> Any:
        """Connect to the WebSocket endpoint."""
        import websockets

        ws_url = self._base_url.replace("http://", "ws://").replace(
            "https://",
            "wss://",
        )
        ws = await websockets.connect(f"{ws_url}/ws")
        return ws

    async def ws_subscribe_risk(self, callback: WsCallback) -> None:
        """Subscribe to risk_score updates via WebSocket."""
        try:
            ws = await self.ws_connect()
            try:
                await ws.send(
                    json.dumps(
                        {"action": "subscribe", "subscription_type": "risk_score"},
                    ),
                )
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    msg_type = msg.get("type", "")
                    if msg_type == "risk_score":
                        await callback(msg)
                    elif msg_type == "subscribed":
                        logger.debug("WS subscribed: %s", msg)
                    elif msg_type == "ping":
                        await ws.send(json.dumps({"action": "pong"}))
            finally:
                try:
                    await ws.close()
                except (OSError, ValueError) as close_exc:
                    logger.debug("WS close after failure: %s", close_exc)
        except (httpx.HTTPError, OSError, ValueError) as exc:
            logger.warning("WS risk subscription failed: %s", exc)
            raise
