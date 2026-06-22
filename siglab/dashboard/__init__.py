"""SigLab Dashboard — FastAPI + WebSocket dashboard on unified port."""

from siglab.dashboard.routes import app, create_app

__all__ = ["app", "create_app"]
