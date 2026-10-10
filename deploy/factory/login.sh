#!/usr/bin/env bash
# Sign the server's Flow profile into Google. Run from your Mac:
#   bash deploy/factory/login.sh        → open http://localhost:6081/vnc.html, sign in, open flow.google.com
#   Ctrl+C when done                    → Chrome closes, the worker resumes
# The worker is paused meanwhile: one Chrome per profile.
set -euo pipefail
HOST=root@155.212.156.162
KEY=~/.ssh/claude_quillon_pilot
SSH=(ssh -i "$KEY" "$HOST")

cleanup() {
  "${SSH[@]}" 'systemctl stop factory-login 2>/dev/null; systemctl reset-failed factory-login 2>/dev/null;
               systemctl is-enabled -q factory-worker 2>/dev/null && systemctl start factory-worker; true'
  echo "login Chrome closed, worker resumed"
}
trap cleanup EXIT

"${SSH[@]}" 'set -e
  systemctl stop factory-worker 2>/dev/null || true
  systemctl stop factory-login 2>/dev/null || true
  systemctl reset-failed factory-login 2>/dev/null || true
  systemd-run --quiet --unit factory-login --slice factory.slice --uid=factory \
    --setenv=DISPLAY=:99 --setenv=HOME=/home/factory \
    /usr/bin/google-chrome --user-data-dir=/home/factory/flow-profile --password-store=basic \
    --no-first-run --no-default-browser-check --window-size=1366,900 --window-position=0,0 \
    https://accounts.google.com/ServiceLogin?continue=https://flow.google.com/
  sleep 3; systemctl is-active factory-login factory-novnc'
echo
echo "Open http://localhost:6081/vnc.html → Connect. Sign in, then open flow.google.com."
echo "(\"It looks like you don't have access\" without the bridge is expected; the worker patches it.)"
echo "Press Ctrl+C here when done."
"${SSH[@]}" -N -L 6081:127.0.0.1:6081
