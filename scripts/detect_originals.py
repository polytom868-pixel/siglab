#!/usr/bin/env python3
from __future__ import annotations

import ast
import dataclasses as dc
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", "node_modules"}
SKIP_FILES = {Path(__file__).resolve()}
TEXT_EXTS = {
    ".py",
    ".md",
    ".yaml",
    ".yml",
    ".json",
    ".js",
    ".html",
    ".sh",
    ".toml",
    ".dsl",
}


def s(*parts: str) -> str:
    return "".join(parts)


PATTERNS = [
    ("wayfinder", re.compile(rf"{s('way','finder')}|{s('Way','finder')}Foundation", re.I)),
    ("autolab", re.compile(rf"{s('auto','lab')}", re.I)),
    ("kimi", re.compile(rf"{s('ki','mi')}", re.I)),
    (
        "legacy_types",
        re.compile(
            r"\b("
            + "|".join(
                [
                    s("Candidate", "Graph"),
                    s("Autolab", "Settings"),
                    s("Universe", "Spec"),
                    s("Risk", "Spec"),
                ]
            )
            + r")\b",
        ),
    ),
    ("legacy_tree", re.compile(rf"{s('wayfinder','_','autolab')}|{s('generated','_','strategies')}|data/lake", re.I)),
    ("legacy_tracks", re.compile(r"directional_perps|systematic_carry|market_neutral_carry", re.I)),
    ("legacy_actions", re.compile(r"\b(promote|promotion|promoted|wallet-label|runnerd)\b", re.I)),
    ("legacy_artifacts", re.compile(r"\bartifacts\b", re.I)),
    ("legacy_runtime", re.compile(r"\b(autolab|autolab_harness|autolab_live_spec_path|autolab_spec_path)\b", re.I)),
]


@dc.dataclass(frozen=True)
class Finding:
    file: str
    line: int
    column: int
    severity: str
    category: str
    excerpt: str
    symbol: str | None = None
    field: str | None = None


def excerpt(line_text: str, start: int, end: int) -> str:
    left = max(0, start - 40)
    right = min(len(line_text), end + 40)
    return line_text[left:right].strip()


def iter_files() -> list[Path]:
    out: list[Path] = []
    for path in ROOT.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_file() and path not in SKIP_FILES and path.suffix.lower() in TEXT_EXTS:
            out.append(path)
    return out


def mark(severity: str, category: str, path: Path, line: int, col: int, snippet: str, *, symbol: str | None = None, field: str | None = None) -> Finding:
    return Finding(str(path.relative_to(ROOT)), line, col, severity, category, snippet, symbol=symbol, field=field)


def scan_text(path: Path) -> list[Finding]:
    findings: list[Finding] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return findings
    lines = text.splitlines()
    for idx, line in enumerate(lines, start=1):
        for key, pattern in PATTERNS:
            for match in pattern.finditer(line):
                sev = "critical" if key in {"wayfinder", "autolab", "kimi", "legacy_types", "legacy_tree", "legacy_runtime"} else "high"
                findings.append(mark(sev, key, path, idx, match.start() + 1, excerpt(line, match.start(), match.end())))
    return findings


def ast_names(tree: ast.AST) -> list[tuple[str, int, int, str]]:
    items: list[tuple[str, int, int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            items.append((node.name, node.lineno, node.col_offset + 1, "symbol"))
        elif isinstance(node, ast.Attribute):
            items.append((node.attr, node.lineno, node.col_offset + 1, "attr"))
        elif isinstance(node, ast.arg):
            items.append((node.arg, node.lineno, node.col_offset + 1, "arg"))
        elif isinstance(node, ast.Name):
            items.append((node.id, node.lineno, node.col_offset + 1, "name"))
    return items


def scan_ast(path: Path) -> list[Finding]:
    if path.suffix.lower() != ".py":
        return []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    findings: list[Finding] = []
    name_patterns = {
        "wayfinder": re.compile(rf"{s('way','finder')}|{s('Way','finder')}", re.I),
        "autolab": re.compile(rf"{s('auto','lab')}", re.I),
        "kimi": re.compile(rf"{s('ki','mi')}", re.I),
        "legacy_types": re.compile(rf"({s('Candidate','Graph')}|{s('Autolab','Settings')}|{s('Universe','Spec')}|{s('Risk','Spec')})"),
        "legacy_runtime": re.compile(rf"({s('autolab','_','harness')}|{s('autolab','_','live','_','spec','_','path')}|{s('autolab','_','spec','_','path')})", re.I),
    }
    for name, line, col, kind in ast_names(tree):
        for category, pattern in name_patterns.items():
            if pattern.search(name):
                findings.append(mark("critical", category, path, line, col, name, symbol=name if kind == "symbol" else None, field=name if kind in {"attr", "arg", "name"} else None))
    return findings


def main() -> int:
    findings: list[Finding] = []
    for path in iter_files():
        findings.extend(scan_text(path))
        findings.extend(scan_ast(path))

    counts = Counter(f.severity for f in findings)
    by_file: dict[str, int] = defaultdict(int)
    for finding in findings:
        by_file[finding.file] += 1

    report = {
        "score": len(findings),
        "status": "clean" if not findings else "critical",
        "severity_counts": dict(counts),
        "files_with_findings": dict(sorted(by_file.items())),
        "findings": [dc.asdict(f) for f in findings],
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
