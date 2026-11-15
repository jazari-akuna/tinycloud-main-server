"""Small admin page for the people who can sign in through Authelia."""
import hashlib, hmac, html, http.client, os, re, secrets, shutil, tempfile, threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import yaml
from argon2 import PasswordHasher

USERS_FILE = os.environ.get("USERS_FILE", "/data/users.yml")
AUTHELIA = urllib.parse.urlparse(os.environ.get("AUTHELIA_URL", "http://authelia:9091"))
PUBLIC_URL = os.environ.get("PUBLIC_URL", "https://sso.raph.io").rstrip("/")
PORTAL = os.environ.get("PORTAL_URL", "https://auth.raph.io").rstrip("/")
SECRET = secrets.token_bytes(32)
LOCK = threading.Lock()
HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16)
WORDS = open(os.path.join(os.path.dirname(__file__), "words.txt")).read().split()
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
COOKIE_RE = re.compile(r"authelia_session=([^\s;,\"]+)")


def check_session(cookie_header, path, method, ip):
    """Ask Authelia itself whether this visitor is signed in. Returns (username, groups) or None."""
    m = COOKIE_RE.search(cookie_header or "")
    if not m:
        return None
    try:
        c = http.client.HTTPConnection(AUTHELIA.hostname, AUTHELIA.port or 80, timeout=5)
        c.request("GET", "/api/authz/auth-request", headers={
            "X-Original-URL": PUBLIC_URL + path, "X-Original-Method": method,
            "X-Forwarded-For": ip, "Cookie": "authelia_session=" + m.group(1)})
        r = c.getresponse(); r.read()
        user, groups = r.getheader("Remote-User"), r.getheader("Remote-Groups") or ""
        c.close()
    except Exception:
        return None
    if r.status != 200 or not user:
        return None
    groups = [g.strip() for g in groups.split(",") if g.strip()]
    return (user, groups) if "admins" in groups else None


def csrf_token(cookie_header):
    m = COOKIE_RE.search(cookie_header or "")
    return hmac.new(SECRET, (m.group(1) if m else "").encode(), hashlib.sha256).hexdigest()


def load():
    with open(USERS_FILE) as f:
        return yaml.safe_load(f) or {"users": {}}


def save(data):
    d = os.path.dirname(USERS_FILE)
    st = os.stat(USERS_FILE)
    shutil.copy2(USERS_FILE, USERS_FILE + ".bak")
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".users-")
    with os.fdopen(fd, "w") as f:
        yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        f.flush(); os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    try:
        os.chown(tmp, st.st_uid, st.st_gid)
    except PermissionError:
        pass
    os.rename(tmp, USERS_FILE)


def make_password():
    r = secrets.SystemRandom()
    return "#".join(r.choice(WORDS) for _ in range(3)) + "#%02d" % r.randrange(100)


def parse_groups(s):
    return sorted({g.strip() for g in re.split(r"[,\s]+", s or "") if g.strip()})


E_ = html.escape
CSS = """
:root{color-scheme:light dark;--bg:#f6f7f9;--card:#fff;--fg:#1c2330;--mut:#667085;--line:#dfe3ea;--acc:#2455d6;--bad:#b42318;--ok:#1a7f4b}
@media (prefers-color-scheme:dark){:root{--bg:#12151b;--card:#1b2029;--fg:#e8ebf1;--mut:#9aa3b2;--line:#2c3340;--acc:#7ea2ff;--bad:#ff8a80;--ok:#5ad19a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.45 system-ui,sans-serif}
main{max-width:760px;margin:0 auto;padding:16px}header{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
h1{font-size:1.4rem;margin:.6em 0}a{color:var(--acc)}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;margin:12px 0}
label{display:block;font-size:.85rem;color:var(--mut);margin:.7em 0 .2em}input[type=text],input[type=email],input[type=password]{width:100%;padding:9px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg);font:inherit}
button{font:inherit;padding:8px 14px;border-radius:6px;border:1px solid var(--line);background:var(--card);color:var(--fg);cursor:pointer;margin:6px 6px 0 0}
button.p{background:var(--acc);color:#fff;border-color:var(--acc)}button.d{color:var(--bad)}summary{cursor:pointer;font-weight:600}
.mut{color:var(--mut);font-size:.9rem}.tag{display:inline-block;font-size:.75rem;border:1px solid var(--line);border-radius:99px;padding:0 8px;margin-right:4px}
.off{color:var(--bad)}.msg{border-left:4px solid var(--ok);padding:8px 12px;background:var(--card);border-radius:6px}.msg.err{border-color:var(--bad)}
code.pw{font-size:1.2rem;user-select:all;word-break:break-all}hr{border:0;border-top:1px solid var(--line);margin:14px 0}
"""


def page(body):
    return ("<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>People who can sign in</title><style>%s</style><main>%s</main></html>" % (CSS, body)).encode()


def render(me, tok, notice=None):
    users = load().get("users") or {}
    h = ["<header><h1>People who can sign in</h1><span class=mut>Signed in as %s · <a href='%s/logout'>Sign out of SSO</a></span></header>" % (E_(me), PORTAL)]
    if notice:
        kind, text = notice
        h.append("<div class='msg %s'>%s</div>" % ("err" if kind == "err" else "", text))
    t = "<input type=hidden name=csrf value='%s'>" % tok

    def pwfields():
        return ("<label>New password (leave empty to keep it)</label><input type=password name=password autocomplete=new-password>"
                "<label><input type=checkbox name=generate> Make one up for me, and show it to me once</label>")
    h.append("<details class=card><summary>Add someone</summary><form method=post action=/add>%s"
             "<label>Username (lowercase letters, digits, . _ -)</label><input type=text name=username required pattern='[a-z0-9][a-z0-9._-]{0,31}'>"
             "<label>Name</label><input type=text name=displayname required>"
             "<label>Email</label><input type=email name=email required>"
             "<label>Groups (separated by commas)</label><input type=text name=groups placeholder='admins'>%s"
             "<button class=p>Add person</button></form></details>" % (t, pwfields()))
    for name, u in sorted(users.items()):
        u = u or {}
        on = not u.get("disabled")
        groups = u.get("groups") or []
        me_ = name == me
        tags = "".join("<span class=tag>%s</span>" % E_(g) for g in groups)
        h.append("<div class=card><b>%s</b> %s%s<div>%s</div><div class=mut>%s</div>" % (
            E_(name), "" if on else "<span class=off>(switched off)</span>", " <span class=mut>(you)</span>" if me_ else "",
            E_(str(u.get("displayname", ""))), E_(str(u.get("email", "")))))
        h.append("<div>%s</div><details><summary class=mut>Change</summary>" % (tags or "<span class=mut>no groups</span>"))
        h.append("<form method=post action=/edit>%s<input type=hidden name=username value='%s'>"
                 "<label>Name</label><input type=text name=displayname value='%s' required>"
                 "<label>Email</label><input type=email name=email value='%s' required>"
                 "<label>Groups (separated by commas)</label><input type=text name=groups value='%s'>%s"
                 "<button class=p>Save changes</button></form>" % (
                     t, E_(name), E_(str(u.get("displayname", ""))), E_(str(u.get("email", ""))), E_(", ".join(groups)), pwfields()))
        if not me_:
            h.append("<hr><form method=post action=/toggle style=display:inline>%s<input type=hidden name=username value='%s'><button>%s</button></form>"
                     "<form method=post action=/delete style=display:inline onsubmit=\"return confirm('Remove %s for good?')\">%s<input type=hidden name=username value='%s'><button class=d>Remove</button></form>" % (
                         t, E_(name), "Let them sign in again" if not on else "Switch off", E_(name), t, E_(name)))
        else:
            h.append("<p class=mut>You can't switch yourself off, remove yourself or leave the admins group.</p>")
        h.append("</details></div>")
    return page("".join(h))


class H(BaseHTTPRequestHandler):
    server_version = "sso-admin"

    def log_message(self, *a):
        pass

    def send(self, code, body, extra=()):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'")
        for k, v in extra:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def auth(self):
        ip = self.headers.get("X-Forwarded-For") or self.client_address[0]
        who = check_session(self.headers.get("Cookie"), self.path, self.command, ip)
        if not who:
            self.send(401, page("<h1>Please sign in first</h1><p>This page is only for the owner. <a href='%s'>Sign in</a>, then come back.</p>" % PORTAL))
        return who

    def do_GET(self):
        who = self.auth()
        if not who:
            return
        if self.path.split("?")[0] != "/":
            return self.send(404, page("<p>Not found. <a href=/>Back to the list</a></p>"))
        self.send(200, render(who[0], csrf_token(self.headers.get("Cookie"))))

    def do_POST(self):
        who = self.auth()
        if not who:
            return
        me = who[0]
        origin = self.headers.get("Origin") or self.headers.get("Referer") or ""
        if urllib.parse.urlparse(origin).netloc != urllib.parse.urlparse(PUBLIC_URL).netloc:
            return self.send(403, page("<p>That request did not come from this page. <a href=/>Back</a></p>"))
        n = min(int(self.headers.get("Content-Length") or 0), 20000)
        f = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(n).decode("utf-8", "replace")).items()}
        tok = csrf_token(self.headers.get("Cookie"))
        if not hmac.compare_digest(f.get("csrf", ""), tok):
            return self.send(403, page("<p>This page has expired. <a href=/>Reload it</a> and try again.</p>"))
        op = self.path.split("?")[0].strip("/")
        try:
            with LOCK:
                notice = self.act(op, f, me)
        except Exception as e:
            notice = ("err", "Something went wrong: %s" % E_(str(e)))
        self.send(200, render(me, tok, notice))

    def act(self, op, f, me):
        data = load()
        users = data.setdefault("users", {})
        name = f.get("username", "")
        if op == "add":
            if not NAME_RE.match(name):
                return ("err", "That username isn't allowed. Use lowercase letters, digits, dots, dashes or underscores.")
            if name in users:
                return ("err", "Someone with that username already exists.")
            u = {"disabled": False}
        elif op in ("edit", "toggle", "delete"):
            if name not in users:
                return ("err", "That person no longer exists.")
            u = users[name] or {}
            if name == me and op in ("toggle", "delete"):
                return ("err", "You can't %s yourself." % ("remove" if op == "delete" else "switch off"))
        else:
            return ("err", "Unknown action.")
        if op == "delete":
            del users[name]; save(data)
            return ("ok", "Removed %s." % E_(name))
        if op == "toggle":
            u["disabled"] = not u.get("disabled"); users[name] = u; save(data)
            return ("ok", "%s is %s." % (E_(name), "switched off" if u["disabled"] else "allowed to sign in again"))
        dn, em, groups = f.get("displayname", "").strip(), f.get("email", "").strip(), parse_groups(f.get("groups"))
        if not dn or not EMAIL_RE.match(em):
            return ("err", "Please give a name and a valid email address.")
        if name == me and "admins" not in groups:
            return ("err", "You can't take yourself out of the admins group.")
        pw, shown = f.get("password", ""), None
        if f.get("generate"):
            pw = shown = make_password()
        if op == "add" and not pw:
            return ("err", "Choose a password, or tick the box to have one made up.")
        if pw and len(pw) < 8:
            return ("err", "The password needs at least 8 characters.")
        u.update(displayname=dn, email=em, groups=groups)
        if pw:
            u["password"] = HASHER.hash(pw)
        users[name] = u
        save(data)
        msg = "%s %s." % ("Added" if op == "add" else "Saved changes for", E_(name))
        if shown:
            msg += "<p>New password: shown once, copy it now.<br><code class=pw>%s</code></p>" % E_(shown)
        return ("ok", msg)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), H).serve_forever()
