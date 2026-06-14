# A1_X5 — profile.py no-op verification

Re-read `siglab/cli/profile.py` (32 lines): imports + `run_command` only call `load_settings`, `build_profile`, `print_json/print_panel`, `strict_failure_count`. No socket/HTTP/LLM client paths. `python3 -m siglab.cli profile --strict --json` summary = `{module_count: 134, finding_count: 5, by_kind: {stub_marker: 5}, by_severity: {medium: 5}}` (0 high-risk). `strace -e network,connect` reports 0 `connect(AF_INET` syscalls.

profile.py is honest; no change needed.
