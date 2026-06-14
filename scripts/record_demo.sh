#!/usr/bin/env bash
# scripts/record_demo.sh
#
# Non-interactive recorder for the SigLab buildathon demo. Wraps the
# 10-step demo flow in asciinema (preferred) or script(1) when available,
# capturing per-step stdout/stderr + timing under runs/demo_steps/ for
# the audit trail. Per docs/demo-script.md and
# docs/buildathon-readiness-audit.md (top red flag: "Add screenshots/video
# of /ops and the market report for judging").
#
# Usage:
#   scripts/record_demo.sh                   # record + run all 10 steps
#   RECORD_DEMO_NO_REEXEC=1 scripts/record_demo.sh
#                                            # run steps without a recorder
#   scripts/record_demo.sh --dry-run         # print the planned plan, exit
#   RECORD_DEMO_DRY_RUN=1 scripts/record_demo.sh
#
# Outputs:
#   runs/demo_recording.cast   (when asciinema is available)
#   runs/demo_recording.log    (script(1) fallback, or synthesized transcript)
#   runs/demo_steps/step_NN_*.log  (per-step stdout/stderr + timing)
#
# Notes:
#   - Optional steps (B.AI loop, benchmark deck) are not allowed to abort
#     the recording; their failures are reported in the final summary.
#   - The script is intentionally non-interactive (no prompts, no TTY input).
#   - asciinema is detected via both `command -v` and `apt list --installed`
#     so the check works on fresh dev containers.

set -uo pipefail  # NOT -e: optional steps must not abort the demo.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR" || exit 1

RUNS_DIR="$ROOT_DIR/runs"
STEPS_DIR="$RUNS_DIR/demo_steps"
CAST_PATH="$RUNS_DIR/demo_recording.cast"
LOG_PATH="$RUNS_DIR/demo_recording.log"
mkdir -p "$RUNS_DIR" "$STEPS_DIR"

# ---- recorder detection ----------------------------------------------------

has_asciinema() {
  if command -v asciinema >/dev/null 2>&1; then
    return 0
  fi
  if command -v apt >/dev/null 2>&1 \
     && apt list --installed 2>/dev/null | grep -qw asciinema; then
    return 0
  fi
  return 1
}

has_script_util() {
  command -v script >/dev/null 2>&1
}

# ---- dry-run ----------------------------------------------------------------
# Print the planned plan and exit before any side effects.

if [[ "${RECORD_DEMO_DRY_RUN:-0}" == 1 || "${1:-}" == "--dry-run" ]]; then
  printf 'DRY_RUN plan: 10 steps -> cast=%s log=%s asciinema=%s script=%s\n' \
    "$CAST_PATH" "$LOG_PATH" \
    "$(has_asciinema && echo yes || echo no)" \
    "$(has_script_util && echo yes || echo no)"
  exit 0
fi

# ---- re-exec into a recorder ----------------------------------------------
# We detect a recorder and re-exec this script inside it, so the actual
# step output (including timing banners) becomes the recording.

if [[ "${RECORD_DEMO_REENTRY:-0}" != 1 && "${RECORD_DEMO_NO_REEXEC:-0}" != 1 ]]; then
  if has_asciinema; then
    RECORD_DEMO_REENTRY=1 RECORD_DEMO_RECORDER=asciinema \
      exec asciinema rec --quiet -c "$0" "$CAST_PATH"
  elif has_script_util; then
    RECORD_DEMO_REENTRY=1 RECORD_DEMO_RECORDER=script \
      exec script -q -c "$0" "$LOG_PATH"
  fi
  # No external recorder available: fall through and synthesize a
  # transcript from the per-step logs at the end.
  export RECORD_DEMO_RECORDER=none
else
  : "${RECORD_DEMO_RECORDER:=reentry}"
fi

# ---- step runner -----------------------------------------------------------

declare -a STEP_RESULTS=()

run_step() {
  local n="$1" name="$2" optional="$3"; shift 3
  local slug
  slug="$(printf 'step_%02d_%s' "$n" "$name")"
  local logfile="$STEPS_DIR/${slug}.log"
  local start end rc seconds status

  start=$(date +%s)
  "$@" >"$logfile" 2>&1
  rc=$?
  end=$(date +%s)
  seconds=$(( end - start ))

  if [[ $rc -eq 0 ]]; then
    status="ok"
  elif [[ "$optional" == 1 ]]; then
    status="skipped"
  else
    status="fail"
  fi
  STEP_RESULTS+=("$status $slug ${seconds}s")

  # Replay the step's captured output to the terminal so the recorder
  # (and any human tailing the run) see the same content as the log file.
  printf '\n----- %s (rc=%d, %ss, %s) -----\n' "$slug" "$rc" "$seconds" "$status"
  cat "$logfile"
  printf '----- end %s -----\n' "$slug"
}

# ---- 10-step demo flow -----------------------------------------------------
# Steps map 1:1 to the numbered sections in docs/demo-script.md.

# 1. Build SoSoValue Evidence
run_step 1 evidence_build 0 \
  python3 -m siglab.cli evidence-build \
    --currency BTC \
    --etf-type us-btc-spot \
    --news-page-size 20 \
    --news-pages 2 \
    --output runs/evidence/live_sosovalue_probe_btc_pages.jsonl \
    --summary-output runs/evidence/live_sosovalue_probe_btc_pages.summary.json \
    --json

# 2. Probe SoDEX Public WebSocket
run_step 2 sodex_ws_probe 0 \
  python3 -m siglab.cli sodex-ws-probe \
    --channel allBookTicker \
    --timeout-seconds 12 \
    --evidence-output runs/evidence/sodex_ws_evidence.jsonl \
    --json

# 3. Render Evidence Graph
run_step 3 evidence_map 0 \
  python3 -m siglab.cli evidence-map \
    --evidence runs/evidence/live_sosovalue_probe_btc_pages.jsonl \
    --output runs/evidence/evidence_graph.html \
    --json

# 4. Generate Market Report
run_step 4 market_report 0 \
  python3 -m siglab.cli market-report \
    --entity BTC \
    --sosovalue-evidence runs/evidence/live_sosovalue_probe_btc_pages.jsonl \
    --sodex-evidence runs/evidence/sodex_ws_evidence.jsonl \
    --output runs/market_report_latest.json \
    --html-output runs/market_report_latest.html \
    --json

# 5. Capture Provider Telemetry
run_step 5 telemetry_report 0 \
  python3 -m siglab.cli telemetry-report \
    --track trend_signals \
    --json

# 6. Verify Live Boundary
run_step 6 sodex_preflight 0 \
  python3 -m siglab.cli sodex-preflight --json

# 7. Optional B.AI Loop With Budget Guard (skipped if no provider env)
if [[ -f "$ROOT_DIR/.siglab-provider.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT_DIR/.siglab-provider.env"
  set +a
  run_step 7 bai_loop 1 \
    python3 -m siglab.cli run \
      --track trend_signals \
      --iterations 1 \
      --max-call-estimated-credits 3000 \
      --max-total-credits 6000 \
      --max-provider-errors 1 \
      --agent-label demo-deepseek-v4-flash \
      --run-label demo-deepseek-v4-flash
else
  printf '\n[step 7] optional, .siglab-provider.env not present -> skipped\n'
  STEP_RESULTS+=("skipped step_07_bai_loop 0s")
fi

# 8. Build Demo Manifest
run_step 8 demo_manifest 0 \
  python3 -m siglab.cli demo-manifest \
    --output runs/demo_manifest_latest.json \
    --html-output runs/demo_manifest_latest.html \
    --json

# 9. Wave Status (the artifact the /ops board reads)
run_step 9 wave_status 0 \
  python3 -m siglab.cli wave-status \
    --wave-number 1 \
    --phase demo \
    --status running \
    --goal "show input-to-action flow with live-boundary truth" \
    --agents "operator,dashboard,hardening" \
    --outputs "market report,ops board,preflight" \
    --blockers "signed SoDEX live execution unproven" \
    --validation-status targeted_pass \
    --next-decision "continue demo refresh"

# 10. Optional External-Agent Benchmark Deck
run_step 10 benchmark_status 1 \
  python3 -m siglab.cli benchmark-status --deck trend_signals_external

# ---- synthesize transcript fallback when no recorder was used --------------

case "${RECORD_DEMO_RECORDER}" in
  asciinema) recording="$CAST_PATH" ;;
  script)    recording="$LOG_PATH" ;;
  *)
    {
      echo "SigLab demo recording transcript ($(date -u +%Y-%m-%dT%H:%M:%SZ))"
      echo "root: $ROOT_DIR"
      echo
      echo "per-step results:"
      for r in "${STEP_RESULTS[@]}"; do echo "  $r"; done
      echo
      for f in "$STEPS_DIR"/step_*.log; do
        [[ -f "$f" ]] || continue
        echo "===== $f ====="
        cat "$f"
        echo
      done
    } > "$LOG_PATH"
    recording="$LOG_PATH"
    ;;
esac

# ---- one-line summary ------------------------------------------------------

ok=0; fail=0; skip=0
for r in "${STEP_RESULTS[@]}"; do
  case "$r" in
    ok*)      ok=$((ok+1)) ;;
    fail*)    fail=$((fail+1)) ;;
    skipped*) skip=$((skip+1)) ;;
  esac
done

printf 'DEMO_SUMMARY ok=%d fail=%d skipped=%d recording=%s steps_dir=%s\n' \
  "$ok" "$fail" "$skip" "$recording" "$STEPS_DIR"
