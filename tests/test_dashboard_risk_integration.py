"""
Tests for dashboard risk integration (VAL-RISK-008 and VAL-RISK-011).

Validates that:
- VAL-RISK-008: GET /risk returns JSON with composite_score, max_drawdown, correlation_matrix
- VAL-RISK-011: WebSocket streams risk_score with composite and drawdown fields
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
from fastapi.testclient import TestClient

from siglab.config import SiglabConfig
from siglab.dashboard.app import DashboardState, WebSocketManager, create_app
from siglab.dashboard.routes import _compute_risk_metrics


def _make_paper_session_file(path: Path, equity_values: list[float]) -> None:
    """Create a mock .npy paper session file with equity curve data."""
    data = np.array(equity_values, dtype=np.float64)
    np.save(str(path), data)


def _create_test_config(tmp_dir: Path) -> SiglabConfig:
    """Create a SiglabConfig pointing to a temporary directory."""
    return SiglabConfig(
        root_dir=tmp_dir,
        sosovalue_config_path=tmp_dir / "config.json",
        generated_strategy_dir=tmp_dir / "generated",
        data_lake_dir=tmp_dir / "data" / "cache",
        artifact_dir=tmp_dir / "runs",
        live_dir=tmp_dir / "live",
        ancestry_db_path=tmp_dir / "siglab.db",
        sosovalue_api_key_override=None,
    )


class TestRiskEndpoint:
    """VAL-RISK-008: /risk endpoint returns composite_score, max_drawdown, correlation_matrix."""

    def test_risk_endpoint_returns_required_fields(self) -> None:
        """GET /risk returns JSON with composite_score, max_drawdown, correlation_matrix."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            config = _create_test_config(tmp_dir)

            app = create_app()
            state = DashboardState()
            state.config = config
            state.ws_manager = WebSocketManager()
            app.state.dashboard = state

            client = TestClient(app)
            response = client.get("/risk")

            assert response.status_code == 200
            data = response.json()
            assert "composite_score" in data, "Missing composite_score field"
            assert "max_drawdown" in data, "Missing max_drawdown field"
            assert "correlation_matrix" in data, "Missing correlation_matrix field"
            assert "generated_at" in data, "Missing generated_at field"

    def test_risk_endpoint_no_data_returns_nulls(self) -> None:
        """With no paper sessions, all risk fields should be None."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            config = _create_test_config(tmp_dir)

            app = create_app()
            state = DashboardState()
            state.config = config
            state.ws_manager = WebSocketManager()
            app.state.dashboard = state

            client = TestClient(app)
            response = client.get("/risk")

            assert response.status_code == 200
            data = response.json()
            assert data["composite_score"] is None
            assert data["max_drawdown"] is None
            assert data["correlation_matrix"] is None

    def test_risk_endpoint_with_paper_sessions(self) -> None:
        """With paper sessions containing equity curves, risk metrics computed."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            config = _create_test_config(tmp_dir)

            # Create paper sessions directory with mock data
            sessions_dir = config.root_dir / "sessions"
            sessions_dir.mkdir(parents=True)

            # Create a few mock equity curves
            _make_paper_session_file(
                sessions_dir / "session_1.npy",
                [1.0, 1.1, 1.2, 1.15, 1.3, 1.25, 1.4],
            )
            _make_paper_session_file(
                sessions_dir / "session_2.npy",
                [1.0, 1.05, 1.1, 1.08, 1.2, 1.18, 1.25],
            )

            app = create_app()
            state = DashboardState()
            state.config = config
            state.ws_manager = WebSocketManager()
            app.state.dashboard = state

            client = TestClient(app)
            response = client.get("/risk")

            assert response.status_code == 200
            data = response.json()
            assert data["composite_score"] is not None
            assert data["max_drawdown"] is not None
            assert isinstance(data["max_drawdown"], float)
            assert data["max_drawdown"] <= 0.0  # Drawdown is negative or zero
            assert "strategy_count" in data
            assert data["strategy_count"] == 2

    def test_risk_endpoint_single_session_no_correlation(self) -> None:
        """Single strategy session produces no correlation matrix."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            config = _create_test_config(tmp_dir)

            sessions_dir = config.root_dir / "sessions"
            sessions_dir.mkdir(parents=True)
            _make_paper_session_file(
                sessions_dir / "session_1.npy",
                [1.0, 1.1, 1.2, 1.15, 1.3],
            )

            app = create_app()
            state = DashboardState()
            state.config = config
            state.ws_manager = WebSocketManager()
            app.state.dashboard = state

            client = TestClient(app)
            response = client.get("/risk")
            data = response.json()
            # With one strategy, correlation_matrix should be None
            assert data["correlation_matrix"] is None
            assert data["strategy_count"] == 1

    def test_risk_endpoint_no_config_returns_nulls(self) -> None:
        """Without config, the endpoint returns None fields."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            config = _create_test_config(tmp_dir)

            app = create_app()
            state = DashboardState()
            state.config = config
            state.ws_manager = WebSocketManager()

            # Set config to None to simulate missing config
            state.config = None
            app.state.dashboard = state

            client = TestClient(app)
            response = client.get("/risk")
            assert response.status_code == 200
            data = response.json()
            assert data["composite_score"] is None
            assert data["max_drawdown"] is None
            assert data["correlation_matrix"] is None

    def test_risk_endpoint_with_correlation(self) -> None:
        """Multiple strategies produce correlation matrix with proper values."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            config = _create_test_config(tmp_dir)

            sessions_dir = config.root_dir / "sessions"
            sessions_dir.mkdir(parents=True)

            # Create perfectly correlated strategies
            rng = np.random.default_rng(42)
            base = rng.normal(0.001, 0.02, 100)
            _make_paper_session_file(sessions_dir / "a.npy", np.cumprod(1 + base))
            _make_paper_session_file(sessions_dir / "b.npy", np.cumprod(1 + base * 1.5))

            app = create_app()
            state = DashboardState()
            state.config = config
            state.ws_manager = WebSocketManager()
            app.state.dashboard = state

            client = TestClient(app)
            response = client.get("/risk")
            data = response.json()

            assert data["correlation_matrix"] is not None
            assert len(data["correlation_matrix"]) == 2
            assert len(data["correlation_matrix"][0]) == 2
            # Diagonal should be 1.0
            assert data["correlation_matrix"][0][0] == pytest.approx(1.0, abs=1e-4)
            assert data["correlation_matrix"][1][1] == pytest.approx(1.0, abs=1e-4)
            # Should be symmetric
            assert data["correlation_matrix"][0][1] == pytest.approx(
                data["correlation_matrix"][1][0], abs=1e-4
            )
            # Highly correlated (same return pattern)
            assert data["correlation_matrix"][0][1] > 0.5

    def test_risk_metrics_compute_function(self) -> None:
        """The _compute_risk_metrics function works in isolation."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            config = _create_test_config(tmp_dir)

            sessions_dir = config.root_dir / "sessions"
            sessions_dir.mkdir(parents=True)
            _make_paper_session_file(
                sessions_dir / "test.npy",
                [1.0, 1.2, 0.9, 0.8, 1.1, 1.3],
            )

            state = DashboardState()
            state.config = config
            result = _compute_risk_metrics(state)

            assert "composite_score" in result
            assert "max_drawdown" in result
            assert "correlation_matrix" in result
            # Max drawdown for this curve should be approx -0.333
            assert result["max_drawdown"] is not None
            assert result["max_drawdown"] <= 0.0


class TestRiskWebSocket:
    """VAL-RISK-011: Risk score integrates with dashboard WebSocket."""

    def test_ws_subscribe_risk_score(self) -> None:
        """Subscribing to risk_score returns risk_score message with required fields."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            config = _create_test_config(tmp_dir)

            app = create_app()
            state = DashboardState()
            state.config = config
            state.ws_manager = WebSocketManager()
            app.state.dashboard = state

            client = TestClient(app)
            with client.websocket_connect("/ws") as ws:
                # Read welcome message
                welcome = ws.receive_json()
                assert welcome["type"] == "connected"

                # Subscribe to risk_score
                ws.send_json({"action": "subscribe", "subscription_type": "risk_score"})
                sub_response = ws.receive_json()
                assert sub_response["type"] == "subscribed"
                assert sub_response["subscription_type"] == "risk_score"

                # Receive risk score message
                risk_data = ws.receive_json()
                assert risk_data["type"] == "risk_score"
                assert "composite_score" in risk_data
                assert "max_drawdown" in risk_data
                assert "correlation_matrix" in risk_data
                assert "strategy_count" in risk_data

    def test_ws_risk_score_with_data(self) -> None:
        """Risk score WebSocket returns real values when paper sessions exist."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            config = _create_test_config(tmp_dir)

            sessions_dir = config.root_dir / "sessions"
            sessions_dir.mkdir(parents=True)
            _make_paper_session_file(
                sessions_dir / "session_1.npy",
                [1.0, 1.2, 0.9, 1.3, 1.25],
            )

            app = create_app()
            state = DashboardState()
            state.config = config
            state.ws_manager = WebSocketManager()
            app.state.dashboard = state

            client = TestClient(app)
            with client.websocket_connect("/ws") as ws:
                ws.receive_json()  # welcome
                ws.send_json({"action": "subscribe", "subscription_type": "risk_score"})
                ws.receive_json()  # subscribe confirmation

                risk_data = ws.receive_json()
                assert risk_data["type"] == "risk_score"
                assert risk_data["composite_score"] is not None
                assert risk_data["max_drawdown"] is not None
                # With one strategy, no correlation
                assert risk_data["correlation_matrix"] is None
                assert risk_data["strategy_count"] == 1

    def test_ws_risk_score_with_multiple_strategies(self) -> None:
        """Multiple strategies produce correlation matrix in WebSocket response."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            config = _create_test_config(tmp_dir)

            sessions_dir = config.root_dir / "sessions"
            sessions_dir.mkdir(parents=True)

            rng = np.random.default_rng(42)
            base = rng.normal(0.001, 0.02, 100)
            _make_paper_session_file(sessions_dir / "a.npy", np.cumprod(1 + base))
            _make_paper_session_file(sessions_dir / "b.npy", np.cumprod(1 + base * -0.5))

            app = create_app()
            state = DashboardState()
            state.config = config
            state.ws_manager = WebSocketManager()
            app.state.dashboard = state

            client = TestClient(app)
            with client.websocket_connect("/ws") as ws:
                ws.receive_json()  # welcome
                ws.send_json({"action": "subscribe", "subscription_type": "risk_score"})
                ws.receive_json()  # subscribe confirmation

                risk_data = ws.receive_json()
                assert risk_data["type"] == "risk_score"
                assert risk_data["correlation_matrix"] is not None
                assert len(risk_data["correlation_matrix"]) == 2
                assert risk_data["strategy_count"] == 2

    def test_ws_risk_score_no_sessions(self) -> None:
        """Without paper sessions, risk fields are None and note explains."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            config = _create_test_config(tmp_dir)

            app = create_app()
            state = DashboardState()
            state.config = config
            state.ws_manager = WebSocketManager()
            app.state.dashboard = state

            client = TestClient(app)
            with client.websocket_connect("/ws") as ws:
                ws.receive_json()  # welcome
                ws.send_json({"action": "subscribe", "subscription_type": "risk_score"})
                ws.receive_json()  # subscribe confirmation

                risk_data = ws.receive_json()
                assert risk_data["type"] == "risk_score"
                assert risk_data["composite_score"] is None
                assert risk_data["max_drawdown"] is None
                assert risk_data["note"] is not None

    def test_ws_get_risk_action(self) -> None:
        """Using 'get_risk' action returns risk score snapshot."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            config = _create_test_config(tmp_dir)

            app = create_app()
            state = DashboardState()
            state.config = config
            state.ws_manager = WebSocketManager()
            app.state.dashboard = state

            client = TestClient(app)
            with client.websocket_connect("/ws") as ws:
                ws.receive_json()  # welcome
                ws.send_json({"action": "get_risk"})

                risk_data = ws.receive_json()
                assert risk_data["type"] == "risk_score"
                assert "composite_score" in risk_data
                assert "max_drawdown" in risk_data

    def test_ws_risk_score_fields_match_rest(self) -> None:
        """WebSocket risk_score and REST /risk return consistent field names."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            config = _create_test_config(tmp_dir)

            sessions_dir = config.root_dir / "sessions"
            sessions_dir.mkdir(parents=True)
            _make_paper_session_file(
                sessions_dir / "s1.npy",
                [1.0, 1.1, 1.05, 1.2, 1.15],
            )

            app = create_app()
            state = DashboardState()
            state.config = config
            state.ws_manager = WebSocketManager()
            app.state.dashboard = state

            client = TestClient(app)

            # Get REST response
            rest_response = client.get("/risk")
            rest_data = rest_response.json()

            # Compare field names with WS response
            rest_fields = {"composite_score", "max_drawdown", "correlation_matrix"}

            with client.websocket_connect("/ws") as ws:
                ws.receive_json()  # welcome
                ws.send_json({"action": "get_risk"})
                ws_data = ws.receive_json()

            ws_fields = {"composite_score", "max_drawdown", "correlation_matrix"}
            assert ws_fields.issubset(ws_data.keys()), (
                f"WS risk_score missing fields: {ws_fields - set(ws_data.keys())}"
            )
            assert rest_fields.issubset(rest_data.keys()), (
                f"REST /risk missing fields: {rest_fields - set(rest_data.keys())}"
            )
            # Field naming should be consistent between REST and WS
            assert ws_data["composite_score"] == rest_data["composite_score"]
            assert ws_data["max_drawdown"] == rest_data["max_drawdown"]


class TestRiskFullFlow:
    """End-to-end verification of risk dashboard integration."""

    def test_risk_endpoint_returns_expected_structure(self) -> None:
        """Verify the exact structure expected by VAL-RISK-008."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            config = _create_test_config(tmp_dir)

            app = create_app()
            state = DashboardState()
            state.config = config
            state.ws_manager = WebSocketManager()
            app.state.dashboard = state

            client = TestClient(app)
            response = client.get("/risk")

            assert response.status_code == 200
            data = response.json()

            # Required fields per VAL-RISK-008
            assert "composite_score" in data
            assert "max_drawdown" in data
            assert "correlation_matrix" in data
            assert "generated_at" in data

            # Types
            assert isinstance(data["generated_at"], str)

            # When no data, values are None but keys exist
            if data["composite_score"] is not None:
                assert isinstance(data["composite_score"], float)
                assert 0.0 <= data["composite_score"] <= 1.0

            if data["max_drawdown"] is not None:
                assert isinstance(data["max_drawdown"], float)
                assert data["max_drawdown"] <= 0.0
