# Single sign-on

One Authelia sign-in (https://auth.raph.io) opens every site behind the proxy, with no second login.

| Site | How it signs you in |
|---|---|
| Nextcloud (c.raph.io) | OpenID Connect through the `user_oidc` app. Other Nextcloud users can still use `/login?direct=1`. Desktop and phone apps keep working through app passwords. |
| Portainer (p.raph.io) | Portainer's OAuth login; `nginx/portainer.js` starts it automatically on the login page. |
| n8n (n8n-alex.raph.io) | `n8n/sso-hook.js`, an n8n external hook that signs you in as the n8n owner. Webhooks and forms stay public. |
| YOURLS admin (s.raph.io/admin) | `yourls/sso-authelia`, a YOURLS plugin. Short links stay public. |
| Proxy Manager (rp.raph.io) | `npm/mint-token.sh` makes a 30-day token for its web UI each week, and `nginx/inject.conf` hands it only to browsers that passed the sign-in. |
| Jellyfin (stream.raph.io) | `jellyfin/`, a small sign-in service: it checks the Authelia session, finds or creates the matching Jellyfin account, and opens a session through Jellyfin's Quick Connect. The web app is behind the sign-in and goes there by itself (`nginx/jellyfin.js`). Password logins are refused at the proxy (`nginx/jellyfin.conf`); TV and phone apps use Quick Connect codes approved on the web. Needs a Jellyfin API key in `/home/sagan/jellyfin-sso/api-key` (not in git). |
| Camera (makercam.raph.io) | Behind the sign-in; the camera still asks for its own password. |
| Users (sso.raph.io) | `admin/`, a small page to add people, change groups and set passwords. |

Each app checks the session by asking Authelia itself with the visitor's cookie, so a forged header can't sign anyone in.

## Layout on the server

- This repo is checked out at `/home/sagan/tinycloud-main-server`.
- Secrets and data stay out of git, in `/home/sagan/authelia/data`:
  - `users.yml` (people and password hashes),
  - `db.sqlite3`,
  - `secrets/` (session, storage and OIDC keys; OIDC client secrets as `client-<name>` with their hashes in `client-<name>.hash`).
- Authelia: `cd sso/authelia && docker compose up -d`.
- Proxy hosts: `sso/npm/apply-proxy-config.sh` puts `nginx/gate.conf` (and the auto sign-in scripts) on the proxy hosts through the Proxy Manager's API. Host ids in the script are this server's.
- `systemd/npm-sso-token.timer` renews the Proxy Manager token weekly.
- YOURLS mounts `yourls/sso-authelia` into `/var/www/html/user/plugins/` (Portainer stack `yourls`); the plugin is switched on in its admin.
- n8n mounts `n8n/` at `/sso` and sets `EXTERNAL_HOOK_FILES=/sso/sso-hook.js` (`/home/sagan/n8n-linkedin-notion/docker-compose.yml`).

## Adding a person

Use https://sso.raph.io. Only people in the `admins` group can open the gated sites; add a group and a rule in `authelia/configuration.yml` to give someone narrower access.
