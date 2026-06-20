# Rich Formatting Strategy for SigLab CLI

## 1. Shared Utilities Module: `siglab/cli/rich_utils.py`

### 1.1 Console Factory

```python
"""Rich formatting utilities for the SigLab CLI.

Provides a shared console instance, semantic color helpers,
table/panel/progress factories, and JSON syntax highlighting.
Respects --no-color flag and NO_COLOR env var.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from rich.console import Console
from rich.highlighter import JSONHighlighter
from rich.json import JSON
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.text import Text
from rich.theme import Theme


# ── Semantic color theme ─────────────────────────────────────────────────
SIGLAB_THEME = Theme({
    "success":  "bold green",
    "error":    "bold red",
    "warning":  "bold yellow",
    "info":     "bold blue",
    "muted":    "dim",
    "accent":   "bold cyan",
    "label":    "bold",
    "value":    "",
})

_json_highlighter = JSONHighlighter()


def make_console(*, force_no_color: bool = False) -> Console:
    """Build a Rich Console respecting NO_COLOR and --no-color.

    Args:
        force_no_color: When True, disables all ANSI styling regardless
                        of environment.  Set from the parsed --no-color flag.

    Returns:
        A themed Rich Console instance.
    """
    no_color = force_no_color or bool(os.environ.get("NO_COLOR"))
    return Console(
        theme=SIGLAB_THEME,
        no_color=no_color,
        highlight=not no_color,
        stderr=False,
    )


# Module-level default console (replaced at CLI startup after arg parse)
_console: Console | None = None


def get_console() -> Console:
    """Return the active console.  Falls back to a default if not initialized."""
    global _console
    if _console is None:
        _console = make_console()
    return _console


def init_console(*, force_no_color: bool = False) -> Console:
    """Initialize the module-level console.  Called once from main()."""
    global _console
    _console = make_console(force_no_color=force_no_color)
    return _console
```

### 1.2 JSON Output Helper

```python
def print_json(data: Any, *, indent: int = 2, sort_keys: bool = True) -> None:
    """Print JSON with syntax highlighting (unless no_color or piped)."""
    console = get_console()
    if not console.is_terminal:
        # Piped output: plain JSON, no ANSI
        print(json.dumps(data, indent=indent, sort_keys=sort_keys, default=str))
        return
    json_obj = JSON.from_data(data, indent=indent, sort_keys=sort_keys, default=str)
    console.print(json_obj)
```

### 1.3 Table Factory

```python
def make_table(
    title: str | None = None,
    *,
    show_lines: bool = False,
    header_style: str = "bold",
    border_style: str = "muted",
    row_styles: tuple[str, ...] = ("", "dim"),
) -> Table:
    """Create a consistently styled Rich Table."""
    return Table(
        title=title,
        show_lines=show_lines,
        header_style=header_style,
        border_style=border_style,
        row_styles=row_styles,
        expand=False,
    )


def print_key_value_pairs(
    title: str | None,
    pairs: list[tuple[str, str, str]],
) -> None:
    """Render key-value pairs as a table.

    Each pair is (label, value, style) where style is a Rich style name
    applied to the value cell (e.g. "success", "error", "warning").
    """
    table = make_table(title=title)
    table.add_column("Field", style="label")
    table.add_column("Value")
    for label, value, style in pairs:
        table.add_column(label, style="label")
    console = get_console()
    # Rebuild: simpler approach
    table = make_table(title=title)
    table.add_column("Field", style="label", no_wrap=True)
    table.add_column("Value")
    for label, value, style in pairs:
        table.add_row(label, Text(value, style=style))
    console.print(table)
```

### 1.4 Panel Factory

```python
def print_panel(
    content: str | Text,
    title: str | None = None,
    *,
    border_style: str = "info",
    expand: bool = False,
) -> None:
    """Print content in a Rich Panel."""
    console = get_console()
    console.print(Panel(content, title=title, border_style=border_style, expand=expand))


def print_status_line(message: str, *, style: str = "info") -> None:
    """Print a single styled status line (replaces bare print of status text)."""
    console = get_console()
    console.print(Text(message, style=style))
```

### 1.5 Progress Bar Factory

```python
def make_progress(**kwargs: Any) -> Progress:
    """Create a consistently styled progress bar for long operations.

    Default columns: spinner, description, bar, M/N, elapsed time.
    """
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=get_console(),
        **kwargs,
    )
```

### 1.6 Semantic Print Helpers

```python
def print_success(message: str) -> None:
    console = get_console()
    console.print(f"[success]✔[/] {message}")


def print_error(message: str) -> None:
    console = get_console()
    console.print(f"[error]✘[/] {message}", stderr=True)


def print_warning(message: str) -> None:
    console = get_console()
    console.print(f"[warning]⚠[/] {message}")


def print_info(message: str) -> None:
    console = get_console()
    console.print(f"[info]ℹ[/] {message}")


def print_header(title: str) -> None:
    """Print a section header."""
    console = get_console()
    console.rule(f"[bold]{title}")


def print_muted(message: str) -> None:
    console = get_console()
    console.print(f"[muted]{message}[/]")
```

---

## 2. `--no-color` / `NO_COLOR` Integration

### 2.1 Argparse Integration

Add `--no-color` as a global flag in `siglab/cli/__init__.py`:

```python
def main() -> None:
    parser = argparse.ArgumentParser(prog="siglab")
    parser.add_argument(
        "--no-color",
        action="store_true",
        default=False,
        help="Disable ANSI color output. Also respects NO_COLOR env var.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    # ... existing subparser registrations ...

    args = parser.parse_args()

    # Initialize shared Rich console before any command runs
    from siglab.cli.rich_utils import init_console
    init_console(force_no_color=getattr(args, "no_color", False))

    # ... existing dispatch logic ...
```

### 2.2 Precedence Order

1. `--no-color` CLI flag (highest priority)
2. `NO_COLOR` env var (any non-empty value)
3. Terminal detection via `Console.is_terminal` (auto-disable for pipes)

### 2.3 Impact on `--json` Output

When `--json` is active, output is always plain JSON (no ANSI). The `print_json()` helper checks `console.is_terminal` and falls back to `json.dumps` when piped. This ensures `siglab profile --json | jq` works cleanly.

---

## 3. Color Scheme Specification

| Semantic Role | Rich Style       | Usage                                    |
|---------------|------------------|------------------------------------------|
| Success       | `bold green`     | Passing checks, READY status, ✔ prefix   |
| Error         | `bold red`       | Failures, NOT READY, missing config, ✘   |
| Warning       | `bold yellow`    | PARTIAL status, blocked prerequisites, ⚠  |
| Info          | `bold blue`      | Section headers, status lines, ℹ          |
| Accent        | `bold cyan`      | Highlighted values, file paths            |
| Muted         | `dim`            | Secondary info, table borders, timestamps |
| Label         | `bold`           | Table column headers, field names         |
| Default       | (no style)       | Regular values                            |

### Status Mapping

| Status String                    | Style     |
|----------------------------------|-----------|
| `READY`, `PASS`, `true`         | `success` |
| `NOT READY`, `FAIL`, `false`    | `error`   |
| `PARTIAL`, `blocked`            | `warning` |
| Informational labels             | `info`    |

---

## 4. Per-Module Transformation Plan

### 4.1 `siglab/cli/__init__.py` (main parser)

**Changes:**
- Add `--no-color` flag to root parser
- Call `init_console(force_no_color=...)` before dispatch

**Lines affected:** ~5 (flag addition + console init)

---

### 4.2 `siglab/cli/sodex.py` (12 print statements)

**Current patterns:**
- JSON dump for `--json` mode
- Key-value status lines for preflight
- Status line for valuechain-preflight

**Transformations:**

| Current Pattern | Replacement |
|-----------------|-------------|
| `print(json.dumps(report, indent=2))` | `print_json(report)` |
| `print(f"public_read_ready={report['public_read_ready']}")` | Key-value table via `print_key_value_pairs()` |
| `print(f"ValueChain RPC {status}: ...")` | `print_status_line(msg, style="success"/"error")` |

**Specific changes:**

`run_sodex_preflight` — Replace the bare key-value prints with a table:
```python
def run_sodex_preflight(args: argparse.Namespace) -> None:
    report = sodex_preflight_report()
    if getattr(args, "json", False):
        print_json(report)
        return
    from siglab.cli.rich_utils import make_table, get_console, status_style
    table = make_table(title="SoDEX Preflight")
    table.add_column("Check", style="label", no_wrap=True)
    table.add_column("Status")
    table.add_row("public_read_ready", Text(str(report["public_read_ready"]), style=status_style(report["public_read_ready"])))
    table.add_row("schema_pinned", Text(str(report["schema_pinned"]), style=status_style(report["schema_pinned"])))
    table.add_row("signed_path_ready", Text(str(report["signed_path"]["ready"]), style=status_style(report["signed_path"]["ready"])))
    table.add_row("environment", Text(report["signed_path"]["environment"]))
    if report["signed_path"]["missing_prerequisites"]:
        table.add_row("missing_prerequisites", Text(", ".join(report["signed_path"]["missing_prerequisites"]), style="warning"))
    table.add_row("live_write_allowed", Text(str(report["live_write_allowed"]), style=status_style(report["live_write_allowed"])))
    get_console().print(table)
```

`run_valuechain_preflight` — Color the status:
```python
status = "READY" if report.get("ready") else "NOT READY"
style = "success" if report.get("ready") else "error"
print_status_line(f"ValueChain RPC {status}: chain_id={report.get('chain_id')} expected={expected} rpc={rpc_url}", style=style)
```

`run_sodex_ws_probe` — The non-JSON path already dumps full JSON; use `print_json()` for syntax highlighting:
```python
# Replace: print(json.dumps(report, ...))
print_json(report)
```

**Total changes:** ~12 print replacements → 6 distinct modifications

---

### 4.3 `siglab/cli/run.py` (8 print statements)

**Current patterns:**
- Status/milestone lines during run loop: `print(f"[{track}] ...")`
- JSON dump in inspect command

**Transformations:**

| Current | Replacement |
|---------|-------------|
| `print(f"[{track}] max runtime reached...")` | `print_warning(f"[{track}] max runtime reached...")` |
| `print(f"[{track}] research_summary failed: {exc}")` | `print_error(f"[{track}] research_summary failed: {exc}")` |
| `print(f"[{track}] planner skipped write: ...")` | `print_info(f"[{track}] planner skipped write: ...")` |
| `print(f"[{track}] credit budget exhausted, stopping")` | `print_warning(f"[{track}] credit budget exhausted, stopping")` |
| `print(f"[{track}] reflection recorded at {reflection}")` | `print_success(f"[{track}] reflection recorded")` |
| `print(json.dumps(summary, indent=2))` | `print_json(summary)` |

**Progress bar for run iterations:**

The `_run_iterations` loop is the prime candidate for a Rich progress bar. The loop already has `iterations` count and `iteration_number` counter. Wrap the main loop:

```python
from siglab.cli.rich_utils import make_progress, print_warning, print_error, print_info, print_success

# In _run_iterations, after seed_specs are loaded:
with make_progress() as progress:
    task = progress.add_task(f"[info]{track}[/] iterations", total=iterations if iterations > 0 else None)
    while True:
        iteration_number = next(iteration)
        if iterations > 0 and iteration_number > iterations:
            break
        # ... existing logic ...
        progress.update(task, advance=1)
```

Note: For `iterations=0` (infinite mode), use `total=None` which shows a spinner instead of a bar.

**Total changes:** ~8 print replacements + progress bar integration

---

### 4.4 `siglab/cli/benchmark.py` (3 print statements)

**Current patterns:**
- All three commands output JSON via `print(json.dumps(payload, indent=2))`

**Transformations:**

Replace all with `print_json(payload)` for syntax-highlighted JSON output.

**Progress bar for `benchmark-eval`:**

```python
async def run_benchmark_eval(args: argparse.Namespace) -> None:
    # ... setup ...
    with make_progress() as progress:
        task = progress.add_task("[info]benchmark-eval[/]", total=None)
        try:
            payload = await evaluate_benchmark_deck(...)
        finally:
            await provider.close()
            progress.update(task, completed=1)
    print_json(payload)
```

**Total changes:** 3 print replacements + optional progress wrapper

---

### 4.5 `siglab/cli/paper.py` (5 print statements)

**Current patterns:**
- JSON dumps for all outputs
- Error JSON with SystemExit

**Transformations:**

| Current | Replacement |
|---------|-------------|
| `print(json.dumps({"session_id": ...}))` | `print_json({"session_id": ...})` |
| `print(json.dumps(status, indent=2, default=str))` | `print_json(status)` |
| `print(json.dumps({"error": str(exc)}, indent=2))` | `print_error(str(exc))` + still emit JSON for `--json` |
| `print(json.dumps(result, indent=2, default=str))` | `print_json(result)` |

For paper-promote, add a summary panel before the JSON:
```python
# After computing eligibility, show a status panel:
style = "success" if eligible else "warning"
print_panel(
    Text.assemble(
        ("Promoted: ", "label"), (str(eligible), style), "\n",
        ("Score: ", "label"), (f"{composite:.4f}", ""), "\n",
        ("Reason: ", "label"), (reason, ""),
    ),
    title="Paper Promotion",
    border_style=style,
)
if getattr(args, "json", False):
    print_json(result)
```

**Total changes:** 5 print replacements + optional panel for promote

---

### 4.6 `siglab/cli/deploy.py` (7 print statements)

**Current patterns:**
- Mixed: status lines + JSON dumps
- "Found spec..." / "Exported snapshot..." / "Deployment already exists..."

**Transformations:**

| Current | Replacement |
|---------|-------------|
| `print(f"Found spec {spec_hash}...")` | `print_info(f"Found spec {spec_hash} in ancestry (not yet deployed):")` |
| `print(json.dumps(detail, indent=2))` | `print_json(detail)` |
| `print(f"Exported snapshot to: ...")` | `print_success(f"Exported snapshot to: {record_result.strategy_dir}")` |
| `print(f"Found existing deployment...")` | `print_info(f"Found existing deployment for {spec_hash}:")` + `print_json(existing)` |
| `print("Deployment already exists...")` | `print_warning("Deployment already exists...")` |

**Total changes:** 7 print replacements

---

### 4.7 `siglab/cli/config_cmd.py` (3 print statements + stderr)

**Current patterns:**
- Success: `print(f"config valid: {config_path}")`
- Error: `print(f"ERROR: {error}", file=sys.stderr)`

**Transformations:**

```python
def config_validate_command(args: argparse.Namespace) -> None:
    # ... validation logic ...
    if errors:
        _report_config_validation(errors)
        return
    from siglab.cli.rich_utils import make_table, get_console, print_success
    table = make_table(title="Config Valid")
    table.add_column("Field", style="label")
    table.add_column("Value")
    table.add_row("config_path", str(config_path))
    table.add_row("api_base_url", system.get("api_base_url"))
    get_console().print(table)
    raise SystemExit(0)


def _report_config_validation(errors: list[str]) -> None:
    from siglab.cli.rich_utils import print_error
    for error in errors:
        print_error(error)
    raise SystemExit(1)
```

**Total changes:** 3 print replacements

---

### 4.8 `siglab/cli/evidence.py` (2 print statements)

| Current | Replacement |
|---------|-------------|
| `print(json.dumps({...}, indent=2))` | `print_json({...})` |
| `print(f"wrote evidence graph: {rendered}")` | `print_success(f"wrote evidence graph: {rendered}")` |

**Total changes:** 2 print replacements

---

### 4.9 `siglab/cli/market.py` (2 print statements)

Both print `json.dumps(payload, ...)` — replace with `print_json(payload)`.

**Total changes:** 2 print replacements

---

### 4.10 `siglab/cli/demo.py` (10 print statements)

**Current patterns:**
- JSON dumps for report/manifest/wave payloads
- Status lines: `print(f"demo_manifest: {path}")`

**Transformations:**

| Current | Replacement |
|---------|-------------|
| `print(json.dumps(payload, ...))` | `print_json(payload)` |
| `print(f"demo_manifest: {path}")` | `print_success(f"demo_manifest: {path}")` |
| `print(f"demo_manifest_html: {path}")` | `print_success(f"demo_manifest_html: {path}")` |
| `print(f"wave_status: {path}")` | `print_success(f"wave_status: {path}")` |

**Total changes:** 10 print replacements

---

### 4.11 `siglab/cli/telemetry.py` (2 print statements)

| Current | Replacement |
|---------|-------------|
| `print(json.dumps(payload, ...))` | `print_json(payload)` |
| Multi-line `print("\n".join([...]))` | Table with `print_key_value_pairs()` |

The non-JSON telemetry output is currently:
```
trace_count: 5
stage_counts: {...}
provider_counts: {...}
```

This should become a Rich table with two columns (metric, value), where complex values are JSON-formatted inline.

**Total changes:** 2 print replacements

---

### 4.12 `siglab/cli/dashboard.py` (3 print statements)

| Current | Replacement |
|---------|-------------|
| `print(f"Starting SigLab FastAPI dashboard...")` | `print_info(f"Starting SigLab FastAPI dashboard on http://{host}:{port}")` |
| `print(f"No process found...")` | `print_error(f"No process found listening on port {port}")` |
| `print(f"Stopped dashboard...")` | `print_success(f"Stopped dashboard on port {port} (PID...)")` |
| `print(f"Timeout checking port {port}")` | `print_error(f"Timeout checking port {port}")` |

**Total changes:** 4 print replacements

---

### 4.13 `siglab/cli/profile.py` (2 print statements)

| Current | Replacement |
|---------|-------------|
| `print(json.dumps(profile, ...))` | `print_json(profile)` |
| `print(profile_as_text(profile))` | Keep as-is or wrap in Panel for visual grouping |

The `profile_as_text()` function returns pre-formatted text. Wrap it:
```python
from siglab.cli.rich_utils import print_panel
print_panel(profile_as_text(profile), title="Hardening Profile", border_style="info")
```

**Total changes:** 2 print replacements

---

### 4.14 `siglab/cli/api.py` (2 print statements)

| Current | Replacement |
|---------|-------------|
| `print(json.dumps(report, ...))` | `print_json(report)` |
| Multi-line f-string print | Table with per-API-surface rows |

The non-JSON output is:
```
sosovalue: exists=True lines=200 paths=15 supported=10 missing=3 blocked=1 file=...
```

Replace with a Rich table:
```python
table = make_table(title="API Surface")
table.add_column("Surface", style="label")
table.add_column("Exists")
table.add_column("Lines", justify="right")
table.add_column("Paths", justify="right")
table.add_column("Supported", justify="right")
table.add_column("Missing", justify="right")
table.add_column("Blocked", justify="right")
for name, payload in report.items():
    table.add_row(
        name,
        Text(str(payload["exists"]), style="success" if payload["exists"] else "error"),
        str(payload["line_count"]),
        str(payload["endpoint_path_mentions"]),
        str(payload["supported_mentions"]),
        str(payload["missing_mentions"]),
        str(payload["blocked_mentions"]),
    )
get_console().print(table)
```

**Total changes:** 2 print replacements

---

### 4.15 `siglab/cli/ancestry_cmd.py` (2 print statements)

| Current | Replacement |
|---------|-------------|
| Per-row `print(f"{created_at} {track} ...")` | Rich table with columns |
| `print(json.dumps(payload, indent=2))` | `print_json(payload)` |

The ancestry command prints one line per row:
```
2024-01-15T10:30:00Z trend_signals momentum_3b abc123 score=1.2345 passed=True deployd=False
```

Replace with a table:
```python
table = make_table(title="Ancestry")
table.add_column("Created", style="muted")
table.add_column("Track")
table.add_column("Family")
table.add_column("Spec Hash", style="accent")
table.add_column("Score", justify="right")
table.add_column("Passed")
table.add_column("Deployed")
for row in rows:
    table.add_row(
        row["created_at"],
        row["track"],
        row["family"],
        row["spec_hash"],
        f"{row['aggregate_score']:.4f}",
        Text(str(row["passed"]), style="success" if row["passed"] else ""),
        Text(str(row["deployd"]), style="success" if row["deployd"] else ""),
    )
get_console().print(table)
```

**Total changes:** 2 print replacements

---

### 4.16 `siglab/cli/helpers.py` (6 print statements)

**`print_run_reflection_short`** — This function outputs structured run reflection data. Transform:

```python
def print_run_reflection_short(*, track: str, reflection: dict[str, Any]) -> None:
    from siglab.cli.rich_utils import make_table, get_console, print_header, status_style
    console = get_console()

    summary = dict(reflection.get("summary") or {})
    table = make_table(title=f"[info]{track}[/] Run Reflection")
    table.add_column("Metric", style="label", no_wrap=True)
    table.add_column("Value")
    table.add_row("LLM runs", str(summary.get("llm_run_count", 0)))
    table.add_row("Passes", Text(str(summary.get("passed_count", 0)), style="success" if summary.get("passed_count", 0) > 0 else ""))
    table.add_row("Median pre-audit return", _format_optional_pct(summary.get("median_pre_audit_canonical_total_return")))
    table.add_row("Median active bars", _format_optional_pct(summary.get("median_active_bar_fraction")))
    console.print(table)

    intent_vs_sweep = dict(reflection.get("intent_vs_sweep") or {})
    console.print(f"[{track}] sweep drift: material_share={_format_optional_pct(intent_vs_sweep.get('material_change_share'))} median_changed_params={_format_optional_number(intent_vs_sweep.get('median_changed_param_count'))}")

    for line in list(reflection.get("what_improved") or [])[:3]:
        print_success(f"[{track}] improved: {line}")
    for line in list(reflection.get("what_failed") or [])[:3]:
        print_error(f"[{track}] failed: {line}")

    last_five_runs = list(reflection.get("last_five_runs") or [])[:5]
    if last_five_runs:
        table2 = make_table(title=f"[info]{track}[/] Last 5 Non-Deterministic Runs")
        table2.add_column("Spec Hash", style="accent")
        table2.add_column("Family")
        table2.add_column("Median", justify="right")
        table2.add_column("Validation", justify="right")
        table2.add_column("Pre-Audit", justify="right")
        table2.add_column("Active", justify="right")
        table2.add_column("Sweep Δ", justify="right")
        table2.add_column("Bottlenecks")
        for row in last_five_runs:
            table2.add_row(
                row["spec_hash"],
                row["family"],
                _format_optional_pct(row.get("median_total_return")),
                _format_optional_pct(row.get("validation_total_return")),
                _format_optional_pct(row.get("pre_audit_canonical_total_return")),
                _format_optional_pct(row.get("active_bar_fraction")),
                str(len(list((row.get("sweep_drift") or {}).get("changed_keys") or []))),
                ", ".join(row.get("gate_bottlenecks") or []),
            )
        console.print(table2)
```

**Total changes:** 6 print replacements in helpers.py

---

## 5. Implementation Priority and Order

### Phase 1: Foundation (must be first)
1. Create `siglab/cli/rich_utils.py` with all helpers
2. Add `--no-color` to `siglab/cli/__init__.py` and wire `init_console()`

### Phase 2: High-Impact Modules (most print statements)
3. `sodex.py` — 12 prints, best table/panel showcase
4. `run.py` — 8 prints + progress bar
5. `helpers.py` — 6 prints, affects run reflection output

### Phase 3: Remaining Modules
6. `config_cmd.py` — 3 prints, clean table demo
7. `deploy.py` — 7 prints, mixed patterns
8. `demo.py` — 10 prints
9. `paper.py` — 5 prints
10. `telemetry.py` — 2 prints
11. `benchmark.py` — 3 prints + progress bar
12. `profile.py` — 2 prints
13. `evidence.py` — 2 prints
14. `market.py` — 2 prints
15. `dashboard.py` — 4 prints
16. `api.py` — 2 prints
17. `ancestry_cmd.py` — 2 prints

### Phase 4: Verification
18. Run `python3 -m pytest -q` to verify no regressions
19. Run `python3 -m siglab.cli profile --strict --json` to verify JSON path
20. Run `python3 -m siglab.cli profile --strict` to verify Rich output
21. Run `python3 -m siglab.cli profile --strict --no-color` to verify no-color
22. Pipe test: `python3 -m siglab.cli profile --json | cat` to verify no ANSI in piped output

---

## 6. Additional Utility: `status_style()` Helper

```python
def status_style(value: Any) -> str:
    """Return a Rich style name for a boolean-ish status value."""
    if isinstance(value, bool):
        return "success" if value else "error"
    s = str(value).strip().upper()
    if s in {"TRUE", "READY", "PASS", "1"}:
        return "success"
    if s in {"FALSE", "NOT READY", "FAIL", "0"}:
        return "error"
    if s in {"PARTIAL", "BLOCKED"}:
        return "warning"
    return ""
```

---

## 7. Files Modified Summary

| File | Action | Changes |
|------|--------|---------|
| `siglab/cli/rich_utils.py` | **CREATE** | New module (~120 lines) |
| `siglab/cli/__init__.py` | EDIT | Add `--no-color`, call `init_console()` (~5 lines) |
| `siglab/cli/sodex.py` | EDIT | Replace 12 prints with Rich helpers |
| `siglab/cli/run.py` | EDIT | Replace 8 prints, add progress bar |
| `siglab/cli/benchmark.py` | EDIT | Replace 3 prints, add progress wrapper |
| `siglab/cli/paper.py` | EDIT | Replace 5 prints |
| `siglab/cli/deploy.py` | EDIT | Replace 7 prints |
| `siglab/cli/config_cmd.py` | EDIT | Replace 3 prints with table/error helpers |
| `siglab/cli/evidence.py` | EDIT | Replace 2 prints |
| `siglab/cli/market.py` | EDIT | Replace 2 prints |
| `siglab/cli/demo.py` | EDIT | Replace 10 prints |
| `siglab/cli/telemetry.py` | EDIT | Replace 2 prints with table |
| `siglab/cli/dashboard.py` | EDIT | Replace 4 prints |
| `siglab/cli/profile.py` | EDIT | Replace 2 prints |
| `siglab/cli/api.py` | EDIT | Replace 2 prints with table |
| `siglab/cli/ancestry_cmd.py` | EDIT | Replace 2 prints with table |
| `siglab/cli/helpers.py` | EDIT | Replace 6 prints with tables/semantic helpers |

**Total: 1 new file, 16 edited files, ~75 print replacements.**
