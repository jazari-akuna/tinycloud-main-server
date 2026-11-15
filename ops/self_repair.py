#!/usr/bin/env python3
"""Simple, safe fixes for failed health checks, tried before any agent is called.

Usage: self_repair.py HEALTH_JSON   (the output of health.py --json)
Prints each action it took. Never deletes anything.
"""
import json
import subprocess
import sys

NEXTCLOUD = "nextcloud-nextcloud-1"


def run(cmd, timeout=300):
    print("+ " + (cmd if isinstance(cmd, str) else " ".join(cmd)))
    try:
        r = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=timeout)
        out = (r.stdout + r.stderr).strip()
        if out:
            print("  " + out.splitlines()[-1][:200])
        return r.returncode == 0
    except Exception as e:
        print("  failed: %s" % e)
        return False


def project_up(container):
    """Starts a container through its compose project when it has one, so it comes back exactly as defined."""
    r = subprocess.run(["docker", "inspect", "-f",
                        '{{index .Config.Labels "com.docker.compose.project"}}|{{index .Config.Labels "com.docker.compose.project.config_files"}}',
                        container], capture_output=True, text=True)
    project, _, files = r.stdout.strip().partition("|")
    files = files.split(",")[0].replace("/data/compose/", "/var/lib/docker/volumes/portainer_data/_data/compose/", 1)
    if project and files:
        import os
        env = next((e for e in (os.path.join(os.path.dirname(files), "..", "stack.env"), os.path.join(os.path.dirname(files), "stack.env"))
                    if os.path.exists(e)), None)
        cmd = ["docker", "compose", "-p", project, "-f", files] + (["--env-file", env] if env else []) + ["up", "-d"]
        return run(cmd)
    return run(["docker", "restart", container])


def main(path):
    failed = [f["check"] for f in json.load(open(path))["failed"]]
    if not failed:
        print("nothing to repair")
        return
    done = set()
    for check in failed:
        if check.startswith("container "):
            project_up(check.split(" ", 1)[1])
        elif check == "site c.raph.io" and "nextcloud" not in done:
            run(["docker", "exec", "-u", "www-data", NEXTCLOUD, "php", "occ", "upgrade", "-n"])
            run(["docker", "exec", "-u", "www-data", NEXTCLOUD, "php", "occ", "maintenance:mode", "--off"])
            done.add("nextcloud")
        elif check.startswith("site stream.") and "jellyfin" not in done:
            run(["docker", "restart", "jellyfin", "jellyfin-sso"])
            done.add("jellyfin")
        elif check.startswith("site auth.") and "authelia" not in done:
            run(["docker", "restart", "authelia"])
            done.add("authelia")
        elif check == "n8n answers":
            run(["docker", "restart", "n8n-linkedin-notion-n8n-1"])
        elif check == "fail2ban running":
            run(["systemctl", "restart", "fail2ban"])
        elif check == "no failed system services":
            out = subprocess.run(["systemctl", "--failed", "--no-legend", "--plain"], capture_output=True, text=True).stdout
            for unit in [l.split()[0] for l in out.splitlines() if l.strip()]:
                run(["systemctl", "restart", unit])
            run(["systemctl", "reset-failed"])
    if any(c.startswith("site ") for c in failed) and "proxy" not in done:
        # the proxy may hold stale addresses for containers that were recreated
        run(["docker", "exec", "nginx", "nginx", "-s", "reload"])


if __name__ == "__main__":
    main(sys.argv[1])
