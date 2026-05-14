from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol


class SoDEXWebSocketError(RuntimeError):
    pass


class SoDEXWebSocketConfigError(SoDEXWebSocketError):
    pass


class SoDEXWebSocketFormatError(SoDEXWebSocketError):
    pass


class SoDEXWebSocketTimeoutError(SoDEXWebSocketError):
    pass


class SoDEXWebSocketDisconnected(SoDEXWebSocketError):
    pass


class WebSocketConnection(Protocol):
    async def send(self, message: str) -> None:
        ...

    async def recv(self) -> str:
        ...

    async def close(self) -> None:
        ...


ConnectFactory = Callable[[str], Awaitable[WebSocketConnection]]


@dataclass
class SoDEXWebSocketMetrics:
    connections: int = 0
    reconnects: int = 0
    subscriptions: int = 0
    pings: int = 0
    pongs: int = 0
    messages: int = 0
    malformed_messages: int = 0
    disconnects: int = 0
    latencies_ms: list[float] = field(default_factory=list)


SODEX_WS_ENDPOINTS = {
    ("mainnet", "spot"): "wss://mainnet-gw.sodex.dev/ws/spot",
    ("mainnet", "perps"): "wss://mainnet-gw.sodex.dev/ws/perps",
    ("testnet", "spot"): "wss://testnet-gw.sodex.dev/ws/spot",
    ("testnet", "perps"): "wss://testnet-gw.sodex.dev/ws/perps",
}

SODEX_WS_CHANNELS = {
    "ticker",
    "allTicker",
    "miniTicker",
    "allMiniTicker",
    "bookTicker",
    "allBookTicker",
    "markPrice",
    "allMarkPrice",
    "l2Book",
    "l4Book",
    "candle",
    "trade",
    "accountFrontendState",
    "accountUpdate",
    "accountOrder",
    "accountTrade",
    "accountEvent",
}

SODEX_WS_ACCOUNT_CHANNELS = {
    "accountFrontendState",
    "accountUpdate",
    "accountOrder",
    "accountTrade",
    "accountEvent",
}

SODEX_WS_SYMBOL_CHANNELS = {
    "ticker",
    "miniTicker",
    "bookTicker",
    "markPrice",
    "l2Book",
    "l4Book",
    "candle",
    "trade",
}

_EVM_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


class SoDEXWebSocketClient:
    def __init__(
        self,
        *,
        environment: str = "mainnet",
        market: str = "perps",
        connect: ConnectFactory | None = None,
        idle_timeout_s: float = 45.0,
        pong_timeout_s: float = 10.0,
        max_reconnects: int = 2,
    ) -> None:
        self.environment = _validate_choice(environment, {"mainnet", "testnet"}, "environment")
        self.market = _validate_choice(market, {"spot", "perps"}, "market")
        self.url = SODEX_WS_ENDPOINTS[(self.environment, self.market)]
        self.idle_timeout_s = float(idle_timeout_s)
        self.pong_timeout_s = float(pong_timeout_s)
        self.max_reconnects = int(max_reconnects)
        self._connect = connect
        self._connection: WebSocketConnection | None = None
        self.metrics = SoDEXWebSocketMetrics()

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    async def connect(self) -> WebSocketConnection:
        if self._connection is None:
            started = time.perf_counter()
            if self._connect is None:
                import websockets

                self._connection = await websockets.connect(self.url)  # type: ignore[assignment]
            else:
                self._connection = await self._connect(self.url)
            self.metrics.connections += 1
            self.metrics.latencies_ms.append((time.perf_counter() - started) * 1000.0)
        return self._connection

    async def subscribe(self, params: dict[str, Any], *, request_id: int | None = None) -> dict[str, Any]:
        _validate_subscription_params(params)
        conn = await self.connect()
        request: dict[str, Any] = {"op": "subscribe", "params": dict(params)}
        if request_id is not None:
            request["id"] = int(request_id)
        await conn.send(json.dumps(request, separators=(",", ":"), ensure_ascii=True))
        ack = await self._recv_json(timeout_s=self.idle_timeout_s)
        self._validate_ack(ack, expected_op="subscribe")
        self.metrics.subscriptions += 1
        return ack

    async def unsubscribe(self, params: dict[str, Any], *, request_id: int | None = None) -> dict[str, Any]:
        _validate_subscription_params(params)
        conn = await self.connect()
        request: dict[str, Any] = {"op": "unsubscribe", "params": dict(params)}
        if request_id is not None:
            request["id"] = int(request_id)
        await conn.send(json.dumps(request, separators=(",", ":"), ensure_ascii=True))
        ack = await self._recv_json(timeout_s=self.idle_timeout_s)
        self._validate_ack(ack, expected_op="unsubscribe")
        return ack

    async def ping(self) -> dict[str, Any]:
        conn = await self.connect()
        await conn.send('{"op":"ping"}')
        self.metrics.pings += 1
        payload = await self._recv_json(timeout_s=self.pong_timeout_s)
        if payload.get("op") != "pong":
            raise SoDEXWebSocketFormatError("SoDEX WebSocket ping did not return pong")
        self.metrics.pongs += 1
        return payload

    async def recv_update(self, *, timeout_s: float | None = None) -> dict[str, Any]:
        payload = await self._recv_json(timeout_s=timeout_s or self.idle_timeout_s)
        if payload.get("op") in {"subscribe", "unsubscribe", "pong"}:
            return payload
        if not isinstance(payload.get("channel"), str) or "type" not in payload:
            raise SoDEXWebSocketFormatError("SoDEX WebSocket update missing channel/type")
        return payload

    async def keepalive_once(self) -> dict[str, Any]:
        try:
            return await self.recv_update(timeout_s=self.idle_timeout_s)
        except SoDEXWebSocketTimeoutError:
            return await self.ping()

    async def reconnect(self) -> None:
        await self.close()
        self.metrics.reconnects += 1
        if self.metrics.reconnects > self.max_reconnects:
            raise SoDEXWebSocketDisconnected("SoDEX WebSocket reconnect budget exhausted")
        await self.connect()

    def snapshot(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "environment": self.environment,
            "market": self.market,
            "metrics": {
                "connections": self.metrics.connections,
                "reconnects": self.metrics.reconnects,
                "subscriptions": self.metrics.subscriptions,
                "pings": self.metrics.pings,
                "pongs": self.metrics.pongs,
                "messages": self.metrics.messages,
                "malformed_messages": self.metrics.malformed_messages,
                "disconnects": self.metrics.disconnects,
            },
        }

    async def _recv_json(self, *, timeout_s: float) -> dict[str, Any]:
        conn = await self.connect()
        try:
            raw = await asyncio.wait_for(conn.recv(), timeout=timeout_s)
        except asyncio.TimeoutError as exc:
            raise SoDEXWebSocketTimeoutError("SoDEX WebSocket receive timed out") from exc
        except Exception as exc:
            self.metrics.disconnects += 1
            raise SoDEXWebSocketDisconnected(f"SoDEX WebSocket disconnected: {exc}") from exc
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            self.metrics.malformed_messages += 1
            raise SoDEXWebSocketFormatError("SoDEX WebSocket returned malformed JSON") from exc
        if not isinstance(payload, dict):
            self.metrics.malformed_messages += 1
            raise SoDEXWebSocketFormatError("SoDEX WebSocket message was not an object")
        self.metrics.messages += 1
        return payload

    def _validate_ack(self, payload: dict[str, Any], *, expected_op: str) -> None:
        if payload.get("op") != expected_op:
            raise SoDEXWebSocketFormatError(f"SoDEX WebSocket ack op was not {expected_op}")
        if payload.get("success") is not True:
            raise SoDEXWebSocketFormatError(f"SoDEX WebSocket {expected_op} failed: {payload.get('error')}")
        if "result" not in payload or "connID" not in payload:
            raise SoDEXWebSocketFormatError("SoDEX WebSocket ack missing result/connID")


def _validate_subscription_params(params: dict[str, Any]) -> None:
    if not isinstance(params, dict):
        raise SoDEXWebSocketConfigError("SoDEX WebSocket subscription params must be an object")
    channel = str(params.get("channel") or "").strip()
    if channel not in SODEX_WS_CHANNELS:
        raise SoDEXWebSocketConfigError(f"Unsupported SoDEX WebSocket channel: {channel}")
    if channel in SODEX_WS_ACCOUNT_CHANNELS:
        user = str(params.get("user") or "").strip()
        if not _EVM_ADDRESS_RE.match(user):
            raise SoDEXWebSocketConfigError(
                f"SoDEX account WebSocket channel {channel} requires user as an EVM address"
            )
        if "accountID" in params:
            try:
                account_id = int(params["accountID"])
            except (TypeError, ValueError) as exc:
                raise SoDEXWebSocketConfigError("SoDEX account WebSocket accountID must be an unsigned integer") from exc
            if account_id < 0:
                raise SoDEXWebSocketConfigError("SoDEX account WebSocket accountID must be an unsigned integer")
    if channel in SODEX_WS_SYMBOL_CHANNELS and not str(params.get("symbol") or "").strip():
        raise SoDEXWebSocketConfigError(f"SoDEX WebSocket channel {channel} requires symbol")


def _validate_choice(value: str, allowed: set[str], field: str) -> str:
    text = str(value or "").strip().lower()
    if text not in allowed:
        raise SoDEXWebSocketConfigError(f"SoDEX WebSocket {field} must be one of {sorted(allowed)}")
    return text
