# TUI Dedup Audit — evidence / telemetry / strategy

**Scope:** `siglab/tui/screens/{evidence.py, telemetry.py, strategy.py}` vs `base.py`.

## evidence.py (700 LoC)
1. **Search input + custom filter handler (L497–516, L687–700)** — sets `_search_input_id` but re-implements kind/text dispatch in `on_input_changed`. Lift into `EvidenceGraphWidget.set_filter_from_text(value)`; the screen handler shrinks to a one-liner.
2. **`_update_status` / `_update_status_running` (L556–565, L681–683)** duplicate `_update_status_text` with bespoke formatting. Collapse to a single `compose_status(...)` helper.
3. **`DEMO_STEPS` block (L47–104) + `_kind_icon` / `_kind_style` (L110–121)** are pure module data — extract to `tui/evidence_data.py`, drops ~80 LoC from this file.

## telemetry.py (820 LoC)
1. **Four `Static` `render()` headers (ProviderMetrics L179, ToolUsage L284, RunDetail L345, ServiceHealth L449)** all repeat `"  TITLE\n" + "─"*50 + "\n" + BORDER_DIM`. Extract `_render_header(text)` or `HeaderedWidget(Static)` base — saves ~50 LoC.
2. **`action_cycle_date_range` / `action_cycle_status_filter` / `action_cycle_track_filter` (L755–788)** are three mechanical index-cycle copies. Extract `_cycle_filter(values, current_attr, setter, label)` — saves ~24 LoC.
3. **`_fetch_telemetry` (L606), `_fetch_ops_board` (L620), `_fetch_runs` (L631)** all do `run_cli → json.loads → assign` with identical JSON-error handling. Extract `_fetch_cli_json(cmd, timeout, attr)` — saves ~30 LoC; also lets strategy.py reuse it.

## strategy.py (613 LoC)
1. **`action_move_up`/`move_down` (L484–492) and `on_input_changed` (L580–586)** are near-clones of telemetry's (L670–678, L792–798). After BaseScreen already delegates to the list, the only real logic is `_on_selection_changed()`. Promote to BaseScreen via a `_selection_list_id` hook.
2. **`_fetch_data` (L431–444) duplicates telemetry's `_fetch_runs` exactly** — same `ancestry --json`, same row derivation. Extract to `tui/cli_queries.py::fetch_ancestry_rows()`.
3. **`MAX_COMPARE = 4` (L50) is defined here *and* in telemetry.py:58.** Promote to `ComparisonWidget` in `tui/widgets/base.py` as a class constant.

## Functions to extract into `BaseScreen` (or sibling)
| Helper | Used in |
|---|---|
| `_cycle_filter(values, current_attr, setter, label)` | telemetry L755–788 (3×) |
| `_fetch_cli_json(cmd, timeout, attr, on_data=None)` | telemetry L606/620/631, strategy L431 |
| `_run_cli_step(step_data, demo, timeout)` | evidence L609–679 (run_step + run_all copy same 12-line try/except) |
| `_render_header(text)` or `HeaderedWidget(Static)` | telemetry L179/284/345/449, evidence widget headers |

## Already-clean (no action)
All three screens already extend `BaseScreen`, declare the 6 class-vars (`_loading_widget_id` etc.), and inherit `BaseScreen.BINDINGS +`. **Zero new duplication of `on_mount` / `_refresh_all` / `_set_loading` exists** — these all live in `base.py:92–126` and are used as-is. No `noqa` / `type:ignore` was needed during the audit.

## Net LoC delta
| File | Now | Extracted | Net |
|---|---|---|---|
| evidence.py | 700 | ~110 | ~590 |
| telemetry.py | 820 | ~100 | ~720 |
| strategy.py | 613 | ~30 | ~583 |
| base.py + helpers | 247 | +60 | ~307 |
| **Total** | **2380** | **~180** | **~2200** |

Slim "minimum-delta" cut (widget header + filter cycler only): **~80 LoC net**. Button bindings: telemetry + strategy already use `Binding(...)`; no button widget wiring required — keep keyboard-only.
