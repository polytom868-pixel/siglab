"""SigLab Dashboard — FastAPI + WebSocket dashboard on port 3100."""

from siglab.dashboard.app import app, create_app
from siglab.dashboard.server import run_dashboard_server

__all__ = [
    "app",
    "create_app",
    "run_dashboard_server",
]
