#!/usr/bin/env bash
# Brain setup: this DGX Spark (relay, mission-control console, concierge, Cosmos judge). Idempotent; no sudo.
#
#   bash scripts/setup_brain.sh                # then: make brain
#
# * our .venv (setup_core.sh first) gets the `brain` + `edge` extras (the mock edge runs here too).
# * Cosmos Reason 2 stays in its own vLLM venv: COSMOS_VENV (default ~/cosmos-reason2/.venv); this only checks it.
# * .env with fresh keys if none exists.
source "$(dirname "$0")/setup/lib.sh"
source "$(dirname "$0")/setup/envfile.sh"
require_core

step "brain packages"
"$PY" -m pip install -q -e ".[dev,edge,brain]"
ok "leukerbad-g1[dev,edge,brain]"

step "Cosmos Reason 2 (vLLM venv)"
CV="${COSMOS_VENV:-$HOME/cosmos-reason2/.venv}"
if [ -x "$CV/bin/vllm" ]; then ok "vllm at $CV"; else warn "no vllm in $CV: set COSMOS_VENV (scripts/cosmos/serve_reason2.sh)"; fi

step "Tailscale"
if command -v tailscale >/dev/null && tailscale status >/dev/null 2>&1; then
  ok "tailnet up: $(tailscale ip -4 2>/dev/null | head -1) ($(hostname))"
else warn "tailscale not up: docs/REMOTE-OPS.md (fallback: scripts/remote/reverse_tunnel.sh)"; fi

step ".env"
write_env_once

step "self-check"
check "imports" "$PY" -c "import brain.relay, brain.console, concierge.agent, venue.facts"
check "brain / concierge / judge tests" env G1_CONFIG=config/fake.yaml CLAWMETRY_OTLP=http://127.0.0.1:1 \
  "$PY" -m pytest -q -p no:cacheprovider tests/test_brain.py tests/test_concierge.py tests/test_cosmos_judge.py
finish "setup_brain"
echo "next: bash scripts/cosmos/serve_reason2.sh (if not running), then make brain"
