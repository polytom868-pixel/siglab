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

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

from siglab.config import SiglabConfig, load_settings
from siglab.dashboard.dashboard_state import DashboardState
from siglab.dashboard.routes import router as api_router
from siglab.dashboard.ws import router as ws_router


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Set security-related HTTP headers on every response.

    Includes Content-Security-Policy, HSTS-family headers, and
    permission restrictions.  Added *after* CORS so the CSP
    reflects the final response.
    """

    async def dispatch(self, request, call_next):
        response: Response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://unpkg.com; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "form-action 'self'; "
            "base-uri 'self'; "
            "frame-ancestors 'none'; "
            "upgrade-insecure-requests; "
            "block-all-mixed-content;"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        return response


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

    # Configure Jinja2Templates for HTMX partials
    _template_dir = Path(__file__).resolve().parent / "templates"
    state.templates = Jinja2Templates(directory=str(_template_dir))

    # Register custom Jinja2 filters
    _env = state.templates.env

    def _jinja2_format_number(value: Any, decimals: int = 2) -> str:
        try:
            v = float(value)
            if not (v != v or v == float('inf') or v == float('-inf')):
                return f"{v:.{decimals}f}"
        except (TypeError, ValueError):
            pass
        return "n/a"

    def _jinja2_format_pct(value: Any) -> str:
        try:
            v = float(value)
            if not (v != v or v == float('inf') or v == float('-inf')):
                return f"{v * 100:.2f}%"
        except (TypeError, ValueError):
            pass
        return "n/a"

    def _jinja2_format_dt(value: Any) -> str:
        if not value:
            return ""
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            return str(value)

    _env.filters["format_number"] = _jinja2_format_number
    _env.filters["format_pct"] = _jinja2_format_pct
    _env.filters["format_dt"] = _jinja2_format_dt

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

    # Configure CORS — restrict origins via CORS_ORIGINS env var (comma-separated).
    # Defaults to localhost:8080 for local development.
    _cors_origins = os.getenv("CORS_ORIGINS", "http://localhost:8080").split(",")
    _cors_origins_stripped = [o.strip() for o in _cors_origins if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins_stripped,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Security headers middleware (registered after CORS so it wraps everything)
    app.add_middleware(SecurityHeadersMiddleware)

    app.include_router(api_router)
    app.include_router(ws_router)

    # Catch-all SPA routes: serve static HTML for client-side navigation paths.
    # These must be defined BEFORE the StaticFiles mount so FastAPI routes
    # take priority over the static file handler.
    _static_dir = Path(__file__).resolve().parent / "static"

    @app.get("/runs/{run_id:path}")
    async def serve_run_page(run_id: str):
        return FileResponse(os.path.join(str(_static_dir), "run.html"))

    @app.get("/experiments/{spec_hash:path}")
    async def serve_experiment_page(spec_hash: str):
        return FileResponse(os.path.join(str(_static_dir), "experiment.html"))

    # Mount static files for the ops-board frontend (legacy server.py compat)
    if _static_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")

    return app


app = create_app()
