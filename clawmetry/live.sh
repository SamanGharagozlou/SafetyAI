#!/usr/bin/env bash
# Reach the ClawMetry instance that runs ON THE ROBOT, from a laptop.
#
#   bash clawmetry/live.sh open      # tunnel + open the dashboard in a browser
#   bash clawmetry/live.sh status    # one-line health check, no browser
#   bash clawmetry/live.sh push      # rsync this directory to the Orin and test
#   bash clawmetry/live.sh restart   # restart the collector on the Orin
#
# The collector binds 127.0.0.1 on the Orin on purpose: the only public thing
# is the Tailscale funnel in front of it, which Vapi needs. Everything else
# goes through SSH, so there is no second door to forget about.
#
# The dashboard key IS the robot token - one secret, so a token that leaks is
# one revocation and not a hunt. It is read off the Jetson at run time and
# never printed, written here, or put in a file in this repo.
set -uo pipefail
JET="${JET:-sepa@d1-jetson.tail5ec076.ts.net}"
LOCAL_PORT="${LOCAL_PORT:-8902}"
REMOTE_PORT="${REMOTE_PORT:-8901}"
CMD="${1:-open}"

tok() { ssh -o BatchMode=yes "$JET" \
  'sed -n "s/.*VAPI_TOKEN=[\"'"'"']\{0,1\}\([A-Za-z0-9_-]\{8,\}\).*/\1/p" ~/vapi-robot/start_server.sh | head -1'; }

tunnel() {
  curl -s -o /dev/null -m 3 "http://127.0.0.1:$LOCAL_PORT/api/state" && return 0
  pkill -f "ssh -N .*-L $LOCAL_PORT:" 2>/dev/null
  nohup ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=20 \
    -L "$LOCAL_PORT:127.0.0.1:$REMOTE_PORT" "$JET" >/dev/null 2>&1 &
  for _ in $(seq 1 15); do
    curl -s -o /dev/null -m 2 "http://127.0.0.1:$LOCAL_PORT/api/state" && return 0
    sleep 1
  done
  echo "tunnel to $JET:$REMOTE_PORT never came up" >&2; return 1
}

case "$CMD" in
  open)
    tunnel || exit 1
    K=$(tok); [ ${#K} -ge 8 ] || { echo "no token found on the Jetson" >&2; exit 1; }
    # The key goes in the first URL only; the page is handed a cookie and
    # every later request carries that instead.
    open "http://127.0.0.1:$LOCAL_PORT/?k=$K" 2>/dev/null \
      || echo "open this in a browser: http://127.0.0.1:$LOCAL_PORT/?k=<the robot token>"
    ;;
  status)
    tunnel || exit 1
    K=$(tok)
    curl -s -m 8 -H "Authorization: Bearer $K" "http://127.0.0.1:$LOCAL_PORT/api/state" \
    | python3 -c '
import json, sys
d = json.load(sys.stdin)
r, c = d["robot"], d["chain"]
print("chain  ", c["count"], "events,", c["reason"])
print("robot  ", "connected=" + str(r["connected"]),
      "moving=" + str(r["moving"]), "battery=" + str(r["battery_pct"]) + "%")
print("sources", "  ".join(k + " " + str(v["age_s"]) + "s" for k, v in d["sources"].items()))
print("breaches", len(d["breaches"]))'
    ;;
  push)
    # --delete keeps the Orin clean, but start.sh lives ONLY there (it reads the
    # robot token at run time and must never be in the repo). Excluding it is
    # not optional: a sync without this line deletes it and the collector will
    # not come back up.
    rsync -az --delete --exclude __pycache__ --exclude 'ledger.jsonl*' \
      --exclude '*.bak' --exclude start.sh --exclude docs/ \
      "$(dirname "$0")/" "$JET:/home/sepa/clawmetry-live/"
    ssh -o BatchMode=yes "$JET" 'cd ~/clawmetry-live && python3 -m pytest test_clawmetry.py -q 2>&1 | tail -2'
    ;;
  restart)
    # pkill pattern is bracketed so it cannot match this very command line -
    # an unbracketed one kills the shell running it and reports exit 255.
    ssh -o BatchMode=yes "$JET" 'pkill -f "python3 -u serv[e]r.py"; sleep 1; cd ~/clawmetry-live && (setsid ./start.sh > run.log 2>&1 < /dev/null &); sleep 5; tail -6 run.log'
    ;;
  *) echo "usage: $0 {open|status|push|restart}" >&2; exit 2 ;;
esac
