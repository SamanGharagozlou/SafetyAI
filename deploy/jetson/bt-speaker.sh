#!/usr/bin/env bash
# Pair, trust, connect and route audio to the robot's Bluetooth speaker.
#
#   bash bt-speaker.sh                      # default speaker, AKA SPK5
#   bash bt-speaker.sh 09:0E:55:AD:E6:4C
#   bash bt-speaker.sh --scan               # find a speaker's MAC
#
# PUT THE SPEAKER IN PAIRING MODE FIRST (hold its Bluetooth button until it
# chirps / the LED flashes fast). Without that, every attempt dies with
# `br-connection-page-timeout`, which means "I paged it and nothing answered" -
# not "the software is broken".
#
# WHY THE BOND KEEPS VANISHING: most cheap speakers hold ONE bond. Pairing the
# speaker to a phone silently invalidates the Jetson's, which then shows the
# confusing state `Trusted: yes, Paired: no` - the Jetson still trusts a device
# whose link key no longer exists. It cannot be bonded to both at once, so
# decide which one is the robot's mouth and leave it there.
#
# A2DP IS OUTPUT ONLY. This gives the robot a voice, never ears. For a
# microphone, use a USB mic on the spare port - the HSP/HFP profile would
# technically work but it is 8-16 kHz and flaky, which is no basis for an STT
# pipeline.
set -uo pipefail

export XDG_RUNTIME_DIR="/run/user/$(id -u)"
MAC="${1:-09:0E:55:AD:E6:4C}"

if [[ "$MAC" == "--scan" ]]; then
  echo "scanning 15s - put the speaker in pairing mode now"
  bluetoothctl --timeout 15 scan on 2>/dev/null | grep -iE 'speaker|spk|audio|soundcore|jbl|bose' | sort -u
  exit 0
fi

say() { printf '\n== %s\n' "$*"; }

say "bluetooth service"
systemctl is-active bluetooth || { echo "bluetooth is down: sudo systemctl start bluetooth"; exit 1; }

say "pulseaudio bluez modules"
# These are NOT persistent across a reboot, so load them every time. Loading an
# already-loaded module is a harmless no-op.
for m in module-bluetooth-policy module-bluetooth-discover; do
  pactl load-module "$m" >/dev/null 2>&1 && echo "  loaded $m" || echo "  $m already loaded"
done

say "powering the adapter and discovering"
bluetoothctl power on >/dev/null 2>&1
bluetoothctl agent NoInputNoOutput >/dev/null 2>&1
bluetoothctl default-agent >/dev/null 2>&1
# A speaker that is not in the adapter's cache cannot be paged, so sweep first.
bluetoothctl --timeout 8 scan on >/dev/null 2>&1

PAIRED=$(bluetoothctl info "$MAC" 2>/dev/null | awk -F': ' '/Paired/{print $2}')
if [[ "$PAIRED" != "yes" ]]; then
  say "pairing $MAC"
  bluetoothctl pair "$MAC" 2>&1 | tail -2 | sed 's/^/  /'
fi
bluetoothctl trust "$MAC" >/dev/null 2>&1

say "connecting"
for attempt in 1 2 3; do
  OUT=$(bluetoothctl connect "$MAC" 2>&1 | tail -1)
  echo "  attempt $attempt: $OUT"
  [[ "$OUT" == *"Connection successful"* ]] && break
  sleep 3
done

sleep 3
say "state"
bluetoothctl info "$MAC" 2>/dev/null | grep -iE 'Name|Paired|Trusted|Connected' | sed 's/^/  /'

CARD=$(pactl list cards short 2>/dev/null | awk '/bluez/{print $2}' | head -1)
if [[ -z "$CARD" ]]; then
  cat <<'MSG'

  No bluez card, so the speaker is not actually connected.

  Almost always one of:
    - the speaker is not in pairing mode   -> hold its BT button, re-run
    - it is still bonded to a phone        -> turn Bluetooth off on the phone first
    - it is off, or further than ~10 m away
MSG
  exit 1
fi

say "routing audio to it"
pactl set-card-profile "$CARD" a2dp_sink 2>&1 | sed 's/^/  /'
sleep 2
SINK=$(pactl list sinks short 2>/dev/null | awk '/bluez/{print $2}' | head -1)
if [[ -z "$SINK" ]]; then echo "  card present but no a2dp sink - profile refused"; exit 1; fi
pactl set-default-sink "$SINK"
pactl set-sink-volume "$SINK" 85%
pactl set-sink-mute "$SINK" 0
echo "  default sink -> $SINK  @85%"

say "speaking a test line"
if command -v spd-say >/dev/null; then
  spd-say -w "Bluetooth speaker connected. The robot has a voice." 2>&1 | sed 's/^/  /'
  echo "  (if you heard nothing, the sink is set but the speaker is muted or asleep)"
else
  echo "  spd-say not installed: sudo apt-get install -y speech-dispatcher"
fi

cat <<'MSG'

NOTE, and this is the part that surprises people: setting this sink does NOT
move the VOICE AGENT's audio here. The Vapi call runs in whichever browser
dials it - today that is the phone, so the phone is both speaker and mic. This
sink only carries audio produced ON the Jetson. For the robot itself to speak
and listen, something on this box has to hold the call, and it needs a USB mic.
MSG
