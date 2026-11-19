#!/bin/bash
# Install the boot-time link service for the robot-Jetson Ethernet cable.
#   On the robot:   sudo ./install.sh robot
#   On the Jetson:  sudo ./install.sh jetson
# Installs and enables the service and runs it once now. Never reboots, never touches Wi-Fi.
set -euo pipefail

side="${1:-}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

case "$side" in
  robot|jetson) ;;
  *) echo "usage: sudo $0 robot|jetson" >&2; exit 2 ;;
esac
[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }

install -m 755 "$here/$side-link-up.sh" "/usr/local/sbin/$side-link-up.sh"
install -m 644 "$here/$side-link.service" "/etc/systemd/system/$side-link.service"
systemctl daemon-reload
systemctl enable "$side-link.service"

if [ "$side" = "robot" ]; then
  # Running it now is a no-op when eth0 already has its address.
  systemctl start robot-link.service
else
  systemctl restart jetson-link.service
fi
sleep 2
systemctl --no-pager status "$side-link.service" | head -12
