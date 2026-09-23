#!/bin/bash
# Runs the pipeline only — no branch switching, no deploy. Safe to run from
# cron regardless of which branch happens to be checked out elsewhere,
# because it never touches git. Deploy (export deals.json + push to
# gh-pages) stays a separate, manual step: run_and_deploy.sh on gh-pages.
set -e
cd "$(dirname "$0")"

echo "=== Pipeline run started $(date '+%Y-%m-%d %H:%M:%S %Z') ==="

if ! docker start nh-test-db > /dev/null 2>&1; then
    echo "ERROR: could not start nh-test-db (is Docker Desktop running?)"
    exit 1
fi

# main.py exits 2 when the run finished but a source or step failed
# (pipeline/run_health.py) -- report it instead of letting set -e hide it
status=0
venv/bin/python3 main.py --no-alerts || status=$?
echo "=== Pipeline run finished $(date '+%Y-%m-%d %H:%M:%S %Z') (exit $status) ==="
if [ "$status" -eq 2 ]; then
    echo "WARNING: run finished with problems -- see the RUN HEALTH lines above"
fi
exit $status
