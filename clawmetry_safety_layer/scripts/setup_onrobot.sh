#!/usr/bin/env bash
# Edge fallback B (no team PC): run OUR edge on the D1 Ultra-W's own computer (ssh firefly@172.24.1.41, aarch64).
# Documented fallback, tested only with D1W_TRANSPORT=mock. Run ON the robot, from a copy of this repo:
#   bash scripts/setup_onrobot.sh            # venv under /home/firefly/clawmetry, edge extra, .env from .env.example
#   bash scripts/setup_onrobot.sh --run      # start the edge pinned to cores 0-3 with nice (motion control is on 7)
#
# Transport sdk needs the robot to talk to itself (SDK doc 5.7), done ONCE by the operator, with a reboot:
#   sudo cp /opt/export/config/sdk_config.yaml /opt/export/config/sdk_config.yaml.bak_onrobot
#   sudo sed -i 's/^\(\s*target_ip:\s*\).*/\1"172.24.1.41"/' /opt/export/config/sdk_config.yaml   # = the robot's own IP
#   sudo reboot
# REVERT (back to the team PC): sudo cp /opt/export/config/sdk_config.yaml.bak_onrobot /opt/export/config/sdk_config.yaml
#   (or the team's own backup sdk_config.yaml.bak_pre_ros) and reboot.
# Only ONE SDK client may run: stop the team's ROS bridge first (the edge refuses if the SDK port is taken).
set -euo pipefail
cd "$(dirname "$0")/.."
PREFIX="${ONROBOT_PREFIX:-/home/firefly/clawmetry}"
VENV="$PREFIX/venv"
PY310="$(command -v python3.10 || true)"   # VERIFY on site: the SDK binding is cp310 only
[ -n "$PY310" ] || { echo "python3.10 not found on the robot: the D1 SDK binding needs CPython 3.10"; exit 1; }
if [ "${1:-}" = "--run" ]; then
  export G1_CONFIG="${G1_CONFIG:-config/d1w.yaml}" D1W_TRANSPORT=sdk FRAME_SOURCE="${FRAME_SOURCE:-rtsp}"
  export ROBOT_RTSP="${ROBOT_RTSP:-rtsp://127.0.0.1:8554/test}"
  export AGIBOT_SDK_DIR="${AGIBOT_SDK_DIR:?set AGIBOT_SDK_DIR to the agibot_D1_Edu-Ultra checkout on the robot}"
  exec taskset -c 0-3 nice -n 10 "$VENV/bin/python" -m skills.edge
fi
mkdir -p "$PREFIX"
[ -x "$VENV/bin/python" ] || "$PY310" -m venv "$VENV"
"$VENV/bin/python" -m pip install -q --upgrade pip
"$VENV/bin/python" -m pip install -q -e ".[edge]"
[ -f .env ] || { cp .env.example .env; chmod 600 .env; echo "wrote .env: fill in the keys (same as the brain's)"; }
# reduced perception on the robot's CPU: keep perception.local_yolo off and set perception.hz: 1 in the config you
# run with (the CLIP scene classifier is optional and not installed by the edge extra)
"$VENV/bin/python" -c "import bridge.agibot_d1w_bridge, safety.fsm, perception.pipeline" && echo "ok: imports"
echo "next: the sdk_config.yaml change above (once), then: bash scripts/setup_onrobot.sh --run"
