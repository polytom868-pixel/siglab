# SigLab TUI — Design & Accessibility Audit

**Auditor:** Wave1 Agent3 (Design + Accessibility)
**Date:** 2026-06-04
**Scope:** `siglab/tui/` — all Python screens, widgets, CSS themes, and formatting helpers

---

## WCAG AA Contrast Ratios

Computed via relative luminance per WCAG 2.1. Thresholds: **4.5:1** (normal text), **3:1** (large text / UI components).

| Foreground | Background | Ratio | AA (4.5:1) | AAA (7:1) |
|---|---|---|---|---|
| TEXT_PRIMARY `#e2ebe5` | BG `#0a0a0a` | **16.4:1** | PASS | PASS |
| TEXT_SECONDARY `#a3b5a8` | SURFACE `#0d1210` | **8.9:1** | PASS | PASS |
| TEXT_MUTED `#7d9483` | BG `#0a0a0a` | **6.1:1** | PASS | FAIL |
| TEXT_MUTED `#7d9483` | SURFACE `#0d1210` | **5.8:1** | PASS | FAIL |
| ACCENT_GREEN `#4ade80` | BG `#0a0a0a` | **11.4:1** | PASS | PASS |
| ERROR_RED `#f87171` | BG `#0a0a0a` | **7.2:1** | PASS | PASS |
| WARNING_YELLOW `#f0b456` | BG `#0a0a0a` | **10.7:1** | PASS | PASS |
| INFO_BLUE `#60a5fa` | BG `#0a0a0a` | **7.8:1** | PASS | PASS |
| INFO_BLUE `#60a5fa` | SURFACE `#0d1210` | **7.4:1** | PASS | PASS |
| BG `#0a0a0a` (black text) | ACCENT_GREEN `#4ade80` | **12.0:1** | PASS | PASS |

**Verdict:** All foreground/background combinations meet WCAG AA. TEXT_MUTED fails AAA on dark backgrounds, which is acceptable for decorative/secondary text but means muted labels should not carry critical information.

---

## Design & Accessibility Findings

### 1. Hardcoded Colors — Should Use Constants or CSS Variables

**Severity: HIGH (maintainability / theme consistency)**

Nearly every screen file duplicates hex color literals instead of importing from `formatting.py` or referencing `$variable` in CSS. This makes theme changes require editing 10+ files.

#### 1a. Python files with hardcoded hex instead of `formatting.py` imports

| File | Lines | Hardcoded Values | Should Use |
|---|---|---|---|
| `screens/market.py` | L40-60 | `_format_change` uses `"#4ade80"`, `"#f87171"`, `"#7d9483"` | `ACCENT_GREEN`, `ERROR_RED`, `TEXT_MUTED` from `formatting` |
| `screens/market.py` | L45-60 | `_format_volume`, `_format_price` duplicate `formatting.py` functions | Import `format_price`, `format_volume` from `formatting` |
| `screens/market.py` | L100+ | `SymbolListWidget.render` uses `"#7d9483"`, `"#000000 on #4ade80"`, `"#a3b5a8"` | Import constants |
| `screens/market.py` | L170+ | `TickerTableWidget.render` uses `"#7d9483"`, `"#2a3a30"`, `"#a3b5a8"`, `"#e2ebe5"`, `"#60a5fa"` | Import constants |
| `screens/market.py` | L220+ | `OrderBookWidget.render` uses `"#4ade80"`, `"#f87171"`, `"#2a3a30"`, `"#a3b5a8"`, `"#e2ebe5"`, `"#7d9483"` | Import constants |
| `screens/paper.py` | L48-80 | `_fmt_pnl`, `_status_style`, `_side_style` duplicate `formatting.py` helpers | Import `format_pnl`, `format_status` from `formatting` |
| `screens/paper.py` | L100+ | `PositionsTableWidget.render` uses `"#7d9483"`, `"#2a3a30"`, `"#a3b5a8"`, `"#e2ebe5"`, `"#60a5fa"` | Import constants |
| `screens/paper.py` | L180+ | `OrderFormWidget.render` uses `"#000000 on #4ade80"`, `"#000000 on #f87171"`, `"#000000 on #60a5fa"` | Import constants |
| `screens/risk.py` | L50-80 | `_gauge_color`, `_severity_color`, `_correlation_color` duplicate semantics | Import constants |
| `screens/strategy.py` | L50-120 | All 6 formatting helpers (`_format_score`, `_format_return`, `_format_sharpe`, `_format_drawdown`, `_format_status`, `_truncate`) are exact duplicates of `formatting.py` | Import from `formatting` |
| `screens/strategy.py` | L310 | `ComparisonPanelWidget._STRAT_COLORS` uses `"#a78bfa"` (purple) | Add purple to `formatting.py` or theme tokens |
| `screens/telemetry.py` | L50-130 | All formatting helpers duplicated from `formatting.py` | Import from `formatting` |
| `screens/telemetry.py` | L500+ | `RunComparisonWidget._RUN_COLORS` uses `"#a78bfa"` (purple) | Add purple to `formatting.py` or theme tokens |
| `screens/evidence.py` | L80-100 | `_kind_style`, `_format_confidence` use hardcoded hex | Import constants |
| `widgets/status_bar.py` | L50 | Uses Rich named colors `"green"`, `"red"` instead of hex constants | Use `ACCENT_GREEN`, `ERROR_RED` |

#### 1b. CSS files with hardcoded hex instead of `$variable` references

| File | Location | Hardcoded Value | Should Use |
|---|---|---|---|
| `app.py` HelpScreen DEFAULT_CSS | L78 | `background: #0d1210`, `border: solid #2a3a30`, `color: #4ade80`, `color: #60a5fa`, `color: #7d9483` | `$surface`, `$border-dim`, `$accent-green`, `$info-blue`, `$text-muted` |
| `app.py` PlaceholderScreen DEFAULT_CSS | L53 | `color: #7d9483` | `$text-muted` |
| `screens/paper.py` _TextInputScreen DEFAULT_CSS | L360+ | `background: #0d1210`, `border: solid #2a3a30`, `color: #4ade80`, `background: #1a2a1f` | `$surface`, `$border-dim`, `$accent-green`, `$input-bg` |
| `screens/risk.py` RiskGaugeWidget DEFAULT_CSS | L120 | `background: #0d1210` | `$surface` |
| `screens/risk.py` DrawdownSparklineWidget DEFAULT_CSS | L155 | `background: #0a0a0a` | `$bg` |
| `screens/risk.py` CorrelationHeatmapWidget DEFAULT_CSS | L195 | `background: #0d1210` | `$surface` |
| `screens/risk.py` AlertStreamWidget DEFAULT_CSS | L235 | `background: #0a0a0a` | `$bg` |
| `screens/telemetry.py` ProviderMetricsWidget DEFAULT_CSS | L250 | `background: #0d1210` | `$surface` |
| `screens/telemetry.py` ToolUsageWidget DEFAULT_CSS | L320 | `background: #0a0a0a` | `$bg` |
| `screens/telemetry.py` RunDetailWidget DEFAULT_CSS | L380 | `background: #0d1210` | `$surface` |
| `screens/telemetry.py` RunComparisonWidget DEFAULT_CSS | L430 | `background: #0a0a0a` | `$bg` |
| `screens/telemetry.py` ServiceHealthWidget DEFAULT_CSS | L470 | `background: #0d1210` | `$surface` |

**Recommendation:** Replace all hardcoded hex values with imports from `formatting.py` (for Rich Text styles) or `$variable` references (for TCSS). Add the missing purple (`#a78bfa`) to `formatting.py` as `COMPARISON_PURPLE` and to `theme.tcss` as `$comparison-purple`.

---

### 2. Duplicate Formatting Functions

**Severity: MEDIUM (DRY violation / maintenance burden)**

| Screen File | Duplicated Functions | Already in `formatting.py` |
|---|---|---|
| `screens/market.py` | `_format_price`, `_format_change`, `_format_volume` | `format_price`, `format_change`, `format_volume` |
| `screens/paper.py` | `_fmt_price`, `_fmt_pnl` | `format_price`, `format_pnl` |
| `screens/strategy.py` | `_format_score`, `_format_return`, `_format_sharpe`, `_format_drawdown`, `_format_status`, `_truncate` | `format_score`, `format_return`, `format_sharpe`, `format_drawdown`, `format_status`, `truncate` |
| `screens/telemetry.py` | `_format_score`, `_format_latency`, `_format_status`, `_truncate` | `format_score`, `format_latency`, `format_status`, `truncate` |

**Recommendation:** Delete all duplicate helper functions. Import from `siglab.tui.formatting` instead. This ensures any future color or logic changes propagate automatically.

---

### 3. Missing Design Tokens

**Severity: MEDIUM**

| Missing Token | Used In | Recommendation |
|---|---|---|
| Purple `#a78bfa` | `strategy.py` `_STRAT_COLORS`, `telemetry.py` `_RUN_COLORS` | Add `COMPARISON_PURPLE = "#a78bfa"` to `formatting.py` and `$comparison-purple: #a78bfa` to `theme.tcss` |
| Focus ring color | All search inputs use `$border-focus` (= `$accent-green`) | Consider a distinct focus color (e.g., `$info-blue`) for inputs to differentiate from success states |
| Error background | Error messages use inline `style="#f87171"` text | Add `$error-bg` token for error message backgrounds if error panels are added |

---

### 4. Screen Transition Effects

**Severity: LOW**

- **Finding:** No explicit screen transition animations are defined. Textual's `push_screen()` uses an instant swap.
- **File:** `app.py` — all `action_switch_to_*` methods call `push_screen()` directly.
- **Recommendation:** Textual v0.40+ supports `Screen.ANIM_IN` / `Screen.ANIM_OUT` with slide/fade transitions. Consider adding subtle slide transitions (e.g., `ANIM_IN = "slide left"`) to screen classes for spatial orientation. Keep duration under 200ms to avoid feeling slow.

---

### 5. Micro-interactions

**Severity: LOW**

| Element | Current State | Finding |
|---|---|---|
| Nav sidebar items | Hover: `background: $surface-raised, color: $text-primary` | Good |
| Nav sidebar items | Focus: `border-left: tall $border-focus` | Good |
| Nav sidebar items | Active: `background: $accent-green, color: $bg` | Good |
| Search inputs | Focus: `border: tall $border-focus` | Good |
| List selections | Green background with black text | Good |
| Loading spinner | Animated braille cycling at 100ms | Good |
| Help overlay | Background dimming with modal | Good |
| Non-nav widgets | No hover/focus feedback | **Missing** — table rows, order book rows, alert entries have no hover state |
| Button-like elements | Order form BUY/SELL toggles render as static text | **No click/hover feedback** — these are `Static` widgets, not `Button` |

**Recommendation:** Add hover states to interactive Static widgets (e.g., table rows that are selectable) using CSS `:hover` pseudo-class. Convert the BUY/SELL and MARKET/LIMIT toggles in the order form to `Button` widgets for proper hover/active/focus states.

---

### 6. Keyboard Navigation Completeness

**Severity: LOW**

| Screen | Coverage | Notes |
|---|---|---|
| Global (app.py) | Excellent | 1-6 screen switch, q quit, ? help, Escape back |
| Market | Good | j/k nav, / search, Enter select, r refresh |
| Paper | Good | s/b/t/Q/p form fields, Enter submit, n new session |
| Risk | Good | j/k scroll, f filter cycle, r refresh |
| Strategy | Good | j/k nav, / search, Space select, c compare, e eval, s sort |
| Telemetry | Good | j/k nav, / search, Space select, c compare, d/f/t filters, v view toggle |
| Evidence | Good | / filter, Tab pane switch, n/p step nav, a run all, Enter run step |

**Issue:** The `PaperScreen` order form uses `Static` widgets for BUY/SELL and MARKET/LIMIT toggles. These are not focusable and cannot be activated via keyboard alone — they rely on screen-level key bindings (`b` for buy/sell, `t` for type). This works but is not discoverable via Tab navigation.

**Recommendation:** Consider making toggle buttons focusable `Button` widgets so Tab navigation reaches them, or add a focus indicator to the order form section.

---

### 7. Terminal Resize Handling

**Severity: LOW**

- **Positive:** Layouts use `1fr` for flexible columns, `min-width`/`max-width` constraints, and `overflow-y: auto` on scrollable areas.
- **Positive:** Sidebar has `width: 24; min-width: 20; max-width: 30`.
- **Issue:** At very narrow terminals (< 80 columns), the two-column layouts (market, paper, risk, strategy, telemetry, evidence) will have content overflow or truncation. No responsive breakpoint collapses to a single column.
- **Issue:** Fixed-width columns in paper screen (`width: 40; min-width: 34; max-width: 44`) may push the right column too narrow.

**Recommendation:** Textual does not support CSS breakpoints natively. Consider adding a reactive `is_compact` mode that detects terminal width on mount/resize and switches to a stacked layout when width < 80 columns.

---

### 8. Visual Hierarchy

**Severity: LOW**

- **Positive:** Section headers use `bold #e2ebe5` (TEXT_PRIMARY) — strong hierarchy.
- **Positive:** Title bar uses `bold` ACCENT_GREEN — draws attention.
- **Positive:** Status bar uses TEXT_MUTED — properly recedes.
- **Positive:** Selected items use green background with black text — high contrast highlight.
- **Issue:** Column headers in tables use TEXT_MUTED (`#7d9483`) which at 5.8-6.1:1 contrast is readable but could be stronger. Consider bumping to TEXT_SECONDARY (`#a3b5a8`) at 8.9:1 for better scannability.

---

### 9. Typography Consistency

**Severity: LOW**

- **Positive:** Bold used consistently for headers and section titles.
- **Positive:** Italic used sparingly (placeholder "Coming soon" text only).
- **Positive:** Consistent monospace rendering (terminal context).
- **Issue:** The `_render_binding` method in `HelpScreen` (app.py L165) uses `"bold #60a5fa"` for key names and `"#7d9483"` for descriptions, but this style is defined in Python, not in the CSS theme. If the help dialog CSS is updated, this inline style won't follow.

---

### 10. Accessibility — Screen Reader / ARIA

**Severity: MEDIUM**

- **Finding:** Textual TUIs operate in terminal environments where traditional ARIA does not apply. However, Textual provides `aria_role` and `aria_label` properties on widgets.
- **Issue:** No widgets in the SigLab TUI set `aria_role` or `aria_label`. Screen readers (via terminal accessibility tools) will fall back to widget content, which may not be descriptive.
- **Recommendation:** Add `aria_label` to key interactive widgets:
  - `NavSidebar`: `aria_label="Navigation sidebar"`
  - `LoadingIndicator`: `aria_role="progressbar"` when loading
  - Search inputs: already have `placeholder` which serves as label
  - `HelpScreen`: `aria_role="dialog"`

---

### 11. Focus Management

**Severity: LOW**

- **Positive:** Search inputs gain focus explicitly via `action_focus_search`.
- **Positive:** Help overlay and text input modals focus their input on mount.
- **Issue:** When dismissing a modal (Escape), focus returns to whatever was focused before, which is correct Textual behavior. However, there is no visible focus ring on most widgets — only nav items and search inputs have explicit focus styling.
- **Recommendation:** Add a global focus-visible style in `theme.tcss`:
  ```css
  :focus-visible {
      border: tall $border-focus;
  }
  ```

---

### 12. Color Theme — Semantic Consistency Audit

**Severity: LOW (mostly good)**

| Semantic Role | Expected Color | Consistent? | Exceptions |
|---|---|---|---|
| Gain / Success / Positive | ACCENT_GREEN `#4ade80` | Yes | — |
| Loss / Error / Negative | ERROR_RED `#f87171` | Yes | — |
| Warning / Caution | WARNING_YELLOW `#f0b456` | Yes | — |
| Info / Link / Data | INFO_BLUE `#60a5fa` | Yes | — |
| Muted / Disabled | TEXT_MUTED `#7d9483` | Yes | — |
| Deployed / Active | INFO_BLUE `#60a5fa` | Yes | Consistent across `formatting.py`, `paper.py`, `strategy.py`, `telemetry.py` |

---

## Summary of Findings by Severity

| Severity | Count | Key Issues |
|---|---|---|
| **Critical** | 0 | — |
| **High** | 1 | Hardcoded hex colors in 12+ files instead of constants/CSS variables |
| **Medium** | 3 | Duplicate formatting functions (6+ files), missing design tokens (purple), no ARIA labels |
| **Low** | 8 | No screen transitions, missing hover states on non-nav elements, no responsive breakpoints, focus ring gaps, column header contrast could improve, inline styles in HelpScreen, order form toggles not focusable, TEXT_MUTED fails AAA |

## Prioritized Recommendations

1. **[High]** Centralize all hex colors: replace hardcoded values in screen files with imports from `formatting.py` (Rich styles) and `$variable` references (TCSS). Estimated: ~15 files, ~200 replacements.
2. **[High]** Delete duplicate formatting helpers in `market.py`, `paper.py`, `strategy.py`, `telemetry.py` and import from `formatting.py`.
3. **[Medium]** Add `COMPARISON_PURPLE = "#a78bfa"` to `formatting.py` and `$comparison-purple` to `theme.tcss`.
4. **[Medium]** Add `aria_label` to `NavSidebar`, `LoadingIndicator`, and modal screens.
5. **[Low]** Add global `:focus-visible` border style in `theme.tcss`.
6. **[Low]** Consider screen transition animations (`ANIM_IN`/`ANIM_OUT`) for spatial orientation.
7. **[Low]** Add hover states to interactive Static widgets (table rows, selectable lists).
8. **[Low]** Bump column header color from TEXT_MUTED to TEXT_SECONDARY for better scannability.
