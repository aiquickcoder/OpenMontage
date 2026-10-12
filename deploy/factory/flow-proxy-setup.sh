#!/usr/bin/env bash
# Pin one VLESS exit from a subscription as the Flow browser's proxy (socks 127.0.0.1:20900).
# Pick the exit with flow-proxy-probe.sh first (GL must not be RU). Run from the Mac:
#   ssh root@155.212.156.162 'bash -s -- /root/flow-sub.txt "Швеция | YT"' < deploy/factory/flow-proxy-setup.sh
# Then set FLOW_PROXY=socks5://127.0.0.1:20900 in /opt/openmontage/.env and restart factory-worker.
set -euo pipefail
SUB="${1:?subscription file}"; MATCH="${2:?server name substring}"
DIR=/etc/factory-flow-proxy; PORT=20900
install -d -m 750 -o root -g factory "$DIR"
python3 - "$SUB" "$MATCH" "$DIR/config.json" "$PORT" <<'PY'
import base64, json, sys, urllib.parse
sub, match, out, port = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
raw = open(sub).read().strip()
try:
    text = base64.b64decode(raw + "=" * (-len(raw) % 4)).decode()
except Exception:
    text = raw
for line in text.splitlines():
    u = urllib.parse.urlsplit(line.strip())
    name = urllib.parse.unquote(u.fragment)
    if u.scheme == "vless" and match in name:
        break
else:
    sys.exit(f"no vless server matching {match!r}")
q = dict(urllib.parse.parse_qsl(u.query))
stream = {"network": q.get("type", "tcp"), "security": q.get("security", "none")}
if stream["security"] == "reality":
    stream["realitySettings"] = {"serverName": q.get("sni", ""), "fingerprint": q.get("fp", "chrome"),
                                 "publicKey": q.get("pbk", ""), "shortId": q.get("sid", ""),
                                 "spiderX": q.get("spx", "")}
elif stream["security"] == "tls":
    stream["tlsSettings"] = {"serverName": q.get("sni", u.hostname), "fingerprint": q.get("fp", "chrome")}
user = {"id": u.username, "encryption": "none"}
if q.get("flow"):
    user["flow"] = q["flow"]
cfg = {"log": {"loglevel": "warning"},
       "inbounds": [{"tag": "flow", "listen": "127.0.0.1", "port": port, "protocol": "socks",
                     "settings": {"udp": True}}],
       "outbounds": [{"tag": "exit", "protocol": "vless", "streamSettings": stream,
                      "settings": {"vnext": [{"address": u.hostname, "port": u.port or 443, "users": [user]}]}}]}
json.dump(cfg, open(out, "w"), indent=1)
print("pinned:", name.strip(), u.hostname)
PY
chown root:factory "$DIR/config.json"; chmod 640 "$DIR/config.json"
cat > /etc/systemd/system/factory-flow-proxy.service <<UNIT
[Unit]
Description=Flow browser egress proxy (pinned VLESS exit, socks 127.0.0.1:$PORT)
After=network-online.target

[Service]
User=factory
Slice=factory.slice
ExecStart=/usr/local/bin/xray run -c $DIR/config.json
Restart=on-failure
RestartSec=5
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now factory-flow-proxy
systemctl restart factory-flow-proxy
sleep 3
systemctl is-active factory-flow-proxy
echo "GL via proxy: $(curl -s -m 20 -x socks5h://127.0.0.1:$PORT https://www.youtube.com/ | grep -o '"GL":"[A-Z]*"' | head -1 || true)"
