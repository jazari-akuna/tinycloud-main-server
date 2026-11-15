#!/usr/bin/env python3
"""Gathers server and app statistics for hub.raph.io into one JSON file.

Runs on the host as root (hub-collect.timer), so the hub's web container needs
no access to the host. Writes $HUB_DATA/stats.json. To stay light, each part
refreshes on its own schedule, and much less often while nobody has the hub
open (the hub touches $HUB_DATA/.viewed when a page asks for data).
Secrets are only read from the running containers, never written out.
"""
import concurrent.futures as cf
import datetime as dt
import json
import os
import secrets
import socket
import sqlite3
import subprocess
import time
import urllib.request

DATA = os.environ.get("HUB_DATA", "/home/sagan/hub/data")
NC = "nextcloud-nextcloud-1"
NC_TOKEN_FILE = os.path.join(DATA, ".nextcloud-token")
JELLYFIN_DB = "/var/lib/docker/volumes/jelly_config/_data/data/jellyfin.db"
N8N_DB = "/var/lib/docker/volumes/n8n-linkedin-notion_n8n_data/_data/database.sqlite"
AUTHELIA_DIR = "/home/sagan/authelia/data"


def sh(cmd, timeout=20):
    try:
        return subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=timeout).stdout.strip()
    except Exception:
        return ""


def sqlite(path, sql):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
    try:
        return c.execute(sql).fetchall()
    finally:
        c.close()


def safe(fn):
    try:
        return fn()
    except Exception as e:
        return {"error": str(e)[:200]}


def host():
    load = open("/proc/loadavg").read().split()[:3]
    mem = {}
    for line in open("/proc/meminfo"):
        k, v = line.split(":")
        mem[k] = int(v.split()[0]) * 1024
    st = os.statvfs("/")
    os_name = next((l.split("=", 1)[1].strip().strip('"') for l in open("/etc/os-release") if l.startswith("PRETTY_NAME=")), "")
    temp = None
    for zone in sorted(os.listdir("/sys/class/thermal")):
        p = f"/sys/class/thermal/{zone}/temp"
        if zone.startswith("thermal_zone") and os.path.exists(p):
            temp = max(temp or 0, int(open(p).read()) / 1000)
    rx = tx = 0
    for line in open("/proc/net/dev").readlines()[2:]:
        name, rest = line.split(":", 1)
        name = name.strip()
        if name == "lo" or name.startswith(("veth", "br-", "docker")):
            continue
        f = rest.split()
        rx += int(f[0]); tx += int(f[8])
    f2b = sh("fail2ban-client status sshd")
    banned = next((l.split(":")[1].strip() for l in f2b.splitlines() if "Currently banned" in l), "0")
    zfs = sh(["zpool", "list", "-Hp", "-o", "name,size,alloc,free,cap,health"]).split("\t")
    scrub = next((l.split("scan:", 1)[1].strip() for l in sh(["zpool", "status"]).splitlines() if "scan:" in l), "")
    zfs_used = sh(["zfs", "list", "-Hp", "-o", "used,avail", "ssdpool"]).split("\t")
    return {
        "hostname": socket.gethostname(),
        "os": os_name,
        "kernel": os.uname().release,
        "uptime_s": float(open("/proc/uptime").read().split()[0]),
        "load": [float(x) for x in load],
        "cpus": os.cpu_count(),
        "mem_total": mem["MemTotal"], "mem_available": mem["MemAvailable"],
        "swap_total": mem.get("SwapTotal", 0), "swap_free": mem.get("SwapFree", 0),
        "root_total": st.f_blocks * st.f_frsize, "root_free": st.f_bavail * st.f_frsize,
        "temp_c": temp,
        "net_rx": rx, "net_tx": tx,
        "reboot_required": os.path.exists("/run/reboot-required"),
        "ssh_banned": int(banned or 0),
        "zfs": {
            "name": zfs[0], "health": zfs[5] if len(zfs) > 5 else "?",
            "used": int(zfs_used[0]), "avail": int(zfs_used[1]), "scrub": scrub,
        } if len(zfs) > 5 and len(zfs_used) == 2 else None,
    }


def containers():
    out = []
    stats = {}
    for line in sh("docker stats --no-stream --format '{{json .}}'", timeout=30).splitlines():
        s = json.loads(line)
        stats[s["Name"]] = s
    for line in sh("docker ps -a --format '{{json .}}'").splitlines():
        c = json.loads(line)
        s = stats.get(c["Names"], {})
        out.append({
            "name": c["Names"], "image": c["Image"], "state": c["State"], "status": c["Status"],
            "cpu": s.get("CPUPerc", ""), "mem": (s.get("MemUsage", "").split(" / ")[0]),
        })
    return sorted(out, key=lambda c: (c["state"] != "running", c["name"]))


def npm_sql(sql):
    """Query the proxy manager's database with the password it uses itself (passed on stdin, never printed)."""
    pw = subprocess.run(["docker", "exec", "nginx", "printenv", "DB_MYSQL_PASSWORD"], capture_output=True, text=True, timeout=10).stdout.strip()
    return subprocess.run(
        ["docker", "exec", "-i", "maria_for_nginx", "sh", "-c",
         'export MYSQL_PWD="$(cat)"; mariadb -h127.0.0.1 -unpm npm -N -B -e "' + sql + '" 2>/dev/null'],
        input=pw, capture_output=True, text=True, timeout=20).stdout


def proxy_hosts():
    hosts = []
    for line in npm_sql("SELECT id, domain_names, enabled, forward_host, forward_port FROM proxy_host WHERE is_deleted=0").splitlines():
        hid, names, enabled, fh, fp = line.split("\t")
        hosts.append({"id": int(hid), "domains": json.loads(names), "enabled": enabled == "1", "target": f"{fh}:{fp}"})
    return hosts


def check(url):
    t = time.time()
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "hub-health"})

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    try:
        r = urllib.request.build_opener(NoRedirect).open(req, timeout=6)
        code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception:
        code = 0
    return {"code": code, "ms": int((time.time() - t) * 1000)}


def services():
    hosts = proxy_hosts()
    with cf.ThreadPoolExecutor(8) as ex:
        results = list(ex.map(lambda h: check("https://" + h["domains"][0] + "/"), hosts))
    for h, r in zip(hosts, results):
        h.update(r)
        h["up"] = 200 <= r["code"] < 500 and r["code"] not in (404,)
    return hosts


def nextcloud():
    if not os.path.exists(NC_TOKEN_FILE):
        tok = secrets.token_urlsafe(32)
        sh(["docker", "exec", "-u", "www-data", NC, "php", "occ", "config:app:set", "serverinfo", "token", "--value=" + tok])
        with open(os.open(NC_TOKEN_FILE, os.O_WRONLY | os.O_CREAT, 0o600), "w") as f:
            f.write(tok)
    tok = open(NC_TOKEN_FILE).read().strip()
    raw = sh(["docker", "exec", NC, "curl", "-s", "-H", "NC-Token: " + tok, "-H", "Host: c.raph.io",
              "http://localhost/ocs/v2.php/apps/serverinfo/api/v1/info?format=json&skipApps=true"])
    try:
        d = json.loads(raw)["ocs"]["data"]
        ns = d["nextcloud"]["storage"]
    except Exception:
        os.remove(NC_TOKEN_FILE)  # token not accepted yet or reset: set a fresh one next run
        raise RuntimeError("Nextcloud did not answer yet")
    act = d.get("activeUsers", {})
    return {
        "version": d["nextcloud"]["system"]["version"],
        "users": ns["num_users"], "files": ns["num_files"],
        "active_5min": act.get("last5minutes"), "active_24h": act.get("last24hours"),
        "shares": d["nextcloud"]["shares"]["num_shares"],
        "free": d["nextcloud"]["system"].get("freespace"),
    }


def jellyfin():
    rows = dict(sqlite(JELLYFIN_DB, "SELECT Type, COUNT(*) FROM BaseItems GROUP BY Type"))
    g = lambda t: rows.get("MediaBrowser.Controller.Entities." + t, 0)
    users = sqlite(JELLYFIN_DB, "SELECT COUNT(*) FROM Users")[0][0]
    return {"movies": g("Movies.Movie"), "series": g("TV.Series"), "episodes": g("TV.Episode"),
            "songs": g("Audio.Audio"), "artists": g("Audio.MusicArtist"), "users": users}


def n8n():
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    wf, active = sqlite(N8N_DB, "SELECT COUNT(*), SUM(active) FROM workflow_entity")[0]
    ex = dict(sqlite(N8N_DB, f"SELECT status, COUNT(*) FROM execution_entity WHERE startedAt >= '{since}' GROUP BY status"))
    return {"workflows": wf, "active": active or 0, "runs_24h": sum(ex.values()), "failed_24h": ex.get("error", 0) + ex.get("crashed", 0)}


def yourls():
    raw = sh(["docker", "exec", "yourls-mysql-1", "sh", "-c",
              'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot -N -B -e "SELECT COUNT(*), COALESCE(SUM(clicks),0) FROM yourls.yourls_url" 2>/dev/null'])
    links, clicks = raw.split("\t")
    return {"links": int(links), "clicks": int(clicks)}


def authelia():
    import yaml
    users = yaml.safe_load(open(os.path.join(AUTHELIA_DIR, "users.yml")))["users"]
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    rows = dict(sqlite(os.path.join(AUTHELIA_DIR, "db.sqlite3"),
                       f"SELECT successful, COUNT(*) FROM authentication_logs WHERE time >= '{since}' GROUP BY successful"))
    return {"users": len(users), "enabled": sum(1 for u in users.values() if not u.get("disabled")),
            "signins_24h": rows.get(1, 0), "failed_24h": rows.get(0, 0)}


def proxy_cert():
    return {"cert_expires": npm_sql("SELECT MIN(expires_on) FROM certificate WHERE is_deleted=0").strip()}


def updates():
    return {"count": int(sh("apt list --upgradable 2>/dev/null | grep -c upgradable") or 0)}


# How often each part refreshes, in seconds: (while someone has the hub open, while nobody does).
# The timer fires every minute, but most runs do almost nothing.
SCHEDULE = {
    "host": (60, 1800),
    "containers": (300, 3600),
    "services": (300, 3600),
    "nextcloud": (900, 3 * 3600),
    "jellyfin": (900, 3 * 3600),
    "n8n": (900, 3 * 3600),
    "yourls": (900, 3 * 3600),
    "authelia": (900, 3 * 3600),
    "proxy": (6 * 3600, 24 * 3600),
    "updates": (6 * 3600, 6 * 3600),
}
JOBS = {"host": host, "containers": containers, "services": services, "nextcloud": nextcloud, "jellyfin": jellyfin,
        "n8n": n8n, "yourls": yourls, "authelia": authelia, "proxy": proxy_cert, "updates": updates}


def main():
    os.makedirs(DATA, exist_ok=True)
    path = os.path.join(DATA, "stats.json")
    try:
        out = json.load(open(path))
    except Exception:
        out = {}
    stamps = out.get("_refreshed", {})
    try:
        watched = time.time() - os.path.getmtime(os.path.join(DATA, ".viewed")) < 600
    except OSError:
        watched = False
    now = time.time()
    due = [k for k, (busy, idle) in SCHEDULE.items() if now - stamps.get(k, 0) >= (busy if watched else idle) - 20]
    if not due:
        return
    with cf.ThreadPoolExecutor(min(4, len(due))) as ex:
        futs = {k: ex.submit(safe, JOBS[k]) for k in due}
        for k, f in futs.items():
            out[k] = f.result()
            stamps[k] = now
    out["_refreshed"] = stamps
    out["generated"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    tmp = os.path.join(DATA, ".stats.json.tmp")
    with open(tmp, "w") as f:
        json.dump(out, f)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


if __name__ == "__main__":
    main()
