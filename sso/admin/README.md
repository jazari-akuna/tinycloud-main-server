## SSO admin page (`sso/admin/`)

A small page at https://sso.raph.io where the owner adds, edits, switches off, removes and sets passwords for the people who can sign in through Authelia. It edits Authelia's users file directly (mounted from `/home/sagan/authelia/data`) and checks every visit against Authelia itself, allowing only the `admins` group. Build and start on the server with `cd sso/admin && docker compose up -d --build`; the proxy host uses `sso/nginx/gate.conf` and forwards to `sso-admin:8080`.
