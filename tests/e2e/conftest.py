"""Playwright E2E fixtures for SigLab frontend tests.

This conftest manages a full server lifecycle with seeded test data:

1.  **Session-scoped ``server`` fixture** — starts a uvicorn server on
    ``localhost:8080`` after swapping in a seeded SQLite database so
    the dashboard API returns deterministic data.
2.  **Per-test ``page`` fixture** — creates a clean Chromium context
    with a 1280×720 viewport.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests
from playwright.sync_api import sync_playwright

from tests.e2e import seed_data

# Paths relative to the repo root (CWD when pytest runs)
REPO_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = REPO_ROOT / "siglab.db"
RUNS_DIR = REPO_ROOT / "runs"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wait_for_server(
    url: str = "http://127.0.0.1:8080/health", timeout: int = 45
) -> bool:
    """Poll the health endpoint until the server responds 200."""
    for _ in range(timeout):
        try:
            resp = requests.get(url, timeout=2)
            if resp.status_code == 200:
                return True
        except requests.ConnectionError:
            pass
        time.sleep(1)
    return False


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def server(request: pytest.FixtureRequest) -> None:
    """Start the SigLab dashboard server with seeded test data.

    This fixture:
    * Backs up the real ``siglab.db`` if it exists.
    * Creates a seeded test database at the expected path.
    * Creates minimal ops-artifact JSON files in ``runs/`` (if not present).
    * Starts uvicorn on port 8080.
    * Waits for the health endpoint.
    * Yields control to the test session.
    * Tears down the server and restores the original database.
    """

    # ── 1. Back up existing database ──────────────────────────────────
    db_backup_path: Path | None = None
    if DB_PATH.exists():
        db_backup_path = REPO_ROOT / "siglab.db.e2e_backup"
        shutil.copy2(str(DB_PATH), str(db_backup_path))

    # ── 2. Create seeded database ────────────────────────────────────
    seed_db_path = seed_data.create_seeded_db()
    shutil.copy2(seed_db_path, str(DB_PATH))
    os.unlink(seed_db_path)

    # ── 3. Ensure ops artifacts exist ─────────────────────────────────
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    required_ops_files = [
        "demo_manifest_latest.json",
        "latest_telemetry_report.json",
        "market_report_latest.json",
        "sodex_preflight_latest.json",
        "wave_status_latest.json",
    ]
    missing = [f for f in required_ops_files if not (RUNS_DIR / f).exists()]
    if missing:
        for _f in missing:
            seed_data.create_ops_artifacts(str(RUNS_DIR))
            break

    # ── 4. Start uvicorn ─────────────────────────────────────────────
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "siglab.dashboard.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8080",
            "--log-level",
            "error",
        ],
        cwd=str(REPO_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # ── 5. Wait for server readiness ─────────────────────────────────
    ready = _wait_for_server(timeout=45)
    if not ready:
        proc.kill()
        proc.wait()
        # Restore backup before failing
        if db_backup_path and db_backup_path.exists():
            shutil.copy2(str(db_backup_path), str(DB_PATH))
            db_backup_path.unlink()
        pytest.fail("Server did not start within 45 seconds")

    yield  # ← tests run here

    # ── 6. Tear down ─────────────────────────────────────────────────
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()

    # Restore original database
    DB_PATH.unlink(missing_ok=True)
    if db_backup_path and db_backup_path.exists():
        shutil.copy2(str(db_backup_path), str(DB_PATH))
        db_backup_path.unlink()


# ---------------------------------------------------------------------------
# Playwright browser fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def browser() -> object:
    """Launch a headless Chromium instance for the test session."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def page(browser: object) -> object:
    """Create a new browser context + page per test with 1280x720 viewport.

    Console messages are printed to stdout for debugging.
    """
    context = browser.new_context(viewport={"width": 1280, "height": 720})
    page = context.new_page()
    page.on("console", lambda msg: print(f"CONSOLE: {msg.type}: {msg.text}"))
    yield page
    context.close()
