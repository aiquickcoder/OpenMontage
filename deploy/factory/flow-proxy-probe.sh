#!/usr/bin/env bash
# Probe which VLESS exits from a subscription Google geolocates outside RU (for Flow).
# Starts a throwaway xray on 127.0.0.1:20800+ (one socks port per server), asks YouTube
# for its GL country through each, prints a table, then stops xray. Nothing persists.
# Run from the Mac:
#   ssh root@155.212.156.162 'bash -s -- "<subscription url>"' < deploy/factory/flow-proxy-probe.sh
set -euo pipefail
SUB="${1:?subscription url}"
XRAY="$(command -v xray || echo /usr/local/bin/xray)"
WORK="$(mktemp -d /root/flow-proxy-probe.XXXX)"; chmod 700 "$WORK"
trap 'kill ${XPID:-} 2>/dev/null; rm -rf "$WORK"' EXIT

# SUB is a URL or a local file (the subscription host serves a stub to some IPs)
if [ -f "$SUB" ]; then cp "$SUB" "$WORK/sub.txt"; else curl -s -m 30 -A "happ/2.7.1/macos" -o "$WORK/sub.txt" "$SUB"; fi

python3 - "$WORK" <<'PY'
import base64, json, sys, urllib.parse
work = sys.argv[1]
raw = open(f"{work}/sub.txt").read().strip()
try:
    text = base64.b64decode(raw + "=" * (-len(raw) % 4)).decode()
except Exception:
    text = raw
inbounds, outbounds, rules, index = [], [], [], []
for line in text.splitlines():
    u = urllib.parse.urlsplit(line.strip())
    if u.scheme != "vless" or not u.hostname or u.hostname in ("0.0.0.0", "asb.com"):
        continue
    q = dict(urllib.parse.parse_qsl(u.query))
    name = urllib.parse.unquote(u.fragment).strip()
    if any(c in name for c in ("Россия", "Беларусь")):
        continue
    i = len(index)
    stream = {"network": q.get("type", "tcp"), "security": q.get("security", "none")}
    if stream["security"] == "reality":
        stream["realitySettings"] = {"serverName": q.get("sni", ""), "fingerprint": q.get("fp", "chrome"),
                                     "publicKey": q.get("pbk", ""), "shortId": q.get("sid", ""),
                                     "spiderX": q.get("spx", "")}
    elif stream["security"] == "tls":
        stream["tlsSettings"] = {"serverName": q.get("sni", u.hostname), "fingerprint": q.get("fp", "chrome")}
    if stream["network"] == "grpc":
        stream["grpcSettings"] = {"serviceName": q.get("serviceName", "")}
    user = {"id": u.username, "encryption": "none"}
    if q.get("flow"):
        user["flow"] = q["flow"]
    outbounds.append({"tag": f"o{i}", "protocol": "vless", "streamSettings": stream,
                      "settings": {"vnext": [{"address": u.hostname, "port": u.port or 443, "users": [user]}]}})
    inbounds.append({"tag": f"i{i}", "listen": "127.0.0.1", "port": 20800 + i, "protocol": "socks",
                     "settings": {"udp": False}})
    rules.append({"type": "field", "inboundTag": [f"i{i}"], "outboundTag": f"o{i}"})
    index.append(f"{20800 + i}\t{u.hostname}\t{name}")
json.dump({"log": {"loglevel": "warning"}, "inbounds": inbounds, "outbounds": outbounds,
           "routing": {"rules": rules}}, open(f"{work}/config.json", "w"))
open(f"{work}/index.tsv", "w").write("\n".join(index) + "\n")
PY
chmod 600 "$WORK"/*

"$XRAY" run -c "$WORK/config.json" >"$WORK/xray.log" 2>&1 & XPID=$!
sleep 3

printf "%-6s %-17s %-4s %-4s %s\n" PORT EXIT_IP GL CC NAME
while IFS=$'\t' read -r port host name; do
  px="socks5h://127.0.0.1:$port"
  gl=$(curl -s -m 20 -x "$px" -H 'Accept-Language: en-US' https://www.youtube.com/ | grep -o '"GL":"[A-Z]*"' | head -1 | cut -d'"' -f4 || true)
  ipinfo=$(curl -s -m 15 -x "$px" https://ipinfo.io/json || true)
  ip=$(echo "$ipinfo" | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d.get("ip",""),d.get("country",""))' 2>/dev/null || echo "- -")
  printf "%-6s %-17s %-4s %-4s %s\n" "$port" "${ip% *}" "${gl:--}" "${ip#* }" "$name"
done < <(grep . "$WORK/index.tsv")
rm -rf "$WORK"
