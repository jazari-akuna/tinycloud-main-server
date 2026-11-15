#!/bin/sh
# Creates the sso.raph.io proxy host (to the sso-admin container) with the Authelia gate, through the proxy manager's API.
set -eu
DIR=$(cd "$(dirname "$0")/.." && pwd)
C=${NPM_CONTAINER:-nginx}
docker exec -i -w /app "$C" node --input-type=module <<'JS'
import fs from "node:fs";
const cfg = await import("/app/lib/config.js");
if (cfg.configure) await cfg.configure();
const Token = (await import("/app/models/token.js")).default;
const { token } = await Token().create({ iss: "api", attrs: { id: 1 }, scope: ["user"], expiresIn: "10m" });
fs.writeFileSync("/tmp/.sso-api", token, { mode: 0o600 });
JS
python3 - "$DIR/nginx/gate.conf" <<'PY' | docker exec -i "$C" sh -c 'curl -s -o /dev/null -w "%{http_code}\n" -X POST -H "Authorization: Bearer $(cat /tmp/.sso-api)" -H "Content-Type: application/json" --data-binary @- http://127.0.0.1:81/api/nginx/proxy-hosts'
import sys, json
print(json.dumps({"domain_names": ["sso.raph.io"], "forward_scheme": "http", "forward_host": "sso-admin", "forward_port": 8080,
  "certificate_id": 11, "ssl_forced": True, "block_exploits": True, "http2_support": True, "allow_websocket_upgrade": False,
  "caching_enabled": False, "hsts_enabled": False, "hsts_subdomains": False, "meta": {}, "locations": [],
  "advanced_config": open(sys.argv[1]).read()}))
PY
docker exec "$C" rm -f /tmp/.sso-api
