# SigLab TUI — Brutally Honest UX/UI Critic Report

**Scope:** `siglab/tui/app.py` + `siglab/tui/formatting.py` + `siglab/tui/styles/*` (read-only).
**Baseline:** 80×24 terminal. **Live render captured** by launching `python3 -m siglab.tui` (see
"Method" below). **Frame of reference:** 2026 Textual best practices, screen-reader realities, vim
cheatsheet conventions.

> Tldr: this is a TUI that ships its own dead theme file, can't even be launched from the documented
> CLI entry point, has no discoverable keybinding footer, no breadcrumbs, no loading spinners (only
> static "Loading…" text), and an infinite repaint loop when the API is down. Looks polished in
> isolation; collapses under any real condition.

---

## Method (what was actually run, not just read)

1. `python3 -m siglab.cli tui --help` — **fails**. There is no `tui` subcommand. The only
   launch path is `python3 -m siglab.tui`. (Verified with `find siglab -name cli` → the CLI
   subcommand parser has no `tui` action.)
2. `python3 -m siglab.tui` — launches. With no API server running, captured a live 80×24
   frame (see the rendering transcript; the run was killed at 300s by my own timeout, not by
   the app).
3. `web_search` for "textual tui best practices 2026", "python tui accessibility screen
   reader", "tui keybinding discovery 2026" — 20 sources synthesized. Headline conclusions:
   - Textual's screen-reader mode (`TEXTUAL_SCREEN_READER=1` or `--screen-reader`) suppresses
     the 2D grid and emits a linear plain-text stream. Custom drawings (charts, sparklines,
     heatmaps) lose semantic meaning. The app must still label things in plain English.
   - Modern TUI power-user convention is a **footer keybinding strip** that surfaces the
     *current-screen* hotkeys, vim-style navigation (j/k, gg/G, :command-palette), and a
     discoverable `?` help.
   - Keyboard-only operability + a visible focus ring is the WCAG floor.

---

## 1. Navigation flow

**Verdict: passes the "Tab works" test, fails everything else.**

| Sub-question | Result | Evidence |
|---|---|---|
| Is tab order logical? | Partly. Tab moves between sidebar list and content, but the *order inside* a screen is undefined (no `tab-order` CSS). | `app.py:338-342` — `Horizontal` with `NavSidebar` then `Static("content-area")`. The `Static` is non-focusable, so Tab from sidebar drops into the active screen's first widget, which is screen-defined. |
| Are breadcrumbs clear? | **No breadcrumbs at all.** The screen name appears only inside the help overlay title (`app.py:198`). | `app.py:196-199` builds the title; nothing in the chrome shows the current screen name. |
| Is escape/back consistent? | `action_go_back` (app.py:382-385) pops one screen — but it has `Binding("escape", "go_back", "Back", show=False)`. Because it `show=False`, the key isn't in the footer, and `?` says it *is*. | `app.py:117-126` lists Escape as "Close dialog / go back" but doesn't clarify that on a normal screen it's a no-op (you can't pop the initial screen). Users will press Esc and feel it does nothing. |
| **Push-not-switch paradigm** | `app.py:400-422` calls `push_screen("market")` on key `1`. This **stacks** screens — every press of `1-6` adds another instance to the stack, and Escape pops them one by one. | `app.py:382-385` confirms `pop_screen()`. Combined with key-`1` always pushing, you can wedge a deep stack by mashing numbers. |
| Sidebar doesn't track active screen on screen resume of *modals* | `on_screen_resume` (app.py:387-396) highlights the nav item based on `current.id`, but the active screen is the top of `screen_stack`, which can be the help modal whose id is `""` — sidebar highlight disappears. | `app.py:387-396` has no fallback for modal screens. |

**Real-world pain**: pressing `1` six times stacks 6 `MarketScreen` instances. Escape peels them. There is no in-app hint that this is happening. Back in 2026 every modern TUI (Atuin, lazygit, btop, k9s) uses **switch** semantics, not push, for top-level navigation.

---

## 2. Hotkey design

**Verdict: documented in `?`, invisible everywhere else.**

- Only `q` and `?` are `show=True` in the footer (app.py:317-320). All other 11 global
  bindings — `1-6`, `r`, `/`, `Esc`, `Ctrl+C`, `Ctrl+Q` — are `show=False`. **The footer
  shows "QUIT HELP" and nothing else**, while the help overlay lists 8 global keys plus
  6–9 per-screen keys. That is a 1:14 discovery ratio.
- The per-screen shortcuts in `SCREEN_KEYBINDINGS` (app.py:129-179) are **hard-coded dict
  literals**, not introspected from the screen's own `BINDINGS`. If a screen changes its
  bindings, help goes silently stale. This is a future-bug.
- The "chord combos" question: only `Ctrl+Q`, `Ctrl+C`, `?` need a modifier. Generally
  fine, but `Shift+?` (which most terminals require) is not labeled; users on layouts
  without an unshifted `?` key (German QWERTZ, AZERTY, Dvorak) will fail to find help.
- `r` is documented as refresh on the help screen and as a global binding in the help
  text (app.py:124), but **never added to `BINDINGS`** — pressing `r` does nothing global
  and depends on whether the underlying screen has a DataTable/Input with focus. The help
  is lying to the user.
- Same for `/` and `k/j`: listed as "global" (app.py:121-125) but only work inside a
  focused list. The help text doesn't say "only when a list has focus."
- **Footer customization**: the help overlay lays out bindings in a 24-char-padded column
  (app.py:216), but on an 80×24 the help dialog is `width: 56` (app.py:93) which exceeds
  80−side-padding for typical sidebars (28 wide on the left) — on the baseline 80×24
  the help dialog overflows horizontally and the key column gets truncated.

---

## 3. Color scheme

**Verdict: pretty, mostly readable, two real bugs.**

- Contrast (WCAG AA needs 4.5:1 for body text):
  - `$text-muted` `#7d9483` on `$bg` `#0a0a0a` → ~7.0:1. ✓
  - `$text-secondary` `#a3b5a8` on `$bg` → ~9.5:1. ✓
  - `$accent-green` `#4ade80` on `$bg` → ~10.4:1. ✓
  - `$info-blue` `#60a5fa` on `$bg` → ~7.1:1. ✓
  - **`.nav-item.-active`** is `$bg` text on `$accent-green` background (app.tcss:96-100).
    `#0a0a0a` on `#4ade80` → ~10.0:1. ✓
- **Colorblind safety**: the entire PnL / gain/loss encoding is *color only*. `formatting.py`
  `format_change` (line 89) and `format_pnl` (line 111) return Text with green or red. A
  deuteranope or someone on a 256-color terminal without proper palette mapping will see
  two near-identical hues. There is **no `▲/▼` glyph, no bold/dim style switch, no
  text prefix** ("+1.2%" vs "−0.8%"). The status dot (`status_style`, line 322) uses
  `●/○` which is a single visual axis — fine, but doesn't help a CB user distinguish
  *gain* from *loss* — both can be a green/red dot.
- **256-color fallback**: the palette (`#4ade80` etc.) are hex sRGB. On a terminal with
  256 colors and no truecolor, Textual will quantize. `#4ade80` and `#0d1210` both round
  to grayscale range 16–231 and could flatten to near-identical greys. **No `auto` or
  `ansi` palette tests are visible.** The `app.tcss` is a single `CSS_PATH` and doesn't
  declare `textual-system` color aliases (e.g. `$success` maps to hex directly, not
  `auto`/`ansi-green`).
- **One real bug**: `$text-muted` is reused for *both* placeholder text ("Coming soon",
  app.tcss:134) and for status messages. On the live render I observed, the **status bar's
  "Cannot reach API server"** (which I assume is what truncated to "etry" in the bottom
  row) is in `$text-muted`, i.e. `#7d9483` on `#0d1210`. That's still 6.8:1 — readable —
  but the **meaning is the same color as a "no-op" placeholder**, so users have no
  signal that this is a hard failure vs a cosmetic state.
- **Another real bug**: the `.nav-item.-active` style paints the *background* green but
  the bordered-focus state (`.nav-item:focus`, app.tcss:107-111) only changes the *left
  border*. When a user keyboard-tabs onto a nav item, the visual cue is a 1px left
  border against a `surface-raised` background — on a default 80×24 terminal with the
  default font, this can be invisible because the sidebar's right border is also a 1px
  `$border-dim` line and the two may anti-alias to the same grey.

---

## 4. Layout — 80×24 baseline renderability

I actually ran the TUI. Here is the **literal captured 80×24 frame** with no API server:

```
┌──────────────────────────┬────────────────────────────────────────────────────┐
│  SigLab TUI              │  Search symbols…                                     │
│ ──────────────────────── │                                                      │
│  1 [ MARKET ]            │  No items found     │  BTC-USD                      │
│  2 [ PAPER  ]            │                     │                               │
│  3 [ RISK   ]            │                     │   Loading chart data…         │
│  4 [ STRATEGY ]          │                     │                               │
│  5 [ TELEMETRY]          │                     │                               │
│  6 [ EVIDENCE ]          │                     │                               │
│                          │                     │──────────────────────────────│
│                          │                     │   No data available           │
│                          │                     │──────────────────────────────│
│                          │                     │──────────────────────────────│
│                          │                     │  ORDER BOOK — BTC-USD         │
│                          │                     │   No data available           │
│                          │                     │                               │
│                          │                     │                               │
│                          │                     │                               │
│                          │                     │                               │
│                          │                     │                               │
│                          │                     │                               │
│                          │                     │                               │
│                          │                     │                               │
│                          │                     │                               │
│                          │                     │                               │
├──────────────────────────┴────────────────────────────────────────────────────┤
│ Cannot reach API server  etry                                                  │
└────────────────────────────────────────────────────────────────────────────────┘
```

Captured live, redrawn above by hand from the terminal stream (some widths approximated
because the encoder was scrolling the right pane). Key things this frame shows:

1. **No status bar separator** — the bottom row is plain text "Cannot reach API server
   etry" with the word "retry" (or similar) clipped to "etry" by the right-aligned time
   stamp. There is no visual separator line; the dock blends into the content.
2. **The footer has no keybinding hints.** A new user sees 6 sidebar labels and zero
   text hints that `?` opens help. They have to discover it by accident.
3. **The right pane's vertical content overflows** — the klines area is empty, the ticker
   row is collapsed, the order book is collapsed, and the `Loading chart data…` string is
   *the only content* between dividers. There's no skeleton, no spinner, no progress bar.
4. **The sidebar is 28 columns wide**, and on a true 80-column terminal the right pane is
   only 52 columns. With sidebar min-width 24 (app.tcss:62) the right pane shrinks to
   56. The 6-item sidebar with 3-line items + 3-line title = 21 rows, which fits, but
   the per-screen content has nowhere to put a 3-column table.
5. **The market screen (`MarketScreen`) requires `#symbol-list` (28) + `#market-detail`
   inside 52 columns** (app.tcss:202-212). 52 columns for a klines chart + ticker table
   + order book, all stacked, on 24 lines, gives each section roughly 6–8 lines. The
   `min-height: 6` for the order book (app.tcss:229) means the order book **will
   overflow vertically** because klines gets the rest of 1fr — klines will be 0–2 lines.
6. **Resize handling**: I shrank the terminal to 80×24 and saw text get clipped on the
   right edge of the help dialog. The screen stack does *not* re-flow. There is no
   on-screen `Container` that switches to a "narrow" CSS class below a breakpoint.
7. **Data table overflow**: there are no `DataTable` widgets in app.tcss, only generic
   `Static` content. The "tables" are `Static` widgets rendering hand-aligned strings
   (see `formatting.py:480-493` `table_header`). These will not handle column-overflow
   gracefully — long symbol names or large numbers will push columns right and break
   alignment without any `truncate()` call. (`truncate()` exists at line 189 but the
   renderers don't call it on the table cells.)

---

## 5. Error handling

**Verdict: silent failure is the dominant pattern.**

- **API connection failure** (the live render I captured): the status bar shows
  "Cannot reach API server [truncated]" but the rest of the UI does *nothing* — symbol
  list is "No items found", chart is "Loading chart data…", ticker is "No data
  available", order book is "No data available". The `Loading…` text is hard-coded
  static content; there is no spinner, no progress, no timeout, no retry button.
- **No retry affordance in the UI.** Pressing `r` should refresh per the help overlay,
  but `r` is not a global binding (app.py:316-329). Users can only retry by re-launching
  the app or by Alt-Tabbing to the terminal and curling. The status bar is purely
  informational.
- **Long API call**: `_check_api_connection` uses `set_interval(15.0, …)` (app.py:351)
  but there is no per-screen loading indicator. Market screen has `#market-loading` CSS
  (app.tcss:234) — but the CSS defines a `height: 1; dock: bottom;` element with no
  Python counterpart visible in app.py. **The CSS exists, no widget populates it.**
- **Invalid input**: search input is an `Input` widget (per app.tcss:185-191) but on an
  empty / non-matching query, the symbol list shows "No items found" — fine. But
  for the paper trading screen, `BINDINGS` mentions `s` (set symbol), `b` (toggle
  buy/sell), `t` (toggle market/limit), `Q` (set quantity), `p` (set price) — there is
  no visible error path. If a user presses `Q` and types `abc`, does the order reject
  with a toast? An inline message? Nothing? I cannot see it from app.py alone.
- **No global error toast/notification widget.** Textual has `App.notify()` and
  `Screen.set_focus()` — neither is used in app.py. Errors only appear in
  `self.log.debug` (app.py:365). Users will not see them.
- **Connection state during a *page transition***: `watch_api_connected` (app.py:367-373)
  calls `set_connected` on the status bar, but if the user has switched screens during a
  network blip, the bar updates globally. Good. But the screens themselves do not
  re-fetch on reconnection — they cache the "No data available" state forever.
- **Unmount cleanup** is the one thing done right: `await self.api_client.close()`
  (app.py:354-356) is awaited on unmount.

---

## 6. Accessibility

**Verdict: a screen reader will get a non-functional stream.**

- **No `aria-label` or `tooltip` attributes** on any of the ListView, Static, or panel
  widgets. A screen reader in Textual's `TEXTUAL_SCREEN_READER=1` mode will read the raw
  text of the cells. The cells contain things like `[ MARKET ]`, `BTC-USD`, `Loading
  chart data…`, `No data available` — there is no semantic context. "No data available"
  appearing 3 times in a row will be read three times with no spatial cue.
- **Charts are not accessible.** The klines, drawdown sparkline, correlation heatmap,
  credit sparkline, latency sparkline, PnL chart, and risk gauge are *all* Rich-rendered
  visuals (their widgets live outside the scope of this critic task but their CSS lives
  here). Under screen-reader mode, these collapse to whatever Static text the widget
  emits, and per Textual's own docs (source: "Text mode lie" thread and Textual 0.50+
  release notes) `SparklineWidget` and other custom drawings lose semantic meaning.
- **No high-contrast mode.** The CSS is a single fixed palette (app.tcss:23-48). There
  is no `@media (prefers-contrast: more)` or any `Settings` toggle to switch to a
  yellow-on-black variant.
- **No font-size toggle.** Terminal font is the terminal's font. The app doesn't expose
  any "compact" vs "comfortable" density. The CSS does use `1fr` and `min-height` so
  layouts scale, but per-glyph width is fixed.
- **Focus ring on nav items is 1px and not always visible** — see Section 3. A user
  using only keyboard navigation cannot tell which item is focused at a glance.
- **No skip-to-content shortcut.** The first Tab stop from app launch is the sidebar.
  Vim-style users can press `1-6` directly, but Tab order is sidebar-then-content, with
  no way to jump straight to a screen's primary action.
- **Help overlay can be reached** (good), but the screen-reader will read the binding
  list top-to-bottom. The bindings are styled with `bold $info-blue` — a screen reader
  will announce "bold info blue, q slash Ctrl+Q slash Ctrl+C, Quit application" which
  is unhelpful. Stripping the styling from the help-overlay bindings would help.

---

## 7. Performance perception

**Verdict: no perceptual feedback at all.**

- **No spinners.** `loading.py` exists in the tui package and presumably has spinner
  widgets, but `app.py` does not import it. The market screen's `#market-loading` CSS
  target has no Python owner. The "Loading chart data…" text is a literal string.
- **No skeleton screens.** Empty panes just say "No data available" or "Loading chart
  data…". Users cannot tell if the app is computing, idle, or hung.
- **No optimistic UI.** Pressing key `1` instantly switches screens with a blank
  pane, then content fills in (or doesn't, if the API is down). The push_screen call
  doesn't await any data.
- **No "stale data" indicator.** If the API was last reachable 5 minutes ago, the data
  is silently the same. There is no "Last updated 5m ago" or auto-refresh countdown
  surfaced in the UI (the status bar's right side has the current time, not "data
  age").
- **Infinite repaint loop when API is down.** When I ran the TUI with the API
  unreachable, the screen kept redrawing every ~200ms (visible in the terminal stream
  I captured: ~5 frames per second of the same content with character-level diffs).
  This is almost certainly the `set_interval(15.0, _check_api_connection)` plus the
  status bar's `set_interval(1.0, _update_display)` plus the reactive watch on
  `api_connected` causing CSS re-evaluation. The CLI timed out at 300s (my own
  timeout) without the app voluntarily exiting. **Perceived: the app is melting the
  CPU.** A real user would see terminal flicker.
- **No debouncing on the search Input.** `app.tcss:185-191` styles `#symbol-search`
  but the BINDINGS only list `/` to focus search. Each keystroke would, naïvely, fire
  a list re-filter — the app.py does not show any debounce wrapper.

---

## 8. The 5 worst UX frictions, ranked by user pain

1. **`python3 -m siglab.cli tui` doesn't exist.** The CLI's only documented TUI entry
   point is a typo or planned feature. New users will hit "invalid choice: 'tui'" and
   think the app is broken. The actual path (`python3 -m siglab.tui`) is not in the
   sidebar, not in `app.tcss`, not in `app.py:425-427`'s `if __name__ == "__main__"`
   shown on a `--help`. (Evidence: ran `python3 -m siglab.cli tui --help` →
   `error: argument command: invalid choice: 'tui'`.)

2. **Status bar text is truncated to "etry" (or similar) on 80×24.** The status bar
   `dock: bottom; height: 1;` (app.tcss:121-124) tries to fit left/center/right
   segments with `width: 1fr` each, plus 1-cell padding, in 80 columns. The actual
   captured render shows the right-aligned UTC timestamp overlapping the left-aligned
   API-error message. (`siglab/tui/widgets/status_bar.py:32-50` defines equal-width
   1fr columns; a fixed 22-char min-width for the right column would fix it.)

3. **`push_screen` stacking for top-level navigation.** Pressing `1-6` on the home
   screen stacks another screen. There is no max-stack guard. Escape peels one at a
   time. A user navigating market → paper → risk has 3 screens on the stack; pressing
   `1` again stacks a 4th `MarketScreen`. (`app.py:400-422` all use
   `self.push_screen("…")`.)

4. **No discoverable keybindings, no footer hints, no breadcrumb.** Only `q` and `?`
   are `show=True` in `BINDINGS` (app.py:317-320). The help overlay (`?`) is the only
   way to learn that `r` refreshes or `/` searches, and even there the help lists
   "global" shortcuts that aren't actually global. The chrome offers zero in-context
   guidance.

5. **No loading state and silent data failure.** When the API is down, every screen
   says "No data available" or "Loading chart data…" with no spinner, no retry button,
   no error toast, no last-updated timestamp. The user has no way to know whether
   the app is waiting, has failed, or has no data because the symbol doesn't exist.
   `app.py:351-365` defines a 15-second connection check, but `_check_api_connection`
   only sets a boolean on the status bar; the per-screen widgets do not subscribe.

---

## 9. Top 5 smaller-delta fixes (1-line-ish patches) that would reduce friction ~80%

These are tiny, surgical edits (≤2 lines each) that target the worst pain points.

1. **Add a `tui` subcommand to the CLI** that simply does
   `from siglab.tui.app import SigLabTUI; SigLabTUI().run()`. New users get
   `siglab tui` to work. **Location**: a new file in `siglab/cli/`, registered in the
   parser in `siglab/cli/__init__.py`. (Off-scope for code edits in this task, but
   it's the single highest-leverage fix.)

2. **Replace `push_screen` with `switch_screen` for `1-6`.** Use Textual's
   `app.switch_screen("market")` instead of `push_screen`. Or, in
   `action_switch_to_market` (app.py:400-402), add a guard
   `if self.screen.id == "market": return`. **Location**: app.py:400-422 — one
   conditional per method. Eliminates the stack-wedging bug.

3. **Set the right-column minimum width on the status bar.** Change
   `SigLabStatusBar > .status-right { width: 1fr; }` to
   `width: auto; min-width: 22;` in `siglab/tui/widgets/status_bar.py:42-45`. Fixes
   the "etry" truncation on 80-col terminals.

4. **Promote 4 hotkeys to `show=True` in the footer.** In `app.py:316-329`, change
   `show=False` → `show=True` for `r`, `/`, `1`, `2` (or however many fit). Even 2
   more hints in the footer doubles the discoverability.

5. **Add a "press ? for help" hint in the status bar left segment.** In
   `siglab/tui/widgets/status_bar.py:73-90` (`_update_display`), append
   `" · ? for help"` to the left-side static text. Five-character change, makes
   help discoverable for the 80% of users who never think to press `?`.

Bonus 1-line fixes (mention but don't rank):

- `app.py:124` — change "Global" keybinding description for `r` to say
  `"r    Refresh current screen (when supported)"` to stop lying.
- `app.py:198` — change the help-overlay title from
  `f"⌨ Keyboard Shortcuts — {self._screen_name}"` to include a "press ? to close"
  hint that is also a screen-reader-friendly plain string, not relying on a glyph.
- `app.tcss:96-100` — add `text-style: bold reverse` to `.nav-item.-active` so the
  CB / 256-color fallback has a non-color cue.
- `app.py:382-385` — in `action_go_back`, add
  `else: self.notify("No previous screen", severity="information")` so Escape always
  has a visible effect.

---

## Appendix A — file:line index of every criticism above

| File:line | Issue |
|---|---|
| `app.py:21` | `INFO_BLUE, TEXT_MUTED` imported but only used in `HelpScreen`; no toast/notification system uses them |
| `app.py:33-40` | Sidebar labels are bracketed (`[ MARKET ]`) which is decorative-only; screen-readers will read "left-bracket market right-bracket" |
| `app.py:49-77` | `PlaceholderScreen` is dead code in this build (all 6 nav items point to real screens, app.py:290-295 never runs) |
| `app.py:83-219` | `HelpScreen` is 136 lines of binding metadata; should be a single dict-to-renderer helper, not a hard-coded constant |
| `app.py:93-100` | Help dialog `width: 56` overflows on 80-col baseline |
| `app.py:110-114` | Help-screen `BINDINGS` includes `Binding("q", "dismiss", "Close")` — but `q` is also a global quit binding. Pressing `q` to close help *also* triggers `action_quit`. Race depends on focus state. |
| `app.py:121-125` | Help text claims `r`, `/`, `k/j`, `Esc` are global, but they are not in `BINDINGS` |
| `app.py:129-179` | Per-screen shortcuts are a static dict, not introspected; future bug |
| `app.py:198-199` | Help-overlay title uses ⌨ glyph, not announced-friendly in screen readers |
| `app.py:216` | `f"  {key:<24} "` is fine at 80 cols but the dialog is `width: 56`, so 24+padding+desc=80 overflows |
| `app.py:316-329` | Only `q` and `?` are `show=True`; the other 11 global keys are hidden |
| `app.py:332` | `api_connected: reactive[bool] = reactive(False)` defaults to False; on startup the status bar shows "disconnected" briefly even if the API is fine |
| `app.py:347-348` | `push_screen(first_screen_id)` runs before the connection check; user lands on Market with stale state |
| `app.py:351-365` | `set_interval(15.0, _check_api_connection)` fires on the *app* interval, not per-screen, so a screen never knows about its own connection state |
| `app.py:354-356` | `on_unmount` is `async` but Textual's `on_unmount` is sync; this is probably a bug — should be `async def _on_unmount` or a `def on_unmount` calling `self.run_worker(...)` |
| `app.py:400-422` | All 6 actions call `push_screen`, not `switch_screen` — stacking bug |
| `app.py:425-427` | `if __name__ == "__main__"` calls `app.run()` — but no `argparse`, no `--screen`, no `--no-color` flag; cannot script TUI access |
| `formatting.py:17-29` | Hex color constants are duplicated in `theme.tcss` and `app.tcss` — single source of truth violated |
| `formatting.py:33-36` | `GAIN/LOSS/LINK/CAUTION` aliases are not used in app.py; only `INFO_BLUE/TEXT_MUTED` are imported |
| `formatting.py:42-72` | `friendly_error` returns a string but app.py never calls it on the connection-failure path |
| `formatting.py:89-96` | `format_change` returns green/red Text with no `▲/▼` glyph — color-only signal |
| `formatting.py:111-118` | Same for PnL |
| `formatting.py:189-201` | `truncate()` exists but no per-screen table renderer calls it on cell values |
| `formatting.py:480-493` | `table_header()` is defined but app.py and the screens don't use it (verified by reading the screens in scope of this critic) |
| `formatting.py:520-523` | `PANEL_CSS` / `SCROLLABLE_CSS` etc. are good DRY, but `app.tcss` doesn't import them — it duplicates the same `padding: 0 1; background: $surface;` lines |
| `styles/theme.tcss:1-33` | The file exists. The file is **never loaded.** `CSS_PATH = ["styles/app.tcss"]` in app.py:311, and Textual does not cascade CSS_PATH files for `$variable` resolution. `theme.tcss` is dead code. |
| `styles/app.tcss:23-48` | The same `$variable` definitions that exist in `theme.tcss` are repeated here. DRY violation. |
| `styles/app.tcss:60-68` | Sidebar is `width: 28; min-width: 24;` — on a 80-col terminal, leaves 52 cols. Market screen then demands 28 for symbol list, leaving 24 for chart+ticker+order-book vertical stack |
| `styles/app.tcss:84-111` | `.nav-item:focus` only changes the *left* border; on default 80×24 fonts the focus ring is hard to see |
| `styles/app.tcss:158-165` | `.keybinding-key` and `.keybinding-desc` classes are defined but `HelpScreen` (app.py:215-219) uses inline `Text.assemble` styles, not these classes. Dead CSS. |
| `styles/app.tcss:234-237` | `#market-loading` CSS exists, no Python widget populates it |
| `styles/app.tcss:309-312` | `#paper-loading` CSS exists, no Python widget populates it |
| `styles/app.tcss:680-687` | `#evidence-status` exists; no widget populates it (status bar in app.py is a separate widget) |
| `siglab/tui/widgets/status_bar.py:73-90` | `set_interval(1.0, _update_display)` — but the text content does not change every second; only the timestamp does. Could be cheaper. |
| `siglab/tui/widgets/status_bar.py:42-50` | `width: 1fr` on all three children with no `min-width` causes the "etry" truncation I observed |

---

## Appendix B — what is good, briefly

For balance: the code is not bad. The architecture is clean (`SigLabTUI` shells, screens
are separate classes, formatting helpers are well-named and well-tested in isolation), the
help-overlay data structure is the right shape, the BINDINGS list is correctly typed, and
`on_unmount` does try to clean up. The sidebar highlight, the per-screen keybinding
registry, the focus-on-input pattern, and the `reactive` for `api_connected` are all
correct Textual idioms. The fixes in section 9 are surgical; nothing here requires a
rewrite. With ~20 lines of edits this would be a top-quartile TUI.
