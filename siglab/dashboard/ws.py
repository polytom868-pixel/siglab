from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()

# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint for streaming klines, ticks, and positions.

    Supports:
    - Ping/pong for keepalive
    - Subscription to symbols for kline/position data
    - JSON message format with 'action', 'symbol', 'type' fields
    """
    await websocket.accept()
    state = websocket.app.state.dashboard
    manager = state.ws_manager
    manager.register(websocket)

    subscribed_symbols: set[str] = set()
    subscription_types: set[str] = set()

    try:
        # Send welcome message
        await _send_json(websocket, {
            "type": "connected",
            "message": "SigLab WebSocket connected",
            "timestamp": datetime.now(UTC).isoformat(),
        })

        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                # Send a periodic keepalive ping
                try:
                    await _send_json(websocket, {"type": "ping", "timestamp": datetime.now(UTC).isoformat()})
                except Exception:
                    break
                continue

            if not raw.strip():
                continue

            try:
                message = json.loads(raw)
            except (json.JSONDecodeError, TypeError, ValueError):
                await _send_json(websocket, {
                    "type": "error",
                    "message": "Invalid JSON payload",
                })
                continue

            await _handle_message(
                websocket, message, manager, subscribed_symbols, subscription_types,
            )

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        manager.unregister(websocket)
        for symbol in list(subscribed_symbols):
            manager.unsubscribe(symbol, websocket)


async def _handle_message(
    websocket: WebSocket,
    message: dict[str, Any],
    manager: Any,
    subscribed_symbols: set[str],
    subscription_types: set[str],
) -> None:
    """Process an incoming WebSocket message."""
    action = str(message.get("action") or message.get("type") or "").strip().lower()

    if action in ("ping", "pong"):
        await _send_json(websocket, {
            "type": "pong" if action == "ping" else "pong",
            "timestamp": datetime.now(UTC).isoformat(),
        })
        return

    if action == "subscribe":
        symbol = str(message.get("symbol") or "").strip().upper()
        sub_type = str(message.get("subscription_type") or "klines").strip().lower()

        if not symbol:
            await _send_json(websocket, {
                "type": "error",
                "message": "Missing 'symbol' field for subscribe",
            })
            return

        subscribed_symbols.add(symbol)
        subscription_types.add(sub_type)
        manager.subscribe(symbol, websocket)

        await _send_json(websocket, {
            "type": "subscribed",
            "symbol": symbol,
            "subscription_type": sub_type,
            "message": f"Subscribed to {sub_type} for {symbol}",
        })

        # Stream initial data for the subscribed symbol
        await _stream_initial_data(websocket, symbol, sub_type)
        return

    if action == "unsubscribe":
        symbol = str(message.get("symbol") or "").strip().upper()
        if symbol:
            subscribed_symbols.discard(symbol)
            manager.unsubscribe(symbol, websocket)
        await _send_json(websocket, {
            "type": "unsubscribed",
            "symbol": symbol if symbol else "all",
        })
        return

    if action == "get_positions":
        # Return current paper trading positions if available
        await _stream_positions(websocket)
        return

    await _send_json(websocket, {
        "type": "error",
        "message": f"Unknown action: {action}. Supported: ping, subscribe, unsubscribe, get_positions",
    })


async def _stream_initial_data(
    websocket: WebSocket,
    symbol: str,
    sub_type: str,
) -> None:
    """Stream a snapshot of initial data for a subscribed symbol."""
    if sub_type == "klines":
        # Return a placeholder kline snapshot
        # In production, this would be fetched from SoDEXFeeds
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        timestamp = int(now.timestamp() * 1000)
        await _send_json(websocket, {
            "type": "klines",
            "symbol": symbol,
            "data": [
                {
                    "timestamp": timestamp - 3600000 * i,
                    "open": 0.0,
                    "high": 0.0,
                    "low": 0.0,
                    "close": 0.0,
                    "volume": 0.0,
                    "quote_volume": 0.0,
                }
                for i in range(5)
            ],
            "interval": "1h",
        })
    elif sub_type in ("ticks", "ticker"):
        await _send_json(websocket, {
            "type": "ticker",
            "symbol": symbol,
            "bid": 0.0,
            "ask": 0.0,
            "last_price": 0.0,
            "timestamp": datetime.now(UTC).isoformat(),
        })
    elif sub_type == "positions":
        await _stream_positions(websocket)


async def _stream_positions(websocket: WebSocket) -> None:
    """Stream current paper trading positions."""
    try:
        state = websocket.app.state.dashboard
        config = state.config
        if config is None:
            await _send_json(websocket, {
                "type": "positions",
                "positions": [],
                "note": "Config not loaded",
            })
            return

        sessions_dir = config.live_dir / "paper_sessions"
        if not sessions_dir.exists():
            await _send_json(websocket, {
                "type": "positions",
                "positions": [],
                "note": "No paper sessions found",
            })
            return

        positions_list: list[dict[str, Any]] = []
        for npy_file in sorted(sessions_dir.glob("*.npy")):
            try:
                session_id = npy_file.stem
                positions_list.append({
                    "session_id": session_id,
                    "symbol": "unknown",
                    "size": 0.0,
                    "entry_price": 0.0,
                    "current_price": 0.0,
                    "unrealized_pnl": 0.0,
                })
            except Exception:
                continue

        await _send_json(websocket, {
            "type": "positions",
            "positions": positions_list,
        })

    except ImportError:
        await _send_json(websocket, {
            "type": "positions",
            "positions": [],
            "note": "Paper trading not available",
        })
    except Exception as exc:
        await _send_json(websocket, {
            "type": "positions",
            "positions": [],
            "note": f"Error: {exc}",
        })


async def _send_json(websocket: WebSocket, data: dict[str, Any]) -> None:
    """Send a JSON message over a WebSocket connection."""
    try:
        await websocket.send_json(data)
    except Exception:
        pass
