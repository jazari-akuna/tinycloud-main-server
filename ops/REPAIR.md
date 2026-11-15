# Repair brief for the weekly update

You are repairing the home server "paraphernalia" (Ubuntu 26.04 LTS) after its weekly automatic update.
Some health checks failed. Make them pass again, then stop. Nobody is watching: don't ask questions,
pick the safest fix that restores the service, and finish with a short report.

## The server
- Apps run in Docker. This repo (/home/sagan/tinycloud-main-server) holds most of their setup;
  read sso/README.md, hub/README.md and sso/apps.yml first. Other stacks: /home/sagan/n8n-linkedin-notion,
  /home/sagan/rathole-server, and Portainer stacks in /var/lib/docker/volumes/portainer_data/_data/compose/<id>
  (8 = proxy manager, 20 = nextcloud, 28 = yourls; 8 and 20 are redeployed by Portainer from GitHub).
- Everything is behind Nginx Proxy Manager (container nginx) and the Authelia sign-in (container authelia).
  Authelia's access rules are generated: edit sso/authelia/configuration.template.yml or sso/apps.yml,
  run sso/authelia/render.py, validate, then restart authelia. Restarting Authelia signs everyone out.
- You run as user sagan, who is in the docker group. You don't have sudo.
- Today's backups are in /root/container-backups/weekly/<date> (databases, sign-in data, hub data).

## Rules
- Never delete data, volumes or backups. Don't remove containers unless you recreate them the same way.
- Prefer rolling back an image to its previous version over changing app settings, when an update broke it.
  Pin the working version in the compose file and say so in the report.
- Don't change passwords, sign-in settings, the firewall, or who can open what.
- Don't commit or push anything; leave changed files for the owner to review, and list them.
- Check your fix with: sudo is not available, so run the individual checks from ops/health.py
  (curl the sites, docker ps, docker logs) and say which ones pass.

## Report
End with: what was broken, what you changed (files and commands), what still fails, and what the owner
should look at. Plain words, at most 15 lines.
