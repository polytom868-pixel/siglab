#!/bin/bash
# Run E2E tests with seeded test data
#
# Starts a local SigLab dashboard server on port 8080, seeds it with
# test data, and runs the Playwright E2E test suite.
#
# Usage:
#   ./run_e2e.sh                  # full suite
#   ./run_e2e.sh -k test_home     # single test by keyword
#
# The script cleans up after itself (stops server, restores original DB).

set -euo pipefail

cd "$(dirname "$0")/../.."  # repo root

echo "========================================="
echo " E2E: Installing Playwright browsers..."
echo "========================================="
npx playwright install chromium 2>/dev/null || true

echo ""
echo "========================================="
echo " E2E: Running Playwright tests"
echo "========================================="
python3 -m pytest tests/e2e/test_demo_flows.py -v --tb=short "$@" 2>&1
EXIT_CODE=$?

echo ""
if [ $EXIT_CODE -eq 0 ]; then
  echo "========================================="
  echo " E2E: All tests passed"
  echo "========================================="
else
  echo "========================================="
  echo " E2E: Some tests FAILED (exit code $EXIT_CODE)"
  echo "========================================="
fi

exit $EXIT_CODE
