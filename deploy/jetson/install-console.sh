#!/usr/bin/env bash
# Install the operator console on the Jetson, reachable from anywhere over
# Tailscale but NOT from the public internet.
#
#   scp -r tools/robot-console sepa@d1-jetson.tail5ec076.ts.net:~/
#   scp deploy/jetson/{d1-console.service,install-console.sh} sepa@d1-jetson...:~/
#   ssh sepa@d1-jetson... 'sudo D1_ROBOT_TOKEN=<token> bash ~/install-console.sh'
#
# Independent of the bridge units in install.sh - either can be installed alone.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP=/home/sepa/robot-console
SERVE_PORT=8443

[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }
[[ -n "${D1_ROBOT_TOKEN:-}" ]] || { echo "D1_ROBOT_TOKEN must be set in the environment" >&2; exit 1; }
[[ -f "$APP/server.mjs" && -f "$APP/index.html" ]] || {
  echo "FAILED: $APP/server.mjs or index.html missing - scp the robot-console directory there first" >&2
  exit 1
}

command -v node >/dev/null || { echo "FAILED: node is not installed on this box" >&2; exit 1; }
echo "==> node $(node --version)"

echo "==> writing the token to a root-only env file"
# NOT in the unit file: those are world-readable and `systemctl cat` prints them.
install -m 0600 /dev/null /etc/d1-console.env
printf 'D1_ROBOT_TOKEN=%s\n' "$D1_ROBOT_TOKEN" > /etc/d1-console.env
chmod 0600 /etc/d1-console.env

echo "==> installing the unit"
install -m 0644 "$HERE/d1-console.service" /etc/systemd/system/d1-console.service
systemctl daemon-reload
systemctl enable --now d1-console.service
sleep 3

echo "==> confirming it is bound to LOOPBACK ONLY"
if ss -ltn | grep -q "127.0.0.1:5174"; then
  echo "    ok: 127.0.0.1:5174"
else
  echo "    FAILED: not listening on 127.0.0.1:5174" >&2
  ss -ltn | grep 5174 || true
  exit 1
fi
if ss -ltn | grep -E "0\.0\.0\.0:5174|\*:5174" >/dev/null; then
  echo "    ABORT: bound to all interfaces - that would expose the robot to the venue wifi" >&2
  exit 1
fi

echo "==> publishing on the tailnet (Serve, NOT Funnel)"
# A SEPARATE HTTPS PORT, and this is load-bearing. FUNNEL IS A PORT-LEVEL
# PROPERTY, NOT A PATH-LEVEL ONE: port 443 is funnel-enabled as a whole
# (443 / -> 8090), so a tailnet-only path mounted beneath it would have
# inherited PUBLIC reachability. Verified on this box - serve status and funnel
# status print the identical tree.
#
# NEVER `tailscale funnel $SERVE_PORT`. 8443 is one of the three ports Funnel is
# allowed to use, so funnelling it would silently publish this console to the
# internet with no login in front of a physical robot. Serve only.
tailscale serve --bg --https=$SERVE_PORT http://127.0.0.1:5174

echo "==> confirming 443's existing funnel was not disturbed"
tailscale funnel status || true

echo
echo "==> serve/funnel state"
tailscale serve status || true
echo
echo "Console:  https://d1-jetson.tail5ec076.ts.net:$SERVE_PORT/"
echo "Reachable from any device signed into this tailnet, on any network."
echo
echo "VERIFY IT IS NOT PUBLIC - do not skip this, it drives a physical robot:"
echo "  1. From a phone with Tailscale ON (cellular, wifi off): the URL should load."
echo "  2. From the same phone with Tailscale OFF: it must FAIL to connect."
echo "  3. From anywhere:  curl --resolve d1-jetson.tail5ec076.ts.net:$SERVE_PORT:176.58.90.145 \\"
echo "       https://d1-jetson.tail5ec076.ts.net:$SERVE_PORT/   # must NOT return the page"
echo
echo "To withdraw it:  sudo tailscale serve --https=$SERVE_PORT off"
