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
exec ros2 launch human_perception human_perception.launch.py "${args[@]}"
