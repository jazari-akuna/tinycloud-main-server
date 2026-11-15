#!/bin/bash
# Weekly update for the server. Runs as root from weekly-update.timer (Sundays 03:30 Swiss time).
#   1. health check before (so problems that were already there are known)
#   2. backups: databases, sign-in data, hub data (last 4 weeks kept)
#   3. updates: every Docker app to its newest image, Nextcloud's apps, Claude Code
#   4. health check after; if anything new fails, Claude (Opus, medium effort) repairs it,
#      unless the Claude account is at 90% or more of its usage limits
#   5. result written to the hub (/home/sagan/hub/data/maintenance.json) and to /var/log/weekly-update/
set -u
REPO=/home/sagan/tinycloud-main-server
PORTAINER=/var/lib/docker/volumes/portainer_data/_data
DAY=$(date +%F)
LOGDIR=/var/log/weekly-update
RUN=$LOGDIR/$DAY
BACKUP=/root/container-backups/weekly/$DAY
mkdir -p "$RUN" "$BACKUP"
exec > >(tee -a "$RUN/update.log") 2>&1
echo "== weekly update $(date -Is)"
UPDATED=()
NOTES=()

say() { echo "-- $*"; }

# ---------------------------------------------------------------- 1. before
say "health check before updating"
python3 "$REPO/ops/health.py" --json "$RUN/health-before.json" > "$RUN/health-before.txt"; cat "$RUN/health-before.txt" | grep FAIL || echo "all passed"

say "Claude ready to repair?"
if ! sudo -u sagan -H claude auth status 2>/dev/null | grep -q '"loggedIn": true'; then
  CLAUDE_READY="Claude is signed out on the server, so it can't repair anything. Sign it in with: ssh -t sagan@192.168.1.52 claude auth login"
else
  USAGE=$(python3 "$REPO/ops/claude-usage.py" 2>/dev/null || echo unknown)
  if [ "${USAGE%% *}" = high ]; then CLAUDE_READY="Claude is at 90% or more of its usage limit with no extra usage left ($USAGE), so a repair would stall"; else CLAUDE_READY=yes; fi
fi
echo "$CLAUDE_READY"
[ "$CLAUDE_READY" != yes ] && NOTES+=("$CLAUDE_READY")

# ---------------------------------------------------------------- 2. backups
say "backups to $BACKUP"
NCPW=$(docker exec -u www-data nextcloud-nextcloud-1 php occ config:system:get dbpassword 2>/dev/null)
printf %s "$NCPW" | docker exec -i nextcloud-nc_db-1 sh -c 'export MYSQL_PWD="$(cat)"; mariadb-dump -unextcloud --single-transaction --skip-lock-tables nextcloud' | gzip > "$BACKUP/nextcloud-db.sql.gz"
docker exec nginx printenv DB_MYSQL_PASSWORD | docker exec -i maria_for_nginx sh -c 'export MYSQL_PWD="$(cat)"; mariadb-dump -h127.0.0.1 -unpm --single-transaction npm' | gzip > "$BACKUP/proxy-db.sql.gz"
docker exec yourls-mysql-1 sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysqldump -uroot --single-transaction yourls' 2>/dev/null | gzip > "$BACKUP/yourls-db.sql.gz"
tar -C /home/sagan -czf "$BACKUP/authelia-hub-jellyfin-sso.tgz" authelia/data hub/data jellyfin-sso 2>/dev/null
cp /var/lib/docker/volumes/jelly_config/_data/data/jellyfin.db "$BACKUP/jellyfin.db" 2>/dev/null
chmod -R go-rwx "$BACKUP"
ls -1 /root/container-backups/weekly | sort | head -n -4 | while read old; do rm -r -- "/root/container-backups/weekly/$old"; done
du -sh "$BACKUP" | cut -f1 | sed 's/^/backup size: /'

# ---------------------------------------------------------------- 3. updates
say "repo"
sudo -u sagan git -C "$REPO" pull -q --ff-only || NOTES+=("The server's copy of the repo could not be updated from GitHub.")

say "app images"
declare -A SEEN
while IFS='|' read -r project workdir files; do
  [ -z "$project" ] && continue
  [ -n "${SEEN[$project]:-}" ] && continue
  SEEN[$project]=1
  workdir=${workdir/#\/data\//$PORTAINER\/}
  file=${files%%,*}; file=${file/#\/data\//$PORTAINER\/}
  [ -f "$file" ] || { NOTES+=("Couldn't find the setup file for $project."); continue; }
  envfile=""
  for e in "$workdir/../stack.env" "$workdir/stack.env"; do [ -f "$e" ] && envfile="--env-file $e" && break; done
  before=$(docker compose -p "$project" -f "$file" $envfile images -q 2>/dev/null | sort | tr '\n' ' ')
  if grep -q '^\s*build:' "$file"; then
    docker compose -p "$project" -f "$file" $envfile build --pull -q >/dev/null 2>&1
  fi
  docker compose -p "$project" -f "$file" $envfile pull -q --ignore-buildable 2>/dev/null || docker compose -p "$project" -f "$file" $envfile pull -q 2>/dev/null
  if [ "$project" = authelia ]; then
    # a new Authelia must accept the current settings before it replaces the running one
    if ! docker compose -p authelia -f "$file" run --rm --no-deps --entrypoint authelia authelia validate-config --config /config/configuration.yml 2>&1 | grep -q successfully; then
      NOTES+=("The newest Authelia doesn't accept the current settings, so it was not switched to."); continue
    fi
  fi
  (cd "$workdir" 2>/dev/null || cd "$(dirname "$file")"; docker compose -p "$project" -f "$file" $envfile up -d --remove-orphans >/dev/null 2>&1)
  after=$(docker compose -p "$project" -f "$file" $envfile images -q 2>/dev/null | sort | tr '\n' ' ')
  [ "$before" != "$after" ] && UPDATED+=("$project") && echo "updated: $project"
done < <(docker ps --format '{{.Label "com.docker.compose.project"}}|{{.Label "com.docker.compose.project.working_dir"}}|{{.Label "com.docker.compose.project.config_files"}}' | sort -u)

say "Portainer"
old=$(docker inspect -f '{{.Image}}' portainer)
docker pull -q portainer/portainer-ce:lts >/dev/null
if [ "$old" != "$(docker image inspect -f '{{.Id}}' portainer/portainer-ce:lts)" ]; then
  docker stop portainer >/dev/null && docker rm portainer >/dev/null
  docker run -d --name portainer --restart=always -p 8000:8000 -p 9000:9000 \
    -v /var/run/docker.sock:/var/run/docker.sock -v portainer_data:/data portainer/portainer-ce:lts >/dev/null
  UPDATED+=("portainer")
fi

say "Nextcloud apps"
sleep 20
docker exec -u www-data nextcloud-nextcloud-1 php occ upgrade -n 2>&1 | tail -1
docker exec -u www-data nextcloud-nextcloud-1 php occ app:update --all 2>&1 | grep -i -E 'updated|new version' && UPDATED+=("nextcloud apps")
docker exec -u www-data nextcloud-nextcloud-1 php occ db:add-missing-indices -n >/dev/null 2>&1

say "Claude Code"
sudo -u sagan -H bash -lc 'timeout 300 claude update' 2>&1 | tail -1

docker image prune -f >/dev/null

# ---------------------------------------------------------------- 4. after, and repair
say "health check after updating"
sleep 60
python3 "$REPO/ops/health.py" --json "$RUN/health-after.json" > "$RUN/health-after.txt"
HEALTH=$?
grep FAIL "$RUN/health-after.txt" || echo "all passed"

REPAIR="not needed"
STATE=/var/lib/weekly-update; mkdir -p $STATE
if [ $HEALTH -ne 0 ]; then
  say "simple fixes first (restarts, reloads)"
  python3 "$REPO/ops/self_repair.py" "$RUN/health-after.json" | tee "$RUN/self-repair.txt"
  sleep 45
  python3 "$REPO/ops/health.py" --json "$RUN/health-final.json" > "$RUN/health-final.txt"; HEALTH=$?
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
        echo; echo "## This week's run ($DAY)"
        echo "Updated: ${UPDATED[*]:-nothing}"
        echo; echo "Failing BEFORE the update (may be older problems):"; grep FAIL "$RUN/health-before.txt" || echo "(none)"
        echo; echo "Still failing AFTER the update and the automatic restarts:"; grep FAIL "$RUN/health-final.txt"
        echo; echo "Automatic fixes already tried:"; cat "$RUN/self-repair.txt"
        echo; echo "Full log: $RUN/update.log. Re-run checks with: sudo python3 $REPO/ops/health.py"
      } > "$RUN/repair-brief.md"
      chmod 644 "$RUN/repair-brief.md"
      sudo -u sagan -H bash -lc "cd $REPO && timeout 45m claude -p \"\$(cat $RUN/repair-brief.md)\" --model opus --effort medium --dangerously-skip-permissions --output-format text" > "$RUN/repair.txt" 2>&1
      python3 "$REPO/ops/health.py" --json "$RUN/health-final.json" > "$RUN/health-final.txt"; HEALTH=$?
      if [ $HEALTH -eq 0 ]; then
        REPAIR="repaired by Claude: all checks pass again"; rm -f $STATE/claude-could-not-fix.txt
      else
        REPAIR="needs you: Claude tried, but some checks still fail"; printf %s "$FAILSET" > $STATE/claude-could-not-fix.txt
      fi
    fi
  fi
  grep FAIL "$RUN/health-final.txt" || echo "all passed after repair"
fi

# ---------------------------------------------------------------- 5. result for the hub
python3 - "$RUN" "$HEALTH" "$REPAIR" "${UPDATED[*]:-}" "$(printf '%s\n' "${NOTES[@]:-}")" <<'PY'
import json, os, sys, datetime
run, health, repair, updated, notes = sys.argv[1:6]
final = os.path.join(run, "health-final.json") if os.path.exists(os.path.join(run, "health-final.json")) else os.path.join(run, "health-after.json")
h = json.load(open(final))
summary = ""
p = os.path.join(run, "repair.txt")
if os.path.exists(p):
    summary = open(p).read().strip()[-1500:]
out = {"when": datetime.datetime.now().astimezone().isoformat(timespec="minutes"), "passed": health == "0",
       "updated": [u for u in updated.split() if u], "failed": h["failed"], "checks": h["count"],
       "repair": repair, "repair_summary": summary, "notes": [n for n in notes.split("\n") if n.strip()],
       "log": os.path.join(run, "update.log")}
tmp = "/home/sagan/hub/data/.maintenance.json"
json.dump(out, open(tmp, "w"), indent=2)
os.chmod(tmp, 0o644)
os.replace(tmp, "/home/sagan/hub/data/maintenance.json")
PY
ls -1 "$LOGDIR" | sort | head -n -8 | while read old; do rm -r -- "$LOGDIR/$old"; done
echo "== done $(date -Is): health $([ $HEALTH -eq 0 ] && echo passed || echo FAILED), repair: $REPAIR"
