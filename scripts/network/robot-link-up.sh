#!/bin/bash
# Robot side of the robot <-> Jetson Ethernet cable link.
#
# Gives eth0 its static address at boot, even before the cable has carrier (the Jetson
# may still be booting), so mc_ctrl can bind SDK_CLIENT_IP=192.168.168.168 when
# robot-launch starts it. Runs before robot-launch.service (see robot-link.service).
#
# Only touches eth0. Never touches Wi-Fi (wlan0), routes or the default gateway.
# Safe to run any number of times.
set -u

IFACE="${IFACE:-eth0}"
ADDR="${ADDR:-192.168.168.168/24}"
WAIT_DEV_SEC="${WAIT_DEV_SEC:-60}"

log() { echo "robot-link: $*"; }

for ((i = 0; i < WAIT_DEV_SEC; i++)); do
  [ -e "/sys/class/net/$IFACE" ] && break
  sleep 1
done
if [ ! -e "/sys/class/net/$IFACE" ]; then
  log "$IFACE not found after ${WAIT_DEV_SEC}s, giving up"
  exit 1
fi

ip link set dev "$IFACE" up

if ip -4 -o addr show dev "$IFACE" | grep -q "inet ${ADDR%/*}/"; then
  log "$IFACE already has ${ADDR%/*}"
else
  ip addr replace "$ADDR" dev "$IFACE" && log "assigned $ADDR to $IFACE"
fi

ip -4 -br addr show dev "$IFACE"
exit 0
