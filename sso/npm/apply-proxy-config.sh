#!/bin/sh
# Puts the Authelia gate on the proxy hosts, plus the auto sign-in scripts for
# Portainer and the proxy manager itself, through the proxy manager's own API
# so its database and generated config stay in step.
# Host ids are this server's: 2 proxy manager, 3 Portainer, 6 YOURLS, 8 n8n, 10 camera.
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

put() {
  id=$1
  script=$2
  extra=${3:-}
  {
    cat "$DIR/nginx/gate.conf"
    if [ -n "$script" ]; then sed "s/SCRIPT/$script/g" "$DIR/nginx/inject.conf"; fi
    if [ -n "$extra" ]; then cat "$DIR/nginx/$extra"; fi
  } | python3 -c 'import sys, json; print(json.dumps({"advanced_config": sys.stdin.read()}))' |
    docker exec -i "$C" sh -c 'curl -s -o /dev/null -w "%{http_code}" -X PUT -H "Authorization: Bearer $(cat /tmp/.sso-api)" -H "Content-Type: application/json" --data-binary @- http://127.0.0.1:81/api/nginx/proxy-hosts/'"$id"
  echo " proxy host $id"
}
put 2 npm.js
put 3 portainer.js
put 6 "" yourls-root.conf
put 8 ""
put 10 ""
docker exec "$C" rm -f /tmp/.sso-api
