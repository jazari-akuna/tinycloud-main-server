"""hub.raph.io: links to everything on the server, plus its statistics.

Stats come from /data/stats.json (written by collect.py on the host).
Links and service names you edit are kept in /data/links.json.
Every request is checked with Authelia using the visitor's own session cookie.
"""
import hashlib
import hmac
import http.client
import json
import os
import re
import secrets
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DATA = os.environ.get("HUB_DATA", "/data")
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
AUTHELIA = urllib.parse.urlparse(os.environ.get("AUTHELIA_URL", "http://authelia:9091"))
PUBLIC_URL = os.environ.get("PUBLIC_URL", "https://hub.raph.io").rstrip("/")
PORTAL = os.environ.get("PORTAL_URL", "https://auth.raph.io").rstrip("/")
SECRET = secrets.token_bytes(32)
LOCK = threading.Lock()
COOKIE_RE = re.compile(r"authelia_session=([^\s;,\"]+)")
URL_RE = re.compile(r"^https?://[^\s<>\"']+$")
CACHE = {}

# Friendly names for the proxy's sites; anything can be renamed from the page.
DEFAULTS = {
    "c.raph.io": ("Nextcloud", "Files, calendar, contacts and office documents"),
    "p.raph.io": ("Portainer", "Containers and stacks"),
    "rp.raph.io": ("Proxy Manager", "Web addresses and certificates"),
    "n8n-alex.raph.io": ("n8n", "Automations"),
    "s.raph.io": ("Short links", "YOURLS link shortener"),
    "stream.raph.io": ("Jellyfin", "Films, series and music"),
    "makercam.raph.io": ("Camera", "Live camera view"),
    "sso.raph.io": ("People", "Who can sign in"),
    "auth.raph.io": ("Sign-in", "Single sign-on portal"),
    "office.raph.io": ("Office server", "Collabora, used by Nextcloud"),
    "hub.raph.io": ("Hub", "This page"),
    "git.raph.io": ("GitLab", "Code hosting"),
    "i.raph.io": ("InvenTree", "Inventory"),
    "rth.raph.io": ("Camera tunnel", "Rathole control port"),
    "mscamftp.raph.io": ("Camera FTP", "Switched off"),
}
HIDDEN_BY_DEFAULT = {"hub.raph.io", "office.raph.io", "rth.raph.io", "mscamftp.raph.io", "auth.raph.io"}


def check_session(cookie_header, path, method, ip):
    """Ask Authelia whether this visitor is signed in and an admin. Cached for 20 s per session."""
    m = COOKIE_RE.search(cookie_header or "")
    if not m:
        return None
    key = hashlib.sha256(m.group(1).encode()).hexdigest()
    hit = CACHE.get(key)
    if hit and hit[1] > time.time():
        return hit[0]
    try:
        c = http.client.HTTPConnection(AUTHELIA.hostname, AUTHELIA.port or 80, timeout=5)
        c.request("GET", "/api/authz/auth-request", headers={
            "X-Original-URL": PUBLIC_URL + path, "X-Original-Method": method,
            "X-Forwarded-For": ip, "Cookie": "authelia_session=" + m.group(1)})
        r = c.getresponse()
        r.read()
        user, groups = r.getheader("Remote-User"), r.getheader("Remote-Groups") or ""
        c.close()
    except Exception:
        return None
    ok = r.status == 200 and user and "admins" in [g.strip() for g in groups.split(",")]
    who = user if ok else None
    if who:
        CACHE[key] = (who, time.time() + 20)
    return who


def csrf_token(cookie_header):
    m = COOKIE_RE.search(cookie_header or "")
    return hmac.new(SECRET, (m.group(1) if m else "").encode(), hashlib.sha256).hexdigest()


def read_json(name, default):
    try:
        with open(os.path.join(DATA, name)) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_links(data):
    fd, tmp = tempfile.mkstemp(dir=DATA, prefix=".links.")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.chmod(tmp, 0o644)
    os.replace(tmp, os.path.join(DATA, "links.json"))


def clean(s, n):
    return str(s or "").strip()[:n]


def services_view(stats, prefs):
    out = []
    for h in stats.get("services") or []:
        if not isinstance(h, dict) or not h.get("domains"):
            continue
        main = next((d for d in h["domains"] if d.endswith(".raph.io")), h["domains"][0])
        name, desc = DEFAULTS.get(main, (main.split(".")[0].capitalize(), ""))
        p = prefs.get(main, {})
        out.append({
            "key": main, "url": "https://" + main + "/", "domain": main,
            "aliases": [d for d in h["domains"] if d != main],
            "name": p.get("name") or name, "description": p.get("description", desc),
            "hidden": p.get("hidden", main in HIDDEN_BY_DEFAULT),
            "up": h.get("up"), "code": h.get("code"), "ms": h.get("ms"), "enabled": h.get("enabled", True),
        })
    out.sort(key=lambda s: s["name"].lower())
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "hub"

    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8", extra=()):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; style-src 'self' https://fonts.googleapis.com; "
                         "font-src https://fonts.gstatic.com; img-src 'self' data:; frame-ancestors 'none'; form-action 'self'")
        for k, v in extra:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def who(self):
        ip = (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()
        return check_session(self.headers.get("Cookie"), self.path, self.command, ip)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/healthz":
            return self.send(200, "ok", "text/plain")
        me = self.who()
        if not me:
            return self.send(302, "", extra=[("Location", PORTAL + "/?rd=" + urllib.parse.quote(PUBLIC_URL + self.path, safe=""))])
        if path == "/":
            html = open(os.path.join(STATIC, "index.html")).read()
            html = html.replace("{{CSRF}}", csrf_token(self.headers.get("Cookie"))).replace("{{USER}}", me)
            return self.send(200, html, "text/html; charset=utf-8")
        if path in ("/app.js", "/style.css"):
            ctype = "text/javascript; charset=utf-8" if path.endswith(".js") else "text/css; charset=utf-8"
            return self.send(200, open(os.path.join(STATIC, path[1:]), "rb").read(), ctype)
        if path == "/api/data":
            try:  # tells the collector someone is looking, so it refreshes more often
                open(os.path.join(DATA, ".viewed"), "a").close()
                os.utime(os.path.join(DATA, ".viewed"))
            except OSError:
                pass
            stats = read_json("stats.json", {})
            links = read_json("links.json", {"links": [], "services": {}})
            return self.send(200, json.dumps({
                "user": me, "generated": stats.get("generated"),
                "host": stats.get("host"), "containers": stats.get("containers"),
                "apps": {k: stats.get(k) for k in ("nextcloud", "jellyfin", "n8n", "yourls", "authelia", "proxy")},
                "updates": (stats.get("updates") or {}).get("count"),
                "services": services_view(stats, links.get("services", {})),
                "links": links.get("links", []),
            }))
        self.send(404, json.dumps({"error": "Not found"}))

    def do_POST(self):
        me = self.who()
        if not me:
            return self.send(401, json.dumps({"error": "Please sign in again."}))
        origin = self.headers.get("Origin") or ""
        if urllib.parse.urlparse(origin).netloc != urllib.parse.urlparse(PUBLIC_URL).netloc:
            return self.send(403, json.dumps({"error": "That request did not come from this page."}))
        if not hmac.compare_digest(self.headers.get("X-CSRF", ""), csrf_token(self.headers.get("Cookie"))):
            return self.send(403, json.dumps({"error": "This page has expired. Reload it and try again."}))
        n = min(int(self.headers.get("Content-Length") or 0), 10000)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self.send(400, json.dumps({"error": "Unreadable request."}))
        parts = self.path.split("?")[0].strip("/").split("/")
        with LOCK:
            data = read_json("links.json", {"links": [], "services": {}})
            data.setdefault("links", [])
            data.setdefault("services", {})
            if parts == ["api", "links"]:
                url = clean(body.get("url"), 500)
                if not URL_RE.match(url):
                    return self.send(400, json.dumps({"error": "The address must start with http:// or https://"}))
                data["links"].append({"id": secrets.token_hex(4), "name": clean(body.get("name"), 60) or urllib.parse.urlparse(url).netloc,
                                      "url": url, "description": clean(body.get("description"), 120)})
            elif len(parts) == 3 and parts[:2] == ["api", "links"]:
                link = next((l for l in data["links"] if l["id"] == parts[2]), None)
                if not link:
                    return self.send(404, json.dumps({"error": "That link is gone already."}))
                if body.get("delete"):
                    data["links"].remove(link)
                else:
                    url = clean(body.get("url"), 500)
                    if not URL_RE.match(url):
                        return self.send(400, json.dumps({"error": "The address must start with http:// or https://"}))
                    link.update(name=clean(body.get("name"), 60) or link["name"], url=url, description=clean(body.get("description"), 120))
            elif len(parts) == 3 and parts[:2] == ["api", "services"]:
                key = clean(parts[2], 100)
                if body.get("reset"):
                    data["services"].pop(key, None)
                else:
                    data["services"][key] = {"name": clean(body.get("name"), 60), "description": clean(body.get("description"), 120),
                                             "hidden": bool(body.get("hidden"))}
            else:
                return self.send(404, json.dumps({"error": "Not found"}))
            save_links(data)
        self.send(200, json.dumps({"ok": True}))


if __name__ == "__main__":
    os.makedirs(DATA, exist_ok=True)
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
