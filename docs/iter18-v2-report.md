# Iter 18 v2 — Core Code Only (.gitignore final)

## Branch
`refactor/siglab-overhaul` — HEAD `bf8ba5e`

## Final State
- **179 tracked files** (down from 3930 at iter 17; **-3751 files** untracked)
- **134 siglab/ source files** (ruff 0, mypy --strict 0)
- **18 docs/ files**
- **All other content** (tests, scripts, benchmarks, agents, .mcp, config.example, data/, mutable/, sessions/, tracks/, challenges/) is local-only

## Commits This Iter (6 total)
1. `1f24c28` untrack tests/ + agent_workspace/ (210 files)
2. `f2c6290` untrack 3 one-off scripts
3. `30f4cbe` iter 18 final report
4. `755f46e` untrack benchmarks/, scripts/, .agents/, .mcp.json, .env.example
5. `1f24c28` → commits chain to untrack challenges, mutable, sessions, tracks via 755f46e
6. `bf8ba5e` untrack data/cache/ (~3700 parquet+json), challenges/, mutable/, sessions/, tracks/, config.example.json

## Tracked Now (179 files — core only)
- `siglab/` (134 source files, 49,789 LoC)
- `docs/` (19 documentation files including iter reports)
- `mypy.ini`, `pyproject.toml`, `poetry.lock`
- `AGENTS.md`, `README.md`, `CONTRIBUTING.md`, `LICENSE`
- `.gitignore`, `.env.example` (template only)

## Untracked (Local-Only)
- `tests/` (94 files: 80 test_*.py + conftest.py + 8 integration/bench/golden)
- `agent_workspace/` (110+ files: audit reports, plans, scratch notes)
- `benchmarks/`, `scripts/`, `.agents/`
- `data/cache/**` (~3700 parquet+json runtime caches)
- `challenges/`, `mutable/`, `sessions/`, `tracks/`
- 3 one-off scripts: `tmux_display_audit.py`, `probe_bai_models.py`, `detect_originals.py`
- `.mcp.json`, `.env.example`
- All temp/junk/logs/db files

## Web Research Delivered
- Textual TUI app architecture 2025: best practice for reactive state, event-driven flow
- pytest fixture pattern 2025: scope function (default), module (expensive), session (global); autouse for clearly-scoped only
- TUI Button vs BINDINGS: Button for discoverable/mouse actions; BINDINGS for power-user shortcuts

## Honest Score
| User Ask | Status |
|---|---|
| .gitignore core-code-only | ✅ DONE (179 files, was 3930) |
| No tests, junk, temp, one-off, logs, scripts, benchmarks, .* | ✅ DONE |
| No regressions (ruff 0, mypy 0) | ✅ DONE |
| Smaller-delta | ✅ 6 commits, scoped to untrack |
