#!/usr/bin/env bash
# Idempotent install of the OpenMontage factory on 155 (Ubuntu 24.04). Run as root ON THE SERVER:
#   curl -fsSL https://raw.githubusercontent.com/aiquickcoder/OpenMontage/feat/flow-cartoon-ad/deploy/factory/install.sh | bash
# or, from a checkout: bash /opt/openmontage/deploy/factory/install.sh
# Does NOT touch nginx (see enable-nginx.sh) and starts worker/bot only once .env is filled.
set -euo pipefail

REPO_URL=${REPO_URL:-https://github.com/aiquickcoder/OpenMontage.git}
BRANCH=${BRANCH:-feat/flow-cartoon-ad}
APP=/opt/openmontage
DATA=/srv/factory
export DEBIAN_FRONTEND=noninteractive

echo "==> packages"
apt-get update -qq
apt-get install -y -qq xvfb x11vnc novnc websockify git ffmpeg python3-venv python3-pip >/dev/null
if ! command -v google-chrome >/dev/null; then
  tmp=$(mktemp -d); wget -q -O "$tmp/chrome.deb" https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
  apt-get install -y -qq "$tmp/chrome.deb" >/dev/null; rm -rf "$tmp"
fi
google-chrome --version

echo "==> user + dirs"
id factory >/dev/null 2>&1 || useradd -m -s /bin/bash factory
install -d -o factory -g factory -m 755 "$DATA" "$DATA/releases" "$DATA/releases/pending"
install -d -o factory -g factory -m 700 "$DATA/flow-state" /home/factory/flow-profile

echo "==> code ($BRANCH)"
if [ -d "$APP/.git" ]; then
  sudo -u factory git -C "$APP" fetch -q origin "$BRANCH"
  sudo -u factory git -C "$APP" checkout -q "$BRANCH"
  sudo -u factory git -C "$APP" reset -q --hard "origin/$BRANCH"
else
  install -d -o factory -g factory "$APP"
  sudo -u factory git clone -q -b "$BRANCH" "$REPO_URL" "$APP"
fi

echo "==> python venv"
sudo -u factory python3 -m venv "$APP/.venv"
sudo -u factory "$APP/.venv/bin/pip" install -q --upgrade pip
sudo -u factory "$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt" -r "$APP/requirements-factory.txt"

echo "==> remotion"
(cd "$APP/remotion-composer" && sudo -u factory npm install --silent --no-fund --no-audit) || echo "  [warn] npm install failed — compose stage will report it"

echo "==> claude cli"
command -v claude >/dev/null || npm install -g --silent @anthropic-ai/claude-code
claude --version || true

echo "==> .env"
if [ ! -f "$APP/.env" ]; then
  sed "s|^RELEASE_TOKEN=.*|RELEASE_TOKEN=$(openssl rand -hex 24)|" "$APP/deploy/factory/env.example" > "$APP/.env"
fi
chown factory:factory "$APP/.env"; chmod 600 "$APP/.env"

echo "==> systemd"
install -m 644 "$APP"/deploy/factory/systemd/factory.slice "$APP"/deploy/factory/systemd/factory-*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now factory-xvfb factory-vnc factory-novnc >/dev/null

set -a; . "$APP/.env"; set +a
if { [ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ] || [ -n "${ANTHROPIC_API_KEY:-}" ]; } && [ -n "${TG_TOKEN:-}" ] && [ -n "${TG_CHAT:-}" ]; then
  systemctl enable --now factory-bot factory-worker >/dev/null
  systemctl restart factory-bot factory-worker
  echo "worker + bot: started"
else
  echo "worker + bot: NOT started — fill CLAUDE_CODE_OAUTH_TOKEN, TG_TOKEN, TG_CHAT in $APP/.env and re-run"
fi

systemctl --no-pager --plain list-units "factory-*"
free -m | sed -n 2p
systemctl is-active xray quillon-pep nginx
