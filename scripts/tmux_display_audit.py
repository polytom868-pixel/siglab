#!/usr/bin/env python3
"""TUI Display Audit — tmux-based programmatic rendering test.

Launches the SigLab TUI inside tmux at 80, 120, and 160 column widths.
Captures rendered output for all 6 screens. Parses visible vs clipped
content. Reports every UI element that overflows, wraps incorrectly,
or disappears.

Usage:
    python3 scripts/tmux_display_audit.py [--fix] [--report PATH]

Flags:
    --fix       Apply automatic fixes for detected issues
    --report    Output report path (default: runs/display_audit_report.md)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# ── Constants ─────────────────────────────────────────────────────────

WIDTHS = [80, 120, 160]
SCREENS = ["market", "paper", "risk", "strategy", "telemetry", "evidence"]
SCREEN_KEYS = {"market": "1", "paper": "2", "risk": "3", "strategy": "4", "telemetry": "5", "evidence": "6"}
SIDEBAR_WIDTH = 24  # Actual sidebar CSS width
SETTLE_TIME = 4.0   # Seconds to wait for TUI to render
TMUX_SESSION = "siglab-tui-audit"
TUI_CMD = "cd /home/eya/soso/siglab && poetry run python -m siglab.tui"


@dataclass
class AuditFinding:
    """A single display audit finding."""
    screen: str
    element: str
    width: int
    issue_type: str  # overflow | wrap | disappear | clip | dynamic
    description: str
    severity: str  # critical | high | medium | low
    coordinates: str = ""
    suggested_fix: str = ""


@dataclass
class ScreenCapture:
    """Captured terminal output for a screen at a specific width."""
    screen: str
    width: int
    height: int
    lines: list[str] = field(default_factory=list)
    max_rendered_width: int = 0
    clipped_lines: int = 0
    empty_lines: int = 0


def run(cmd: str, timeout: float = 10.0) -> subprocess.CompletedProcess:
    """Run a shell command."""
    return subprocess.run(
        cmd, shell=True, capture_output=True, text=True, timeout=timeout
    )


def tmux(*args: str, timeout: float = 10.0, literal: bool = False) -> subprocess.CompletedProcess:
    """Run a tmux command.

    Args:
        *args: tmux subcommand and arguments.
        timeout: Timeout in seconds.
        literal: If True, use -l flag for send-keys (sends literal string).
    """
    if literal:
        cmd = ["tmux"] + list(args)
    else:
        cmd = f"tmux {' '.join(args)}"
    return subprocess.run(
        cmd, shell=not literal, capture_output=True, text=True, timeout=timeout
    )


def cleanup_tmux() -> None:
    """Kill existing tmux session if any."""
    tmux("kill-session", "-t", TMUX_SESSION, timeout=5.0)


def start_tui(width: int, height: int = 40) -> bool:
    """Start the TUI in a tmux session at the given dimensions."""
    cleanup_tmux()

    # Create detached tmux session with specific dimensions
    result = run(
        f"tmux new-session -d -s {TMUX_SESSION} -x {width} -y {height}",
        timeout=10.0,
    )
    if result.returncode != 0:
        print(f"  ✗ Failed to create tmux session: {result.stderr}")
        return False

    # Send TUI launch command using send-keys with -l for literal text
    run(f"tmux send-keys -t {TMUX_SESSION} -l '{TUI_CMD}'")
    run(f"tmux send-keys -t {TMUX_SESSION} Enter")

    # Wait for TUI to render
    time.sleep(SETTLE_TIME)
    return True


def resize_tui(width: int, height: int = 40) -> None:
    """Resize the tmux window."""
    run(f"tmux resize-window -t {TMUX_SESSION} -x {width} -y {height}")
    time.sleep(1.0)  # Wait for re-render


def navigate_to_screen(screen_name: str) -> None:
    """Navigate to a specific screen via keyboard shortcut."""
    key = SCREEN_KEYS.get(screen_name)
    if key:
        run(f"tmux send-keys -t {TMUX_SESSION} '{key}'")
        time.sleep(2.0)  # Wait for screen to render and data to load


def capture_pane(width: int) -> list[str]:
    """Capture the current tmux pane content with joined wrapped lines."""
    result = run(f"tmux capture-pane -t {TMUX_SESSION} -p -J")
    if result.returncode != 0:
        return []

    lines = result.stdout.split("\n")
    return lines


def capture_pane_raw(width: int) -> list[str]:
    """Capture raw pane content without joining wrapped lines."""
    result = run(f"tmux capture-pane -t {TMUX_SESSION} -p")
    if result.returncode != 0:
        return []

    return result.stdout.split("\n")


def analyze_capture(screen: str, width: int, lines: list[str], raw_lines: list[str]) -> tuple[ScreenCapture, list[AuditFinding]]:
    """Analyze captured output for display issues."""
    height = len(lines)
    content_area = width - SIDEBAR_WIDTH - 1  # -1 for border

    capture = ScreenCapture(
        screen=screen,
        width=width,
        height=height,
        lines=lines,
    )

    findings: list[AuditFinding] = []

    # Track max rendered width
    for i, line in enumerate(lines):
        # Strip ANSI escape codes for length measurement
        clean = strip_ansi(line)
        line_width = len(clean)
        if line_width > capture.max_rendered_width:
            capture.max_rendered_width = line_width

        if line_width > width:
            capture.clipped_lines += 1
            findings.append(AuditFinding(
                screen=screen,
                element="rendered_line",
                width=width,
                issue_type="overflow",
                description=f"Line {i+1} renders {line_width} chars, terminal is {width} cols",
                severity="high",
                coordinates=f"row={i+1}, col={width+1}-{line_width}",
                suggested_fix="Truncate or wrap content to fit terminal width",
            ))

        if not clean.strip():
            capture.empty_lines += 1

    # Check for wrapped lines (raw has more visual lines than joined)
    if len(raw_lines) > len(lines) + 2:  # Allow 2 lines tolerance
        wrapped_count = len(raw_lines) - len(lines)
        findings.append(AuditFinding(
            screen=screen,
            element="screen_content",
            width=width,
            issue_type="wrap",
            description=f"{wrapped_count} lines wrapped at {width} cols",
            severity="medium",
            coordinates=f"total_wrapped={wrapped_count}",
            suggested_fix="Reduce content width or add horizontal scrolling",
        ))

    # Screen-specific analysis
    if screen == "market":
        findings.extend(analyze_market_screen(width, content_area, lines))
    elif screen == "paper":
        findings.extend(analyze_paper_screen(width, content_area, lines))
    elif screen == "risk":
        findings.extend(analyze_risk_screen(width, content_area, lines))
    elif screen == "strategy":
        findings.extend(analyze_strategy_screen(width, content_area, lines))
    elif screen == "telemetry":
        findings.extend(analyze_telemetry_screen(width, content_area, lines))
    elif screen == "evidence":
        findings.extend(analyze_evidence_screen(width, content_area, lines))

    return capture, findings


def strip_ansi(text: str) -> str:
    """Strip ANSI escape codes from text."""
    import re
    ansi_escape = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07')
    return ansi_escape.sub('', text)


def analyze_market_screen(width: int, content_area: int, lines: list[str]) -> list[AuditFinding]:
    """Analyze market screen for display issues."""
    findings = []

    # Check actual rendered content for overflow
    # Find lines that look like ticker table headers or data
    for i, line in enumerate(lines):
        clean = strip_ansi(line)
        # Check for ticker-like content that exceeds content area
        if "SYMBOL" in clean and "PRICE" in clean and len(clean) > width:
            findings.append(AuditFinding(
                screen="market",
                element="TickerTableWidget",
                width=width,
                issue_type="overflow",
                description=f"Ticker table header ({len(clean)} chars) overflows terminal ({width} cols)",
                severity="high",
                coordinates=f"row={i+1}, rendered={len(clean)}",
                suggested_fix="Reduce column widths or use compact layout below 100 cols",
            ))

    # Klines chart caps at 80 chars but should use available width
    if width > 120 and content_area > 80:
        findings.append(AuditFinding(
            screen="market",
            element="KlinesChartWidget",
            width=width,
            issue_type="clip",
            description=f"Sparkline chart capped at 80 chars, {content_area - 80} cols of space wasted",
            severity="low",
            suggested_fix="Scale chart width to fill available content area",
        ))

    return findings


def analyze_paper_screen(width: int, content_area: int, lines: list[str]) -> list[AuditFinding]:
    """Analyze paper screen for display issues."""
    findings = []

    # Check actual rendered content for overflow
    for i, line in enumerate(lines):
        clean = strip_ansi(line)
        # Positions table header
        if "UNREAL PnL" in clean and len(clean) > width:
            findings.append(AuditFinding(
                screen="paper",
                element="PositionsTableWidget",
                width=width,
                issue_type="overflow",
                description=f"Positions table ({len(clean)} chars) overflows terminal ({width} cols)",
                severity="critical",
                coordinates=f"row={i+1}, rendered={len(clean)}",
                suggested_fix="Reduce column widths; hide MARK column below 120 cols",
            ))
        # Order history header
        if "ORDER HISTORY" in clean and i + 2 < len(lines):
            header_line = strip_ansi(lines[i + 1]) if i + 1 < len(lines) else ""
            if len(header_line) > width:
                findings.append(AuditFinding(
                    screen="paper",
                    element="OrderHistoryWidget",
                    width=width,
                    issue_type="overflow",
                    description=f"Order history header ({len(header_line)} chars) overflows terminal ({width} cols)",
                    severity="critical",
                    coordinates=f"row={i+2}, rendered={len(header_line)}",
                    suggested_fix="Reduce column widths; hide PRICE column below 120 cols",
                ))

    return findings


def analyze_risk_screen(width: int, content_area: int, lines: list[str]) -> list[AuditFinding]:
    """Analyze risk screen for display issues."""
    findings = []

    # Check actual rendered content for correlation matrix overflow
    for i, line in enumerate(lines):
        clean = strip_ansi(line)
        # Correlation matrix rows contain block characters and values
        if ("█" in clean or "▓" in clean or "▒" in clean or "░" in clean) and len(clean) > width:
            findings.append(AuditFinding(
                screen="risk",
                element="CorrelationHeatmapWidget",
                width=width,
                issue_type="overflow",
                description=f"Correlation matrix row ({len(clean)} chars) overflows terminal ({width} cols)",
                severity="critical",
                coordinates=f"row={i+1}, rendered={len(clean)}",
                suggested_fix="Truncate strategy names or use abbreviated headers for n>4",
            ))

    return findings


def analyze_strategy_screen(width: int, content_area: int, lines: list[str]) -> list[AuditFinding]:
    """Analyze strategy screen for display issues."""
    findings = []

    # Check actual rendered content for overflow
    for i, line in enumerate(lines):
        clean = strip_ansi(line)
        # Results table header contains column names
        if "NAME" in clean and "SCORE" in clean and len(clean) > width:
            findings.append(AuditFinding(
                screen="strategy",
                element="ResultsTableWidget",
                width=width,
                issue_type="overflow",
                description=f"Results table header ({len(clean)} chars) overflows terminal ({width} cols)",
                severity="critical",
                coordinates=f"row={i+1}, rendered={len(clean)}",
                suggested_fix="Hide SPARKLINE and MAXDD columns below 120 cols; hide PnL% below 100 cols",
            ))

    return findings


def analyze_telemetry_screen(width: int, content_area: int, lines: list[str]) -> list[AuditFinding]:
    """Analyze telemetry screen for display issues."""
    findings = []

    # Check actual rendered content for overflow
    for i, line in enumerate(lines):
        clean = strip_ansi(line)
        if len(clean) > width:
            findings.append(AuditFinding(
                screen="telemetry",
                element="telemetry_content",
                width=width,
                issue_type="overflow",
                description=f"Content line ({len(clean)} chars) overflows terminal ({width} cols)",
                severity="high",
                coordinates=f"row={i+1}, rendered={len(clean)}",
                suggested_fix="Truncate or wrap content to fit terminal width",
            ))

    return findings


def analyze_evidence_screen(width: int, content_area: int, lines: list[str]) -> list[AuditFinding]:
    """Analyze evidence screen for display issues."""
    findings = []

    # Check actual rendered content for overflow
    for i, line in enumerate(lines):
        clean = strip_ansi(line)
        if len(clean) > width:
            findings.append(AuditFinding(
                screen="evidence",
                element="evidence_content",
                width=width,
                issue_type="overflow",
                description=f"Content line ({len(clean)} chars) overflows terminal ({width} cols)",
                severity="medium",
                coordinates=f"row={i+1}, rendered={len(clean)}",
                suggested_fix="Truncate source/target names or demo step results",
            ))

    return findings


def check_disappearing_content(captures: dict[int, dict[str, ScreenCapture]]) -> list[AuditFinding]:
    """Check for UI elements that disappear at narrower widths."""
    findings = []

    for screen in SCREENS:
        widths_available = sorted(captures.keys())
        if len(widths_available) < 2:
            continue

        wide = captures[widths_available[-1]].get(screen)
        narrow = captures[widths_available[0]].get(screen)

        if not wide or not narrow:
            continue

        # Count visible content indicators per width
        wide_content_lines = sum(1 for ln in wide.lines if strip_ansi(ln).strip())
        narrow_content_lines = sum(1 for ln in narrow.lines if strip_ansi(ln).strip())

        if wide_content_lines > 0 and narrow_content_lines == 0:
            findings.append(AuditFinding(
                screen=screen,
                element="screen_content",
                width=widths_available[0],
                issue_type="disappear",
                description=f"Screen has {wide_content_lines} content lines at {widths_available[-1]} cols but 0 at {widths_available[0]} cols",
                severity="critical",
                suggested_fix="Ensure screen renders content at all widths",
            ))
        elif wide_content_lines > 0 and narrow_content_lines < wide_content_lines * 0.5:
            pct = (1 - narrow_content_lines / wide_content_lines) * 100
            findings.append(AuditFinding(
                screen=screen,
                element="screen_content",
                width=widths_available[0],
                issue_type="clip",
                description=f"Screen loses {pct:.0f}% of content lines at {widths_available[0]} cols vs {widths_available[-1]} cols",
                severity="high",
                coordinates=f"wide_lines={wide_content_lines}, narrow_lines={narrow_content_lines}",
                suggested_fix="Add responsive layout or scrolling for narrow terminals",
            ))

    return findings


def generate_report(
    captures: dict[int, dict[str, ScreenCapture]],
    findings: list[AuditFinding],
    output_path: Path,
) -> None:
    """Generate the display audit report in Markdown."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Sort findings by severity
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    findings.sort(key=lambda f: (severity_order.get(f.severity, 9), f.screen))

    lines: list[str] = []
    lines.append("# TUI Display Audit Report")
    lines.append("")
    lines.append(f"**Generated:** {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    lines.append(f"**Widths tested:** {', '.join(str(w) for w in WIDTHS)}")
    lines.append(f"**Screens tested:** {', '.join(SCREENS)}")
    lines.append(f"**Total findings:** {len(findings)}")
    lines.append("")

    # Summary table
    lines.append("## Summary")
    lines.append("")
    lines.append("| Severity | Count | Description |")
    lines.append("|----------|-------|-------------|")

    severity_counts: dict[str, int] = {}
    for f in findings:
        severity_counts[f.severity] = severity_counts.get(f.severity, 0) + 1

    for sev in ["critical", "high", "medium", "low"]:
        count = severity_counts.get(sev, 0)
        if count > 0:
            desc = {
                "critical": "Must fix — content disappears or layout breaks completely",
                "high": "Should fix — significant overflow or readability issue",
                "medium": "Nice to fix — minor overflow or UX gap",
                "low": "Cosmetic — minor visual issue",
            }
            lines.append(f"| {sev} | {count} | {desc[sev]} |")

    lines.append("")

    # Per-width summary
    lines.append("## Width Compatibility Matrix")
    lines.append("")
    lines.append("| Screen | 80 cols | 120 cols | 160 cols |")
    lines.append("|--------|---------|----------|----------|")

    for screen in SCREENS:
        row = f"| {screen} "
        for w in WIDTHS:
            screen_findings = [f for f in findings if f.screen == screen and f.width == w]
            critical = sum(1 for f in screen_findings if f.severity == "critical")
            high = sum(1 for f in screen_findings if f.severity == "high")
            if critical > 0:
                row += f"| ✗ {critical} critical "
            elif high > 0:
                row += f"| ⚠ {high} high "
            elif screen_findings:
                row += f"| △ {len(screen_findings)} minor "
            else:
                row += "| ✓ pass "
        row += "|"
        lines.append(row)

    lines.append("")

    # Detailed findings
    lines.append("## Detailed Findings")
    lines.append("")

    current_severity = None
    for f in findings:
        if f.severity != current_severity:
            current_severity = f.severity
            lines.append(f"### {current_severity.upper()} Issues")
            lines.append("")

        lines.append(f"#### {f.screen}/{f.element}")
        lines.append("")
        lines.append(f"- **Width:** {f.width} cols")
        lines.append(f"- **Type:** {f.issue_type}")
        lines.append(f"- **Description:** {f.description}")
        if f.coordinates:
            lines.append(f"- **Coordinates:** {f.coordinates}")
        if f.suggested_fix:
            lines.append(f"- **Suggested Fix:** {f.suggested_fix}")
        lines.append("")

    # Screen captures summary
    lines.append("## Screen Capture Analysis")
    lines.append("")

    for w in WIDTHS:
        lines.append(f"### {w} Columns")
        lines.append("")
        screen_data = captures.get(w, {})
        for screen_name in SCREENS:
            cap = screen_data.get(screen_name)
            if cap:
                content_lines = sum(1 for ln in cap.lines if strip_ansi(ln).strip())
                lines.append(f"- **{screen_name}:** {content_lines} content lines, "
                           f"max width {cap.max_rendered_width}, "
                           f"{cap.clipped_lines} clipped, "
                           f"{cap.empty_lines} empty")
            else:
                lines.append(f"- **{screen_name}:** no capture")
        lines.append("")

    # Recommendations
    lines.append("## Recommendations")
    lines.append("")

    critical_findings = [f for f in findings if f.severity == "critical"]
    if critical_findings:
        lines.append("### Critical Fixes Required")
        lines.append("")
        for f in critical_findings:
            lines.append(f"1. **{f.screen}/{f.element}** at {f.width} cols: {f.suggested_fix}")
        lines.append("")

    lines.append("### General Improvements")
    lines.append("")
    lines.append("1. Add responsive CSS media queries for widths < 120 cols")
    lines.append("2. Implement column hiding for tables at narrow widths")
    lines.append("3. Use percentage widths instead of fixed pixel widths where possible")
    lines.append("4. Add horizontal scrolling for wide content areas")
    lines.append("5. Test all screens at 80, 120, and 160 columns during development")
    lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  ✓ Report written to {output_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="TUI Display Audit")
    parser.add_argument("--fix", action="store_true", help="Apply automatic fixes")
    parser.add_argument("--report", default="runs/display_audit_report.md", help="Report output path")
    args = parser.parse_args()

    project_root = Path("/home/eya/soso/siglab")
    report_path = project_root / args.report

    print("=" * 60)
    print("  SigLab TUI Display Audit")
    print("=" * 60)
    print()

    all_findings: list[AuditFinding] = []
    all_captures: dict[int, dict[str, ScreenCapture]] = {}

    for width in WIDTHS:
        print(f"── Testing at {width} columns {'─' * 40}")

        # Start TUI at this width
        print(f"  Starting TUI at {width}x40...")
        if not start_tui(width):
            print(f"  ✗ Failed to start TUI at {width} cols")
            continue

        screen_captures: dict[str, ScreenCapture] = {}

        for screen_name in SCREENS:
            print(f"  Navigating to {screen_name} screen...")
            navigate_to_screen(screen_name)

            # Capture pane content
            lines = capture_pane(width)
            raw_lines = capture_pane_raw(width)

            if not lines:
                print(f"  ✗ Failed to capture {screen_name} at {width} cols")
                continue

            # Analyze capture
            capture, findings = analyze_capture(screen_name, width, lines, raw_lines)
            screen_captures[screen_name] = capture
            all_findings.extend(findings)

            content_lines = sum(1 for ln in lines if strip_ansi(ln).strip())
            print(f"  {screen_name}: {content_lines} content lines, "
                  f"max_width={capture.max_rendered_width}, "
                  f"{len(findings)} issues found")

        all_captures[width] = screen_captures

        # Cleanup tmux session before next width
        cleanup_tmux()

    # Check for disappearing content across widths
    disappear_findings = check_disappearing_content(all_captures)
    all_findings.extend(disappear_findings)

    print()
    print("── Analysis Complete ──")
    print(f"  Total findings: {len(all_findings)}")

    severity_counts: dict[str, int] = {}
    for f in all_findings:
        severity_counts[f.severity] = severity_counts.get(f.severity, 0) + 1

    for sev in ["critical", "high", "medium", "low"]:
        count = severity_counts.get(sev, 0)
        if count:
            print(f"  {sev}: {count}")

    # Generate report
    print()
    print("── Generating Report ──")
    generate_report(all_captures, all_findings, report_path)

    # Also write JSON findings for programmatic consumption
    json_path = report_path.with_suffix(".json")
    findings_json = [
        {
            "screen": f.screen,
            "element": f.element,
            "width": f.width,
            "type": f.issue_type,
            "severity": f.severity,
            "description": f.description,
            "coordinates": f.coordinates,
            "suggested_fix": f.suggested_fix,
        }
        for f in all_findings
    ]
    json_path.write_text(json.dumps(findings_json, indent=2), encoding="utf-8")
    print(f"  ✓ JSON findings written to {json_path}")

    # Return non-zero if critical findings
    critical_count = severity_counts.get("critical", 0)
    if critical_count > 0:
        print(f"\n  ✗ {critical_count} critical findings — audit FAILED")
        return 1

    high_count = severity_counts.get("high", 0)
    if high_count > 0:
        print(f"\n  ⚠ {high_count} high findings — audit has warnings")
        return 0

    print("\n  ✓ All screens pass display audit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
