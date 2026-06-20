"""SigLab FastAPI dashboard application.

Merged from legacy server.py (Track 2.3).  Serves the ops-board
frontend from ``static/``, exposes REST + WebSocket endpoints,
and supports CORS for demo / multi-origin deployment.
"""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from siglab.config import SiglabConfig, load_settings
from siglab.dashboard.dashboard_state import DashboardState
from siglab.dashboard.routes import router as api_router
from siglab.dashboard.ws import router as ws_router


class WebSocketManager:
    """Manages active WebSocket connections for streaming."""

    def __init__(self) -> None:
        self._connections: set[Any] = set()
        self._subscriptions: dict[str, set[Any]] = {}
        self._update_task: asyncio.Task[None] | None = None

    def register(self, websocket: Any) -> None:
        self._connections.add(websocket)

    def unregister(self, websocket: Any) -> None:
        self._connections.discard(websocket)
        for subs in self._subscriptions.values():
            subs.discard(websocket)

    def subscribe(self, symbol: str, websocket: Any) -> None:
        self._subscriptions.setdefault(symbol, set()).add(websocket)

    def unsubscribe(self, symbol: str, websocket: Any) -> None:
        subs = self._subscriptions.get(symbol)
        if subs:
            subs.discard(websocket)

    @property
    def active_count(self) -> int:
        return len(self._connections)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: load config and lineage store on startup."""
    state = DashboardState()
    try:
        state.config = load_settings()
    except Exception:
        from pathlib import Path as _Path

        state.config = SiglabConfig(
            root_dir=_Path.cwd(),
            sosovalue_config_path=_Path("config.json"),
            generated_strategy_dir=_Path("generated"),
            data_lake_dir=_Path("data/cache"),
            artifact_dir=_Path("runs"),
            live_dir=_Path("live"),
            ancestry_db_path=_Path("siglab.db"),
            sosovalue_api_key_override=None,
        )
    try:
        from siglab.data.deployment_store import DeploymentStore

        state.deployment_store = DeploymentStore(state.config.ancestry_db_path)
    except Exception:
        state.deployment_store = None

    state.static_dir = Path(__file__).resolve().parent / "static"
    state.ws_manager = WebSocketManager()
    state.start_time = time.time()

    app.state.dashboard = state
    yield


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Includes CORS middleware, REST router, WebSocket router,
    and static file serving for the ops-board frontend.
    """
    app = FastAPI(
        title="SigLab Dashboard",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router)
    app.include_router(ws_router)

    # Mount static files for the ops-board frontend (legacy server.py compat)
    _static_dir = Path(__file__).resolve().parent / "static"
    if _static_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")

    return app


app = create_app()
