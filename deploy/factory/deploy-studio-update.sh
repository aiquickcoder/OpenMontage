#!/usr/bin/env bash
# Studio phases 2-4 update on 155 + one-off retry of the cat brief at 07:00 UTC.
# Run from the Mac:  ssh root@155.212.156.162 'bash -s' < deploy/factory/deploy-studio-update.sh
set -euo pipefail
cd /opt/openmontage
as_factory() { sudo -u factory -H "$@"; }

as_factory git -c safe.directory=/opt/openmontage pull -q --ff-only origin feat/flow-cartoon-ad
as_factory git -c safe.directory=/opt/openmontage log --oneline -1
as_factory .venv/bin/pip install -q -r requirements-factory.txt
(cd studio/web && as_factory npm ci --silent && as_factory npm run build --silent >/dev/null)
systemctl restart factory-studio factory-worker factory-bot
sleep 3
systemctl is-active factory-studio factory-worker factory-bot
curl -s -o /dev/null -w 'studio /api/overview without login: %{http_code}\n' http://127.0.0.1:4760/api/overview

BRIEF=20261011-071918-kot-povar-gotovit-piccu-i-reklam
systemctl stop factory-retry-cat.timer 2>/dev/null || true
systemd-run --unit=factory-retry-cat --uid=factory --slice=factory.slice \
  -p EnvironmentFile=/opt/openmontage/.env -p WorkingDirectory=/opt/openmontage \
  --on-calendar='2026-10-12 07:00:00 UTC' --timer-property=Persistent=false \
  /opt/openmontage/.venv/bin/python -c "
from factory import config, actions
from factory.db import BriefStore
s = config.load()
actions.retry(BriefStore(s.db_path), '$BRIEF', source=' (таймер 07:00 UTC)')
print('retried $BRIEF')"
systemctl list-timers factory-retry-cat.timer --no-pager
