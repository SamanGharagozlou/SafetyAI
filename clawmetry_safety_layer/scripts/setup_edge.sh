#!/usr/bin/env bash
# Edge setup: the venue PC next to the D1 Ultra-W (Ubuntu 22.04, ROS 2 Humble, Python 3.10). Idempotent; no sudo.
#
#   bash scripts/setup_edge.sh                 # then: make edge
#
# * venv .venv on python3.10 with --system-site-packages when ROS 2 is sourced, so rclpy and the std message
#   packages come from /opt/ros/humble (they are not on PyPI). numpy stays 1.26 there (Humble is built against 1.x).
# * our package with the `edge` extra (+ dev for the self-check tests); SCENE=1 adds CLIP (CPU torch), YOLO=1 adds
#   the local YOLO fallback.
# * .env with fresh keys if none exists (copy EDGE_API_KEY and BRAIN_API_KEY to the brain's .env too).
# Self-check: imports, the d1w bridge / safety / perception tests in mock mode, and `ros2 topic list` if sourced.
source "$(dirname "$0")/setup/lib.sh"
source "$(dirname "$0")/setup/envfile.sh"

step "Python"
PYBIN="${PYTHON:-python3.10}"
command -v "$PYBIN" >/dev/null || PYBIN=python3
"$PYBIN" - <<'P' || die "Python 3.10+ required"
import sys; assert sys.version_info >= (3, 10), sys.version
P
ok "$("$PYBIN" --version) ($PYBIN)"

step "ROS 2"
if [ -z "${ROS_DISTRO:-}" ] && [ -f /opt/ros/humble/setup.bash ]; then
  set +u; source /opt/ros/humble/setup.bash; set -u
fi
SYS=""
if [ -n "${ROS_DISTRO:-}" ] && "$PYBIN" -c "import rclpy" 2>/dev/null; then
  SYS="--system-site-packages"; ok "ROS 2 $ROS_DISTRO: rclpy importable (venv gets system site-packages)"
else
  warn "ROS 2 not sourced / rclpy missing: only D1W_TRANSPORT=mock (or sdk) will work on this machine"
fi

step "venv $VENV"
if [ ! -x "$PY" ]; then "$PYBIN" -m venv $SYS "$VENV" || die "venv failed (apt: python3.10-venv)"; ok created; else ok exists; fi
"$PY" -m pip install -q --upgrade pip
"$PY" -m pip install -q -e ".[edge,dev]"
ok "clawmetry-safety-layer[edge,dev]"
if [ "${SCENE:-0}" = 1 ]; then
  "$PY" -m pip install -q torch torchvision --index-url https://download.pytorch.org/whl/cpu && "$PY" -m pip install -q -e ".[scene]" && ok "CLIP scene classifier"
fi
if [ "${YOLO:-0}" = 1 ]; then "$PY" -m pip install -q -e ".[yolo]" && ok "local YOLO fallback"; fi

step ".env"
write_env_once
grep -q '^EDGE_API_KEY=change-me' .env && warn ".env still has the placeholder EDGE_API_KEY"

step "self-check"
check "imports" "$PY" -c "import bridge.agibot_d1w_bridge, safety.fsm, perception.observation, skills.api"
check "d1w / safety / perception tests (mock)" env G1_CONFIG=config/fake.yaml CLAWMETRY_OTLP=http://127.0.0.1:1 \
  "$PY" -m pytest -q -p no:cacheprovider tests/test_agibot_d1w_bridge.py tests/test_safety.py tests/test_perception.py
if [ -n "$SYS" ]; then check "ros2 topics visible" bash -c "timeout 10 ros2 topic list | head -50"; fi
finish "setup_edge"
cat <<'T'
next:
  1. team bridge:  ros2 launch agibot_d1_bridge bridge.launch.py local_ip:=172.24.0.239 robot_ip:=172.24.1.41
  2. our edge:     make edge          (skill server on BIND_HOST:8765, D1W_TRANSPORT from .env)
  docs/D1W-RUNBOOK.md has the 30-minute ladder.
T
