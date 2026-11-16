#!/usr/bin/env bash
# The greeter's VOICE, on this Mac.
#
# Launches a dedicated Chrome holding the microphone and the speaker and
# running the Vapi call. It uses its own profile so it cannot disturb the
# browser you are working in, and it is launched with the microphone and
# autoplay already granted - so it needs no tap and comes back by itself
# after a restart. Requiring a human tap is fine for a person sitting at the
# machine and hopeless for something that has to be up when a guest arrives.
#
# Audio follows the MAC'S SYSTEM DEFAULTS, so set them first:
#   System Settings -> Sound -> Input: DJI mic, Output: the Bluetooth speaker
#
#   ./tools/robot-face/brain-mac.sh                    # uses the deployed Jetson
#   HOST=http://127.0.0.1:5180 ./tools/robot-face/brain-mac.sh
set -euo pipefail

HOST="${HOST:-https://d1-jetson.tail5ec076.ts.net:10000}"
KEY="${FACE_KEY:-}"
[[ -n "$KEY" ]] || { echo "FACE_KEY is not set. FACE_KEY=... $0" >&2; exit 1; }
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
[[ -x "$CHROME" ]] || { echo "no Chrome at $CHROME" >&2; exit 1; }

PROFILE="${PROFILE:-$HOME/.d1-brain-profile}"
URL="$HOST/face?k=$KEY&brain=1&auto=1"

echo "voice   -> $URL"
echo "profile -> $PROFILE   (separate, so your own Chrome is untouched)"
echo "audio   -> whatever macOS has as the default input and output right now:"
/usr/sbin/system_profiler SPAudioDataType 2>/dev/null \
  | awk '/Default Input Device: Yes/{print "          in : " name} /Default Output Device: Yes/{print "          out: " name} /^ {8}[^ ].*:$/{name=$0; sub(/^ +/,"",name); sub(/:$/,"",name)}' \
  || true

exec "$CHROME" \
  --user-data-dir="$PROFILE" \
  --use-fake-ui-for-media-stream \
  --autoplay-policy=no-user-gesture-required \
  --no-first-run --no-default-browser-check \
  --disable-background-timer-throttling \
  --disable-backgrounding-occluded-windows \
  --disable-renderer-backgrounding \
  --app="$URL"
