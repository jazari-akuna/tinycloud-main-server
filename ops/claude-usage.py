#!/usr/bin/env python3
"""Prints "high ..." when sagan's Claude account is at 90% or more of a usage limit, "ok ..." otherwise,
"unknown" when it can't tell (for example an API-key account, which has no such limits).
Used by weekly-update.sh so a repair doesn't start only to stall on a limit."""
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
used = {k: v.get("utilization") for k, v in data.items() if isinstance(v, dict) and v.get("utilization") is not None}
worst = max(used.values(), default=0)
print(("high" if worst >= 90 else "ok"), " ".join("%s=%s%%" % (k, round(v)) for k, v in used.items()))
