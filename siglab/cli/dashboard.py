"""Dashboard subcommands: dashboard, dashboard-start, dashboard-stop."""

from __future__ import annotations

import argparse
import subprocess

from siglab.cli.rich_utils import print_error, print_info, print_success
from siglab.config import load_settings
from siglab.dashboard import run_dashboard_server


def add_subparser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    # dashboard (legacy embedded)
    parser = subparsers.add_parser("dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)

    # dashboard-start (FastAPI)
    start_parser = subparsers.add_parser(
        "dashboard-start",
        help="Start the FastAPI dashboard on port 3100.",
    )
    start_parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    start_parser.add_argument("--port", type=int, default=3100, help="Port (default: 3100)")
    start_parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")

    # dashboard-stop
    stop_parser = subparsers.add_parser(
        "dashboard-stop",
        help="Stop the running FastAPI dashboard on port 3100.",
    )
    stop_parser.add_argument("--port", type=int, default=3100, help="Port (default: 3100)")


def run_dashboard(args: argparse.Namespace) -> None:
    settings = load_settings()
    run_dashboard_server(settings, host=args.host, port=args.port)


def run_dashboard_start(args: argparse.Namespace) -> None:
    """Start the FastAPI dashboard server on port 3100 (default)."""
    import uvicorn

    host = str(args.host)
    port = int(args.port)
    reload = bool(args.reload)

    print_info(f"Starting SigLab FastAPI dashboard on http://{host}:{port}")
    uvicorn.run(
        "siglab.dashboard.app:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )


def run_dashboard_stop(args: argparse.Namespace) -> None:
    """Stop the running FastAPI dashboard on the specified port."""
    import os
    import signal

    port = int(args.port)
    try:
        result = subprocess.run(
            f"lsof -ti :{port}",
            shell=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        pids = [int(p) for p in result.stdout.strip().split() if p.strip()]
        if not pids:
            print_error(f"No process found listening on port {port}")
            raise SystemExit(1)
        for pid in pids:
            os.kill(pid, signal.SIGTERM)
        print_success(f"Stopped dashboard on port {port} (PID{' '.join(str(p) for p in pids)})")
    except subprocess.TimeoutExpired:
        print_error(f"Timeout checking port {port}")
        raise SystemExit(1) from None
    except ProcessLookupError:
        pass
