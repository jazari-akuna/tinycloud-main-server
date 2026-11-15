#!/bin/bash
# Health check, then simple fixes, then Claude only if something still fails.
# Usage: check-and-repair.sh RUN_DIR "what just happened (goes into Claude's brief)"
# Writes RUN_DIR/health-*.txt|json, RUN_DIR/repair-status.txt; exit code 0 = all checks pass.
# Used by weekly-update.sh (after updating) and after-restart.sh (after every boot).
set -u
REPO=/home/sagan/tinycloud-main-server
RUN=$1
CONTEXT=${2:-}
DAY=$(date +%F)
STATE=/var/lib/weekly-update
mkdir -p "$RUN" "$STATE"
say() { echo "-- $*"; }

if ! sudo -u sagan -H claude auth status 2>/dev/null | grep -q '"loggedIn": true'; then
  CLAUDE_READY="Claude is signed out on the server, so it can't repair anything. Sign it in with: ssh -t sagan@192.168.1.52 claude auth login"
else
  USAGE=$(python3 "$REPO/ops/claude-usage.py" 2>/dev/null || echo unknown)
  if [ "${USAGE%% *}" = high ]; then CLAUDE_READY="Claude is at 90% or more of its usage limit with no extra usage left ($USAGE), so a repair would stall"; else CLAUDE_READY=yes; fi
fi
echo "$CLAUDE_READY" > "$RUN/claude-ready.txt"

say "health check"
python3 "$REPO/ops/health.py" --json "$RUN/health-after.json" > "$RUN/health-after.txt" < /dev/null
HEALTH=$?
grep FAIL "$RUN/health-after.txt" || echo "all passed"

REPAIR="not needed"
if [ $HEALTH -ne 0 ]; then
  say "simple fixes first (restarts, reloads)"
  python3 "$REPO/ops/self_repair.py" "$RUN/health-after.json" < /dev/null | tee "$RUN/self-repair.txt"
  sleep 45
  python3 "$REPO/ops/health.py" --json "$RUN/health-final.json" > "$RUN/health-final.txt" < /dev/null; HEALTH=$?
  if [ $HEALTH -eq 0 ]; then
    REPAIR="fixed automatically with a restart or reload; Claude wasn't needed"
  else
    FAILSET=$(python3 -c 'import json,sys;print("\n".join(sorted(f["check"] for f in json.load(open(sys.argv[1]))["failed"])))' "$RUN/health-final.json")
    if [ -f $STATE/claude-could-not-fix.txt ] && [ "$(cat $STATE/claude-could-not-fix.txt)" = "$FAILSET" ]; then
      REPAIR="needs you: Claude already tried these same failures last time and couldn't fix them"
    elif [ "$CLAUDE_READY" != yes ]; then
      REPAIR="needs you: $CLAUDE_READY"
    else
      say "asking Claude to repair"
      {
        cat "$REPO/ops/REPAIR.md"
        echo; echo "## What just happened ($DAY)"
        echo "$CONTEXT"
        if [ -f "$RUN/health-before.txt" ]; then echo; echo "Failing BEFORE (may be older problems):"; grep FAIL "$RUN/health-before.txt" || echo "(none)"; fi
        echo; echo "Still failing after the automatic restarts and reloads:"; grep FAIL "$RUN/health-final.txt"
        echo; echo "Automatic fixes already tried:"; cat "$RUN/self-repair.txt"
        echo; echo "Logs: $RUN/. Re-run checks with: sudo python3 $REPO/ops/health.py"
      } > "$RUN/repair-brief.md"
      chmod 644 "$RUN/repair-brief.md"
      sudo -u sagan -H bash -lc "cd $REPO && timeout 45m claude -p \"\$(cat $RUN/repair-brief.md)\" --model opus --effort medium --dangerously-skip-permissions --output-format text" > "$RUN/repair.txt" 2>&1 < /dev/null
      python3 "$REPO/ops/health.py" --json "$RUN/health-final.json" > "$RUN/health-final.txt" < /dev/null; HEALTH=$?
      if [ $HEALTH -eq 0 ]; then
        REPAIR="repaired by Claude: all checks pass again"; rm -f $STATE/claude-could-not-fix.txt
      else
        REPAIR="needs you: Claude tried, but some checks still fail"; printf %s "$FAILSET" > $STATE/claude-could-not-fix.txt
      fi
    fi
  fi
  grep FAIL "$RUN/health-final.txt" || echo "all passed after repair"
fi

echo "$REPAIR" > "$RUN/repair-status.txt"
exit $HEALTH
