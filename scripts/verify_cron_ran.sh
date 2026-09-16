#!/bin/bash
# One-shot check: did the 8am cron job (run_pipeline.sh) actually fire
# today after the ~/Downloads -> ~/Projects move + crontab path fix?
# Writes a clear PASS/FAIL result with evidence to /tmp/nh-cron-verification.txt.

OUT=/tmp/nh-cron-verification.txt
BASELINE_COUNT=1088
TODAY=$(date '+%Y-%m-%d')

{
  echo "=== Cron verification run: $(date) ==="
  echo

  echo "--- /tmp/nh-pipeline.log tail ---"
  tail -20 /tmp/nh-pipeline.log 2>&1
  echo

  LOG_HIT=$(grep -c "Pipeline run started $TODAY" /tmp/nh-pipeline.log 2>/dev/null || echo 0)
  echo "Log entries for today ($TODAY): $LOG_HIT"
  echo

  CURRENT_COUNT=$(docker exec nh-test-db psql -U postgres -d nh_alerts_test -t -c "SELECT COUNT(*) FROM deals;" 2>&1 | tr -d ' ')
  echo "Deal count: baseline=$BASELINE_COUNT current=$CURRENT_COUNT"

  TODAY_DEALS=$(docker exec nh-test-db psql -U postgres -d nh_alerts_test -t -c "SELECT COUNT(*) FROM deals WHERE created_at::date = '$TODAY';" 2>&1 | tr -d ' ')
  echo "Deals with created_at date = today: $TODAY_DEALS"
  echo

  if [ "$LOG_HIT" -gt 0 ]; then
    echo "RESULT: PASS -- cron fired today, log entry found."
  else
    echo "RESULT: FAIL -- no 'Pipeline run started $TODAY' entry in the log. Cron did not fire, or is still blocked."
  fi
} > "$OUT" 2>&1

cat "$OUT"

# Self-cleanup: this is a one-shot check, don't leave a stray launchd job
# around the way com.nursinghome.pipeline was left around for weeks.
PLIST=~/Library/LaunchAgents/com.nursinghome.cron-verify-once.plist
if [ -f "$PLIST" ]; then
  (sleep 2; launchctl unload "$PLIST" 2>/dev/null; rm -f "$PLIST") &
  disown
fi

