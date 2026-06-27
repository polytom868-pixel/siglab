"""Backward-compat shim — app factory moved to routes.py."""
from siglab.dashboard.routes import app

__all__ = ["app"]
