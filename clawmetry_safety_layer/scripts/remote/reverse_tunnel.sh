#!/usr/bin/env bash
# Fallback link when Tailscale is not possible on the team PC: an SSH tunnel from the TEAM PC to the Spark.
#   bash scripts/remote/reverse_tunnel.sh <user>@<spark-host-or-ip>
# Forward:  team PC 127.0.0.1:8780 -> Spark brain :8780,  team PC 127.0.0.1:8000 -> Spark Cosmos :8000
# Reverse:  Spark 127.0.0.1:8765 -> team PC edge :8765 (the brain posts verdicts back; the console reaches the edge)
# Then on the team PC: BRAIN_URL=http://127.0.0.1:8780 COSMOS_URL=http://127.0.0.1:8000/v1, edge BIND_HOST=127.0.0.1;
# on the Spark: EDGE_URL=http://127.0.0.1:8765. Keys (X-Api-Key) are still required on every call.
# Last resort for the brain's HTTP only: `cloudflared tunnel --url http://127.0.0.1:8780` (not installed here).
set -euo pipefail
TARGET="${1:?usage: reverse_tunnel.sh user@spark}"
exec ssh -N -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes \
  -L 127.0.0.1:8780:127.0.0.1:8780 -L 127.0.0.1:8000:127.0.0.1:8000 \
  -R 127.0.0.1:8765:127.0.0.1:8765 "$TARGET"
