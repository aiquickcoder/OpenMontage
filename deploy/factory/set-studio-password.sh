#!/usr/bin/env bash
# Set the Studio owner password on 155. Run from your Mac:  bash deploy/factory/set-studio-password.sh
# The password is typed on the server (ssh -t), only its scrypt hash is stored in /opt/openmontage/.env.
set -euo pipefail
HOST=root@155.212.156.162
KEY=~/.ssh/claude_quillon_pilot

ssh -t -i "$KEY" "$HOST" 'set -e
  cd /opt/openmontage
  HASH=$(sudo -u factory .venv/bin/python -m studio hash-password) || exit 1
  grep -q "^STUDIO_ADMIN_PASSWORD_HASH=" .env || echo "STUDIO_ADMIN_PASSWORD_HASH=" >> .env
  sed -i "s|^STUDIO_ADMIN_PASSWORD_HASH=.*|STUDIO_ADMIN_PASSWORD_HASH=$HASH|" .env
  systemctl enable --now factory-studio >/dev/null 2>&1 || true
  systemctl restart factory-studio && sleep 2 && systemctl is-active factory-studio
  echo "Готово: войдите на https://studio.luvael.ru (логин: $(grep "^STUDIO_ADMIN_USER=" .env | cut -d= -f2))"'
