#!/bin/bash
# Jetson side of the robot <-> Jetson Ethernet cable link.
#
# Makes sure the NetworkManager profile "robot-link" (192.168.168.10/24 on enP8p1s0)
# is active. NetworkManager already autoconnects it when the cable gets carrier; this
# keeps retrying in case the robot powers on later or the first attempt failed.
#
# Only touches the cable interface. Never touches Wi-Fi or the default route.
# Safe to run any number of times.
set -u

CON="${CON:-robot-link}"
IFACE="${IFACE:-enP8p1s0}"
ADDR="${ADDR:-192.168.168.10}"
RETRY_SEC="${RETRY_SEC:-5}"
MAX_TRIES="${MAX_TRIES:-120}"   # 120 x 5 s = 10 min

log() { echo "jetson-link: $*"; }

if ! nmcli -t -f NAME con show | grep -qx "$CON"; then
  log "NetworkManager profile '$CON' not found; create it first (see docs/controls.md)"
  exit 1
fi

for ((i = 1; i <= MAX_TRIES; i++)); do
  if ip -4 -o addr show dev "$IFACE" 2>/dev/null | grep -q "inet $ADDR/"; then
    log "$IFACE has $ADDR"
    exit 0
  fi
  if [ "$(cat "/sys/class/net/$IFACE/carrier" 2>/dev/null)" = "1" ]; then
    nmcli -w 15 con up "$CON" >/dev/null 2>&1 && log "activated '$CON'"
  elif (( i == 1 )); then
    log "waiting for cable carrier on $IFACE"
  fi
  sleep "$RETRY_SEC"
done

log "$IFACE still has no $ADDR after $((MAX_TRIES * RETRY_SEC))s"
exit 1
