"""FastAPI HTTP client for the SigLab TUI.

Connects to the FastAPI dashboard running on port 3100.
Supports both HTTP REST and WebSocket connections.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Awaitable

import httpx

logger = logging.getLogger(__name__)

# Type for WebSocket message callbacks
WsCallback = Callable[[dict[str, Any]], Awaitable[None]]


class TuiApiClient:
    """Async HTTP client for the SigLab FastAPI dashboard.

    Args:
        base_url: Base URL of the FastAPI dashboard (default http://localhost:3100).
        timeout: Request timeout in seconds (default 10).
    """

    def __init__(
        self, base_url: str = "http://localhost:3100", timeout: float = 10.0
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None
        self._timeout = timeout

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
            )
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client session."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get_health(self) -> dict[str, Any]:
        """Fetch the /health endpoint.

        Returns:
            Dict with status, version, uptime_seconds fields.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = await self._ensure_client()
        response = await client.get("/health")
        response.raise_for_status()
        return response.json()

    async def get_config(self) -> dict[str, Any]:
        """Fetch the /config endpoint.

        Returns:
            Dict with system, sosovalue, claude configuration.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = await self._ensure_client()
        response = await client.get("/config")
        response.raise_for_status()
        return response.json()

    async def get_ops_board(self) -> dict[str, Any]:
        """Fetch the /ops-board endpoint.

        Returns:
            Dict with artifact_status, summary, and service_health.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = await self._ensure_client()
        response = await client.get("/ops-board")
        response.raise_for_status()
        return response.json()

    async def get_evidence_graph(self) -> dict[str, Any]:
        """Fetch the /evidence-graph endpoint.

        Returns:
            Dict with nodes and edges arrays.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = await self._ensure_client()
        response = await client.get("/evidence-graph")
        response.raise_for_status()
        return response.json()

    async def get_skill_report(self) -> dict[str, Any]:
        """Fetch the /skill-report endpoint.

        Returns:
            Dict with per-skill metrics.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = await self._ensure_client()
        response = await client.get("/skill-report")
        response.raise_for_status()
        return response.json()

    async def get_risk(self) -> dict[str, Any]:
        """Fetch the /risk endpoint.

        Returns:
            Dict with composite_score, max_drawdown, correlation_matrix.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = await self._ensure_client()
        response = await client.get("/risk")
        response.raise_for_status()
        return response.json()

    # ── Strategy Research ──────────────────────────────────────────────

    async def get_strategies(
        self,
        track: str | None = None,
        family: str | None = None,
    ) -> dict[str, Any]:
        """Fetch strategy list from the ancestry/experiment database.

        Args:
            track: Optional track filter.
            family: Optional family filter.

        Returns:
            Dict with 'strategies' list and 'count'.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = await self._ensure_client()
        params: dict[str, str] = {}
        if track:
            params["track"] = track
        if family:
            params["family"] = family
        response = await client.get("/strategies", params=params)
        response.raise_for_status()
        return response.json()

    async def get_strategy_detail(self, spec_hash: str) -> dict[str, Any]:
        """Fetch detailed results for a single strategy.

        Args:
            spec_hash: The strategy's spec hash.

        Returns:
            Dict with spec, summary, equity_curve, etc.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = await self._ensure_client()
        response = await client.get(f"/strategies/{spec_hash}")
        response.raise_for_status()
        return response.json()

    async def get_benchmark_status(self, deck: str = "trend_signals_external") -> dict[str, Any]:
        """Fetch benchmark deck status.

        Args:
            deck: Benchmark deck name.

        Returns:
            Dict with state, recent_results.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = await self._ensure_client()
        response = await client.get("/benchmark/status", params={"deck": deck})
        response.raise_for_status()
        return response.json()

    async def get_benchmark_results(self, deck: str = "trend_signals_external") -> dict[str, Any]:
        """Fetch benchmark evaluation results.

        Args:
            deck: Benchmark deck name.

        Returns:
            Dict with results list.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = await self._ensure_client()
        response = await client.get("/benchmark/results", params={"deck": deck})
        response.raise_for_status()
        return response.json()

    # ── Market Data ──────────────────────────────────────────────────

    async def get_market_symbols(self) -> dict[str, Any]:
        """Fetch all tradable SoDEX perp symbols.

        Returns:
            Dict with 'symbols' list and 'count'.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = await self._ensure_client()
        response = await client.get("/market/symbols")
        response.raise_for_status()
        return response.json()

    async def get_market_tickers(self) -> dict[str, Any]:
        """Fetch 24-hour ticker data for all perp symbols.

        Returns:
            Dict with 'tickers' list and 'count'.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = await self._ensure_client()
        response = await client.get("/market/tickers")
        response.raise_for_status()
        return response.json()

    async def get_market_klines(
        self, symbol: str, interval: str = "1h", limit: int = 60
    ) -> dict[str, Any]:
        """Fetch kline/candlestick data for a perp symbol.

        Args:
            symbol: Perp symbol (e.g. "BTC-USD").
            interval: Kline interval (1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w, 1M).
            limit: Maximum number of candles.

        Returns:
            Dict with 'klines' list, 'symbol', 'interval', 'count'.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = await self._ensure_client()
        response = await client.get(
            f"/market/klines/{symbol}",
            params={"interval": interval, "limit": limit},
        )
        response.raise_for_status()
        return response.json()

    async def get_market_orderbook(
        self, symbol: str, limit: int = 20
    ) -> dict[str, Any]:
        """Fetch order book depth for a perp symbol.

        Args:
            symbol: Perp symbol (e.g. "BTC-USD").
            limit: Number of price levels per side.

        Returns:
            Dict with 'bids', 'asks', 'symbol'.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = await self._ensure_client()
        response = await client.get(
            f"/market/orderbook/{symbol}",
            params={"limit": limit},
        )
        response.raise_for_status()
        return response.json()

    # ── Paper Trading ────────────────────────────────────────────────

    async def list_paper_sessions(self) -> dict[str, Any]:
        """List all paper trading sessions.

        Returns:
            Dict with 'sessions' list.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = await self._ensure_client()
        response = await client.get("/paper/sessions")
        response.raise_for_status()
        return response.json()

    async def create_paper_session(self, name: str | None = None) -> dict[str, Any]:
        """Create a new paper trading session.

        Args:
            name: Optional session label.

        Returns:
            Dict with 'session_id' and 'name'.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = await self._ensure_client()
        body: dict[str, Any] = {}
        if name:
            body["name"] = name
        response = await client.post("/paper/sessions", json=body)
        response.raise_for_status()
        return response.json()

    async def get_paper_session(self, session_id: str) -> dict[str, Any]:
        """Get paper trading session status.

        Args:
            session_id: The session ID.

        Returns:
            Dict with session_id, name, position, pnl, orders.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = await self._ensure_client()
        response = await client.get(f"/paper/sessions/{session_id}")
        response.raise_for_status()
        return response.json()

    async def get_paper_positions(self, session_id: str) -> dict[str, Any]:
        """Get positions for a paper trading session.

        Args:
            session_id: The session ID.

        Returns:
            Dict with 'positions' list.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = await self._ensure_client()
        response = await client.get(f"/paper/sessions/{session_id}/positions")
        response.raise_for_status()
        return response.json()

    async def get_paper_orders(self, session_id: str) -> dict[str, Any]:
        """Get orders for a paper trading session.

        Args:
            session_id: The session ID.

        Returns:
            Dict with 'orders' list.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = await self._ensure_client()
        response = await client.get(f"/paper/sessions/{session_id}/orders")
        response.raise_for_status()
        return response.json()

    async def get_paper_pnl(self, session_id: str) -> dict[str, Any]:
        """Get PnL summary for a paper trading session.

        Args:
            session_id: The session ID.

        Returns:
            Dict with realized_pnl, unrealized_pnl, total_pnl, etc.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = await self._ensure_client()
        response = await client.get(f"/paper/sessions/{session_id}/pnl")
        response.raise_for_status()
        return response.json()

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
        """Place a paper order.

        Args:
            session_id: Target session ID.
            symbol: Perp symbol (e.g. "BTC-USD").
            side: "BUY" or "SELL".
            quantity: Order quantity.
            order_type: "MARKET" or "LIMIT".
            price: Limit price (required for LIMIT orders).

        Returns:
            Dict with order details.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = await self._ensure_client()
        body: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "order_type": order_type,
        }
        if price is not None:
            body["price"] = price
        response = await client.post(
            f"/paper/sessions/{session_id}/orders", json=body
        )
        response.raise_for_status()
        return response.json()

    async def cancel_paper_order(
        self, session_id: str, order_id: str
    ) -> dict[str, Any]:
        """Cancel a paper order.

        Args:
            session_id: Target session ID.
            order_id: Order ID to cancel.

        Returns:
            Dict with updated order details.

        Raises:
            httpx.HTTPError: If the request fails.
        """
        client = await self._ensure_client()
        response = await client.delete(
            f"/paper/sessions/{session_id}/orders/{order_id}"
        )
        response.raise_for_status()
        return response.json()

    # ── WebSocket ────────────────────────────────────────────────────

    async def ws_connect(self) -> Any:
        """Connect to the WebSocket endpoint.

        Returns:
            A websockets client connection.

        Raises:
            Exception: If the connection fails.
        """
        import websockets

        ws_url = self._base_url.replace("http://", "ws://").replace(
            "https://", "wss://"
        )
        ws = await websockets.connect(f"{ws_url}/ws")
        return ws

    async def ws_subscribe_risk(self, callback: WsCallback) -> None:
        """Subscribe to risk_score updates via WebSocket.

        Connects, subscribes to risk_score, and calls callback for each
        incoming risk_score message. Runs until cancelled.

        Args:
            callback: Async function called with each risk_score message dict.
        """
        try:
            ws = await self.ws_connect()
            try:
                # Subscribe to risk_score
                await ws.send(json.dumps({
                    "action": "subscribe",
                    "subscription_type": "risk_score",
                }))

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
                await ws.close()
        except Exception as exc:
            logger.debug("WS risk subscription failed: %s", exc)
