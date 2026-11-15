#!/usr/bin/env bash
# Self-heal for the DDS matching-order bug.
#
# Restarts the BRIDGE, never the agent. That asymmetry is the whole point:
# measured behaviour is that restarting the agent does NOT restore callbacks,
# and restarting the bridge while the agent is live does, instantly.
#
# Reads the agent's own loopback status on 8091, which is the only thing that
# knows whether ROS messages are actually arriving. A TCP check would pass
# happily while the bridge served nulls.
set -uo pipefail

STATUS_URL="http://127.0.0.1:8091/status"
STATE_DIR=/run/d1-bridge-watchdog
STRIKES_FILE="$STATE_DIR/strikes"
LAST_RESTART_FILE="$STATE_DIR/last_restart"

# Hard floor between restarts.
#
# HISTORY, because it explains why this stays even though it should never fire:
# sampling /status read T,F,F,F,F,T,F,F,F,T and looked like a flaky link. It was
# not. /connected published at 19.98 Hz in a repeating false,false,true pattern
# - 218 false to 76 true over 10 s - on a robot that was healthy throughout.
# cmd_vel_bridge polled the SDK's checkConnect() faster than the robot's
# heartbeat updated, so it answered False two times in three, and every sampler
# was aliasing against a 2:1 square wave.
#
# Fixed at the source: `connected` is now LATCHED over a 10 s window, and 14
# consecutive samples read true. link_ok is a clean binary again.
#
# The cooldown stays as defence in depth. Against the OLD signal this watchdog
# would have struck out within seconds and restarted the bridge every five
# minutes forever - and a restart loop is strictly worse than a hand-started
# process, because a bridge that is always restarting is never up long enough
# to re-pair. A restart only cures the matching-order wedge, which is
# persistent. If that signal ever regresses, this is what stops a loop.
RESTART_COOLDOWN_S=300

# The link genuinely flaps: ctrl_mode has been observed bouncing 1/18/21 within
# seconds, and status reads alternate connected true/false over a minute while
# the robot itself is fine and standing. Restarting on a single bad read would
# mean restarting the bridge constantly and never letting it settle. Three
# consecutive failures at 20 s spacing is ~1 minute of genuinely dead link.
STRIKES_BEFORE_RESTART=3

mkdir -p "$STATE_DIR"
strikes=$(cat "$STRIKES_FILE" 2>/dev/null || echo 0)

body=$(curl -s -m 8 "$STATUS_URL" 2>/dev/null)

if [[ -z "$body" ]]; then
  # The AGENT is unreachable, which is not the bridge's fault. Leave it to
  # the agent's own Restart=always and do not touch the bridge.
  echo "agent unreachable on 8091; not touching the bridge"
  echo 0 >"$STRIKES_FILE"
  exit 0
fi

# link_ok is the agent's own "connected AND a ctrl_mode seen in the last 5s".
# Falls back to .connected on an older agent that predates link_ok.
healthy=$(printf '%s' "$body" | python3 -c '
import json,sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("unknown"); sys.exit()
v = d.get("link_ok")
if v is None:
    v = d.get("connected")
print("yes" if v is True else "no")
' 2>/dev/null)

if [[ "$healthy" == "yes" ]]; then
  [[ "$strikes" != "0" ]] && echo "link recovered after $strikes strike(s)"
  echo 0 >"$STRIKES_FILE"
  exit 0
fi

if [[ "$healthy" == "unknown" ]]; then
  echo "could not parse agent status; making no decision"
  exit 0
fi

strikes=$((strikes + 1))
echo "$strikes" >"$STRIKES_FILE"
echo "link down ($strikes/$STRIKES_BEFORE_RESTART)"

if (( strikes >= STRIKES_BEFORE_RESTART )); then
  now=$(date +%s)
  last=$(cat "$LAST_RESTART_FILE" 2>/dev/null || echo 0)
  since=$(( now - last ))

  if (( since < RESTART_COOLDOWN_S )); then
    echo "link still down, but last restart was ${since}s ago (cooldown ${RESTART_COOLDOWN_S}s) - holding off"
    # Park strikes one below the threshold so the next bad check re-evaluates
    # the cooldown immediately instead of waiting out another full run.
    echo $(( STRIKES_BEFORE_RESTART - 1 )) >"$STRIKES_FILE"
    exit 0
  fi

  echo "restarting d1-bridge.service to re-pair DDS"
  systemctl restart d1-bridge.service
  echo "$now" >"$LAST_RESTART_FILE"
  echo 0 >"$STRIKES_FILE"
fi
