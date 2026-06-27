"""OperatorPipeline — research-to-decision production pipeline."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from siglab.live.paper_client import SoDEXPaperPerpsClient
from siglab.dashboard.risk_utils import (
    BreachReport,
    CircuitBreakerState,
    check_concentration,
    compute_position_size,
)

logger = logging.getLogger(__name__)


@dataclass
class RiskReport:
    """Outcome of a risk-check cycle."""

    passed: bool
    reasons: list[str] = field(default_factory=list)
    composite_score: float = 0.0


@dataclass
class TradeSignal:
    """A trade decision produced from evidence evaluation."""

    direction: str
    symbol: str
    confidence: float
    size: float
    reasoning: str
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class Position:
    """A planned or executed position."""

    symbol: str
    side: str
    quantity: float
    entry_price: float
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class OperatorPipeline:
    """Orchestrates the full research-to-decision pipeline."""

    def __init__(
        self,
        dry_run: bool,
        paper_client: SoDEXPaperPerpsClient | None = None,
    ) -> None:
        if not isinstance(dry_run, bool):
            raise TypeError("dry_run must be a bool")
        self.dry_run = dry_run
        self.paper_client = paper_client
        self._circuit_breaker = CircuitBreakerState()

    def evidence_to_decision(
        self,
        evidence_records: list[dict[str, Any]],
    ) -> TradeSignal:
        """Aggregate raw evidence records into a consensus trade signal."""
        if not evidence_records:
            return TradeSignal(
                direction="HOLD",
                symbol="",
                confidence=0.0,
                size=0.0,
                reasoning="No evidence records to evaluate",
            )
        buy_score = 0.0
        sell_score = 0.0
        total_weight = 0.0
        symbols: dict[str, float] = {}
        for record in evidence_records:
            weight = float(record.get("weight", 1.0))
            if weight <= 0.0:
                continue
            signal_dir = str(record.get("signal", "HOLD")).strip().upper()
            confidence = max(0.0, min(1.0, float(record.get("confidence", 0.5))))
            symbol = str(record.get("symbol", "")).strip().upper()
            if signal_dir == "BUY":
                buy_score += weight * confidence
            elif signal_dir == "SELL":
                sell_score += weight * confidence
            total_weight += weight
            if symbol:
                symbols[symbol] = symbols.get(symbol, 0.0) + weight * confidence
        if total_weight <= 0.0:
            return TradeSignal(
                direction="HOLD",
                symbol="",
                confidence=0.0,
                size=0.0,
                reasoning="Total evidence weight is zero",
            )
        net = (buy_score - sell_score) / total_weight
        max_conf = max(buy_score, sell_score) / total_weight
        if abs(net) < 0.1:
            consensus = "HOLD"
        elif net > 0:
            consensus = "BUY"
        else:
            consensus = "SELL"
        primary_symbol = (
            max(symbols, key=lambda k: symbols.get(k, 0.0)) if symbols else ""
        )
        position_size = max_conf * 0.02
        return TradeSignal(
            direction=consensus,
            symbol=primary_symbol,
            confidence=max_conf,
            size=position_size,
            reasoning=f"buy_score={buy_score:.4f}, sell_score={sell_score:.4f}, net={net:.4f}, max_conf={max_conf:.4f} → {consensus}",
        )

    def risk_check(
        self,
        signal: TradeSignal,
        portfolio_value: float = 100000.0,
        allocation: dict[str, float] | None = None,
    ) -> RiskReport:
        """Run risk checks before execution."""
        reasons: list[str] = []
        if signal.direction == "HOLD":
            return RiskReport(
                passed=True,
                reasons=["HOLD — no execution required"],
                composite_score=1.0,
            )
        cb = self._circuit_breaker
        cb.equity = portfolio_value
        if cb.daily_start_equity <= 0.0:
            cb.daily_start_equity = portfolio_value
        if cb.peak_equity <= 0.0 or portfolio_value > cb.peak_equity:
            cb.peak_equity = portfolio_value
        cb_passed, cb_reason = cb.check_circuit_breakers()
        if not cb_passed:
            return RiskReport(
                passed=False,
                reasons=[f"Circuit breaker tripped: {cb_reason}"],
                composite_score=0.0,
            )
        limits: dict[str, float] = {"default": cb.max_position_pct}
        breach = check_concentration(dict(allocation or {}), limits)
        for b in breach.breaches:
            reasons.append(
                f"Concentration breach: {b['strategy']} alloc={b['allocation']:.1%} limit={b['limit']:.1%}",
            )
        max_pos_frac = compute_position_size(
            risk_budget=cb.max_risk_per_trade_pct,
            volatility=0.05,
            max_size=cb.max_position_pct,
        )
        max_size_usd = max_pos_frac * portfolio_value
        if signal.size > max_size_usd:
            reasons.append(
                f"Signal size ${signal.size:.2f} exceeds risk-budgeted cap ${max_size_usd:.2f}",
            )
        hard_breach = bool(breach.breaches)
        if not reasons:
            composite = 1.0
        elif not hard_breach:
            composite = 0.7
        else:
            composite = 0.3
        return RiskReport(
            passed=not hard_breach,
            reasons=reasons or ["All risk checks passed"],
            composite_score=composite,
        )

    def position_to_paper(
        self,
        signal: TradeSignal,
        session_id: str,
        *,
        mark_price: float | None = None,
    ) -> dict[str, Any]:
        """Convert a trade signal into a paper order."""
        if signal.direction == "HOLD":
            return {"status": "noop", "reason": "HOLD signal — no order placed"}
        side = "BUY" if signal.direction == "BUY" else "SELL"
        order_payload: dict[str, Any] = {
            "session_id": session_id,
            "symbol": signal.symbol,
            "side": side,
            "quantity": signal.size,
            "order_type": "MARKET",
        }
        if self.dry_run:
            logger.info(
                "DRY RUN: would submit order to session %s — %s %s %.6f",
                session_id,
                side,
                signal.symbol,
                signal.size,
            )
            return {"status": "dry_run", "dry_run": True, **order_payload}
        if self.paper_client is None:
            raise RuntimeError(
                "paper_client is required to place paper orders. Set paper_client in OperatorPipeline constructor or run with dry_run=True.",
            )
        result = self.paper_client.place_order(**order_payload)
        logger.info(
            "Placed paper order %s: %s %s %.6f (session %s)",
            result.get("order_id", "?"),
            side,
            signal.symbol,
            signal.size,
            session_id,
        )
        return result

    async def run_once(
        self,
        spec: dict[str, Any],
        market_data: dict[str, Any],
    ) -> tuple[TradeSignal, Position | None, RiskReport]:
        """Full single-pass pipeline: evidence → signal → risk → position."""
        evidence_records: list[dict[str, Any]] = list(
            spec.get("evidence", spec.get("evidence_records", [])),
        )
        runtime_cfg: dict[str, Any] = dict(spec.get("runtime", {}))
        signal = self.evidence_to_decision(evidence_records)
        portfolio_value = float(
            market_data.get("portfolio_value")
            or runtime_cfg.get("portfolio_value", 100000.0),
        )
        allocation: dict[str, float] = dict(market_data.get("allocation", {}))
        risk_report = self.risk_check(signal, portfolio_value, allocation)
        position: Position | None = None
        if risk_report.passed and signal.direction != "HOLD" and signal.symbol:
            entry_price = float(market_data.get("price", 0.0))
            if entry_price <= 0.0:
                entry_price = float(market_data.get("mark_price", 0.0))
            if entry_price <= 0.0 and market_data.get("mids"):
                mids: dict[str, float] = dict(market_data["mids"])
                entry_price = float(mids.get(signal.symbol, 0.0))
            if entry_price > 0.0:
                position = Position(
                    symbol=signal.symbol,
                    side=signal.direction,
                    quantity=signal.size,
                    entry_price=entry_price,
                )
            else:
                logger.warning(
                    "No valid price for %s — cannot build position",
                    signal.symbol,
                )
        return (signal, position, risk_report)
