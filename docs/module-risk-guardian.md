> **NOTE**: The risk guardian module, TUI risk screen, and WebSocket risk streaming have been removed. Core risk computation functions remain in `siglab/utils.py`. This document is retained for historical reference.

# Risk Guardian Module

## Purpose

The Risk Guardian module monitors portfolio-level risk across paper trading sessions in SigLab. It provides:

- **Composite risk scoring** — a single [0, 1] score synthesizing Sharpe ratio, drawdown, concentration, and cross-strategy correlation.
- **Drawdown analysis** — max drawdown, current drawdown from running peak, and recovery period tracking.
- **Cross-strategy correlation** — pairwise Pearson correlation matrix across all active strategies.
- **Concentration limit detection** — breach reports when strategy allocations exceed configured limits.
- **Risk-based position sizing** — volatility-adjusted position recommendations capped by risk budgets.
- **Alerting** — threshold-based alerts at INFO, WARNING, and CRITICAL severity levels.

The module is a pure computation layer with no side effects or I/O. All functions handle empty and edge-case input gracefully.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                      Paper Trading Sessions                         │
│                      (sessions/*.npy)                               │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ equity curves, returns
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   siglab/risk/guardian.py                           │
│                                                                     │
│  compute_composite_score()    compute_position_size()               │
│  max_drawdown()               check_concentration()                 │
│  current_drawdown()           check_risk_thresholds()               │
│  recovery_time()              track_drawdown_events()               │
│  correlation_matrix()                                             │
└──────┬───────────────────────────┬──────────────────────────────────┘
       │                           │
       ▼                           ▼
┌──────────────────┐    ┌──────────────────────┐
│ Dashboard REST   │    │ Dashboard WebSocket  │
│ GET /risk        │    │ /ws subscribe risk   │
│ (routes.py)      │    │ _stream_risk_scores  │
│ _compute_risk    │    │ _periodic_risk_push  │
│ _metrics()       │    │ (15s interval)       │
└────────┬─────────┘    └──────────┬───────────┘
         │                         │
         ▼                         ▼
┌─────────────────────────────────────────────────────────────────────┐
│              TUI Risk Screen (tui/screens/risk.py)                  │
│                                                                     │
│  RiskGaugeWidget          DrawdownSparklineWidget                   │
│  CorrelationHeatmapWidget AlertStreamWidget                         │
└─────────────────────────────────────────────────────────────────────┘
```

**Data flow:**

1. Paper trading sessions produce `.npy` files under `sessions/`.

2. The dashboard route `_compute_risk_metrics()` and WebSocket handler `_stream_risk_scores()` load these files, extract equity curves, and compute returns.
3. Guardian functions (`max_drawdown`, `correlation_matrix`, `compute_composite_score`, etc.) process the returns into risk metrics.
4. Results are served via REST (`GET /risk`) and streamed via WebSocket (`risk_score` subscription, 15-second periodic push).
5. The TUI Risk screen consumes both the REST endpoint (for initial load and periodic refresh) and the WebSocket subscription (for live updates).

---

## Composite Score

The composite score is a weighted average of four normalized sub-scores, each in [0, 1], where **1.0 = least risky** and **0.0 = most risky**.

```python
composite = Σ(sub_score[k] × weight[k]) / Σ(weight[k])
```

**Default weights:**

| Component           | Weight |
|---------------------|--------|
| Sharpe ratio        | 0.25   |
| Drawdown            | 0.30   |
| Concentration       | 0.25   |
| Correlation risk    | 0.20   |

Custom weights can be passed via the `weights` parameter to `compute_composite_score()`. Only recognized keys are used; unrecognized keys are ignored.

---

## Sub-Scores

### Sharpe Ratio (`sharpe`, weight 0.25)

**What it measures:** Risk-adjusted return quality. Higher Sharpe → lower risk contribution.

**Normalization:**
- Clipped to [−20, +20]
- Score = 1.0 if Sharpe ≥ 3.0
- Score = 0.0 if Sharpe ≤ 0.0
- Linear interpolation between 0 and 3.0

**Dashboard computation:** Annualized from daily returns: `mean(returns) / std(returns) × √365`.

### Drawdown (`drawdown`, weight 0.30)

**What it measures:** Worst peak-to-trough decline. Less negative → lower risk.

**Normalization:**
- Clipped to [−1.0, 0.0] (−100% to 0%)
- Score = 1.0 if drawdown ≥ 0.0 (no drawdown)
- Score = 0.0 if drawdown ≤ −20%
- Linear interpolation between 0% and −20%

### Concentration (`concentration`, weight 0.25)

**What it measures:** How much allocations deviate from configured limits. 0.0 = at or under limit.

**Normalization:**
- Clipped to [0.0, 1.0]
- Score = 1.0 if deviation ≤ 0.0 (no breach)
- Score = 0.0 if deviation ≥ 20% over limit
- Linear interpolation between 0% and 20%

In the current dashboard integration, concentration is set to 0.0 (no limit configuration) until concentration limits are configured in the session.

### Correlation Risk (`correlation_risk`, weight 0.20)

**What it measures:** Average pairwise correlation across all strategies. Higher correlation → less diversification → higher risk.

**Normalization:**
- Clipped to [0.0, 1.0]
- Score = 1.0 if avg correlation ≤ 0.0
- Score = 0.0 if avg correlation ≥ 0.70
- Linear interpolation between 0.0 and 0.70

---

## Drawdown Analysis

### Max Drawdown (`max_drawdown`)

Computes the largest peak-to-trough decline over the entire equity curve using the running-maximum formula:

```
peak[i] = max(equity[0..i])
drawdown[i] = (equity[i] − peak[i]) / peak[i]
max_drawdown = min(drawdown)
```

Returns a negative fraction (e.g., −0.15 = 15% drawdown) or 0.0 for empty/monotonic series. Handles zero-peak values safely to avoid division by zero.

### Current Drawdown (`current_drawdown`)

Measures decline from the running peak at the **last** data point only, unlike `max_drawdown` which looks at the historical worst. Returns a negative fraction or 0.0.

### Recovery Time (`recovery_time`)

Returns the number of periods from the deepest trough to the point where equity recovers to the pre-trough peak. Returns `None` if the series is still in drawdown, has no drawdown events, or has fewer than 2 data points.

### Drawdown Event Tracking (`track_drawdown_events`)

Scans the equity curve for peak→trough→recovery cycles and returns a list of `DrawdownEvent` dataclass instances:

| Field             | Description                                          |
|-------------------|------------------------------------------------------|
| `start_date`      | Period index when drawdown began (peak)              |
| `peak_date`       | Period index of the pre-drawdown peak                |
| `trough_date`     | Period index of the deepest point                    |
| `recovery_date`   | Period index of recovery (None if still in drawdown) |
| `max_drawdown_pct`| Drawdown magnitude as negative fraction              |

---

## Correlation Matrix

`correlation_matrix(strategy_returns)` computes an N×N Pearson correlation matrix from N strategy return series.

- Handles **unequal-length** arrays by using only overlapping periods for each pair.
- Returns 0.0 correlation for constant series (std = 0) or arrays with fewer than 2 observations.
- Returns an empty (0, 0) array if fewer than 2 strategies are provided.
- Diagonal is always 1.0.

The dashboard computes average pairwise correlation (upper triangle, excluding diagonal) to feed into the composite score's `correlation_risk` component.

---

## Alerts

### Threshold-Based Alerts (`check_risk_thresholds`)

Checks metric values against configurable thresholds. Each metric can have:

| Key         | Description                                |
|-------------|--------------------------------------------|
| `info`      | Value that triggers an INFO alert          |
| `warning`   | Value that triggers a WARNING alert        |
| `critical`  | Value that triggers a CRITICAL alert       |
| `direction` | `"above"` (default) or `"below"` — which direction triggers |

Alerts are returned as `AlertEvent` dataclass instances:

| Field       | Type            | Description                          |
|-------------|-----------------|--------------------------------------|
| `timestamp` | `str`           | UTC ISO timestamp                    |
| `metric`    | `str`           | Metric name                          |
| `severity`  | `AlertSeverity` | `INFO`, `WARNING`, or `CRITICAL`     |
| `value`     | `float`         | Current metric value                 |
| `threshold` | `float`         | Threshold that was breached          |
| `message`   | `str`           | Human-readable alert description     |

### Concentration Breach Alerts (`check_concentration`)

Compares strategy allocations against configured limits. Returns a `BreachReport`:

| Field       | Description                              |
|-------------|------------------------------------------|
| `breached`  | `True` if any allocation exceeds a limit |
| `allocation`| Current allocation map                   |
| `limits`    | Configured limit map                     |
| `breaches`  | List of breach details (strategy, allocation, limit, excess) |

Supports a `"default"` key in the limits dict as a fallback for strategies without explicit limits.

### Dashboard Alert Generation

The `/risk` endpoint generates drawdown alerts from `track_drawdown_events()`:
- Events with `|max_drawdown_pct| < 15%` → **WARNING**
- Events with `|max_drawdown_pct| ≥ 15%` → **CRITICAL**
- Limited to the last 20 events.

---

## Position Sizing

`compute_position_size(risk_budget, volatility, max_size)` computes a volatility-adjusted position size:

```
size = risk_budget / volatility
```

- `risk_budget`: fraction of portfolio at risk (e.g., 0.02 = 2%)
- `volatility`: expected standard deviation of returns (must be > 0)
- `max_size`: hard cap on position size (e.g., 0.25 = 25%)
- Returns 0.0 if volatility ≤ 0 or risk_budget < 0
- Result is clamped to `[0, max_size]`

This ensures higher-volatility assets receive smaller positions, maintaining a constant risk budget per trade.

---

## Historical Tracking

### Drawdown Events (`track_drawdown_events`)

The `track_drawdown_events()` function scans an entire equity curve and identifies all peak→trough→recovery cycles. Each cycle becomes a `DrawdownEvent` with:

- Period indices for peak, trough, and recovery
- `recovery_date = None` for the most recent drawdown if still in progress
- `max_drawdown_pct` as a negative fraction

This enables trend analysis of drawdown frequency, severity, and recovery time over the portfolio's history.

### Dashboard Drawdown History

The `/risk` endpoint builds a downsampled drawdown history (up to 60 points) from the equity curve for sparkline visualization:

```python
peak = np.maximum.accumulate(equity_curve)
drawdown_series = (equity_curve - peak) / peak
# Downsampled to ~60 points for sparkline display
```

---

## Dashboard Integration

### REST Endpoint: `GET /risk`

Defined in `siglab/dashboard/routes.py`, the `/risk` endpoint:

1. Loads `.npy` session files from `sessions/`.
2. Extracts equity curves (structured arrays with `"equity"` field, or raw float arrays).
3. Computes: max drawdown, current drawdown, recovery time, Sharpe ratio, correlation matrix, sub-scores, composite score, drawdown history, and alerts.
4. Returns a JSON response:

```json
{
  "generated_at": "2025-01-01T00:00:00+00:00",
  "composite_score": 0.75,
  "max_drawdown": -0.08,
  "correlation_matrix": [[1.0, 0.3], [0.3, 1.0]],
  "strategy_count": 2,
  "strategy_names": ["session_1", "session_2"],
  "sub_scores": {
    "sharpe": 0.8,
    "drawdown": 0.7,
    "concentration": 1.0,
    "correlation_risk": 0.6
  },
  "current_drawdown": -0.02,
  "recovery_periods": 5,
  "drawdown_history": [-0.01, -0.03, ...],
  "alerts": [...],
  "sharpe_ratio": 1.5
}
```

All fields are `None`/empty when no session data is available.

### WebSocket Streaming

Defined in `siglab/dashboard/ws.py`:

- **Subscription:** Client sends `{"action": "subscribe", "subscription_type": "risk_score"}` (no symbol required).
- **Initial push:** Risk scores are sent immediately upon subscription.
- **Periodic push:** `_periodic_risk_push()` sends updated risk scores every **15 seconds** via `_stream_risk_scores()`.
- **On-demand:** Client can send `{"action": "get_risk"}` for an immediate snapshot.

The WebSocket handler computes the same metrics as the REST endpoint (composite score, max drawdown, correlation matrix, Sharpe ratio, strategy count).

---

## TUI Integration

The TUI Risk screen (`siglab/tui/screens/risk.py`) displays four widgets in a two-column layout:

```
┌──────────────────────┬──────────────────────┐
│  RiskGaugeWidget     │  DrawdownSparkline   │
│  (composite score)   │  (dd history chart)  │
│                      │  max/current/recovery│
├──────────────────────┼──────────────────────┤
│  AlertStreamWidget   │  CorrelationHeatmap  │
│  (severity log)      │  (N×N matrix grid)   │
└──────────────────────┴──────────────────────┘
```

### RiskGaugeWidget

- ASCII bar gauge showing composite score as a 24-character bar (0–100%).
- Color-coded: green (≥0.7), yellow (≥0.4), red (<0.4).
- Sub-scores displayed as 10-character bars with labels (Sharpe, Drawdown, Concentr., Corr.Risk).
- Shows strategy count.

### DrawdownSparklineWidget

- Unicode sparkline chart from drawdown history values.
- Summary stats: Max DD, Current DD, Recovery periods.
- Color coding: red (<−10%), yellow (<−5%), muted otherwise.
- Shows "in progress" for recovery when still in drawdown.

### CorrelationHeatmapWidget

- N×N grid with block characters representing correlation intensity:
  - `█` = 1.0 (identity)
  - `▓` ≥ 0.7 (high — red)
  - `▒` ≥ 0.4 (moderate — yellow)
  - `░` ≥ 0.1 (low — muted)
  - `·` < 0.1 (negligible)
- Responsive: truncates strategies that don't fit the terminal width.
- Includes a color legend.

### AlertStreamWidget

- Chronological log of risk alerts (newest first, max 50 displayed).
- Each entry: timestamp, severity badge (color-coded), metric name, message.
- Supports severity filtering via `f` key: all → critical → warning → info → all.

### Data Sources

- **REST (`GET /risk`):** Used for initial data load and periodic 15-second refresh via `_fetch_risk_data()`.
- **WebSocket (`risk_score` subscription):** Used for live updates via `_ws_risk_loop()` with exponential backoff reconnection (1s → 30s max).

---

## Testing

Risk guardian tests are in `tests/test_risk_guardian.py` covering:

| Assertion       | Description                                      |
|-----------------|--------------------------------------------------|
| VAL-RISK-001    | Composite risk score from weighted inputs        |
| VAL-RISK-002    | Max drawdown calculation correct                 |
| VAL-RISK-003    | Current drawdown tracks from running peak        |
| VAL-RISK-004    | Recovery time calculated correctly               |
| VAL-RISK-005    | Cross-strategy correlation matrix correct        |
| VAL-RISK-006    | Concentration limit breach detected              |
| VAL-RISK-007    | Alert thresholds trigger notifications           |
| VAL-RISK-009    | Empty data handling                              |
| VAL-RISK-010    | Position sizing respects risk limits             |
| VAL-RISK-012    | Historical drawdown events tracked               |

**Run tests:**

```bash
# All risk guardian tests
python3 -m pytest tests/test_risk_guardian.py -v

# Quick (quiet mode)
python3 -m pytest tests/test_risk_guardian.py -q

# Full test suite
python3 -m pytest -q
```
