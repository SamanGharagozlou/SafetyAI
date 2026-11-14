#!/usr/bin/env bash
# WSL2 only: stream the Windows laptop webcam into a named pipe (WSL2 has no /dev/video*).
# Windows ffmpeg.exe writes MJPEG into the FIFO; video_source reads it as source_type=file.
# No network port is opened, nothing is recorded.  Used by `make up source=wsl-webcam`.
#
#   scripts/wsl_webcam_pipe.sh start <fifo> [camera name]   (default "Integrated Webcam")
#   scripts/wsl_webcam_pipe.sh stop  <fifo>
set -euo pipefail
cmd="${1:?start|stop}"; fifo="${2:?fifo path}"; camera="${3:-Integrated Webcam}"
pidfile="${fifo}.pid"

stop() {
  if [[ -f "$pidfile" ]]; then kill "$(cat "$pidfile")" 2>/dev/null || true; rm -f "$pidfile"; fi
  rm -f "$fifo"
}

case "$cmd" in
  start)
    stop
    ff_win="$(cd /mnt/c && cmd.exe /c where ffmpeg 2>/dev/null | tr -d '\r' | head -n1 || true)"
    [[ -n "$ff_win" ]] || { echo "ffmpeg.exe not found on Windows: winget install Gyan.FFmpeg" >&2; exit 1; }
    ff="$(wslpath -u "$ff_win")"
    mkdir -p "$(dirname "$fifo")"
    mkfifo "$fifo"
    # ffmpeg blocks until the container opens the pipe; it exits when the reader closes it.
    (cd /mnt/c && exec setsid "$ff" -hide_banner -loglevel error -f dshow -video_size 640x480 \
        -framerate 30 -rtbufsize 16M -i "video=$camera" -an -c:v mjpeg -q:v 5 -f mjpeg - \
        > "$fifo") </dev/null >/dev/null 2>&1 &   # detach stdio: never hold the caller's pipes
    echo $! > "$pidfile"
    echo "webcam '$camera' -> $fifo"
    ;;
  stop) stop ;;
  *) echo "usage: $0 start|stop <fifo> [camera]" >&2; exit 2 ;;
esac
