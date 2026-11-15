"""Signs a browser into Jellyfin with its Authelia session, no Jellyfin password.

Served at https://stream.raph.io/sso-login (behind the Authelia gate). For each visit it:
1. asks Authelia itself whether the visitor's session is valid for that address,
2. finds the Jellyfin account with the same username, or creates it
   (administrator in Jellyfin only if the person is an administrator in Authelia),
3. starts a Quick Connect request, approves it for that account with the server's
   API key, and exchanges it for a normal Jellyfin session,
4. hands the session to the Jellyfin web app and opens it.
Jellyfin's password login is blocked at the proxy, so this and Quick Connect codes
approved by a signed-in person are the only ways in.
"""
import html
import http.client
import json
import os
import re
import secrets
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AUTHELIA = urllib.parse.urlparse(os.environ.get("AUTHELIA_URL", "http://authelia:9091"))
JELLYFIN = urllib.parse.urlparse(os.environ.get("JELLYFIN_URL", "http://jellyfin:8096"))
PUBLIC_URL = os.environ.get("PUBLIC_URL", "https://stream.raph.io").rstrip("/")
PORTAL = os.environ.get("PORTAL_URL", "https://auth.raph.io").rstrip("/")
API_KEY = open(os.environ.get("API_KEY_FILE", "/run/secrets/jellyfin-api-key")).read().strip()
COOKIE_RE = re.compile(r"authelia_session=([^\s;,\"]+)")
NAME_RE = re.compile(r"^[A-Za-z0-9._@-]{1,64}$")


def authelia_user(cookie_header, ip):
    m = COOKIE_RE.search(cookie_header or "")
    if not m:
        return None
    c = http.client.HTTPConnection(AUTHELIA.hostname, AUTHELIA.port or 80, timeout=5)
    c.request("GET", "/api/authz/auth-request", headers={
        "X-Original-URL": PUBLIC_URL + "/sso-login", "X-Original-Method": "GET",
        "X-Forwarded-For": ip, "Cookie": "authelia_session=" + m.group(1)})
    r = c.getresponse()
    r.read()
    user, groups = r.getheader("Remote-User"), r.getheader("Remote-Groups") or ""
    c.close()
    if r.status != 200 or not user or not NAME_RE.match(user):
        return None
    return user, [g.strip() for g in groups.split(",") if g.strip()]


def jf(method, path, body=None, token=API_KEY, device=None):
    auth = 'MediaBrowser Client="SSO", Device="Authelia", DeviceId="%s", Version="1.0"' % (device or "sso-service")
    if token:
        auth += ', Token="%s"' % token
    c = http.client.HTTPConnection(JELLYFIN.hostname, JELLYFIN.port or 80, timeout=15)
    c.request(method, path, body=json.dumps(body) if body is not None else None,
              headers={"Authorization": auth, "Content-Type": "application/json", "Accept": "application/json"})
    r = c.getresponse()
    data = r.read()
    c.close()
    if r.status >= 400:
        raise RuntimeError("Jellyfin answered %s on %s" % (r.status, path.split("?")[0]))
    return json.loads(data) if data.strip() else None


def ensure_user(name, admin):
    users = jf("GET", "/Users")
    user = next((u for u in users if u["Name"].lower() == name.lower()), None)
    if not user:
        user = jf("POST", "/Users/New", {"Name": name, "Password": secrets.token_urlsafe(32)})
    policy = dict(user.get("Policy") or jf("GET", "/Users/%s" % user["Id"])["Policy"])
    want = {"IsAdministrator": admin, "IsDisabled": False}
    if any(policy.get(k) != v for k, v in want.items()) or not user.get("Policy"):
        policy.update(want)
        policy.setdefault("EnableAllFolders", True)
        jf("POST", "/Users/%s/Policy" % user["Id"], policy)
    return user["Id"]


def session_for(user_id):
    device = "sso-" + secrets.token_hex(8)
    qc = jf("POST", "/QuickConnect/Initiate", token=None, device=device)
    jf("POST", "/QuickConnect/Authorize?code=%s&userId=%s" % (urllib.parse.quote(qc["Code"]), user_id))
    result = jf("POST", "/Users/AuthenticateWithQuickConnect", {"Secret": qc["Secret"]}, token=None, device=device)
    return result["AccessToken"], result["User"]["Id"], result["ServerId"], device


PAGE = """<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Opening Jellyfin</title><style>body{font:16px system-ui,sans-serif;background:#101010;color:#ddd;display:grid;place-items:center;height:100vh;margin:0}</style>
<p>%s</p>%s"""


class Handler(BaseHTTPRequestHandler):
    server_version = "jellyfin-sso"

    def log_message(self, *a):
        pass

    def page(self, code, text, script="", nonce=""):
        body = (PAGE % (text, script)).encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-%s'; frame-ancestors 'none'" % nonce)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?")[0] == "/healthz":
            return self.page(200, "ok")
        ip = (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()
        try:
            who = authelia_user(self.headers.get("Cookie"), ip)
        except Exception:
            who = None
        if not who:
            return self.page(401, 'Please <a href="%s/?rd=%s">sign in</a> first.' % (
                PORTAL, urllib.parse.quote(PUBLIC_URL + "/sso-login", safe="")))
        user, groups = who
        try:
            user_id = ensure_user(user, "admins" in groups)
            token, user_id, server_id, device = session_for(user_id)
        except Exception as e:
            return self.page(502, "Jellyfin couldn't sign you in right now (%s). Try again in a minute." % html.escape(str(e)))
        nonce = secrets.token_urlsafe(16)
        creds = {"Servers": [{"Id": server_id, "Name": "Jellyfin", "ManualAddress": PUBLIC_URL, "manualAddressOnly": True,
                              "LastConnectionMode": 2, "AccessToken": token, "UserId": user_id,
                              "DateLastAccessed": 0}]}
        script = ("<script nonce='%s'>try{var c=%s;c.Servers[0].DateLastAccessed=Date.now();"
                  "localStorage.setItem('jellyfin_credentials',JSON.stringify(c));"
                  "localStorage.setItem('_deviceId2',%s);}catch(e){}"
                  "sessionStorage.removeItem('sso-auto-login');location.replace('/web/');</script>") % (
            nonce, json.dumps(creds), json.dumps(device))
        self.page(200, "Opening Jellyfin…", script, nonce)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
