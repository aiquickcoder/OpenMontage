#!/bin/sh
# The viral TikTok autopilot instead of the city / Russia runs (owner 2026-11-01). Run as root on the server after
# `sudo -u factory git pull --ff-only`. The old timers are only switched off — their unit files stay, so
# `systemctl enable --now news-digest-moscow-am.timer …` brings any of them back.
set -eu
cd /opt/openmontage/deploy/news
install -m 644 news-viral@.service news-viral-1.timer news-viral-2.timer news-viral-3.timer news-viral-4.timer \
  news-viral-tune.service news-viral-tune.timer /etc/systemd/system/
install -d -o factory -g factory /srv/factory/news_viral
systemctl daemon-reload
for t in news-morning-ru news-digest-kazan-am news-digest-moscow-am news-digest-spb-am news-digest-kazan-pm \
         news-digest-moscow-pm news-digest-spb-pm; do
  systemctl disable --now "$t.timer" 2>/dev/null || true
done
systemctl enable --now news-viral-1.timer news-viral-2.timer news-viral-3.timer news-viral-4.timer news-viral-tune.timer
systemctl list-timers --all | grep -E 'news-(viral|morning|digest)' || true
