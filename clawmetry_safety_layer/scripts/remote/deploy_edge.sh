#!/usr/bin/env bash
# Deploy the edge to the venue team PC over SSH (Tailscale first): rsync the repo WITHOUT .env and WITHOUT assets,
# run scripts/setup_edge.sh there, then print the three checks.
#
#   bash scripts/remote/deploy_edge.sh <user@host> [remote_dir]      # remote_dir default: leukerbad-g1 (in ~)
#   YOLO=1 bash scripts/remote/deploy_edge.sh <user@host>             # passed through to setup_edge.sh
#   LIST=1 bash scripts/remote/deploy_edge.sh                         # only list what would be copied (no SSH)
#
# The team PC must be on the tailnet first (docs/REMOTE-OPS.md):  sudo tailscale up --hostname=edge
# then deploy to <user>@edge. A venue-LAN address (172.24.x.x) only works from a machine on the venue LAN.
# Never copied: .env (each machine keeps its own keys; copy the three shared keys by hand), assets/ (the walkthrough
# video), venue stills, datasets/, export/, .venv/, .git/, caches, recordings, Isaac frames and USD (sim only).
set -euo pipefail
cd "$(dirname "$0")/../.."

EXCLUDES=(
  --include=/.env.example
  --exclude=/.env --exclude='/.env.*'
  --exclude=/assets/ --exclude=/datasets/ --exclude=/export/ --exclude=/third_party/
  --exclude=/docs/venue/ --exclude='*.mp4' --exclude='*.webm' --exclude='*.gif'
  --exclude=/.venv/ --exclude=/.git/ --exclude=__pycache__/ --exclude=.pytest_cache/ --exclude=.mypy_cache/
  --exclude=.ruff_cache/ --exclude='*.egg-info/'
  --exclude=/docs/isaac_frames/ --exclude=/docs/gallery_live/ --exclude=/docs/logs/ --exclude=/vendor_usd/ --exclude='*.usd' --exclude='*.usdc' --exclude='*.zip'
)

if [ "${LIST:-0}" = 1 ]; then
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  rsync -a --dry-run --out-format='%n' "${EXCLUDES[@]}" ./ "$tmp/"
  exit 0
fi

TARGET="${1:?usage: deploy_edge.sh <user@host> [remote_dir]}"
RDIR="${2:-leukerbad-g1}"
HOST="${TARGET#*@}"

case "$HOST" in
  172.24.*|192.168.*|10.*) echo "note: $HOST is a LAN address; from outside the venue use the tailnet name (e.g. ${TARGET%@*}@edge)";;
esac

echo "== SSH to $TARGET"
if ! ssh -o BatchMode=yes -o ConnectTimeout=8 "$TARGET" true; then
  echo "cannot reach $TARGET over SSH. Put the team PC on Tailscale first (sudo tailscale up --hostname=edge;"
  echo "tailscale ping edge from here), make sure your SSH key is in its authorized_keys, then retry." >&2
  exit 2
fi

echo "== rsync -> $TARGET:$RDIR (no .env, no assets)"
rsync -az --info=stats1 "${EXCLUDES[@]}" ./ "$TARGET:$RDIR/"

echo "== setup_edge.sh on $TARGET"
ssh -t "$TARGET" "cd '$RDIR' && YOLO='${YOLO:-0}' SCENE='${SCENE:-0}' bash scripts/setup_edge.sh"

cat <<EOF

== Done. Fill the team PC's .env (the same EDGE_API_KEY / BRAIN_API_KEY / COSMOS_API_KEY as the Spark; BRAIN_URL,
   BIND_HOST = its tailnet IP, D1W_TRANSPORT=ros2), start it with: ssh $TARGET 'cd $RDIR && make edge'
   Then the three checks (keys from your .env; nothing secret is printed here):

  1. edge state        curl -s -H "X-Api-Key: \$EDGE_API_KEY" http://$HOST:8765/state | head -c 400; echo
  2. perception        curl -s -H "X-Api-Key: \$EDGE_API_KEY" http://$HOST:8765/observation | head -c 400; echo
  3. brain from edge   ssh $TARGET 'cd $RDIR && set -a && . ./.env && curl -s -H "X-Api-Key: \$BRAIN_API_KEY" "\$BRAIN_URL/health"'; echo
EOF
