#!/usr/bin/env bash
# Check the hass-4 add-on logs for errors, warnings, and deprecation warnings.
# Run after every deploy to catch regressions early.
#
# Usage:
#   bash scripts/check-addon-logs.sh
#
# Exit codes:
#   0 — clean (no errors or warnings)
#   1 — errors or warnings found (printed to stdout)

set -euo pipefail

HOST="${HASS4_HOST:-root@hass-4.local}"
ADDON_SLUG="local_esphome_dist_server"

echo "==> Checking add-on logs on ${HOST}…"

# Fetch logs and filter for problems
ISSUES=$(ssh "$HOST" "ha addons logs $ADDON_SLUG 2>/dev/null" 2>/dev/null \
  | grep -iE "ERROR|WARNING|Traceback|DeprecationWarning|Exception" \
  | grep -v "DeprecationWarning: Changing state of started or joined application" \
  | grep -v "PytestDeprecationWarning" \
  | grep -v "NotAppKeyWarning" \
  | tail -30 \
  || true)

if [[ -z "$ISSUES" ]]; then
    echo "✅ No errors or warnings in add-on logs."
    exit 0
else
    echo ""
    echo "⚠ Found issues in add-on logs:"
    echo "$ISSUES"
    echo ""
    echo "Review the above and fix before moving on."
    exit 1
fi
