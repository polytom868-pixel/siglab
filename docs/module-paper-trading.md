# Paper Trading System

## Purpose

Paper trading provides a simulated perpetual futures trading environment that uses **real SoDEX market data** (klines, mark prices, funding rates) without submitting live orders. It enables:

- **Strategy validation** — test trading strategies against real market conditions before committing capital
- **Promotion pipeline** — paper sessions that meet performance thresholds become eligible for live trading promotion
- **Reconciliation** — compare paper PnL against backtest results to detect simulation-to-reality drift
- **TUI-driven workflow** — interactive order entry, position monitoring, and PnL charting from the terminal

Paper trading is the critical bridge between backtesting and live execution in the SigLab research-to-action pipeline.

---

## Architecture

### Core Classes

| Class | Module | Role |
|---|---|---|
| `SoDEXPaperPerpsClient` | `siglab/live/paper_client.py` | Paper trading engine — order management, fill simulation, funding, persistence |
| `SoDEXFeeds` | `siglab/data/sodex_feeds.py` | Real-time market data provider (klines, mark prices, funding rates) |
| `ReconciliationEngine` | `siglab/live/reconciliation.py` | Backtest vs paper PnL divergence comparison |
| `PaperScreen` | `siglab/tui/screens/paper.py` | Textual TUI screen for interactive paper trading |

### Data Flow

```
SoDEXFeeds ──klines/funding──▶ SoDEXPaperPerpsClient
                                     │
                     ┌───────────────┼───────────────┐
                     ▼               ▼               ▼
              .npy session      CLI commands     TUI PaperScreen
              persistence       (paper-*)        (live updates)
```

### Session Storage

Each paper trading session is persisted as a `.npy` file (NumPy binary) in the `sessions/` directory. The file contains a dict with the full session state: orders, positions, PnL, funding history, and metadata. Files are read/written via `np.save()` / `np.load()` with `allow_pickle=True`.

```
sessions/
  <session_id>.npy
  <session_id>.npy
  ...
```

---

## Order Lifecycle

Orders progress through four states:

```
OPEN ──┬──▶ FILLED     (kline crosses limit price or market order processed)
       ├──▶ CANCELLED   (user cancels an open order)
       └──▶ EXPIRED     (time-in-force elapsed)
```

### Order Types

| Type | Description |
|---|---|
| `LIMIT` | Fills when kline crosses the specified price. BUY fills at `min(limit, max(open, low))`; SELL fills at `max(limit, min(open, high))` |
| `MARKET` | Fills immediately at kline close price |

### Time-in-Force Options

| TIF | Expiry |
|---|---|
| `GTC` | Good-til-cancelled — expires after 72 hours |
| `IOC` | Immediate-or-cancel — expires after 60 seconds |
| `FOK` | Fill-or-kill — expires after 10 seconds |
| `GTX` | Good-til-crossed (post-only) — expires after 72 hours |

### Fill Simulation

When `process_klines()` is called with new kline data:

1. **Expire stale orders** — orders past their `expires_at` timestamp move to `EXPIRED`
2. **Match orders against klines** — for each open order, check if the kline's OHLC crosses the limit price
3. **Compute fill price** — conservative fill pricing for LIMIT orders; close price for MARKET orders
4. **Update position** — adjust quantity, entry price, and realized PnL
5. **Remove flat positions** — positions with zero quantity are cleaned up

### Position Management

When a fill reduces a position, realized PnL is computed:

- **Long closing (SELL)**: `qty × (fill_price − entry_price)`
- **Short closing (BUY)**: `qty × (entry_price − fill_price)`

When a fill increases a position (same direction), the entry price is averaged across existing and new quantity. Positions flipping direction (e.g., long → short) first realize PnL on the closing portion, then open a new position at the fill price.

---

## Session Management

### Creation

```python
client = SoDEXPaperPerpsClient(feeds=feeds, sessions_dir="sessions")
session_id = client.create_session(name="my-test")
```

A 12-character hex session ID is generated via `uuid4().hex[:12]`. The session is immediately saved to disk.

### Persistence

Every mutation (place order, cancel, fill, funding) triggers `_save_session_to_disk()`, which serializes the full `PaperSession` dataclass to a `.npy` file. This ensures sessions survive process restarts.

### Resume

`get_session()` checks an in-memory cache first, then falls back to loading from the `.npy` file on disk. The TUI automatically resumes the most recent `tui-session` if one exists.

### List

`list_sessions()` scans the `sessions/` directory for `.npy` files and returns metadata for each: session ID, name, creation time, order count, position count, and cumulative PnL.

---

## Funding Simulation

Funding costs simulate the perpetual futures funding mechanism:

- **Interval**: Every 8 hours (`FUNDING_INTERVAL_HOURS = 8`)
- **Source**: Real funding rates fetched from SoDEX via `feeds.fetch_mark_prices()`
- **Formula**: `funding_cost = position_value × funding_rate`
  - **Long positions**: pay when funding rate is positive (cost is negative)
  - **Short positions**: receive when funding rate is positive (cost is positive)

Funding is applied via `process_funding()`, which:

1. Checks if 8 hours have elapsed since last funding (skipped if not, unless `force=True`)
2. Fetches current funding rates and mark prices from SoDEX
3. Computes cost per position and accumulates it in `position.accumulated_funding`
4. Deducts/adds to session-level PnL
5. Persists updated state to disk

---

## Mark Prices

Mark prices are real-time fair-value prices fetched from SoDEX via `feeds.fetch_mark_prices()`. They serve two purposes:

1. **Unrealized PnL computation** — the TUI computes `qty × (mark_price − entry_price)` for each position using current mark prices
2. **Funding cost basis** — `position_value = |qty| × mark_price` determines the funding cost magnitude

Mark prices are shared by reference between the TUI screen and the positions table widget to avoid unnecessary copies.

The `get_mark_prices()` method on `SoDEXPaperPerpsClient` returns `dict[str, float]` mapping symbol to current mark price for all tracked symbols. The `paper-status` CLI command calls this method and includes the result in its response.

---

## CLI Commands

### `paper-start`

Create a new paper trading session.

```bash
python3 -m siglab.cli paper-start --session "my-session" --sessions-dir ./sessions
```

**Output**: JSON with `session_id` and `name`.

### `paper-status`

Show full session status including positions, PnL, and orders. Also processes any open orders against the latest 1-minute klines before reporting.

```bash
python3 -m siglab.cli paper-status --session <session_id>
```

**Output**: JSON with `session_id`, `name`, `position` (list), `pnl` (realized/unrealized/total/funding), `orders` (list), `mark_prices` (dict).

### `paper-promote`

Evaluate promotion eligibility from paper to live trading.

```bash
python3 -m siglab.cli paper-promote --session <session_id> [--threshold 0.65] [--consecutive-days 5] [--min-trading-days 10]
```

**Output**: JSON with `promoted` (bool), `reason`, `composite_score`, `sub_scores` (pnl/sharpe/win_rate/drawdown), and configuration values.

---

## TUI Integration

The `PaperScreen` (Textual screen) provides an interactive terminal interface for paper trading.

### Layout

```
┌──────────────────┬──────────────────────────────────┐
│  ORDER FORM      │  PnL SPARKLINE CHART             │
│  (symbol, side,  │  (real-time performance plot)    │
│   type, qty,     ├──────────────────────────────────┤
│   price)         │  POSITIONS TABLE                 │
├──────────────────┤  (symbol, size, entry, mark, PnL) │
│  ACCOUNT SUMMARY ├──────────────────────────────────┤
│  (realized,      │  ORDER HISTORY                   │
│   unrealized,    │  (all orders with status)        │
│   total, funding)│                                  │
└──────────────────┴──────────────────────────────────┘
```

### Key Bindings

| Key | Action |
|---|---|
| `s` | Set symbol |
| `b` | Toggle BUY/SELL |
| `t` | Toggle MARKET/LIMIT |
| `Q` | Set quantity |
| `p` | Set limit price |
| `Enter` | Submit order |
| `n` | New session |
| `c` | Cancel order (auto-selects if only one open) |
| `r` | Refresh data |

### Session Lifecycle

On mount, the TUI checks for an existing `tui-session` (by name). If found, it resumes that session. Otherwise, it creates a new one via `paper-start`. Data refreshes every 15 seconds via `_refresh_all()`, which calls `paper-status` through a CLI bridge subprocess.

### Order Placement

Orders are placed via a subprocess that directly invokes `SoDEXPaperPerpsClient.place_order()`. Parameters are serialized as JSON to stdin to avoid shell injection. Success/failure is displayed in the order form widget and as a Textual notification.

---

## Promotion Engine

The promotion engine (`siglab/live/promotion.py`) evaluates whether a paper session has demonstrated sufficient performance quality to warrant live trading.

### Composite Scoring

Four equally-weighted sub-scores (each normalized to [0, 1]):

| Sub-Score | Metric | Normalization |
|---|---|---|
| `pnl` | Total return | 0% → 0.0, 30% annualized → 1.0 (linear) |
| `sharpe` | Sharpe ratio | 0 → 0.0, ≥3.0 → 1.0 (linear) |
| `win_rate` | Fraction of profitable trades | Natural [0, 1] |
| `drawdown` | Maximum drawdown | 0% → 1.0, ≤-30% → 0.0 (linear) |

The composite score is the weighted average: `Σ(sub_score × weight) / Σ(weight)`.

### Promotion Eligibility

A session is eligible for promotion when ALL conditions are met:

1. **Minimum trading days** — at least 10 days with trades (default `DEFAULT_MIN_TRADING_DAYS`)
2. **Consecutive days** — the last 5 consecutive days (default `DEFAULT_CONSECUTIVE_DAYS`) each have a composite score ≥ 0.65 (default `DEFAULT_PROMOTION_THRESHOLD`)

### Metric Extraction

- `extract_session_metrics()` — aggregates all fills across the session to compute total return, annualized Sharpe, win rate, and max drawdown
- `extract_daily_metrics()` — groups fills by calendar day, computing per-day PnL, win rate, and drawdown for the consecutive-day check

---

## Reconciliation

The `ReconciliationEngine` (`siglab/live/reconciliation.py`) compares backtest PnL series against paper PnL series to detect simulation-to-reality drift.

### Metrics

| Metric | Description |
|---|---|
| **Correlation** | Pearson correlation coefficient of overlapping returns |
| **Tracking error** | Standard deviation of (backtest − paper) return differences |
| **Bias** | Mean of (backtest − paper) return differences |
| **Divergence warning** | Boolean — true when tracking error exceeds threshold |

### Threshold

Default divergence warning threshold: **5%** (`DEFAULT_DIVERGENCE_WARNING_THRESHOLD = 0.05`). Configurable via `ReconciliationEngine(divergence_threshold=...)`.

### Usage

```python
engine = ReconciliationEngine()
result = engine.compare(backtest_pnl_series, paper_pnl_series)
# result["divergence_warning"] → True if tracking_error > 0.05
```

Both input series must be `pd.Series` with datetime-like indices. The engine aligns on common index points and requires at least 2 overlapping periods.

---

## Data Storage

### `.npy` File Format

Session state is stored as a Python dict inside a `.npy` (NumPy) file using `np.save(..., allow_pickle=True)`. The dict structure:

```python
{
    "session_id": str,           # 12-char hex ID
    "name": str,                 # Human-readable label
    "created_at": float,         # Unix timestamp
    "orders": {                  # Order ID → order dict
        "<order_id>": {
            "order_id": str,
            "symbol": str,       # e.g. "BTC-USD"
            "side": str,         # "BUY" or "SELL"
            "quantity": float,
            "price": float,
            "order_type": str,   # "LIMIT" or "MARKET"
            "time_in_force": str,# "GTC", "IOC", "FOK", "GTX"
            "status": str,       # "OPEN", "FILLED", "CANCELLED", "EXPIRED"
            "fill_price": float | None,
            "fill_timestamp": float | None,
            "created_at": float,
            "expires_at": float | None,
            "cancelled_at": float | None,
        }
    },
    "positions": {               # Symbol → position dict
        "<symbol>": {
            "symbol": str,
            "quantity": float,   # Positive = long, negative = short
            "entry_price": float,
            "realized_pnl": float,
            "unrealized_pnl": float,
            "accumulated_funding": float,
        }
    },
    "pnl": float,                # Cumulative realized PnL
    "last_funding_time": float | None,
    "metadata": dict,            # Extensible key-value store
}
```

### Directory Structure

```
sessions/
├── a1b2c3d4e5f6.npy    # Session files named by session ID
├── f7g8h9i0j1k2.npy
└── ...
```

The `sessions/` directory is created automatically by `SoDEXPaperPerpsClient.__init__()`.

---

## Testing

### Run All Paper Trading Tests

```bash
python3 -m pytest -q -k paper
```

### Core Test Files

| Test | Covers |
|---|---|
| `tests/test_paper_client.py` | `SoDEXPaperPerpsClient` — session creation, order placement, fill simulation, cancellation, expiry, position management, PnL, funding |
| `tests/test_promotion.py` | Promotion engine — sub-score normalization, composite scoring, eligibility logic, metric extraction |
| `tests/test_reconciliation.py` | Reconciliation — correlation, tracking error, bias, divergence warning |

### Key Test Scenarios

- Order placement with valid and invalid parameters (empty symbol, negative quantity, missing limit price)
- Fill simulation: BUY limit filled when kline low crosses price; SELL limit filled when kline high crosses price
- Position entry price averaging on increased positions
- Realized PnL on position reduction and close
- Order expiry for IOC/FOK/GTC time-in-force types
- Funding cost application with positive and negative funding rates
- Session persistence and resume across `_save_session_to_disk` / `_load_session_from_disk`
- Promotion eligibility with insufficient days, below-threshold scores, and qualifying sessions
- Reconciliation with sufficient and insufficient overlapping periods
