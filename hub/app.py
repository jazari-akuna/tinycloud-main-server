"""hub.raph.io: links to everything on the server, plus its statistics.

Stats come from /data/stats.json (written by collect.py on the host).
Links and service names you edit are kept in /data/links.json.
Every request is checked with Authelia using the visitor's own session cookie.
"""
import base64
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
import urllib.error
import urllib.parse
import urllib.request
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
ICON_DIR = os.path.join(DATA, "icons")
MAX_ICON = 256 * 1024
LINK_ICON_RE = re.compile(r"^/link-icons/[0-9a-f]{8}\.(png|jpg|webp|svg)$")
DASHBOARD_ICONS = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/{fmt}/{name}.{fmt}"
ICON_TYPES = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp", "svg": "image/svg+xml"}

APPS_FILE = os.environ.get("APPS_FILE", "/app/apps.yml")
ICON_RE = re.compile(r"^/icons/[a-z0-9-]+\.svg$")


def apps():
    """The shared app list (sso/apps.yml): names, logos, and who may open each app."""
    import yaml
    try:
        with open(APPS_FILE) as f:
            return yaml.safe_load(f)["apps"]
    except Exception:
        return []


def can_open(app, groups):
    if "admins" in groups or not app or app.get("access") == "open":
        return True
    return app.get("access") == "app" and ("app-" + app["key"]) in groups


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
    ok = r.status == 200 and user
    who = (user, [g.strip() for g in groups.split(",") if g.strip()]) if ok else None
    if who:
        CACHE[key] = (who, time.time() + 20)
    return who


def api_user(auth_header):
    """Checks an 'Authorization: Bearer <token>' header against /data/api-tokens.json (hashes only)."""
    if not (auth_header or "").startswith("Bearer "):
        return None
    digest = hashlib.sha256(auth_header[7:].strip().encode()).hexdigest()
    for t in read_json("api-tokens.json", {"tokens": []}).get("tokens", []):
        if hmac.compare_digest(t.get("sha256", ""), digest):
            return "token:" + t.get("label", t.get("id", "?"))
    return None


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


def sniff(b):
    """Returns the image type from the file's first bytes, or None."""
    if b.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if b[:3] == b"\xff\xd8\xff":
        return "jpg"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "webp"
    head = b[:1024].lstrip().lower()
    if head.startswith((b"<svg", b"<?xml", b"<!--")) and b"<svg" in b[:4096].lower():
        if re.search(rb"<script|\son[a-z]+\s*=|javascript:", b, re.I):
            raise ValueError("That SVG contains scripts. Use a plain SVG or a PNG.")
        return "svg"
    return None


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "hub (logo fetch)"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.read(MAX_ICON + 1)


def load_icon(spec):
    """A logo from a data: URI (uploaded file), an image address, or a dashboard-icons name. Returns (bytes, ext)."""
    spec = str(spec).strip()
    if spec.startswith("data:"):
        head, _, payload = spec.partition(",")
        if ";base64" not in head:
            raise ValueError("Unreadable image.")
        b = base64.b64decode(payload)
    elif URL_RE.match(spec):
        try:
            b = fetch(spec)
        except Exception:
            raise ValueError("Couldn't download the logo from that address.")
    elif re.fullmatch(r"[a-z0-9][a-z0-9-]{0,60}", spec):
        b = None
        for fmt in ("svg", "png"):
            try:
                b = fetch(DASHBOARD_ICONS.format(fmt=fmt, name=spec))
                break
            except Exception:
                continue
        if b is None:
            raise ValueError("No logo called '%s' in the dashboard-icons set." % spec)
    else:
        raise ValueError("Give an image file, an image address, or a logo name such as home-assistant.")
    if len(b) > MAX_ICON:
        raise ValueError("The logo is larger than 256 KB.")
    ext = sniff(b)
    if not ext:
        raise ValueError("The logo must be a PNG, JPEG, WebP or SVG image.")
    return b, ext


def remove_icon(link_id):
    for ext in ICON_TYPES:
        try:
            os.remove(os.path.join(ICON_DIR, "%s.%s" % (link_id, ext)))
        except FileNotFoundError:
            pass


def store_icon(link, icon):
    remove_icon(link["id"])
    if icon is None:
        link.pop("icon", None)
        return
    b, ext = icon
    os.makedirs(ICON_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=ICON_DIR, prefix=".icon.")
    with os.fdopen(fd, "wb") as f:
        f.write(b)
    os.chmod(tmp, 0o644)
    os.replace(tmp, os.path.join(ICON_DIR, "%s.%s" % (link["id"], ext)))
    link["icon"] = "%s.%s?v=%d" % (link["id"], ext, int(time.time()))


def services_view(stats, prefs, groups=("admins",)):
    by_domain = {d: a for a in apps() for d in a.get("domains", [])}
    out = []
    for h in stats.get("services") or []:
        if not isinstance(h, dict) or not h.get("domains"):
            continue
        app = next((by_domain[d] for d in h["domains"] if d in by_domain), None)
        if not can_open(app, groups):
            continue
        main = app["domains"][0] if app else next((d for d in h["domains"] if d.endswith(".raph.io")), h["domains"][0])
        p = prefs.get(main, {})
        name = (app or {}).get("name") or main.split(".")[0].capitalize()
        out.append({
            "key": main, "url": "https://" + main + "/", "domain": main,
            "aliases": [d for d in h["domains"] if d != main],
            "name": p.get("name") or name, "description": p.get("description", (app or {}).get("description", "")),
            # Sites that are switched off or not answering stay out of the way until you unhide them.
            "hidden": p.get("hidden", bool((app or {}).get("hidden")) or not h.get("up") or not h.get("enabled", True)),
            "icon": "/icons/%s.svg" % app["icon"] if app and app.get("icon") else None,
            "up": h.get("up"), "code": h.get("code"), "ms": h.get("ms"), "enabled": h.get("enabled", True),
        })
    out.sort(key=lambda s: s["name"].lower())
    return out


def change(parts, body):
    """Applies one change to links.json. parts is the path after /api (or /api/v1). Returns (status, json)."""
    icon, icon_given = None, parts[:1] == ["links"] and "icon" in body
    if icon_given and str(body.get("icon") or "").strip().lower() not in ("", "none"):
        try:
            icon = load_icon(body["icon"])  # downloads happen outside the lock
        except ValueError as e:
            return 400, {"error": str(e)}
    with LOCK:
        data = read_json("links.json", {"links": [], "services": {}})
        data.setdefault("links", [])
        data.setdefault("services", {})
        result = {"ok": True}
        if parts == ["links"]:
            url = clean(body.get("url"), 500)
            if not URL_RE.match(url):
                return 400, {"error": "The address must start with http:// or https://"}
            link = {"id": secrets.token_hex(4), "name": clean(body.get("name"), 60) or urllib.parse.urlparse(url).netloc,
                    "url": url, "description": clean(body.get("description"), 120)}
            data["links"].append(link)
            if icon_given:
                store_icon(link, icon)
            result["link"] = link
        elif len(parts) == 2 and parts[0] == "links":
            link = next((l for l in data["links"] if l["id"] == parts[1]), None)
            if not link:
                return 404, {"error": "No link with that id."}
            if body.get("delete"):
                data["links"].remove(link)
                remove_icon(link["id"])
            else:
                url = clean(body.get("url", link["url"]), 500)
                if not URL_RE.match(url):
                    return 400, {"error": "The address must start with http:// or https://"}
                link.update(name=clean(body.get("name", link["name"]), 60) or link["name"], url=url,
                            description=clean(body.get("description", link.get("description", "")), 120))
                if icon_given:
                    store_icon(link, icon)
                result["link"] = link
        elif len(parts) == 2 and parts[0] == "services":
            key = clean(parts[1], 100)
            if body.get("reset"):
                data["services"].pop(key, None)
            else:
                cur = data["services"].get(key, {})
                data["services"][key] = {"name": clean(body.get("name", cur.get("name", "")), 60),
                                         "description": clean(body.get("description", cur.get("description", "")), 120),
                                         "hidden": bool(body.get("hidden", cur.get("hidden", False)))}
        else:
            return 404, {"error": "Not found"}
        save_links(data)
        return 200, result


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
        if path.startswith("/api/v1/"):
            if not api_user(self.headers.get("Authorization")):
                return self.send(401, json.dumps({"error": "Missing or unknown API token."}))
            stats = read_json("stats.json", {})
            links = read_json("links.json", {"links": [], "services": {}})
            if path == "/api/v1/links":
                return self.send(200, json.dumps({"links": links.get("links", [])}))
            if path == "/api/v1/services":
                return self.send(200, json.dumps({"services": services_view(stats, links.get("services", {}))}))
            if path == "/api/v1/stats":
                return self.send(200, json.dumps({k: v for k, v in stats.items() if not k.startswith("_")}))
            return self.send(404, json.dumps({"error": "Not found"}))
        me = self.who()
        if not me:
            return self.send(302, "", extra=[("Location", PORTAL + "/?rd=" + urllib.parse.quote(PUBLIC_URL + self.path, safe=""))])
        if path == "/":
            html = open(os.path.join(STATIC, "index.html")).read()
            html = html.replace("{{CSRF}}", csrf_token(self.headers.get("Cookie"))).replace("{{USER}}", me[0])
            return self.send(200, html, "text/html; charset=utf-8")
        if LINK_ICON_RE.match(path):
            f = os.path.join(ICON_DIR, path.rsplit("/", 1)[1])
            if not os.path.exists(f):
                return self.send(404, "", "text/plain")
            # sandboxed so an SVG opened on its own can't run anything
            self.send_response(200)
            body = open(f, "rb").read()
            self.send_header("Content-Type", ICON_TYPES[f.rsplit(".", 1)[1]])
            self.send_header("Cache-Control", "max-age=604800")
            self.send_header("Content-Security-Policy", "default-src 'none'; img-src data:; style-src 'unsafe-inline'; sandbox")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        if ICON_RE.match(path) and os.path.exists(os.path.join(STATIC, path[1:])):
            return self.send(200, open(os.path.join(STATIC, path[1:]), "rb").read(), "image/svg+xml",
                             extra=[("Cache-Control", "max-age=86400")])
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
            admin = "admins" in me[1]
            return self.send(200, json.dumps({
                "user": me[0], "admin": admin, "generated": stats.get("generated"),
                # server figures are for administrators; everyone sees the apps they can open and the shared links
                "host": stats.get("host") if admin else None, "containers": stats.get("containers") if admin else None,
                "apps": {k: stats.get(k) for k in ("nextcloud", "jellyfin", "n8n", "yourls", "authelia", "proxy")} if admin else {},
                "updates": (stats.get("updates") or {}).get("count") if admin else None,
                "services": services_view(stats, links.get("services", {}), me[1]),
                "links": links.get("links", []),
            }))
        self.send(404, json.dumps({"error": "Not found"}))

    def do_POST(self):
        path = self.path.split("?")[0]

        if path.startswith("/api/v1/"):
            if not api_user(self.headers.get("Authorization")):
                return self.send(401, json.dumps({"error": "Missing or unknown API token."}))
            n = min(int(self.headers.get("Content-Length") or 0), 400000)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                return self.send(400, json.dumps({"error": "Body must be JSON."}))
            code, result = change(path.strip("/").split("/")[2:], body)
            return self.send(code, json.dumps(result))
        me = self.who()
        if not me:
            return self.send(401, json.dumps({"error": "Please sign in again."}))
        if "admins" not in me[1]:
            return self.send(403, json.dumps({"error": "Only administrators can change the hub."}))
        origin = self.headers.get("Origin") or ""
        if urllib.parse.urlparse(origin).netloc != urllib.parse.urlparse(PUBLIC_URL).netloc:
            return self.send(403, json.dumps({"error": "That request did not come from this page."}))
        if not hmac.compare_digest(self.headers.get("X-CSRF", ""), csrf_token(self.headers.get("Cookie"))):
            return self.send(403, json.dumps({"error": "This page has expired. Reload it and try again."}))
        n = min(int(self.headers.get("Content-Length") or 0), 400000)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self.send(400, json.dumps({"error": "Unreadable request."}))
        parts = self.path.split("?")[0].strip("/").split("/")
        code, result = change(parts[1:], body)
        self.send(code, json.dumps(result))


if __name__ == "__main__":
    os.makedirs(DATA, exist_ok=True)
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
