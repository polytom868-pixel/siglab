"""WebSocket handlers for SigLab Dashboard."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from siglab.utils import _now_iso

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint for streaming klines, ticks, and positions."""
    await websocket.accept()
    state = websocket.app.state.dashboard
    manager = state.ws_manager
    manager.register(websocket)

    subscribed_symbols: set[str] = set()
    subscription_types: set[str] = set()
    risk_push_tasks: set[asyncio.Task[None]] = set()

    try:
        # Send welcome message
        await _send_json(websocket, {
            "type": "connected",
            "message": "SigLab WebSocket connected",
            "timestamp": _now_iso(),
        })

        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                try:
                    await _send_json(websocket, {"type": "ping", "timestamp": _now_iso()})
                except (OSError, ValueError):
                    logger.debug("WebSocket send error in ping keepalive")
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
                risk_push_tasks,
            )

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("WS error: %s", exc)
    finally:
        for task in risk_push_tasks:
            task.cancel()
        risk_push_tasks.clear()
        manager.unregister(websocket)
        for symbol in list(subscribed_symbols):
            manager.unsubscribe(symbol, websocket)


async def _periodic_risk_push(ws: WebSocket) -> None:
    """Push risk scores to a WebSocket client every 15 seconds."""
    try:
        while True:
            await asyncio.sleep(15)
            await _stream_risk_scores(ws)
    except (asyncio.CancelledError, Exception):
        pass


async def _handle_message(
    websocket: WebSocket,
    message: dict[str, Any],
    manager: Any,
    subscribed_symbols: set[str],
    subscription_types: set[str],
    risk_push_tasks: set[asyncio.Task[None]] | None = None,
) -> None:
    """Process an incoming WebSocket message."""
    action = str(message.get("action") or message.get("type") or "").strip().lower()

    if action in ("ping", "pong"):
        await _send_json(websocket, {
            "type": "pong" if action == "ping" else "pong",
            "timestamp": _now_iso(),
        })
        return

    if action == "subscribe":
        symbol = str(message.get("symbol") or "").strip().upper()
        sub_type = str(message.get("subscription_type") or "klines").strip().lower()

        if sub_type == "risk_score":
            subscribed_symbols.add("_risk")
            subscription_types.add("risk_score")
            manager.subscribe("_risk", websocket)
            await _send_json(websocket, {
                "type": "subscribed",
                "subscription_type": "risk_score",
                "message": "Subscribed to risk score updates",
            })
            await _stream_risk_scores(websocket)
            if risk_push_tasks is not None:
                task = asyncio.create_task(_periodic_risk_push(websocket))
                risk_push_tasks.add(task)
                task.add_done_callback(risk_push_tasks.discard)
            return

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

        await _stream_initial_data(websocket, symbol, sub_type)
        return

    if action == "unsubscribe":
        symbol = str(message.get("symbol") or "").strip().upper()
        if symbol:
            subscribed_symbols.discard(symbol)
            manager.unsubscribe(symbol, websocket)
        else:
            for sym in list(subscribed_symbols):
                manager.unsubscribe(sym, websocket)
            subscribed_symbols.clear()
        await _send_json(websocket, {
            "type": "unsubscribed",
            "symbol": symbol if symbol else "all",
        })
        return

    if action == "get_positions":
        await _stream_positions(websocket)
        return

    if action == "get_risk":
        await _stream_risk_scores(websocket)
        return

    await _send_json(websocket, {
        "type": "error",
        "message": f"Unknown action: {action}. Supported: ping, subscribe, unsubscribe, get_positions, get_risk",
    })


async def _stream_initial_data(
    websocket: WebSocket,
    symbol: str,
    sub_type: str,
) -> None:
    """Stream a snapshot of initial data for a subscribed symbol."""
    if sub_type == "klines":
        await _send_json(websocket, {
            "type": "klines",
            "symbol": symbol,
            "data": await _fetch_cached_klines(websocket, symbol),
            "interval": "1h",
        })
    elif sub_type in ("ticks", "ticker"):
        await _send_json(websocket, {
            "type": "ticker",
            "symbol": symbol,
            **await _fetch_cached_ticker(websocket, symbol),
            "timestamp": _now_iso(),
        })
    elif sub_type == "positions":
        await _stream_positions(websocket)


async def _fetch_cached_klines(websocket: WebSocket, symbol: str) -> list[dict[str, Any]]:
    """Fetch klines from SoDEXFeeds cache, falling back gracefully."""
    try:
        state = websocket.app.state.dashboard
        feeds = state.get_sodex_feeds()
        if feeds is not None:
            frame = await feeds.fetch_klines(symbol, "1h", limit=60)
            if not frame.empty:
                records = frame.reset_index().to_dict(orient="records")
                for rec in records:
                    ts = rec.get("timestamp")
                    if ts is not None and hasattr(ts, "isoformat"):
                        rec["timestamp"] = int(ts.timestamp() * 1000)
                    for col in ("open", "high", "low", "close", "volume", "quote_volume"):
                        val = rec.get(col)
                        if val is not None:
                            rec[col] = float(val)
                return records
    except Exception as exc:
        logger.warning("WS klines fetch error for %s: %s", symbol, exc)

    # Fallback: return a single placeholder candle
    now = datetime.now(UTC)
    timestamp = int(now.timestamp() * 1000)
    return [
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
    ]


async def _fetch_cached_ticker(websocket: WebSocket, symbol: str) -> dict[str, Any]:
    """Fetch ticker data from SoDEXFeeds cache, falling back gracefully."""
    try:
        state = websocket.app.state.dashboard
        feeds = state.get_sodex_feeds()
        if feeds is not None:
            tickers = await feeds.fetch_tickers(symbol=symbol)
            if tickers:
                t = tickers[0]
                return {
                    "bid": float(t.get("bidPrice", t.get("bid", 0.0))),
                    "ask": float(t.get("askPrice", t.get("ask", 0.0))),
                    "last_price": float(t.get("lastPrice", t.get("close", 0.0))),
                }
    except Exception as exc:
        logger.warning("WS ticker fetch error for %s: %s", symbol, exc)

    return {"bid": 0.0, "ask": 0.0, "last_price": 0.0}


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

        sessions_dir = config.root_dir / "sessions"
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
            except (OSError, ValueError, TypeError):
                logger.debug("Failed to read npy session file %s", npy_file)
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


async def _stream_risk_scores(websocket: WebSocket) -> None:
    """Stream current risk metrics to a WebSocket client."""
    from siglab.dashboard.risk_utils import compute_risk_metrics, empty_risk_response

    try:
        state = websocket.app.state.dashboard
        config = state.config
        if config is None:
            await _send_json(websocket, {
                "type": "risk_score",
                **empty_risk_response(),
                "note": "Config not loaded",
            })
            return

        sessions_dir = config.root_dir / "sessions"
        if not sessions_dir.exists():
            await _send_json(websocket, {
                "type": "risk_score",
                **empty_risk_response(),
                "note": "No paper sessions found",
            })
            return

        metrics = compute_risk_metrics(sessions_dir)
        await _send_json(websocket, {
            "type": "risk_score",
            **metrics,
            "timestamp": _now_iso(),
        })

    except ImportError:
        await _send_json(websocket, {
            "type": "risk_score",
            **empty_risk_response(),
            "note": "numpy not available",
        })
    except Exception as exc:
        await _send_json(websocket, {
            "type": "risk_score",
            **empty_risk_response(),
            "note": f"Error: {exc}",
        })


async def _send_json(websocket: WebSocket, data: dict[str, Any]) -> None:
    """Send a JSON message over a WebSocket connection."""
    try:
        await websocket.send_json(data)
    except Exception:
        logger.debug("WebSocket send_json failed (client likely disconnected)")
        pass
