#!/usr/bin/env bash
# start.sh - bring up the D1 stack on the Jetson with one command.
#
#   ./start.sh            start everything that is not running yet (safe to run again)
#   ./start.sh status     what runs, robot link, battery
#   ./start.sh stop       stop what start.sh started. The robot link (bridge) is left alone: stopping it
#                         while the robot stands can make the robot damp (drop). ./start.sh stop --all
#                         also stops the bridge, only when the robot is lying.
#
# What it starts
#   d1bridge  robot link (agibot_d1_bridge) ........ only if the d1-bridge service / a bridge container is not already up
#   d1dash    developer dashboard  :8080 ........... create / save maps, load a map, POIs, "Start" from home
#   d1gw      gateway (ROS <-> WebSocket) :8765 ..... follows the places of the map loaded in the dashboard
#   face      robot-face server  :5180 .............. the guest interface (QR, join page, voice, gestures) - unchanged
#   (the d1-ros-agent and d1-vapi-server services that carry the voice tool calls are systemd units: checked, not started here)
#
# Flow: open the dashboard -> create + save a map, or pick one and "Load map" -> the popup says "put the robot on its HOME
# position, then click Start" -> after Start it says "Ready for the app" -> guests scan the QR and use the interface.
#
# Optional settings in ~/.d1_start.env (sourced if present): VAPI_PUBLIC_KEY, VAPI_ASSISTANT_ID, FACE_KEY, PUBLIC_URL, BIND, AGENT_URL.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN="$HOME/.d1_run"; mkdir -p "$RUN"
ROBOT_IP="${ROBOT_IP:-192.168.168.168}"; LOCAL_IP="${LOCAL_IP:-192.168.168.10}"
DASH_PORT=8080; GW_PORT=8765; FACE_PORT="${FACE_PORT:-5180}"
[ -f "$HOME/.d1_start.env" ] && . "$HOME/.d1_start.env"

say()  { printf '%s\n' "$*"; }
ok()   { printf '  \033[32m✔\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m✘\033[0m %s\n' "$*"; }
up()   { docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$1"; }
lan_ip() { hostname -I | tr ' ' '\n' | grep -E '^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.)' | grep -v -E '^(172\.17\.|192\.168\.168\.)' | head -1; }

# run <container-name> <command inside the d1-stack container>   (background, logs to ~/.d1_run/<name>.log)
run() {
  local name="$1"; shift
  D1_NAME="$name" setsid nohup "$ROOT/docker/d1-jetson/run.sh" bash -c "source /opt/ros/humble/setup.bash; source /ws/ros2_ws/install/setup.bash; $*" \
    > "$RUN/$name.log" 2>&1 < /dev/null &
}
wait_http() { local url="$1" n="${2:-60}"; for _ in $(seq 1 "$n"); do curl -s -m 2 -o /dev/null "$url" && return 0; sleep 1; done; return 1; }
dash_state() { curl -s -m 4 "http://127.0.0.1:$DASH_PORT/api/state" 2>/dev/null; }
jq_py() { python3 -c "import sys,json
try:
    d=json.load(sys.stdin)
except Exception:
    sys.exit(1)
print($1)" 2>/dev/null; }

status() {
  say "D1 stack"
  for c in d1bridge d1dash d1gw; do up "$c" && ok "$c container up" || { [ "$c" = d1bridge ] && systemctl is-active --quiet d1-bridge && ok "d1-bridge service up" || bad "$c not running"; }; done
  curl -s -m 2 -o /dev/null "http://127.0.0.1:$FACE_PORT/api/state" && ok "robot-face :$FACE_PORT up" || bad "robot-face :$FACE_PORT not running"
  for s in d1-ros-agent d1-vapi-server; do systemctl is-active --quiet "$s" && ok "$s service up" || warn "$s service not active (voice tool calls will not reach the robot)"; done
  local st; st="$(dash_state)"
  if [ -n "$st" ]; then
    echo "$st" | jq_py "'  robot: link=%s mode=%s battery=%s%%  workflow=%s map=%s' % (d['robot']['connected'], d['robot']['mode_name'], d['robot']['battery'], d['ui_mode'], d['current_map'])"
    local b; b="$(echo "$st" | jq_py "d['robot']['battery'] if d['robot']['battery'] is not None else ''")"
    [ -n "$b" ] && [ "$b" -lt 20 ] 2>/dev/null && warn "battery $b%: the SDK refuses movement commands under 20% - charge the robot"
  fi
}

stop_all() {
  say "Stopping"
  [ -f "$RUN/face.pid" ] && kill "$(cat "$RUN/face.pid")" 2>/dev/null && rm -f "$RUN/face.pid" && ok "robot-face stopped"
  for c in d1gw d1dash; do up "$c" && docker stop "$c" >/dev/null 2>&1 && ok "$c stopped"; done
  if [ "${1:-}" = "--all" ]; then
    local mode; mode="$(curl -s -m 3 "http://127.0.0.1:$DASH_PORT/api/state" | jq_py "d['robot']['mode_name']")"
    if [ "$mode" = "lying" ] || [ -z "$mode" ]; then up d1bridge && docker stop d1bridge >/dev/null 2>&1 && ok "d1bridge stopped"
    else warn "bridge kept: the robot is '$mode' (stop it only when lying)"; fi
  else
    say "  (robot link left running; ./start.sh stop --all stops it when the robot is lying)"
  fi
}

case "${1:-start}" in
  status) status; exit 0 ;;
  stop)   shift; stop_all "${1:-}"; exit 0 ;;
  start)  ;;
  *) say "usage: $0 [start|status|stop [--all]]"; exit 2 ;;
esac

say "Starting the D1 stack  ($(date +%T))"

# ---- preflight
docker image inspect d1-stack:humble-jp6 >/dev/null 2>&1 || { bad "docker image d1-stack:humble-jp6 missing (docker build -t d1-stack:humble-jp6 docker/d1-jetson)"; exit 1; }
[ -d "$ROOT/third_party/agibot_D1_Edu-Ultra/lib" ] || { say "  fetching the robot SDK submodule"; git -C "$ROOT" submodule update --init >/dev/null 2>&1; }
[ -d "$ROOT/third_party/agibot_D1_Edu-Ultra/lib" ] && ok "robot SDK present" || { bad "robot SDK missing: git submodule update --init"; exit 1; }
ping -c1 -W2 "$ROBOT_IP" >/dev/null 2>&1 && ok "robot reachable at $ROBOT_IP" || warn "robot $ROBOT_IP does not answer (cable? powered on?) - the stack still starts"
[ -d "$HOME/d1_maps/pylibs/websockets" ] || { say "  installing websockets 13 (the image's 9.1 is broken on Python 3.10)"; pip3 install -q --target "$HOME/d1_maps/pylibs" websockets==13.1 >/dev/null 2>&1; }
if [ ! -d "$ROOT/ros2_ws/install/d1_dashboard" ] || [ ! -d "$ROOT/ros2_ws/install/d1_gateway" ]; then
  say "  building the ROS workspace (first start, a few minutes)"
  D1_NAME=d1build "$ROOT/docker/d1-jetson/run.sh" bash -c "source /opt/ros/humble/setup.bash && cd /ws/ros2_ws && colcon build --symlink-install --packages-select agibot_d1_bridge d1_camera d1_depth d1_slam d1_nav d1_lidar d1_gateway d1_dashboard >/dev/null 2>&1"
fi

# ---- robot link: never start a second bridge (the SDK allows one client; a second one fights the first over UDP 43988 and flips /connected)
bridge_present() {
  systemctl is-active --quiet d1-bridge && return 0
  [ "$(systemctl show -p ActiveState --value d1-bridge 2>/dev/null)" = "activating" ] && return 0
  up d1bridge && return 0
  up d1mono && return 0
  ss -ulpn 2>/dev/null | grep -q ':43988 ' && return 0          # something already holds the SDK port
  return 1
}
if bridge_present; then
  ok "robot link already running"
elif [ "$(awk '{print int($1)}' /proc/uptime)" -lt 180 ] && systemctl is-enabled --quiet d1-bridge 2>/dev/null && sleep 45 && bridge_present; then
  ok "robot link already running (the d1-bridge service came up after boot)"       # just booted: give the service its chance first
else
  run d1bridge "ros2 launch agibot_d1_bridge bridge.launch.py local_ip:=$LOCAL_IP robot_ip:=$ROBOT_IP"
  ok "robot link starting (d1bridge)"
fi

# ---- developer dashboard
if up d1dash; then ok "dashboard already running"
else
  run d1dash "ros2 launch d1_dashboard dashboard.launch.py external_robot:=true mono_slam:=true host:=0.0.0.0 robot_ip:=$ROBOT_IP local_ip:=$LOCAL_IP"
  wait_http "http://127.0.0.1:$DASH_PORT/api/pois" 60 && ok "dashboard up :$DASH_PORT" || bad "dashboard did not answer - see $RUN/d1dash.log"
fi

# ---- gateway (follows ~/d1_maps/active_places.yaml, written by the dashboard for the loaded map)
if up d1gw; then ok "gateway already running"
else
  run d1gw "export PYTHONPATH=/root/d1_maps/pylibs:\$PYTHONPATH; exec python3 /ws/ros2_ws/install/d1_gateway/lib/d1_gateway/gateway --host 127.0.0.1 --port $GW_PORT --places /root/d1_maps/active_places.yaml"
  ok "gateway starting :$GW_PORT"
fi

# ---- guest interface (robot-face): host Node process
if curl -s -m 2 -o /dev/null "http://127.0.0.1:$FACE_PORT/api/state"; then ok "robot-face already running :$FACE_PORT"
elif ! command -v node >/dev/null; then bad "node not installed: the guest interface needs Node"
else
  ( cd "$ROOT" && PORT="$FACE_PORT" setsid nohup node tools/robot-face/server.mjs > "$RUN/face.log" 2>&1 < /dev/null & echo $! > "$RUN/face.pid" )
  wait_http "http://127.0.0.1:$FACE_PORT/api/state" 15 && ok "robot-face up :$FACE_PORT" || bad "robot-face did not answer - see $RUN/face.log"
fi

# ---- is the robot visible to the dashboard?
for _ in $(seq 1 20); do [ "$(dash_state | jq_py "d['robot']['connected']")" = "True" ] && break; sleep 2; done
echo
status
IP="$(lan_ip)"
cat <<MSG

  Developer dashboard   http://${IP:-<jetson-ip>}:$DASH_PORT      (create / save map, load map, POIs, Start)
  Guest interface       http://127.0.0.1:$FACE_PORT/face   (on the Jetson; expose with 'tailscale serve', never funnel the console)
  Logs                  $RUN/

  Each run: load a map -> put the robot on its HOME position -> click Start -> "Ready for the app".
MSG
