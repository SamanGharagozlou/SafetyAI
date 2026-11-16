#!/usr/bin/env bash
# Serve Cosmos-Reason2-2B with vLLM on the brain (this DGX Spark): OpenAI-compatible at http://<host>:8000/v1.
#   bash scripts/cosmos/serve_reason2.sh            # foreground; Ctrl-C stops it
#
# Binding: all interfaces (0.0.0.0) so the team PC reaches it over Tailscale (decided 2026-11-14), and therefore
# ALWAYS with --api-key COSMOS_API_KEY from .env: the script refuses to start without a key. Set COSMOS_HOST to the
# tailnet IP (tailscale ip -4) to narrow it further. Clients send "Authorization: Bearer $COSMOS_API_KEY".
# Never while Isaac Sim is open (GPU memory): refuses if an Isaac process runs (COSMOS_IGNORE_ISAAC=1 overrides).
# COSMOS_VENV (default ~/cosmos-reason2/.venv: vLLM 0.13, torch 2.9 cu130). The model is gated: HF_TOKEN must have
# accepted nvidia/Cosmos-Reason2-2B.
set -euo pipefail
cd "$(dirname "$0")/../.."
if [ -f .env ]; then set -a; . ./.env; set +a; fi
CV="${COSMOS_VENV:-$HOME/cosmos-reason2/.venv}"
HOST="${COSMOS_HOST:-0.0.0.0}"
PORT="${COSMOS_PORT:-8000}"
[ -n "${COSMOS_API_KEY:-}" ] || { echo "COSMOS_API_KEY is not set (.env): refusing to serve on $HOST without a key"; exit 1; }
if pgrep -f "isaac_server.py|isaacsim" >/dev/null 2>&1 && [ "${COSMOS_IGNORE_ISAAC:-0}" != 1 ]; then
  echo "Isaac Sim is running: stop it first (CLAUDE.md: never Cosmos and Isaac at once), or COSMOS_IGNORE_ISAAC=1."
  pgrep -af "isaac_server.py|isaacsim" | cut -c1-160 | head -3; exit 1
fi
[ -x "$CV/bin/vllm" ] || { echo "no vllm in $CV (set COSMOS_VENV)"; exit 1; }
export TRITON_PTXAS_PATH="${TRITON_PTXAS_PATH:-/usr/local/cuda/bin/ptxas}"
exec "$CV/bin/vllm" serve nvidia/Cosmos-Reason2-2B --host "$HOST" --port "$PORT" --api-key "$COSMOS_API_KEY" \
  --max-model-len 8192 --gpu-memory-utilization 0.35
