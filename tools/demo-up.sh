#!/usr/bin/env bash
# Bring up the whole Leukerbad greeter demo WITHOUT the Jetson or the robot.
#
#   bash tools/demo-up.sh
#
# Starts the simulator, tunnels it, repoints the Vapi assistant at it, stands
# the simulated robot up, starts both consoles pointed at the simulator,
# tunnels the public one, and prints the phone URL.
#
# WHY THIS EXISTS: every piece of this has broken at least once in a way that
# produced no error anywhere - a trycloudflare hostname that stopped routing
# while its process stayed alive, a console restart that dropped
# VAPI_ASSISTANT_ID and silently downgraded the greeter into a robot remote
# that shrugged at spa questions. Each was a few minutes to find and a second
# to fix. Holding the whole arrangement in one script is cheaper than
# rediscovering it.
#
# Nothing here touches the robot. To go back to the real one, see the end.
set -uo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ASSISTANT="${ASSISTANT:-a366d544-fcf3-4b98-a436-d1b8c458891a}"   # Leukerbad greeter
SIM_PORT=5175; PUB_PORT=5174; LOC_PORT=5176
RUN="${TMPDIR:-/tmp}/d1-demo"; mkdir -p "$RUN"

command -v cloudflared >/dev/null || { echo "cloudflared not installed"; exit 1; }
[ -f frontend/scripts/vapi/credentials.json ] || {
  echo "frontend/scripts/vapi/credentials.json missing - needed to sync the assistant"; exit 1; }

# A key is required because the public console binds 0.0.0.0 and drives a robot
# with no login of its own. Generated if not supplied, so there is no way to
# end up with an unprotected public console by forgetting a variable.
CONSOLE_KEY="${CONSOLE_KEY:-$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 24)}"

wait_url() {   # $1 = logfile -> prints the hostname once it RESOLVES
  local u=""
  for _ in $(seq 1 40); do
    u=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$1" 2>/dev/null | head -1)
    [ -n "$u" ] && break
    sleep 1
  done
  [ -z "$u" ] && return 1

  # Do NOT touch the hostname until DNS has it. cloudflared prints the name a
  # couple of seconds before the record exists, and on macOS one early lookup
  # gets the NXDOMAIN cached by the system resolver - after which curl fails
  # with "could not resolve host" for minutes while `dig` answers perfectly.
  # That looked exactly like a dead tunnel and was self-inflicted. Flushing
  # needs sudo; not asking in the first place does not.
  local host="${u#https://}"
  for _ in $(seq 1 45); do
    if [ -n "$(dig +short "$host" 2>/dev/null | head -1)" ]; then
      sleep 2                      # let the edge finish registering
      echo "$u"; return 0
    fi
    sleep 1
  done
  echo "$u"                        # resolved or not, hand it back and let the checks judge
  return 0
}

tool() {       # $1 = base url, $2 = name, $3 = json args (default {})
  # Build the args in a variable. Writing ${3:-{}} inline emits a literal
  # \{\} under brace expansion, which produced invalid JSON - and the
  # simulator used to die on that rather than return 400.
  local args="${3:-}"
  [ -z "$args" ] && args='{}'
  curl -s -m 40 -X POST -H 'Content-Type: application/json' \
    -d "{\"message\":{\"type\":\"tool-calls\",\"toolCallList\":[{\"id\":\"up\",\"name\":\"$2\",\"arguments\":$args}]}}" \
    "$1/vapi"
}

echo "==> stopping anything already running"
pkill -f 'robot-sim/server.mjs'      2>/dev/null
pkill -f 'robot-console/server.mjs'  2>/dev/null
pkill -f 'cloudflared tunnel --url'  2>/dev/null
sleep 2

echo "==> simulator on :$SIM_PORT"
nohup node tools/robot-sim/server.mjs > "$RUN/sim.log" 2>&1 &
for _ in $(seq 1 10); do
  tool "http://127.0.0.1:$SIM_PORT" robot_status | grep -q result && break
  sleep 1
done

echo "==> tunnelling the simulator (Vapi has to reach it from the internet)"
nohup cloudflared tunnel --url "http://127.0.0.1:$SIM_PORT" > "$RUN/tun-sim.log" 2>&1 &
SIM_URL=$(wait_url "$RUN/tun-sim.log") || { echo "sim tunnel never came up"; exit 1; }
echo "    $SIM_URL"

echo "==> pointing the assistant at it (all tools, no robot)"
( cd frontend && SIM_URL="$SIM_URL" node scripts/vapi/concierge.mjs sync "$ASSISTANT" ) \
  | sed 's/^/    /' || { echo "assistant sync failed"; exit 1; }

echo "==> standing the simulated robot up"
# The simulator refuses gestures while lying down, exactly as the real one
# does. Left down, every gesture in the demo fails for a reason that looks
# like a bug and is not.
tool "http://127.0.0.1:$SIM_PORT" robot_command '{"command":"stand_up"}' >/dev/null
sleep 1

echo "==> consoles, both pointed at the simulator"
# The console refuses to start without D1_ROBOT_TOKEN even when it is aimed at
# the simulator, which authenticates nothing. A placeholder satisfies it and
# keeps the real token out of this script - there is no robot here to reach.
nohup env HOST=0.0.0.0 PORT=$PUB_PORT CONSOLE_KEY="$CONSOLE_KEY" \
  D1_ROBOT_TOKEN=simulator-no-robot \
  ROBOT_URL="http://127.0.0.1:$SIM_PORT" VAPI_ASSISTANT_ID="$ASSISTANT" \
  node tools/robot-console/server.mjs > "$RUN/pub.log" 2>&1 &
nohup env PORT=$LOC_PORT D1_ROBOT_TOKEN=simulator-no-robot \
  ROBOT_URL="http://127.0.0.1:$SIM_PORT" VAPI_ASSISTANT_ID="$ASSISTANT" \
  node tools/robot-console/server.mjs > "$RUN/loc.log" 2>&1 &
sleep 3
for _ in $(seq 1 10); do
  curl -sf -m 3 -o /dev/null "http://127.0.0.1:$LOC_PORT/api/voice-config" && break
  sleep 1
done

echo "==> tunnelling the public console"
nohup cloudflared tunnel --url "http://127.0.0.1:$PUB_PORT" > "$RUN/tun-pub.log" 2>&1 &
PUB_URL=$(wait_url "$RUN/tun-pub.log") || { echo "console tunnel never came up"; exit 1; }

echo
echo "==> checking it actually works"
ok=1
# ?k= sets a cookie and redirects, so a check without a cookie jar follows the
# 302 to / with no credential and gets a 401 - which looks exactly like a
# rejected key and is not. Keep the jar.
JAR="$RUN/cookies.txt"; rm -f "$JAR"
code=$(curl -s -m 25 -L -c "$JAR" -o /dev/null -w '%{http_code}' "$PUB_URL/?k=$CONSOLE_KEY")
[ "$code" = 200 ] && echo "    phone page        OK" || { echo "    phone page        HTTP $code"; ok=0; }
curl -s -m 20 -b "$JAR" "$PUB_URL/api/status" | grep -q '"link_ok":true' \
  && echo "    phone -> robot    OK (simulated)" || { echo "    phone -> robot    FAILED"; ok=0; }
curl -s -m 10 "http://127.0.0.1:$LOC_PORT/api/voice-config" | grep -q "$ASSISTANT" \
  && echo "    assistant         Leukerbad greeter" || { echo "    assistant         WRONG"; ok=0; }
tool "$SIM_URL" list_offerings | grep -q 'Thermal bath' \
  && echo "    venue tools       OK" || { echo "    venue tools       FAILED"; ok=0; }
tool "$SIM_URL" do_gesture '{"gesture":"nod"}' | grep -q 'Performing' \
  && echo "    robot tools       OK (simulated)" || { echo "    robot tools       FAILED"; ok=0; }

cat <<MSG

------------------------------------------------------------------
  PHONE   $PUB_URL/?k=$CONSOLE_KEY
  LOCAL   http://127.0.0.1:$LOC_PORT
  logs    $RUN
------------------------------------------------------------------

  No Jetson, no robot. Movement is simulated: the console will report a
  robot standing and walking because the simulator says so.

  Audio comes out of whatever device runs the browser - the phone - not
  the robot's speaker.

  Quick tunnel hostnames die after a while WITHOUT the process exiting.
  When the greeter goes vague about prices, that is the sim tunnel; just
  run this script again.

  Back to the real robot, once the Jetson is up:
    cd frontend && SIM_URL=$SIM_URL \\
      REAL_ROBOT=https://d1-jetson.tail5ec076.ts.net/vapi \\
      D1_ROBOT_TOKEN=<token> node scripts/vapi/concierge.mjs sync $ASSISTANT
MSG
[ "$ok" = 1 ] || { echo "  SOMETHING ABOVE FAILED - see $RUN"; exit 1; }
