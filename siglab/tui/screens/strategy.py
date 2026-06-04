"""Strategy Research TUI screen for SigLab.

Displays:
- Strategy list with search/filter by name, family, status
- Results table showing score, PnL, Sharpe, MaxDD per strategy
- Run evaluation with progress indication via CLI bridge
- Side-by-side comparison of 2+ strategies

Connects to CLI bridge for benchmark commands and ancestry data.
Auto-refreshes strategy list every 30 seconds.
"""

from __future__ import annotations

import json
import logging
from typing import Any, ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Input, Static

from siglab.tui.cli_bridge import run_cli
from siglab.tui.formatting import (
    ACCENT_GREEN,
    BORDER_DIM,
    ERROR_RED,
    INFO_BLUE,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    format_drawdown,
    format_return,
    format_score,
    format_sharpe,
    format_status,
    friendly_error,
    truncate,
)
from siglab.tui.loading import LoadingIndicator
from siglab.tui.screens.base import BaseScreen
from siglab.tui.widgets.base import ComparisonWidget, FilterableListWidget
from siglab.tui.widgets.sparkline import sparkline_text

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────

MAX_COMPARE = 4
DEFAULT_DECK = "trend_signals_external"

# Spinner frames for evaluation progress
_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

# Family filter options
FAMILY_FILTERS: list[str] = [
    "ALL",
    "MOM",
    "REV",
    "CARRY",
    "PAIR",
    "BASKET",
]

# Status filter options
STATUS_FILTERS: list[str] = [
    "ALL",
    "PASSED",
    "FAILED",
    "PENDING",
]


# ══════════════════════════════════════════════════════════════════════
# Strategy List Widget
# ══════════════════════════════════════════════════════════════════════


class StrategyListWidget(FilterableListWidget):
    """Vertical list of strategy specs with selection and multi-select."""

    __slots__ = ("_family_filter", "_status_filter")

    strategies: reactive[list[dict[str, Any]]] = reactive(list, layout=True)
    _items_reactive: ClassVar[str] = "strategies"
    _multi_select: ClassVar[bool] = True
    _max_select: ClassVar[int] = MAX_COMPARE

    DEFAULT_CSS = """
    StrategyListWidget {
        width: 1fr;
        height: 1fr;
        overflow-y: auto;
        padding: 0 1;
        background: #0d1210;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._family_filter: str = "ALL"
        self._status_filter: str = "ALL"

    def set_strategies(self, strategies: list[dict[str, Any]]) -> None:
        """Store a reference to the strategy list."""
        self.set_data(strategies)

    def set_family_filter(self, family: str) -> None:
        self._family_filter = family.upper().strip()
        self._apply_filters()

    def set_status_filter(self, status: str) -> None:
        self._status_filter = status.upper().strip()
        self._apply_filters()

    def _matches(self, item: dict[str, Any]) -> bool:
        ft = self._filter_text
        ff = self._family_filter
        sf = self._status_filter
        if ft:
            if not (
                ft in str(item.get("spec_hash", "")).lower()
                or ft in str(item.get("family", "")).lower()
                or ft in str(item.get("hypothesis", "")).lower()
                or ft in str(item.get("track", "")).lower()
            ):
                return False
        if ff and ff != "ALL":
            if ff not in str(item.get("family", "")).upper():
                return False
        if sf and sf != "ALL":
            if sf == "PASSED" and item.get("passed") is not True:
                return False
            if sf == "FAILED" and item.get("passed") is not False:
                return False
            if sf == "PENDING" and item.get("passed") is not None:
                return False
        return True

    def _get_item_key(self, item: dict[str, Any]) -> str | None:
        return str(item.get("spec_hash", "")) or None

    def get_current_hash(self) -> str | None:
        """Return the hash of the currently highlighted strategy."""
        item = self.get_current_item()
        if item:
            return str(item.get("spec_hash"))
        return None

    def _render_item(self, item: dict[str, Any], index: int, is_selected: bool) -> Text:
        h = str(item.get("spec_hash", "?"))[:12]
        family = str(item.get("family", ""))
        passed = item.get("passed")
        score = item.get("aggregate_score")
        key = str(item.get("spec_hash", ""))
        is_multi = key in self._selected_hashes

        prefix = "\u2713 " if is_multi else "  "
        status_dot = "\u25cf" if passed is True else ("\u25cb" if passed is False else "\u00b7")
        status_color = ACCENT_GREEN if passed is True else (ERROR_RED if passed is False else TEXT_MUTED)
        score_str = f"{score:.2f}" if score is not None and score == score else "\u2500"

        padding = max(0, 16 - len(h) - len(prefix) - 2)

        if is_selected:
            styled_row = Text()
            styled_row.append(prefix, style=INFO_BLUE if is_multi else "#000000")
            styled_row.append(status_dot + " ", style=status_color if is_multi else "#000000")
            styled_row.append(truncate(h, 12), style="bold #000000")
            styled_row.append(" " * padding, style="#000000")
            styled_row.append(truncate(family, 10), style="#000000")
            result = Text()
            result.append("\u25b8 ", style=ACCENT_GREEN)
            result.append_text(styled_row)
            result.append(f"  {score_str}", style=f"bold #000000 on {ACCENT_GREEN}")
            return result
        else:
            row = Text()
            row.append(prefix, style=INFO_BLUE if is_multi else TEXT_MUTED)
            row.append(status_dot + " ", style=status_color)
            row.append(truncate(h, 12), style=TEXT_PRIMARY)
            row.append(" " * padding, style=TEXT_MUTED)
            row.append(truncate(family, 10), style=TEXT_SECONDARY)
            result = Text()
            result.append("  ")
            result.append_text(row)
            result.append(f"  {score_str}", style=TEXT_MUTED)
            return result


# ══════════════════════════════════════════════════════════════════════
# Results Table Widget
# ══════════════════════════════════════════════════════════════════════


class ResultsTableWidget(Static):
    """Displays evaluation results for strategies in a sortable table.

    Zero-copy: stores a reference to the results list; sorting
    produces a new list of references (no dict copies).
    """

    results: reactive[list[dict[str, Any]]] = reactive(list, layout=True)
    sort_column: reactive[str] = reactive("aggregate_score")
    sort_ascending: reactive[bool] = reactive(False)

    DEFAULT_CSS = """
    ResultsTableWidget {
        height: 1fr;
        min-height: 8;
        padding: 0 1;
        overflow-y: auto;
        background: #0a0a0a;
    }
    """

    SORT_COLUMNS: ClassVar[list[str]] = [
        "aggregate_score",
        "validation_total_return",
        "sharpe",
        "max_drawdown",
        "family",
    ]

    def set_results(self, results: list[dict[str, Any]]) -> None:
        """Update results data."""
        self.results = results

    def cycle_sort(self) -> None:
        """Cycle to the next sort column."""
        idx = self.SORT_COLUMNS.index(self.sort_column) if self.sort_column in self.SORT_COLUMNS else -1
        next_idx = (idx + 1) % len(self.SORT_COLUMNS)
        self.sort_column = self.SORT_COLUMNS[next_idx]

    def toggle_sort_direction(self) -> None:
        """Toggle ascending/descending."""
        self.sort_ascending = not self.sort_ascending

    def _sorted_results(self) -> list[dict[str, Any]]:
        """Return results sorted by the current column."""
        col = self.sort_column
        asc = self.sort_ascending

        def sort_key(item: dict[str, Any]) -> Any:
            val = item.get(col)
            if val is None:
                return float("-inf") if not asc else float("inf")
            return val

        if col == "family":
            def sort_key(item: dict[str, Any]) -> Any:
                return str(item.get(col, ""))

        try:
            return sorted(self.results, key=sort_key, reverse=not asc)
        except TypeError:
            return list(self.results)

    def render(self) -> Text:
        result = Text()
        result.append(" EVALUATION RESULTS\n", style=f"bold {TEXT_PRIMARY}")

        if not self.results:
            result.append("  No results — select a strategy or run evaluation\n", style=TEXT_MUTED)
            return result

        # Header
        header = Text()
        header.append("  ")
        cols = [
            ("NAME", 14),
            ("FAMILY", 10),
            ("SCORE", 8),
            ("PnL%", 9),
            ("SHARPE", 8),
            ("MAXDD", 8),
            ("STATUS", 6),
            ("SPARKLINE", 16),
        ]
        for name, width in cols:
            marker = " ▼" if name.lower().replace("%", "").replace(" ", "_") == self.sort_column.replace("_", " ").replace(" ", "") else ""
            header.append(f"{name + marker:<{width}}", style=TEXT_MUTED)
        result.append_text(header)
        result.append("\n")
        col_total = sum(w for _, w in cols) + 2  # +2 for leading indent
        result.append("  " + "─" * (col_total - 2) + "\n", style=BORDER_DIM)

        # Rows
        for item in self._sorted_results()[:50]:  # Max 50 rows
            row = Text()
            row.append("  ")

            # Name (spec_hash truncated)
            name = str(item.get("spec_hash", "?"))[:12]
            row.append(f"{name:<14}", style=TEXT_SECONDARY)

            # Family
            family = str(item.get("family", ""))[:8]
            row.append(f"{family:<10}", style=INFO_BLUE)

            # Score
            score = item.get("aggregate_score")
            row.append_text(format_score(score))
            row.append("  " if score is not None else "    ")

            # PnL
            pnl = item.get("validation_total_return")
            row.append_text(format_return(pnl))
            row.append(" " if pnl is not None else " ")

            # Sharpe
            sharpe = item.get("sharpe")
            row.append_text(format_sharpe(sharpe))
            row.append("  " if sharpe is not None else "   ")

            # MaxDD
            dd = item.get("max_drawdown")
            row.append_text(format_drawdown(dd))
            row.append("  " if dd is not None else "   ")

            # Status
            passed = item.get("passed")
            row.append_text(format_status(passed))
            row.append("   ")

            # Sparkline (equity curve)
            equity = item.get("equity_curve", [])
            if equity and len(equity) > 1:
                spark = sparkline_text(equity, width=14)
                row.append_text(spark)
            else:
                row.append("─" * 14, style=TEXT_MUTED)

            result.append_text(row)
            result.append("\n")

        return result


# ══════════════════════════════════════════════════════════════════════
# Comparison Panel Widget
# ══════════════════════════════════════════════════════════════════════


class ComparisonPanelWidget(ComparisonWidget):
    """Side-by-side comparison of 2+ selected strategies."""

    strategies: reactive[list[dict[str, Any]]] = reactive(list, layout=True)
    items = strategies  # alias for ComparisonWidget base

    _metrics: ClassVar[list[tuple[str, str, str]]] = [
        ("Score", "aggregate_score", "{:.3f}"),
        ("PnL%", "validation_total_return", "{:+.2f}%"),
        ("Sharpe", "sharpe", "{:.2f}"),
        ("MaxDD", "max_drawdown", "{:.1f}%"),
        ("Family", "family", "{}"),
    ]
    _empty_message: ClassVar[str] = "Select 2+ strategies with Space, then press c"
    _col_width_base: ClassVar[int] = 76

    def set_strategies(self, strategies: list[dict[str, Any]]) -> None:
        self.set_items(strategies)

    def _get_item_name(self, item: dict[str, Any], index: int) -> str:
        return str(item.get("spec_hash", f"S{index + 1}"))

    def _render_extra(self) -> Text | None:
        """Render equity curve overlay sparkline."""
        if len(self.items) < 2:
            return None
        result = Text()
        result.append("\n")
        result.append("  EQUITY CURVES\n", style=f"bold {TEXT_PRIMARY}")
        result.append("  " + "\u2500" * 60 + "\n", style=BORDER_DIM)
        for i, strat in enumerate(self.items):
            equity = strat.get("equity_curve", [])
            color = self._COLORS[i % len(self._COLORS)]
            name = str(strat.get("spec_hash", f"S{i + 1}"))[:10]
            result.append(f"  {name:<12}", style=color)
            if equity and len(equity) > 1:
                spark = sparkline_text(equity, width=40, bullish_color=color, bearish_color=color)
                result.append_text(spark)
            else:
                result.append("\u2500" * 40, style=TEXT_MUTED)
            result.append("\n")
        return result


# ══════════════════════════════════════════════════════════════════════
# Strategy Screen
# ══════════════════════════════════════════════════════════════════════


class StrategyScreen(BaseScreen):
    """Strategy Research screen — browse, search, evaluate, and compare strategies."""

    BINDINGS: ClassVar[list[Binding]] = BaseScreen.BINDINGS + [
        Binding("e", "run_eval", "Evaluate", show=True),
        Binding("i", "init_deck", "Init Deck", show=True),
        Binding("c", "toggle_compare", "Compare", show=True),
        Binding("space", "toggle_select", "Select", show=True),
        Binding("s", "cycle_sort", "Sort", show=True),
        Binding("/", "focus_search", "Search", show=True),
    ]

    is_evaluating: reactive[bool] = reactive(False)
    eval_status: reactive[str] = reactive("")
    compare_mode: reactive[bool] = reactive(False)
    strategy_count: reactive[int] = reactive(0)

    DEFAULT_CSS = """
    StrategyScreen {
        layout: vertical;
    }
    """

    _loading_widget_id: ClassVar[str] = "#strategy-loading"
    _status_widget_id: ClassVar[str] = "#strategy-status"
    _refresh_interval: ClassVar[float] = 30.0

    def __init__(self, deck: str = DEFAULT_DECK, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._deck = deck
        self._spinner_idx = 0
        self._results_cache: dict[str, dict[str, Any]] = {}

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Search strategies… (hash, family, track)", id="strategy-search")
        with Horizontal(id="strategy-main"):
            yield StrategyListWidget(id="strategy-list")
            with Vertical(id="strategy-detail"):
                yield ResultsTableWidget(id="results-table")
                yield ComparisonPanelWidget(id="comparison-panel", classes="hidden")
        yield LoadingIndicator(id="strategy-loading")
        yield Static(id="strategy-status")

    def on_mount(self) -> None:
        """Initialize the screen after mounting."""
        super().on_mount()
        self._update_status_text_text("Loading strategies\u2026")
        self._spinner_timer = self.set_interval(0.5, self._tick_spinner)

    async def on_unmount(self) -> None:
        """Clean up timers when leaving the screen."""
        await super().on_unmount()
        if hasattr(self, "_spinner_timer"):
            self._spinner_timer.stop()

    def _tick_spinner(self) -> None:
        """Update the spinner animation during evaluation."""
        if self.is_evaluating:
            self._spinner_idx = (self._spinner_idx + 1) % len(_SPINNER)
            frame = _SPINNER[self._spinner_idx]
            self._update_status_text_text(f"{frame} {self.eval_status}")

    async def _fetch_data(self) -> None:
        """Load strategy list from CLI ancestry command."""
        try:
            result = await run_cli("ancestry", "--json", timeout=15.0)
            if result.returncode == 0 and result.stdout.strip():
                data = json.loads(result.stdout)
                rows = data if isinstance(data, list) else data.get("rows", data.get("experiments", []))
                self._update_strategy_list(rows)
            else:
                logger.debug("ancestry returned non-zero or empty: %s", result.stderr[:100])
        except json.JSONDecodeError:
            logger.warning("Failed to parse ancestry JSON output")
        except Exception as exc:
            logger.debug("Failed to load strategies: %s", exc)

    def _update_strategy_list(self, rows: list[dict[str, Any]]) -> None:
        """Update the strategy list widget with loaded data."""
        try:
            list_widget = self.query_one("#strategy-list", StrategyListWidget)
            list_widget.set_strategies(rows)
            self.strategy_count = len(rows)
            self._update_status_text_text(
                f"  {len(rows)} strategies loaded  |  [e]valuate  [c]ompare  [s]ort  [/]search"
            )
        except Exception as exc:
            logger.warning("Strategy list update failed: %s", exc)
            self.notify(friendly_error(exc), severity="error")

    async def _load_results_for_hash(self, spec_hash: str) -> dict[str, Any] | None:
        """Load detailed results for a single strategy hash."""
        if spec_hash in self._results_cache:
            return self._results_cache[spec_hash]
        try:
            result = await run_cli("ancestry", "--json", timeout=15.0)
            if result.returncode == 0 and result.stdout.strip():
                data = json.loads(result.stdout)
                rows = data if isinstance(data, list) else data.get("rows", data.get("experiments", []))
                for row in rows:
                    if str(row.get("spec_hash", "")) == spec_hash:
                        self._results_cache[spec_hash] = row
                        return row
        except Exception as exc:
            logger.debug("Failed to load results for %s: %s", spec_hash, exc)
        return None

    # ── Actions ──────────────────────────────────────────────────────

    def action_refresh_now(self) -> None:
        """Force refresh strategy data."""
        self._results_cache.clear()
        self.call_after_refresh(self._refresh_all)

    def action_focus_search(self) -> None:
        """Focus the search input."""
        try:
            self.query_one("#strategy-search", Input).focus()
        except Exception:
            pass

    def action_move_up(self) -> None:
        try:
            self.query_one("#strategy-list", StrategyListWidget).action_move_up()
            self._on_selection_changed()
        except Exception:
            pass

    def action_move_down(self) -> None:
        try:
            self.query_one("#strategy-list", StrategyListWidget).action_move_down()
            self._on_selection_changed()
        except Exception:
            pass

    def action_toggle_select(self) -> None:
        """Toggle multi-select on current strategy for comparison."""
        try:
            lw = self.query_one("#strategy-list", StrategyListWidget)
            lw.toggle_select()
            self._update_comparison()
        except Exception:
            pass

    def action_toggle_compare(self) -> None:
        """Toggle between results table and comparison view."""
        self.compare_mode = not self.compare_mode
        try:
            results = self.query_one("#results-table", ResultsTableWidget)
            comparison = self.query_one("#comparison-panel", ComparisonPanelWidget)
            if self.compare_mode:
                results.add_class("hidden")
                comparison.remove_class("hidden")
                self._update_comparison()
            else:
                comparison.add_class("hidden")
                results.remove_class("hidden")
        except Exception:
            pass

    def action_cycle_sort(self) -> None:
        """Cycle the sort column in the results table."""
        try:
            rt = self.query_one("#results-table", ResultsTableWidget)
            rt.cycle_sort()
            self._update_status_text(f"  Sorted by: {rt.sort_column}")
        except Exception:
            pass

    async def action_run_eval(self) -> None:
        """Run benchmark evaluation via CLI."""
        if self.is_evaluating:
            self.notify("Evaluation already in progress", severity="warning")
            return

        self.is_evaluating = True
        self.eval_status = f"Evaluating deck '{self._deck}'…"
        try:
            result = await run_cli(
                "benchmark-eval", "--deck", self._deck, "--json",
                timeout=180.0,
            )
            if result.returncode == 0 and result.stdout.strip():
                data = json.loads(result.stdout)
                status = data.get("status", "unknown")
                spec_hash = data.get("spec_hash", "?")
                score = data.get("summary", {}).get("aggregate_score")
                self.eval_status = f"Eval complete: {status} — hash={spec_hash[:12]} score={score}"
                self.notify(
                    f"Evaluation {status}: {spec_hash[:12]}",
                    severity="information" if status == "keep" else "warning",
                )
                # Refresh results
                self._results_cache.clear()
                await self._load_strategies()
            else:
                self.eval_status = f"Eval failed (exit {result.returncode})"
                logger.warning("benchmark-eval stderr: %s", result.stderr[:200])
                self.notify("Evaluation failed", severity="error")
        except json.JSONDecodeError:
            self.eval_status = "Eval failed: invalid JSON output"
            self.notify("Invalid evaluation output", severity="error")
        except Exception as exc:
            self.eval_status = f"Eval error: {exc}"
            self.notify(f"Evaluation error: {exc}", severity="error")
        finally:
            self.is_evaluating = False

    async def action_init_deck(self) -> None:
        """Initialize the benchmark deck via CLI."""
        self._update_status_text(f"Initializing deck '{self._deck}'…")
        try:
            result = await run_cli(
                "benchmark-init", "--deck", self._deck, "--json", "--force",
                timeout=30.0,
            )
            if result.returncode == 0:
                self.notify(f"Deck '{self._deck}' initialized", severity="information")
                await self._load_strategies()
            else:
                self.notify(f"Init failed: {result.stderr[:80]}", severity="error")
        except Exception as exc:
            self.notify(f"Init error: {exc}", severity="error")

    # ── Event Handlers ───────────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        """Handle search input changes."""
        if event.input.id == "strategy-search":
            try:
                lw = self.query_one("#strategy-list", StrategyListWidget)
                lw.set_filter(event.value)
                self.strategy_count = len(lw.strategies)
            except Exception:
                pass

    def _on_selection_changed(self) -> None:
        """Update detail panel when selection changes."""
        try:
            lw = self.query_one("#strategy-list", StrategyListWidget)
            current_hash = lw.get_current_hash()
            if current_hash:
                self.run_worker(self._update_results_for_current(current_hash))
        except Exception:
            pass

    async def _update_results_for_current(self, spec_hash: str) -> None:
        """Load and display results for the currently selected strategy."""
        detail = await self._load_results_for_hash(spec_hash)
        if detail:
            try:
                rt = self.query_one("#results-table", ResultsTableWidget)
                rt.set_results([detail])
            except Exception:
                pass

    def _update_comparison(self) -> None:
        """Update the comparison panel with selected strategies."""
        try:
            lw = self.query_one("#strategy-list", StrategyListWidget)
            selected = lw.get_selected_hashes()
            comparison = self.query_one("#comparison-panel", ComparisonPanelWidget)

            # Gather data for selected hashes
            strats = []
            for h in selected:
                cached = self._results_cache.get(h)
                if cached:
                    strats.append(cached)

            comparison.set_strategies(strats)
            count = len(strats)
            if count >= 2:
                self._update_status_text(f"  Comparing {count} strategies  |  [c] toggle view  [space] select")
        except Exception:
            pass
