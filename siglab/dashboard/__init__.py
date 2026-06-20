"""SigLab Dashboard — FastAPI + WebSocket dashboard on unified port."""

from siglab.dashboard.app import app, create_app

__all__ = [
    "app",
    "create_app",
]
