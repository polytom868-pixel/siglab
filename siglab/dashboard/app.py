from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from siglab.config import SiglabConfig, load_settings
from siglab.search.lineage import LineageStore
from siglab.dashboard.routes import router as api_router
from siglab.dashboard.ws import router as ws_router


class WebSocketManager:
    """Manages active WebSocket connections for streaming."""

    def __init__(self) -> None:
        self._connections: set[Any] = set()
        self._subscriptions: dict[str, set[Any]] = {}
        self._update_task: asyncio.Task | None = None

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


class DashboardState:
    """Holds runtime state for the FastAPI dashboard application."""

    def __init__(self) -> None:
        self.config: SiglabConfig | None = None
        self.lineage: LineageStore | None = None
        self.start_time: float = time.time()
        self.ws_manager: WebSocketManager = WebSocketManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: load config and open lineage store on startup."""
    state = DashboardState()
    try:
        state.config = load_settings()
    except Exception:
        # Fall back to a minimal config for healthcheck-only mode
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
        state.lineage = LineageStore(state.config.ancestry_db_path)
    except Exception:
        state.lineage = None
    app.state.dashboard = state
    yield


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="SigLab Dashboard",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router)
    app.include_router(ws_router)

    return app


app = create_app()
