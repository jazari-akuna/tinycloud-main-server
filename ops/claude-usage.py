#!/usr/bin/env python3
"""Says whether a Claude repair on this server would stall on a usage limit.

Prints "high ..." when a usage limit is at 90% or more and there is no paid extra usage
left to carry on (extra usage off, or itself at 90% or more); "ok ..." otherwise;
"unknown" when it can't tell. Used by weekly-update.sh before it calls Claude.
"""
import json
import urllib.request

try:
    creds = json.load(open("/home/sagan/.claude/.credentials.json"))
    token = creds["claudeAiOauth"]["accessToken"]
    req = urllib.request.Request("https://api.anthropic.com/api/oauth/usage", headers={
        "Authorization": "Bearer " + token, "anthropic-beta": "oauth-2025-04-20", "User-Agent": "weekly-update"})
    data = json.load(urllib.request.urlopen(req, timeout=15))
except Exception:
    print("unknown")
    raise SystemExit(0)

limits = {k: v.get("utilization") for k, v in data.items()
          if k != "extra_usage" and isinstance(v, dict) and v.get("utilization") is not None}
for lim in data.get("limits") or []:
    if lim.get("is_active") and lim.get("percent") is not None:
        limits[lim.get("kind", "limit")] = lim["percent"]
worst = max(limits.values(), default=0)
# paid extra usage: percent of it spent, None when it's off
eu = data.get("extra_usage")
if isinstance(eu, dict):
    extra = eu.get("utilization") if eu.get("is_enabled", True) else None
else:
    extra = eu
blocked = worst >= 90 and (extra is None or extra >= 90)
detail = " ".join("%s=%s%%" % (k, round(v)) for k, v in sorted(limits.items()))
detail += " extra_usage=%s" % ("off" if extra is None else "%s%%" % round(extra))
print(("high " if blocked else "ok ") + detail)
