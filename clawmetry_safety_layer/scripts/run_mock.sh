#!/usr/bin/env bash
# Everything on this machine (make mock): brain (relay + console + concierge) and the D1W edge on the mock twin with
# the venue walkthrough as the camera. Cosmos is used if it answers at COSMOS_URL (scripts/cosmos/serve_reason2.sh).
# Loopback only. Logs: $LOGDIR (default /tmp/leukerbad-mock). Ctrl-C stops both.
#   bash scripts/run_mock.sh            then open http://127.0.0.1:8780/ (log in with BRAIN_API_KEY from .env)
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "no .env: bash scripts/setup_brain.sh (or copy .env.example)"; exit 1; }
set -a; . ./.env; set +a
export G1_CONFIG="${G1_CONFIG:-config/d1w_mock.yaml}" D1W_TRANSPORT=mock FRAME_SOURCE="${FRAME_SOURCE:-video}"
export BIND_HOST=127.0.0.1 EDGE_PORT="${EDGE_PORT:-8766}" BRAIN_PORT="${BRAIN_PORT:-8780}"
export EDGE_URL="http://127.0.0.1:$EDGE_PORT" BRAIN_URL="http://127.0.0.1:$BRAIN_PORT"
LOGDIR="${LOGDIR:-/tmp/leukerbad-mock}"; mkdir -p "$LOGDIR"
PY=.venv/bin/python
if [ "$FRAME_SOURCE" = video ] && [ ! -f "${FRAME_VIDEO:-assets/venue/spa_walkthrough.mp4}" ]; then
  # the walkthrough video is private (guests are recognisable, docs/PRIVACY.md) and not shipped in the SafetyAI kit
  echo "no walkthrough video: running without camera (FRAME_SOURCE=none; set FRAME_VIDEO=<file> for your own)"
  export FRAME_SOURCE=none
fi
pids=()
cleanup() { for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done; wait 2>/dev/null || true; }
trap cleanup EXIT INT TERM
$PY -m brain >"$LOGDIR/brain.log" 2>&1 & pids+=($!)
$PY -m skills.edge >"$LOGDIR/edge.log" 2>&1 & pids+=($!)
for i in $(seq 1 60); do
  curl -sf -H "x-api-key: $BRAIN_API_KEY" "$BRAIN_URL/health" >/dev/null 2>&1 && curl -sf "$EDGE_URL/health" >/dev/null 2>&1 && break
  sleep 1
done
curl -sf "$EDGE_URL/health" >/dev/null || { echo "edge did not come up: $LOGDIR/edge.log"; tail -20 "$LOGDIR/edge.log"; exit 1; }
echo "brain  $BRAIN_URL   (console: open it in a browser, key = BRAIN_API_KEY)"
echo "edge   $EDGE_URL   ($(curl -s "$EDGE_URL/health"))"
echo "cosmos $(curl -s -H "x-api-key: $BRAIN_API_KEY" "$BRAIN_URL/health" | $PY -c 'import json,sys; print("ok" if json.load(sys.stdin)["cosmos"] else "DOWN (judge_unavailable -> CAUTION)")')"
echo "logs   $LOGDIR   Ctrl-C stops both."
if [ "${DETACH:-0}" = 1 ]; then trap - EXIT INT TERM; echo "${pids[*]}" > "$LOGDIR/pids"; exit 0; fi
wait
