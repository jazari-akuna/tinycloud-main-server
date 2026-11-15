#!/bin/sh
# Puts the Authelia gate on the proxy hosts, plus the auto sign-in scripts for
# Portainer and the proxy manager itself, through the proxy manager's own API
# so its database and generated config stay in step.
# Sites are found by their first address, so this keeps working when ids change.
set -eu
DIR=$(cd "$(dirname "$0")/.." && pwd)
C=${NPM_CONTAINER:-nginx}

docker exec "$C" mkdir -p /data/nginx/custom/sso
docker cp "$DIR/nginx/portainer.js" "$C:/data/nginx/custom/sso/portainer.js"
"$DIR/npm/mint-token.sh"

# A 10-minute API token for the changes below, kept inside the container.
docker exec -i -w /app "$C" node --input-type=module <<'JS'
import fs from "node:fs";
const cfg = await import("/app/lib/config.js");
if (cfg.configure) await cfg.configure();
const Token = (await import("/app/models/token.js")).default;
const { token } = await Token().create({ iss: "api", attrs: { id: 1 }, scope: ["user"], expiresIn: "10m" });
fs.writeFileSync("/tmp/.sso-api", token, { mode: 0o600 });
JS

docker exec "$C" sh -c 'curl -s -H "Authorization: Bearer $(cat /tmp/.sso-api)" http://127.0.0.1:81/api/nginx/proxy-hosts' > /tmp/.sso-hosts.json

put() {
  domain=$1
  script=$2
  extra=${3:-}
  id=$(python3 -c 'import json,sys; print(next((h["id"] for h in json.load(open("/tmp/.sso-hosts.json")) if sys.argv[1] in h["domain_names"]), ""))' "$domain")
  if [ -z "$id" ]; then echo "no proxy host for $domain, skipped"; return; fi
  {
    cat "$DIR/nginx/gate.conf"
    if [ -n "$script" ]; then sed "s/SCRIPT/$script/g" "$DIR/nginx/inject.conf"; fi
    if [ -n "$extra" ]; then cat "$DIR/nginx/$extra"; fi
  } | python3 -c 'import sys, json; print(json.dumps({"advanced_config": sys.stdin.read()}))' |
    docker exec -i "$C" sh -c 'curl -s -o /dev/null -w "%{http_code}" -X PUT -H "Authorization: Bearer $(cat /tmp/.sso-api)" -H "Content-Type: application/json" --data-binary @- http://127.0.0.1:81/api/nginx/proxy-hosts/'"$id"
  echo " $domain"
}
put rp.raph.io npm.js
put p.raph.io portainer.js
put s.raph.io "" yourls-root.conf
put n8n-alex.raph.io ""
put makercam.raph.io ""
put sso.raph.io ""
put hub.raph.io ""
rm -f /tmp/.sso-hosts.json
docker exec "$C" rm -f /tmp/.sso-api
