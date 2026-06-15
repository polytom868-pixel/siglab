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
from typing import Any, Callable, ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Input, Static

from siglab.tui.cli_bridge import MAX_COMPARE, parse_rows_from_json, run_cli
from siglab.tui.formatting import (
    BORDER_DIM,
    INFO_BLUE,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    format_drawdown,
    format_return,
    format_score,
    format_sharpe,
    format_status,
    render_list_item,
    safe_query,
)
from siglab.tui.loading import LoadingIndicator
from siglab.tui.screens.base import BaseScreen
from siglab.tui.widgets.base import ComparisonWidget, FilterableListWidget
from siglab.tui.widgets.sparkline import sparkline_text

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────

DEFAULT_DECK = "trend_signals_external"

# Spinner frames for evaluation progress
_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


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
        return render_list_item(
            hash_text=str(item.get("spec_hash", "?")),
            secondary_text=str(item.get("family", "")),
            score=item.get("aggregate_score"),
            passed=item.get("passed"),
            is_selected=is_selected,
            is_multi=str(item.get("spec_hash", "")) in self._selected_hashes,
        )


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

        # Responsive column selection based on available width
        avail = getattr(self.size, 'width', 120) or 120

        # All columns with their widths
        all_cols = [
            ("NAME", 14, "name"),
            ("FAMILY", 10, "family"),
            ("SCORE", 8, "score"),
            ("PnL%", 9, "pnl"),
            ("SHARPE", 8, "sharpe"),
            ("MAXDD", 8, "maxdd"),
            ("STATUS", 6, "status"),
            ("SPARKLINE", 16, "sparkline"),
        ]
        # Total width: sum of all = 89 + 2 indent = 91

        # Progressive column hiding:
        # >= 92: show all 8 columns (89 + indent)
        # >= 76: hide SPARKLINE (73 + indent)
        # >= 66: hide SPARKLINE + MAXDD (65 + indent)
        # >= 56: hide SPARKLINE + MAXDD + PnL% (56 + indent)
        # < 56: show NAME + SCORE + STATUS only (28 + indent)
        if avail >= 92:
            cols = all_cols
        elif avail >= 76:
            cols = [c for c in all_cols if c[2] != "sparkline"]
        elif avail >= 66:
            cols = [c for c in all_cols if c[2] not in ("sparkline", "maxdd")]
        elif avail >= 56:
            cols = [c for c in all_cols if c[2] not in ("sparkline", "maxdd", "pnl")]
        else:
            cols = [c for c in all_cols if c[2] in ("name", "score", "status")]

        # Header
        header = Text()
        header.append("  ")
        for name, width, _key in cols:
            marker = " ▼" if name.lower().replace("%", "").replace(" ", "_") == self.sort_column.replace("_", " ").replace(" ", "") else ""
            header.append(f"{name + marker:<{width}}", style=TEXT_MUTED)
        result.append_text(header)
        result.append("\n")
        col_total = sum(w for _, w, _ in cols) + 2  # +2 for leading indent
        result.append("  " + "─" * (col_total - 2) + "\n", style=BORDER_DIM)

        # Build column renderers
        col_renderers: dict[str, Callable[..., Any]] = {
            "name": lambda item, row: (
                row.append(f"{str(item.get('spec_hash', '?'))[:12]:<14}", style=TEXT_SECONDARY)
            ),

            "family": lambda item, row: (
                row.append(f"{str(item.get('family', ''))[:8]:<10}", style=INFO_BLUE)
            ),
            "score": lambda item, row: (
                row.append_text(format_score(item.get("aggregate_score"))),
                row.append("  " if item.get("aggregate_score") is not None else "    "),
            ),
            "pnl": lambda item, row: (
                row.append_text(format_return(item.get("validation_total_return"))),
                row.append(" " if item.get("validation_total_return") is not None else " "),
            ),
            "sharpe": lambda item, row: (
                row.append_text(format_sharpe(item.get("sharpe"))),
                row.append("  " if item.get("sharpe") is not None else "   "),
            ),
            "maxdd": lambda item, row: (
                row.append_text(format_drawdown(item.get("max_drawdown"))),
                row.append("  " if item.get("max_drawdown") is not None else "   "),
            ),
            "status": lambda item, row: (
                row.append_text(format_status(item.get("passed"))),
                row.append("   "),
            ),
            "sparkline": lambda item, row: (
                row.append_text(sparkline_text(item.get("equity_curve", []), width=14))
                if item.get("equity_curve") and len(item.get("equity_curve", [])) > 1
                else row.append("─" * 14, style=TEXT_MUTED)
            ),
        }

        # Rows
        for item in self._sorted_results()[:50]:  # Max 50 rows
            row = Text()
            row.append("  ")

            for _name, _width, key in cols:
                col_renderers[key](item, row)

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

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = BaseScreen.BINDINGS + [
        Binding("e", "run_eval", "Evaluate", show=True),
        Binding("i", "init_deck", "Init Deck", show=True),
        Binding("c", "toggle_compare", "Compare", show=True),
        Binding("space", "toggle_select", "Select", show=True),
        Binding("s", "cycle_sort", "Sort", show=True),
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
    _search_input_id: ClassVar[str] = "strategy-search"
    _search_list_id: ClassVar[str] = "strategy-list"

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
        self._update_status_text("Loading strategies\u2026")
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
            self._update_status_text(f"{frame} {self.eval_status}")

    async def _fetch_data(self) -> None:
        """Load strategy list from CLI ancestry command."""
        try:
            result = await run_cli("ancestry", "--json", timeout=15.0)
            if result.returncode == 0 and result.stdout.strip():
                self._update_strategy_list(parse_rows_from_json(result.stdout))
            else:
                logger.debug("ancestry returned non-zero or empty: %s", result.stderr[:100])
        except json.JSONDecodeError:
            logger.warning("Failed to parse ancestry JSON output")
        except Exception as exc:
            logger.debug("Failed to load strategies: %s", exc)

    def _update_strategy_list(self, rows: list[dict[str, Any]]) -> None:
        """Update the strategy list widget with loaded data."""
        widget = safe_query(self, "#strategy-list", StrategyListWidget)
        if widget is None:
            logger.warning("Strategy list widget not found")
            return
        widget.set_strategies(rows)
        self.strategy_count = len(rows)
        self._update_status_text(
            f"  {len(rows)} strategies loaded  |  [e]valuate  [c]ompare  [s]ort  [/]search"
        )

    async def _load_results_for_hash(self, spec_hash: str) -> dict[str, Any] | None:
        """Load detailed results for a single strategy hash."""
        if spec_hash in self._results_cache:
            return self._results_cache[spec_hash]
        try:
            result = await run_cli("ancestry", "--json", timeout=15.0)
            if result.returncode == 0 and result.stdout.strip():
                rows: list[dict[str, Any]] = [
                    r for r in parse_rows_from_json(result.stdout) if isinstance(r, dict)
                ]
                for row in rows:
                    if str(row.get("spec_hash", "")) == spec_hash:
                        cached: dict[str, Any] = row
                        self._results_cache[spec_hash] = cached
                        return cached
        except Exception as exc:
            logger.debug("Failed to load results for %s: %s", spec_hash, exc)
        return None

    def action_refresh_now(self) -> None:
        """Force refresh strategy data."""
        self._results_cache.clear()
        self.call_after_refresh(self._refresh_all)


    def action_toggle_select(self) -> None:
        """Toggle multi-select on current strategy for comparison."""
        lw = safe_query(self, "#strategy-list", StrategyListWidget)
        if lw:
            lw.toggle_select()
            self._update_comparison()

    def action_toggle_compare(self) -> None:
        """Toggle between results table and comparison view."""
        self.compare_mode = not self.compare_mode
        results = safe_query(self, "#results-table", ResultsTableWidget)
        comparison = safe_query(self, "#comparison-panel", ComparisonPanelWidget)
        if not results or not comparison:
            return
        if self.compare_mode:
            results.add_class("hidden")
            comparison.remove_class("hidden")
            self._update_comparison()
        else:
            comparison.add_class("hidden")
            results.remove_class("hidden")

    def action_cycle_sort(self) -> None:
        """Cycle the sort column in the results table."""
        rt = safe_query(self, "#results-table", ResultsTableWidget)
        if rt:
            rt.cycle_sort()
            self._update_status_text(f"  Sorted by: {rt.sort_column}")

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
                await self._fetch_data()
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
                await self._fetch_data()
            else:
                self.notify(f"Init failed: {result.stderr[:80]}", severity="error")
        except Exception as exc:
            self.notify(f"Init error: {exc}", severity="error")

    # ── Event Handlers ───────────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        """Handle search input changes."""
        if self._on_search_input_changed(event):
            # Update strategy count after filter
            lw = safe_query(self, "#strategy-list", StrategyListWidget)
            if lw:
                self.strategy_count = len(lw.strategies)

    def _on_selection_changed(self) -> None:
        """Update detail panel when selection changes."""
        lw = safe_query(self, "#strategy-list", StrategyListWidget)
        if lw:
            current_hash = lw.get_current_hash()
            if current_hash:
                self.run_worker(self._update_results_for_current(current_hash))

    async def _update_results_for_current(self, spec_hash: str) -> None:
        """Load and display results for the currently selected strategy."""
        detail = await self._load_results_for_hash(spec_hash)
        if detail:
            safe_query(self, "#results-table", ResultsTableWidget,
                       lambda w: w.set_results([detail]))

    def _update_comparison(self) -> None:
        """Update the comparison panel with selected strategies."""
        lw = safe_query(self, "#strategy-list", StrategyListWidget)
        comparison = safe_query(self, "#comparison-panel", ComparisonPanelWidget)
        if not lw or not comparison:
            return
        selected = lw.get_selected_hashes()
        strats = [c for h in selected if (c := self._results_cache.get(h))]
        comparison.set_strategies(strats)
        if len(strats) >= 2:
            self._update_status_text(f"  Comparing {len(strats)} strategies  |  [c] toggle view  [space] select")
