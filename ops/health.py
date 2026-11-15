#!/usr/bin/env python3
"""Health check for the server: containers, every site in sso/apps.yml, storage, firewall.

Prints one line per check and a JSON summary to --json FILE. Exit code 0 = all passed.
Runs as root (weekly-update.sh), but only reads.
"""
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def sh(cmd, timeout=60):
    try:
        r = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as e:
        return 1, str(e)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def get(url, timeout=15):
    """Returns (status, location, body) without following redirects."""
    req = urllib.request.Request(url, headers={"User-Agent": "server-health-check"})
    try:
        r = urllib.request.build_opener(NoRedirect).open(req, timeout=timeout)
        return r.status, r.headers.get("Location", ""), r.read(20000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Location", ""), e.read(20000).decode("utf-8", "replace")
    except Exception as e:
        return 0, "", str(e)


results = []


def check(name, ok, detail=""):
    results.append({"check": name, "ok": bool(ok), "detail": detail})


def containers():
    code, out = sh(["docker", "ps", "-a", "--format", "{{json .}}"])
    for line in out.splitlines():
        c = json.loads(line)
        _, pol = sh(["docker", "inspect", "-f", "{{.HostConfig.RestartPolicy.Name}}", c["Names"]])
        if pol not in ("always", "unless-stopped"):
            continue  # not meant to run all the time
        healthy = c["State"] == "running" and "unhealthy" not in c["Status"] and "Restarting" not in c["Status"]
        check("container " + c["Names"], healthy, c["Status"])


def sites():
    import yaml
    apps = yaml.safe_load(open(os.path.join(REPO, "sso", "apps.yml")))["apps"]
    for app in apps:
        d = app["domains"][0]
        if app["key"] in ("camera-tunnel", "camera-ftp"):
            continue  # not websites
        if app["key"] == "nextcloud":
            code, _, body = get(f"https://{d}/status.php")
            try:
                s = json.loads(body)
                check("site " + d, s.get("installed") and not s.get("maintenance") and not s.get("needsDbUpgrade"),
                      f"version {s.get('versionstring')}, maintenance {s.get('maintenance')}")
            except Exception:
                check("site " + d, False, f"status.php answered {code}")
            continue
        if app["key"] == "jellyfin":
            code, _, body = get(f"https://{d}/health")
            check("site " + d, code == 200 and "Healthy" in body, f"{code} {body[:40]}")
            continue
        if app["key"] == "signin":
            code, _, body = get(f"https://{d}/api/health")
            check("site " + d, code == 200, f"{code}")
            continue
        if app["key"] == "office":
            code, _, _ = get(f"https://{d}/hosting/discovery")
            check("site " + d, code == 200, f"{code}")
            continue
        path = "/admin/" if app["key"] == "shortlinks" else "/"
        code, loc, _ = get(f"https://{d}{path}")
        if app.get("access") in ("app", "admins"):
            # Without a session the gate must send people to the sign-in page (and nothing else).
            check("site " + d, code == 302 and loc.startswith("https://auth."), f"{code} {loc[:60]}")
        else:
            check("site " + d, 0 < code < 500, f"{code}")


def inside():
    code, out = sh(["docker", "exec", "n8n-linkedin-notion-n8n-1", "wget", "-qO-", "http://localhost:5678/healthz"])
    check("n8n answers", code == 0 and "ok" in out, out[:60])
    code, out = sh(["docker", "exec", "nginx", "nginx", "-t"])
    check("proxy config valid", code == 0, out.splitlines()[-1] if out else "")
    code, out = sh("cd %s/sso/authelia && docker compose run --rm --no-deps --entrypoint authelia authelia "
                   "validate-config --config /config/configuration.yml" % REPO, timeout=120)
    check("sign-in config valid", "successfully" in out, out.splitlines()[-1] if out else "")


def system():
    code, out = sh(["zpool", "list", "-H", "-o", "health,cap", "ssdpool"])
    parts = out.split()
    check("storage pool healthy", code == 0 and parts[:1] == ["ONLINE"], out)
    if len(parts) == 2:
        check("storage pool below 90% full", int(parts[1].rstrip("%")) < 90, parts[1])
    du = shutil.disk_usage("/")
    check("system disk below 85% full", du.used / du.total < 0.85, "%d%%" % (100 * du.used / du.total))
    code, out = sh(["ufw", "status"])
    check("firewall on", "Status: active" in out)
    code, out = sh(["systemctl", "is-active", "fail2ban"])
    check("fail2ban running", out == "active", out)
    code, out = sh(["systemctl", "--failed", "--no-legend", "--plain"])
    check("no failed system services", not out.strip(), out.strip()[:200])


def main():
    for part in (containers, sites, inside, system):
        try:
            part()
        except Exception as e:
            check(part.__name__, False, "check itself failed: %s" % e)
    failed = [r for r in results if not r["ok"]]
    for r in results:
        print(("ok   " if r["ok"] else "FAIL ") + r["check"] + (f"  ({r['detail']})" if r["detail"] and not r["ok"] else ""))
    if "--json" in sys.argv:
        with open(sys.argv[sys.argv.index("--json") + 1], "w") as f:
            json.dump({"passed": not failed, "failed": failed, "count": len(results)}, f, indent=2)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
