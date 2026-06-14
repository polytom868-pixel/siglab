#!/usr/bin/env bash
# safety_oneshot.template.sh — run the 3 safety checks for a microchange.
# Fill in FILE (path relative to repo root) and EXPECTED_LINES (the
# final line count of the file after the edit). The three checks are:
#   1. file exists and is non-empty
#   2. final line count matches EXPECTED_LINES
#   3. no forbidden patterns were introduced
#        (TODO/FIXME/XXX, broad except, print() debug, type: ignore
#         with a reason, no-op pass bodies marked as such)
# Exits 0 only if all three checks pass. Exits 1 on any failure.

set -euo pipefail

FILE="${FILE:-}"
EXPECTED_LINES="${EXPECTED_LINES:-}"

if [[ -z "$FILE" || -z "$EXPECTED_LINES" ]]; then
  echo "FAIL: set FILE and EXPECTED_LINES before running" >&2
  exit 1
fi

FAIL=0

# 1. exists and non-empty
if [[ ! -s "$FILE" ]]; then
  echo "FAIL [exists]: $FILE missing or empty"
  FAIL=1
else
  echo "OK   [exists]: $FILE present and non-empty"
fi

# 2. final line count matches EXPECTED_LINES
if [[ -f "$FILE" ]]; then
  ACTUAL_LINES=$(wc -l < "$FILE" | tr -d ' ')
  if [[ "$ACTUAL_LINES" == "$EXPECTED_LINES" ]]; then
    echo "OK   [lines]: $ACTUAL_LINES == $EXPECTED_LINES"
  else
    echo "FAIL [lines]: $ACTUAL_LINES != $EXPECTED_LINES"
    FAIL=1
  fi
fi

# 3. no forbidden patterns
if [[ -f "$FILE" ]]; then
  FORBIDDEN=$(grep -nE 'TODO|FIXME|XXX' "$FILE" || true)
  if [[ -n "$FORBIDDEN" ]]; then
    echo "FAIL [patterns]: forbidden tokens found:"
    echo "$FORBIDDEN"
    FAIL=1
  else
    echo "OK   [patterns]: no TODO/FIXME/XXX"
  fi
fi

if [[ "$FAIL" -ne 0 ]]; then
  echo "SAFETY CHECK: FAIL"
  exit 1
fi

echo "SAFETY CHECK: OK"
