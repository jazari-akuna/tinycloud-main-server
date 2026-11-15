# Hub

https://hub.raph.io, behind the single sign-on: links to every site on the server, your own links, and the server's and apps' figures.

- `collect.py` runs on the host every minute (`systemd/hub-collect.timer`, low priority). Most runs do nothing: each part has its own refresh interval, much longer while nobody has the hub open (fast figures every minute when open, every 30 min otherwise; app figures every 15 min / 3 h; pending updates every 6 h). It writes `/home/sagan/hub/data/stats.json`.
- `app.py` (container `hub`, no host access, read-only, 128 MB / half a core max) serves the page, checks every visitor with Authelia, and keeps the links you add in `/home/sagan/hub/data/links.json`.
- Services come from the proxy manager's site list, so a new proxy host shows up by itself. Rename, describe or hide one with its Edit button.
- Agents and scripts: the `hub` command (`hub/hub`, installed as `/usr/local/bin/hub` on the server) or the API at `https://hub.raph.io/api/v1` with `Authorization: Bearer <token>`:
  - `GET /api/v1/links`, `GET /api/v1/services`, `GET /api/v1/stats`
  - `POST /api/v1/links` `{"name", "url", "description", "icon"}` to add; `POST /api/v1/links/<id>` with changed fields, or `{"delete": true}`
  - `icon` (optional): a `data:image/...;base64,` image, an image address, or a name from https://dashboardicons.com such as `home-assistant`; `"none"` removes it. Square PNG, SVG or WebP, at least 64 × 64 px (128 × 128 looks sharpest), up to 256 KB. Stored in `/home/sagan/hub/data/icons/`.
  - `POST /api/v1/services/<domain>` `{"name", "description", "hidden"}`, or `{"reset": true}`
  - Tokens: `hub token new LABEL` on the server (only hashes are stored, in `/home/sagan/hub/data/api-tokens.json`), `hub token list`, `hub token revoke ID`. Authelia lets `/api/v1/` through; the hub checks the token itself.
- `add-proxy-host.sh` created the hub.raph.io proxy host.

Deploy: `cd hub && docker compose up -d --build`, then install the two systemd units and `systemctl enable --now hub-collect.timer`.
