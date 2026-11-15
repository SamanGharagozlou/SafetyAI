#!/usr/bin/env bash
# Install the D1 bridge as a systemd unit, ordered before the agent, with a
# self-heal timer. Run ON THE JETSON, as a user with sudo.
#
#   scp -r deploy/jetson sepa@d1-jetson.tail5ec076.ts.net:~/
#   ssh sepa@d1-jetson.tail5ec076.ts.net 'sudo bash ~/jetson/install.sh'
#
# READ d1-bridge.service FIRST. Three values in it are assumptions that must be
# confirmed against this box before you install:
#   - the workspace path  /home/sepa/SafetyAI/ros2_ws
#   - the ROS distro      /opt/ros/humble
#   - the launch command  ros2 launch agibot_d1_bridge bridge.launch.py
# Install a unit with any of these wrong and you get a unit that restarts in a
# loop, which is worse than a hand-started process because nobody is watching.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $EUID -ne 0 ]]; then
  echo "run with sudo" >&2
  exit 1
fi

echo "==> verifying the bridge launch target exists"
if ! sudo -u sepa bash -lc 'source /opt/ros/humble/setup.bash && source /home/sepa/SafetyAI/ros2_ws/install/setup.bash && ros2 pkg prefix agibot_d1_bridge' >/dev/null 2>&1; then
  echo "FAILED: agibot_d1_bridge not found in that workspace. Fix the paths in" >&2
  echo "d1-bridge.service before installing - do not install a unit that cannot start." >&2
  exit 1
fi

echo "==> stopping any hand-started bridge"
# A hand-started bridge in a terminal will fight the unit for the SDK socket.
pkill -f 'ros2 launch agibot_d1_bridge' 2>/dev/null || true
sleep 2

echo "==> installing units"
install -m 0644 "$HERE/d1-bridge.service"          /etc/systemd/system/d1-bridge.service
install -m 0644 "$HERE/d1-bridge-watchdog.service" /etc/systemd/system/d1-bridge-watchdog.service
install -m 0644 "$HERE/d1-bridge-watchdog.timer"   /etc/systemd/system/d1-bridge-watchdog.timer
install -m 0755 "$HERE/d1-bridge-watchdog.sh"      /usr/local/bin/d1-bridge-watchdog.sh

echo "==> ordering the agent after the bridge"
mkdir -p /etc/systemd/system/d1-ros-agent.service.d
install -m 0644 "$HERE/d1-ros-agent.service.d-ordering.conf" \
  /etc/systemd/system/d1-ros-agent.service.d/ordering.conf

echo "==> making journald persistent so the next outage can be diagnosed"
# Currently volatile: there is no previous boot, so two separate power-loss
# post-mortems have already come back empty.
mkdir -p /var/log/journal
systemd-tmpfiles --create --prefix /var/log/journal >/dev/null 2>&1 || true

systemctl daemon-reload
systemctl enable --now d1-bridge.service
systemctl enable --now d1-bridge-watchdog.timer

echo "==> restarting the agent so it comes up AFTER the bridge"
systemctl restart d1-ros-agent.service

sleep 12
echo
echo "==> state"
systemctl is-active d1-bridge.service d1-ros-agent.service d1-vapi-server.service || true
systemctl is-enabled d1-bridge.service d1-ros-agent.service d1-vapi-server.service || true
echo
echo "==> link"
curl -s -m 8 http://127.0.0.1:8091/status || echo "(agent not answering yet)"
echo
echo
echo "Verify it actually survives a reboot - that is the whole point:"
echo "  sudo reboot      # then, once it is back:"
echo "  systemctl is-active d1-bridge d1-ros-agent d1-vapi-server"
echo "  curl -s http://127.0.0.1:8091/status"
