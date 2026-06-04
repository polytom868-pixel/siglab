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
from textual.screen import Screen
from textual.widgets import Input, Static

from siglab.tui.cli_bridge import run_cli
from siglab.tui.formatting import (
    ACCENT_GREEN,
    ACCENT_PURPLE,
    BORDER_DIM,
    ERROR_RED,
    INFO_BLUE,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    WARNING_YELLOW,
    format_drawdown,
    format_return,
    format_score,
    format_sharpe,
    friendly_error,
    truncate,
)
from siglab.tui.loading import LoadingIndicator
from siglab.tui.widgets.sparkline import sparkline_text

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────

REFRESH_SECONDS = 30.0
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


# ── Formatting helpers ───────────────────────────────────────────────
# Centralized in siglab.tui.formatting; local helpers removed.


def _format_status(passed: bool | None) -> Text:
    """Format pass/fail status."""
    if passed is None:
        return Text("pending", style=TEXT_MUTED)
    if passed:
        return Text("●", style=ACCENT_GREEN)
    return Text("○", style=ERROR_RED)


# ══════════════════════════════════════════════════════════════════════
# Strategy List Widget
# ══════════════════════════════════════════════════════════════════════


class StrategyListWidget(Static):
    """Vertical list of strategy specs with selection and multi-select.

    Zero-copy: stores a reference to the strategy list from the API.
    Filtering produces a new list of references (no dict copies).
    """

    __slots__ = ("_all_strategies", "_filter_text", "_family_filter",
                 "_status_filter", "_selected_hashes")

    strategies: reactive[list[dict[str, Any]]] = reactive(list, layout=True)
    selected_index: reactive[int] = reactive(0)

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
        self._all_strategies: tuple[dict[str, Any], ...] = ()
        self._filter_text: str = ""
        self._family_filter: str = "ALL"
        self._status_filter: str = "ALL"
        self._selected_hashes: set[str] = set()

    def set_strategies(self, strategies: list[dict[str, Any]]) -> None:
        """Store a reference to the strategy list.

        Converts to tuple for immutability — individual dicts are
        shared, not copied.
        """
        self._all_strategies = tuple(strategies)
        self._apply_filters()

    def set_filter(self, text: str) -> None:
        """Update the text search filter."""
        self._filter_text = text.lower().strip()
        self._apply_filters()

    def set_family_filter(self, family: str) -> None:
        """Update the family filter."""
        self._family_filter = family.upper().strip()
        self._apply_filters()

    def set_status_filter(self, status: str) -> None:
        """Update the status filter."""
        self._status_filter = status.upper().strip()
        self._apply_filters()

    def _apply_filters(self) -> None:
        """Apply all active filters to the strategy list.

        Uses a single pass through the source data combining all
        filter predicates, avoiding the previous chained list copies.
        """
        ft = self._filter_text
        ff = self._family_filter
        sf = self._status_filter

        def _matches(s: dict[str, Any]) -> bool:
            if ft:
                if not (
                    ft in str(s.get("spec_hash", "")).lower()
                    or ft in str(s.get("family", "")).lower()
                    or ft in str(s.get("hypothesis", "")).lower()
                    or ft in str(s.get("track", "")).lower()
                ):
                    return False
            if ff and ff != "ALL":
                if ff not in str(s.get("family", "")).upper():
                    return False
            if sf and sf != "ALL":
                if sf == "PASSED" and s.get("passed") is not True:
                    return False
                if sf == "FAILED" and s.get("passed") is not False:
                    return False
                if sf == "PENDING" and s.get("passed") is not None:
                    return False
            return True

        # Single-pass filter — no intermediate copies
        filtered = [s for s in self._all_strategies if _matches(s)]
        self.strategies = filtered
        # Clamp selected index
        if self.strategies and self.selected_index >= len(self.strategies):
            self.selected_index = max(0, len(self.strategies) - 1)

    def toggle_select(self) -> None:
        """Toggle multi-select on the current strategy for comparison."""
        if not self.strategies or self.selected_index >= len(self.strategies):
            return
        item = self.strategies[self.selected_index]
        h = str(item.get("spec_hash", ""))
        if not h:
            return
        if h in self._selected_hashes:
            self._selected_hashes.discard(h)
        else:
            if len(self._selected_hashes) < MAX_COMPARE:
                self._selected_hashes.add(h)

    def get_selected_hashes(self) -> set[str]:
        """Return the set of multi-selected strategy hashes."""
        return set(self._selected_hashes)

    def get_current_hash(self) -> str | None:
        """Return the hash of the currently highlighted strategy."""
        if self.strategies and 0 <= self.selected_index < len(self.strategies):
            return str(self.strategies[self.selected_index].get("spec_hash"))
        return None

    def action_move_up(self) -> None:
        if self.selected_index > 0:
            self.selected_index -= 1

    def action_move_down(self) -> None:
        if self.selected_index < len(self.strategies) - 1:
            self.selected_index += 1

    def render(self) -> Text:
        if not self.strategies:
            return Text("  No strategies found", style=TEXT_MUTED)

        lines = Text()
        for i, strat in enumerate(self.strategies):
            h = str(strat.get("spec_hash", "?"))[:12]
            family = str(strat.get("family", ""))
            passed = strat.get("passed")
            score = strat.get("aggregate_score")
            is_selected_hash = h in self._selected_hashes or str(strat.get("spec_hash", "")) in self._selected_hashes

            # Build row text
            prefix = "✓ " if is_selected_hash else "  "
            status_dot = "●" if passed is True else ("○" if passed is False else "·")
            status_color = ACCENT_GREEN if passed is True else (ERROR_RED if passed is False else TEXT_MUTED)
            score_str = f"{score:.2f}" if score is not None and score == score else "─"

            row = Text()
            row.append(prefix, style=INFO_BLUE if is_selected_hash else TEXT_MUTED)
            row.append(status_dot + " ", style=status_color)
            row.append(truncate(h, 12), style=TEXT_PRIMARY)

            # Family tag right-aligned
            padding = max(0, 16 - len(h) - len(prefix) - 2)
            row.append(" " * padding, style=TEXT_MUTED)
            row.append(truncate(family, 10), style=TEXT_SECONDARY)

            if i == self.selected_index:
                lines.append("▸ ", style=ACCENT_GREEN)
                styled_row = Text()
                styled_row.append(prefix, style=INFO_BLUE if is_selected_hash else "#000000")
                styled_row.append(status_dot + " ", style=status_color if is_selected_hash else "#000000")
                styled_row.append(truncate(h, 12), style="bold #000000")
                styled_row.append(" " * padding, style="#000000")
                styled_row.append(truncate(family, 10), style="#000000")
                lines.append_text(styled_row)
                lines.append(f"  {score_str}", style=f"bold #000000 on {ACCENT_GREEN}")
            else:
                lines.append("  ")
                lines.append_text(row)
                lines.append(f"  {score_str}", style=TEXT_MUTED)
            lines.append("\n")

        return lines


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
            row.append_text(_format_status(passed))
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


class ComparisonPanelWidget(Static):
    """Side-by-side comparison of 2+ selected strategies."""

    strategies: reactive[list[dict[str, Any]]] = reactive(list, layout=True)

    DEFAULT_CSS = """
    ComparisonPanelWidget {
        height: 1fr;
        min-height: 10;
        padding: 0 1;
        overflow-y: auto;
        background: #0a0a0a;
    }
    """

    # Colors for each strategy in the overlay sparkline
    _STRAT_COLORS: ClassVar[list[str]] = [
        ACCENT_GREEN,   # green
        INFO_BLUE,      # blue
        WARNING_YELLOW, # yellow
        ACCENT_PURPLE,  # purple
    ]

    def set_strategies(self, strategies: list[dict[str, Any]]) -> None:
        """Set strategies for comparison."""
        self.strategies = strategies

    def render(self) -> Text:
        result = Text()
        result.append(" STRATEGY COMPARISON\n", style=f"bold {TEXT_PRIMARY}")

        if len(self.strategies) < 2:
            result.append(
                "  Select 2+ strategies with Space, then press c\n", style=TEXT_MUTED
            )
            return result

        n = len(self.strategies)
        col_w = max(12, (76) // (n + 1))  # +1 for delta column

        # Column headers
        header = Text()
        header.append("  ")
        for i, strat in enumerate(self.strategies):
            name = str(strat.get("spec_hash", f"S{i+1}"))[:col_w]
            color = self._STRAT_COLORS[i % len(self._STRAT_COLORS)]
            header.append(f"{name:<{col_w}}", style=f"bold {color}")
        header.append("DELTA", style=f"bold {WARNING_YELLOW}")
        result.append_text(header)
        result.append("\n")
        result.append("  " + "─" * (col_w * (n + 1) + 4) + "\n", style=BORDER_DIM)

        # Metrics rows
        metrics = [
            ("Score", "aggregate_score", "{:.3f}"),
            ("PnL%", "validation_total_return", "{:+.2f}%"),
            ("Sharpe", "sharpe", "{:.2f}"),
            ("MaxDD", "max_drawdown", "{:.1f}%"),
            ("Family", "family", "{}"),
        ]

        for label, key, fmt in metrics:
            row = Text()
            row.append(f"  {label:<10}", style=TEXT_PRIMARY)

            values: list[float] = []
            for strat in self.strategies:
                val = strat.get(key)
                if val is not None and val == val:  # not NaN
                    if isinstance(val, (int, float)):
                        values.append(float(val))
                    else:
                        values.append(0.0)

            for i, strat in enumerate(self.strategies):
                val = strat.get(key)
                color = self._STRAT_COLORS[i % len(self._STRAT_COLORS)]
                if val is None or (isinstance(val, float) and val != val):
                    row.append(f"{'─':<{col_w}}", style=TEXT_MUTED)
                elif isinstance(val, str):
                    row.append(f"{val:<{col_w}}", style=color)
                else:
                    formatted = fmt.format(val) if "%" not in fmt else fmt.format(val)
                    row.append(f"{formatted:<{col_w}}", style=color)

            # Delta column
            if values and len(values) >= 2 and key != "family":
                delta = max(values) - min(values)
                if key in ("aggregate_score", "sharpe"):
                    row.append(f"±{delta:.3f}", style=WARNING_YELLOW)
                elif key in ("validation_total_return",):
                    row.append(f"±{delta:.2f}%", style=WARNING_YELLOW)
                elif key in ("max_drawdown",):
                    row.append(f"±{delta:.1f}%", style=WARNING_YELLOW)
                else:
                    row.append(f"±{delta:.3f}", style=WARNING_YELLOW)
            elif key == "family":
                families = [str(s.get("family", ""))[:8] for s in self.strategies]
                unique = len(set(families))
                row.append("diff" if unique > 1 else "same", style=WARNING_YELLOW if unique > 1 else TEXT_MUTED)

            result.append_text(row)
            result.append("\n")

        # Overlay sparkline
        result.append("\n")
        result.append("  EQUITY CURVES\n", style=f"bold {TEXT_PRIMARY}")
        result.append("  " + "─" * 60 + "\n", style=BORDER_DIM)
        for i, strat in enumerate(self.strategies):
            equity = strat.get("equity_curve", [])
            color = self._STRAT_COLORS[i % len(self._STRAT_COLORS)]
            name = str(strat.get("spec_hash", f"S{i+1}"))[:10]
            result.append(f"  {name:<12}", style=color)
            if equity and len(equity) > 1:
                spark = sparkline_text(equity, width=40, bullish_color=color, bearish_color=color)
                result.append_text(spark)
            else:
                result.append("─" * 40, style=TEXT_MUTED)
            result.append("\n")

        return result


# ══════════════════════════════════════════════════════════════════════
# Strategy Screen
# ══════════════════════════════════════════════════════════════════════


class StrategyScreen(Screen):
    """Strategy Research screen — browse, search, evaluate, and compare strategies."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "go_back", "Back", show=False),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("e", "run_eval", "Evaluate", show=True),
        Binding("i", "init_deck", "Init Deck", show=True),
        Binding("c", "toggle_compare", "Compare", show=True),
        Binding("space", "toggle_select", "Select", show=True),
        Binding("s", "cycle_sort", "Sort", show=True),
        Binding("/", "focus_search", "Search", show=True),
        Binding("j", "move_down", "Down", show=False),
        Binding("k", "move_up", "Up", show=False),
        Binding("ctrl+c", "go_back", "Back", show=False),
        Binding("question_mark", "app.show_help", "Help", show=False),
    ]

    # Reactive state
    is_evaluating: reactive[bool] = reactive(False)
    eval_status: reactive[str] = reactive("")
    compare_mode: reactive[bool] = reactive(False)
    strategy_count: reactive[int] = reactive(0)

    DEFAULT_CSS = """
    StrategyScreen {
        layout: vertical;
    }
    """

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
        try:
            loading = self.query_one("#strategy-loading", LoadingIndicator)
            loading.loading = True
        except Exception:
            pass
        self._update_status("Loading strategies…")
        self.call_after_refresh(self._load_strategies)
        self._refresh_timer = self.set_interval(REFRESH_SECONDS, self._load_strategies)
        self._spinner_timer = self.set_interval(0.5, self._tick_spinner)

    async def on_unmount(self) -> None:
        """Clean up timers when leaving the screen."""
        if hasattr(self, "_refresh_timer"):
            self._refresh_timer.stop()
        if hasattr(self, "_spinner_timer"):
            self._spinner_timer.stop()

    def _tick_spinner(self) -> None:
        """Update the spinner animation during evaluation."""
        if self.is_evaluating:
            self._spinner_idx = (self._spinner_idx + 1) % len(_SPINNER)
            frame = _SPINNER[self._spinner_idx]
            self._update_status(f"{frame} {self.eval_status}")

    def _update_status(self, text: str) -> None:
        """Update the status bar text."""
        try:
            status = self.query_one("#strategy-status", Static)
            status.update(text)
        except Exception:
            pass

    # ── Data Loading ─────────────────────────────────────────────────

    async def _load_strategies(self) -> None:
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
            self._update_status(f"  {len(rows)} strategies loaded  |  [e]valuate  [c]ompare  [s]ort  [/]search")
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

    def action_go_back(self) -> None:
        """Go back to the main screen."""
        self.app.pop_screen()

    def action_refresh(self) -> None:
        """Force refresh strategy data."""
        self._results_cache.clear()
        self.run_worker(self._load_strategies())

    def action_focus_search(self) -> None:
        """Focus the search input."""
        try:
            search = self.query_one("#strategy-search", Input)
            search.focus()
        except Exception:
            pass

    def action_move_up(self) -> None:
        """Move selection up in the strategy list."""
        try:
            lw = self.query_one("#strategy-list", StrategyListWidget)
            lw.action_move_up()
            self._on_selection_changed()
        except Exception:
            pass

    def action_move_down(self) -> None:
        """Move selection down in the strategy list."""
        try:
            lw = self.query_one("#strategy-list", StrategyListWidget)
            lw.action_move_down()
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
            self._update_status(f"  Sorted by: {rt.sort_column}")
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
        self._update_status(f"Initializing deck '{self._deck}'…")
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
                self._update_status(f"  Comparing {count} strategies  |  [c] toggle view  [space] select")
        except Exception:
            pass
