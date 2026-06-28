from __future__ import annotations

import argparse
import subprocess

from siglab.cli.rich_utils import print_error, print_info, print_success
from siglab.config import _DEFAULT_PORT



def add_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    start_parser = subparsers.add_parser(
        "dashboard-start",
        help="Start the FastAPI dashboard (default port from $PORT or 8080).",
    )
    start_parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Bind address (default: 0.0.0.0)",
    )
    start_parser.add_argument(
        "--port",
        type=int,
        default=_DEFAULT_PORT,
        help=f"Port (default: {_DEFAULT_PORT} from $PORT)",
    )
    start_parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload for development",
    )
    stop_parser = subparsers.add_parser(
        "dashboard-stop",
        help="Stop the running FastAPI dashboard.",
    )
    stop_parser.add_argument(
        "--port",
        type=int,
        default=_DEFAULT_PORT,
        help=f"Port (default: {_DEFAULT_PORT})",
    )
    compat_parser = subparsers.add_parser(
        "dashboard",
        help="[Legacy] Start dashboard (same as dashboard-start).",
    )
    compat_parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Bind address (default: 0.0.0.0)",
    )
    compat_parser.add_argument(
        "--port",
        type=int,
        default=_DEFAULT_PORT,
        help=f"Port (default: {_DEFAULT_PORT})",
    )
    compat_parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload for development",
    )


def run_dashboard_start(args: argparse.Namespace) -> None:
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


def run_dashboard(args: argparse.Namespace) -> None:
    """Legacy compatibility — delegates to dashboard-start."""
    run_dashboard_start(args)


def run_dashboard_stop(args: argparse.Namespace) -> None:
    import os as _os
    import signal as _signal

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
            _os.kill(pid, _signal.SIGTERM)
        print_success(
            f"Stopped dashboard on port {port} (PID{' '.join(str(p) for p in pids)})",
        )
    except subprocess.TimeoutExpired:
        print_error(f"Timeout checking port {port}")
        raise SystemExit(1) from None
    except ProcessLookupError:
        pass
