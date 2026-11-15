#!/usr/bin/env bash
# Source ROS + workspace. Without arguments, start the perception stack using the
# environment variables set by docker-compose / the Makefile; only non-empty values are
# passed (ros2 launch rejects empty "name:=" arguments).
set -e
source /opt/ros/humble/setup.bash
source /ws/install/setup.bash

if [[ $# -gt 0 ]]; then
  exec "$@"
fi

if [[ "${SOURCE_TYPE:-}" == "d1_camera" ]]; then
  [[ "${FOLLOW:-false}" == "true" ]] \
    && echo "follow=true is not supported with source=d1-camera yet; starting without d1_follow"
  # Teammate's d1_camera driver + detector in this container (RTSP_URL = robot stream).
  args=()
  [[ -n "${RTSP_URL:-}" ]] && args+=("url:=${RTSP_URL}")
  [[ -n "${DETECTOR_PARAMS:-}" ]] && args+=("detector_params:=${DETECTOR_PARAMS}")
  [[ -n "${MODEL_NAME:-}" ]] && args+=("model_name:=${MODEL_NAME}")
  [[ -n "${DEVICE:-}" ]] && args+=("device:=${DEVICE}")
  exec ros2 launch human_perception d1_camera.launch.py rviz:="${RVIZ:-false}" "${args[@]}"
fi

args=()
add() { [[ -n "$2" ]] && args+=("$1:=$2") || true; }
add source_type     "${SOURCE_TYPE:-webcam}"
add webcam_device   "${WEBCAM_DEVICE:-}"
add rtsp_url        "${RTSP_URL:-}"
add file_path       "${FILE_PATH:-}"
add source_params   "${SOURCE_PARAMS:-}"
add detector_params "${DETECTOR_PARAMS:-}"
add model_name      "${MODEL_NAME:-}"
add device          "${DEVICE:-}"
add rviz            "${RVIZ:-false}"
if [[ "${FOLLOW:-false}" == "true" ]]; then
  # follow.launch.py starts perception itself and owns rviz (follow.rviz).
  add auto_lock "${AUTO_LOCK:-false}"
  add embedder  "${EMBEDDER:-}"
  exec ros2 launch d1_follow follow.launch.py "${args[@]}"
fi
exec ros2 launch human_perception human_perception.launch.py "${args[@]}"
