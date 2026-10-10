#!/usr/bin/env bash
# Publishes studio.luvael.ru on 155 (releases for the phone farm). Run from your Mac.
# Needs: DNS A studio.luvael.ru → 155.212.156.162, and install.sh already run (RELEASE_TOKEN in .env).
# Reloads nginx only after `nginx -t` passes; neighbour sites are checked before and after.
set -euo pipefail
HOST=root@155.212.156.162
KEY=~/.ssh/claude_quillon_pilot
DOMAIN=studio.luvael.ru
CONF="$(dirname "$0")/nginx-$DOMAIN.conf"
SITES="https://luvael.ru https://lab.quillon.ru https://hookahmania.ru https://looqa.ru https://ig.luvael.ru/health"
SSH=(ssh -i "$KEY" "$HOST")

check_sites() { for s in $SITES; do printf '%s %s\n' "$(curl -s -o /dev/null -m 10 -w '%{http_code}' "$s")" "$s"; done; }
apply() {  # $1 = config text; installs, tests, reloads or rolls back
  printf '%s\n' "$1" | "${SSH[@]}" "cat > /etc/nginx/sites-enabled/$DOMAIN"
  if "${SSH[@]}" 'nginx -t 2>&1'; then "${SSH[@]}" 'systemctl reload nginx'
  else "${SSH[@]}" "rm -f /etc/nginx/sites-enabled/$DOMAIN"; echo "!! nginx -t failed — vhost removed"; exit 1; fi
}

echo "== DNS"; dig +short "$DOMAIN" | grep -qx 155.212.156.162 || { echo "!! $DOMAIN does not resolve to 155.212.156.162"; exit 1; }
echo "== sites before"; check_sites

if ! "${SSH[@]}" "test -s /etc/letsencrypt/live/$DOMAIN/fullchain.pem"; then
  echo "== phase 1: http-only vhost + certificate"
  apply "$(sed -n '1,/^}/p' "$CONF")"
  "${SSH[@]}" "certbot certonly --webroot -w /var/www/letsencrypt -d $DOMAIN --non-interactive --agree-tos --register-unsafely-without-email --keep-until-expiring"
fi

echo "== phase 2: full vhost"
TOKEN=$("${SSH[@]}" "grep '^RELEASE_TOKEN=' /opt/openmontage/.env | cut -d= -f2")
[ -n "$TOKEN" ] || { echo "!! RELEASE_TOKEN missing in /opt/openmontage/.env"; exit 1; }
apply "$(sed "s/__RELEASE_TOKEN__/$TOKEN/" "$CONF")"
"${SSH[@]}" "chmod 600 /etc/nginx/sites-enabled/$DOMAIN"

sleep 2
echo "== sites after"; check_sites
echo "== $DOMAIN"
curl -s -o /dev/null -m 10 -w '%{http_code} <- no token, expect 401\n' "https://$DOMAIN/releases/index.json"
curl -s -o /dev/null -m 10 -w '%{http_code} <- with token (404 until the first approval)\n' \
  -H "Authorization: Bearer $TOKEN" "https://$DOMAIN/releases/index.json"
echo "Farm token: put RELEASE_TOKEN into phone-farm/.env as FARM_RELEASE_TOKEN (not printed here)."
