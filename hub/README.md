# Hub

https://hub.raph.io, behind the single sign-on: links to every site on the server, your own links, and the server's and apps' figures.

- `collect.py` runs on the host every minute (`systemd/hub-collect.timer`, low priority). Most runs do nothing: each part has its own refresh interval, much longer while nobody has the hub open (fast figures every minute when open, every 30 min otherwise; app figures every 15 min / 3 h; pending updates every 6 h). It writes `/home/sagan/hub/data/stats.json`.
- `app.py` (container `hub`, no host access, read-only, 128 MB / half a core max) serves the page, checks every visitor with Authelia, and keeps the links you add in `/home/sagan/hub/data/links.json`.
- Services come from the proxy manager's site list, so a new proxy host shows up by itself. Rename, describe or hide one with its Edit button.
- `add-proxy-host.sh` created the hub.raph.io proxy host.

Deploy: `cd hub && docker compose up -d --build`, then install the two systemd units and `systemctl enable --now hub-collect.timer`.
