"""Base widget classes for the SigLab TUI.

Provides reusable base classes that eliminate duplicated patterns
across list widgets and comparison widgets in screen modules.
"""

from __future__ import annotations

from typing import Any, ClassVar

from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Static

from siglab.tui.formatting import TEXT_MUTED


class FilterableListWidget(Static):
    """Base for list widgets with filtering, selection, and optional multi-select.

    Subclasses must implement:
    - ``_matches(item)`` — filter predicate
    - ``_render_item(item, index, is_selected)`` — per-item rendering
    - ``_get_item_key(item)`` — unique key for multi-select (optional)

    Provides:
    - ``selected_index`` reactive with navigation actions
    - ``_filter_text`` with ``set_filter()``
    - Multi-select via ``toggle_select()`` / ``get_selected_hashes()``
    - Single-pass filtering via ``_apply_filters()``
    """

    selected_index: reactive[int] = reactive(0)

    # Subclass sets this to the reactive list attribute name
    _items_reactive: ClassVar[str] = "items"
    # Set to True to enable multi-select
    _multi_select: ClassVar[bool] = False
    _max_select: ClassVar[int] = 4

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._all_data: tuple[Any, ...] = ()
        self._filter_text: str = ""
        self._selected_hashes: set[str] = set()

    # ── Data management ──────────────────────────────────────────────

    def set_data(self, items: list[Any]) -> None:
        """Store a reference to the data list as an immutable tuple."""
        self._all_data = tuple(items)
        self._apply_filters()

    def set_filter(self, text: str) -> None:
        """Update the text search filter."""
        self._filter_text = text.lower().strip()
        self._apply_filters()

    def _apply_filters(self) -> None:
        """Apply all active filters in a single pass."""
        filtered = [item for item in self._all_data if self._matches(item)]
        setattr(self, self._items_reactive, filtered)
        items = getattr(self, self._items_reactive)
        if items and self.selected_index >= len(items):
            self.selected_index = max(0, len(items) - 1)

    def _matches(self, item: Any) -> bool:
        """Override to add custom filter predicates. Default: text search only."""
        ft = self._filter_text
        if not ft:
            return True
        return ft in str(item).lower()

    # ── Navigation ───────────────────────────────────────────────────

    def action_move_up(self) -> None:
        if self.selected_index > 0:
            self.selected_index -= 1

    def action_move_down(self) -> None:
        items = getattr(self, self._items_reactive)
        if self.selected_index < len(items) - 1:
            self.selected_index += 1

    def get_current_item(self) -> Any | None:
        """Return the currently highlighted item."""
        items = getattr(self, self._items_reactive)
        if items and 0 <= self.selected_index < len(items):
            return items[self.selected_index]
        return None

    # ── Multi-select ─────────────────────────────────────────────────

    def toggle_select(self) -> None:
        """Toggle multi-select on the current item."""
        if not self._multi_select:
            return
        items = getattr(self, self._items_reactive)
        if not items or self.selected_index >= len(items):
            return
        key = self._get_item_key(items[self.selected_index])
        if not key:
            return
        if key in self._selected_hashes:
            self._selected_hashes.discard(key)
        elif len(self._selected_hashes) < self._max_select:
            self._selected_hashes.add(key)

    def get_selected_hashes(self) -> set[str]:
        """Return the set of multi-selected item keys."""
        return set(self._selected_hashes)

    def _get_item_key(self, item: Any) -> str | None:
        """Override to return a unique key for multi-select."""
        return None

    # ── Rendering ────────────────────────────────────────────────────

    def render(self) -> Text:
        items = getattr(self, self._items_reactive)
        if not items:
            return Text("  No items found", style=TEXT_MUTED)
        lines = Text()
        for i, item in enumerate(items):
            is_selected = i == self.selected_index
            lines.append_text(self._render_item(item, i, is_selected))
            lines.append("\n")
        return lines

    def _render_item(self, item: Any, index: int, is_selected: bool) -> Text:
        """Override in subclass to define per-item rendering."""
        return Text("")


class ComparisonWidget(Static):
    """Base for side-by-side comparison of 2+ items.

    Subclasses must define ``_metrics`` as a list of
    ``(label, key, format_str)`` tuples.
    """

    _COLORS: ClassVar[list[str]] = [
        "#4ade80",  # green
        "#60a5fa",  # blue
        "#f0b456",  # yellow
        "#a78bfa",  # purple
    ]

    _metrics: ClassVar[list[tuple[str, str, str]]] = []
    _empty_message: ClassVar[str] = "Select 2+ items with Space, then press c"
    _col_width_base: ClassVar[int] = 60

    items: reactive[list[dict[str, Any]]] = reactive(list, layout=True)

    def set_items(self, items: list[dict[str, Any]]) -> None:
        """Set items for comparison."""
        self.items = items

    def render(self) -> Text:
        from siglab.tui.formatting import BORDER_DIM, TEXT_PRIMARY, WARNING_YELLOW, truncate

        result = Text()
        result.append(" COMPARISON\n", style=f"bold {TEXT_PRIMARY}")

        if len(self.items) < 2:
            result.append(f"  {self._empty_message}\n", style=TEXT_MUTED)
            return result

        n = len(self.items)
        col_w = max(12, self._col_width_base // (n + 1))

        # Column headers
        header = Text()
        header.append("  ")
        for i, item in enumerate(self.items):
            name = self._get_item_name(item, i)[:col_w]
            color = self._COLORS[i % len(self._COLORS)]
            header.append(f"{name:<{col_w}}", style=f"bold {color}")
        header.append("DELTA", style=f"bold {WARNING_YELLOW}")
        result.append_text(header)
        result.append("\n")
        result.append("  " + "─" * (col_w * (n + 1) + 4) + "\n", style=BORDER_DIM)

        # Metrics rows
        for label, key, fmt in self._metrics:
            row = Text()
            row.append(f"  {label:<12}", style=TEXT_PRIMARY)

            values: list[float] = []
            for item in self.items:
                val = item.get(key)
                if val is not None and isinstance(val, (int, float)) and val == val:
                    values.append(float(val))

            for i, item in enumerate(self.items):
                val = item.get(key)
                color = self._COLORS[i % len(self._COLORS)]
                if val is None:
                    row.append(f"{'─':<{col_w}}", style=TEXT_MUTED)
                elif isinstance(val, bool):
                    status = "passed" if val else "failed"
                    row.append(f"{status:<{col_w}}", style=color)
                elif isinstance(val, str):
                    row.append(f"{truncate(val, col_w - 1):<{col_w}}", style=color)
                else:
                    formatted = fmt.format(val)
                    row.append(f"{formatted:<{col_w}}", style=color)

            # Delta column
            if values and len(values) >= 2 and key not in ("family", "track"):
                delta = max(values) - min(values)
                row.append(f"±{delta:.3f}", style=WARNING_YELLOW)
            elif key in ("family", "track"):
                unique_vals = len(set(str(item.get(key, "")) for item in self.items))
                row.append(
                    "diff" if unique_vals > 1 else "same",
                    style=WARNING_YELLOW if unique_vals > 1 else TEXT_MUTED,
                )

            result.append_text(row)
            result.append("\n")

        # Hook for extra content (e.g., equity overlay)
        extra = self._render_extra()
        if extra:
            result.append_text(extra)

        return result

    def _get_item_name(self, item: dict[str, Any], index: int) -> str:
        """Return display name for an item. Override for custom names."""
        return str(item.get("spec_hash", f"Item{index + 1}"))

    def _render_extra(self) -> Text | None:
        """Override to render additional content below the metrics table."""
        return None
