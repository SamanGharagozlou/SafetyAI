#!/usr/bin/env bash
# Try the visitor app on a phone WITHOUT the robot: mock robot backend + the app in robot mode, reachable on your Wi-Fi.
#   tools/phone_demo.sh start [--not-localized] [--speed 0.3]    prints the URL and writes a QR code (SVG)
#   tools/phone_demo.sh stop | status
# The phone must be on the same Wi-Fi as this PC. While it runs, the app is visible to that network; the join token is random per start.
# Plain HTTP on a LAN IP is fine here (no microphone is used); live voice (Vapi) is switched off in this demo.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN=/tmp/d1_phone_demo; PORT="${PORT:-5173}"
export PATH="$HOME/tools/node/bin:$PATH"
lan_ip() { hostname -I | tr ' ' '\n' | grep -E '^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.)' | grep -v '^172\.17\.' | head -1; }
stop_all() { for f in "$RUN"/*.pid; do [ -f "$f" ] && kill "$(cat "$f")" 2>/dev/null; rm -f "$f"; done; for p in $(ps -eo pid,cmd | grep -E "tools/mock_stack.py|vite --mode robot" | grep -v -E "grep|awk|phone_demo" | awk '{print $1}'); do kill "$p" 2>/dev/null; done; }
case "${1:-start}" in
  stop) stop_all; echo "stopped"; exit 0;;
  status) curl -s -m 3 localhost:8780/healthz || echo "backend not running"; echo; curl -s -m 3 -o /dev/null -w "app: HTTP %{http_code}\n" "localhost:$PORT/" || echo "app not running"; exit 0;;
  start) shift;;
  *) echo "usage: $0 start|stop|status"; exit 2;;
esac
command -v node >/dev/null || { echo "Node 24 is needed (frontend/README.md)"; exit 1; }
[ -d "$ROOT/frontend/node_modules" ] || (cd "$ROOT/frontend" && npm ci --include=dev --no-audit --no-fund)
stop_all; mkdir -p "$RUN"; sleep 1
TOKEN="$(python3 -c 'import secrets;print(secrets.token_urlsafe(9))')"
SPEED=0.3; EXTRA=""
while [ $# -gt 0 ]; do case "$1" in --not-localized) EXTRA="--not-localized";; --speed) SPEED="$2"; shift;; esac; shift; done
( exec setsid nohup python3 "$ROOT/tools/mock_stack.py" --token "$TOKEN" --speed "$SPEED" $EXTRA ) > "$RUN/backend.log" 2>&1 < /dev/null &
echo $! > "$RUN/backend.pid"
# the redirects are on the whole group, so no process keeps this terminal's output open
( cd "$ROOT/frontend" && export VITE_VAPI_PUBLIC_KEY= VITE_VAPI_ASSISTANT_ID= && exec setsid nohup npx vite --mode robot --host 0.0.0.0 --port "$PORT" --strictPort ) > "$RUN/app.log" 2>&1 < /dev/null &
echo $! > "$RUN/app.pid"
for i in $(seq 1 30); do curl -s -m 2 localhost:8780/healthz >/dev/null && curl -s -m 2 -o /dev/null "localhost:$PORT/" && break; sleep 1; done
IP="$(lan_ip)"; URL="http://${IP:-<this-PC-ip>}:$PORT/join?t=$TOKEN"
rm -f "$RUN/qr.svg"; (cd "$ROOT/frontend" && node --input-type=module -e "import e from 'qr'; import {writeFileSync} from 'node:fs'; writeFileSync(process.argv[1], e(process.argv[2],'svg',{ecc:'quartile',border:4,scale:10}))" "$RUN/qr.svg" "$URL")
echo "backend: $(curl -s -m 3 localhost:8780/healthz)"
echo
echo "Open on the phone (same Wi-Fi):  $URL"
echo "QR code (open this file on the PC screen and scan it):  $RUN/qr.svg"
echo "Stop with:  tools/phone_demo.sh stop      Logs: $RUN/"
exit 0
