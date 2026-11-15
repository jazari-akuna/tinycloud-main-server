# Weekly update

Every Sunday at 03:30 Swiss time (`weekly-update.timer`), `weekly-update.sh`:

1. runs `health.py` (containers, every site in `sso/apps.yml`, sign-in and proxy settings, storage, firewall),
2. backs up the databases, sign-in data and hub data to `/root/container-backups/weekly/<date>` (4 weeks kept),
3. updates every Docker app to its newest image (Authelia only if the new version accepts the current settings),
   Portainer, Nextcloud's apps, and Claude Code,
4. runs `health.py` again. If something fails:
   - `self_repair.py` tries simple fixes first (start or restart the app, finish a Nextcloud upgrade,
     switch maintenance mode off, reload the proxy, restart failed services), then the checks run again;
   - only if something still fails, Claude (Opus, medium effort, as user sagan) gets `REPAIR.md` and the
     failing checks. It isn't called when it's signed out, when the account is at 90% or more of a usage
     limit (`claude-usage.py`), or when it already failed on exactly the same checks the week before;
     those cases show on the hub as something for you to do.
   Whether Claude is signed in and has usage left is checked at the start of every run and shown on the hub.
5. writes the result to the hub (`/home/sagan/hub/data/maintenance.json`) and the full log to
   `/var/log/weekly-update/<date>/`.

Run it by hand: `sudo systemctl start weekly-update.service`, then `journalctl -u weekly-update -f`.
Checks only: `sudo python3 ops/health.py`.
