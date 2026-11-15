#!/bin/bash
# Runs 10 minutes after every boot (after-restart.timer): the same health check, simple fixes
# and Claude repair as the weekly update, so a restart can't leave something broken unnoticed.
set -u
REPO=/home/sagan/tinycloud-main-server
RUN=/var/log/weekly-update/after-restart-$(date +%F-%H%M)
mkdir -p "$RUN"
exec > >(tee -a "$RUN/check.log") 2>&1
echo "== check after restart $(date -Is) (booted $(uptime -s))"
"$REPO/ops/check-and-repair.sh" "$RUN" "The server just restarted (an automatic restart after a system update, a power cut, or a manual reboot)."
HEALTH=$?
python3 - "$RUN" "$HEALTH" <<'PY'
import json, os, sys, datetime
run, health = sys.argv[1:3]
final = os.path.join(run, "health-final.json")
h = json.load(open(final if os.path.exists(final) else os.path.join(run, "health-after.json")))
out = {"when": datetime.datetime.now().astimezone().isoformat(timespec="minutes"), "passed": health == "0",
       "failed": h["failed"], "checks": h["count"], "repair": open(os.path.join(run, "repair-status.txt")).read().strip(),
       "claude": open(os.path.join(run, "claude-ready.txt")).read().strip(), "log": run}
tmp = "/home/sagan/hub/data/.after-restart.json"
json.dump(out, open(tmp, "w"), indent=2); os.chmod(tmp, 0o644)
os.replace(tmp, "/home/sagan/hub/data/after-restart.json")
PY
ls -1d /var/log/weekly-update/after-restart-* 2>/dev/null | sort | head -n -10 | while read old; do rm -r -- "$old"; done
echo "== done: health $([ $HEALTH -eq 0 ] && echo passed || echo FAILED), repair: $(cat "$RUN/repair-status.txt")"
