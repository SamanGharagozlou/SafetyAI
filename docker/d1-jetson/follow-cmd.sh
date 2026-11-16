#!/usr/bin/env bash
# Control a running follow.sh pipeline: follow-cmd.sh drive on | drive off | lock | release
# Anything else is run as a ros2 command, e.g. follow-cmd.sh ros2 topic echo /follow/state
if [[ "$1" == ros2 ]]; then cmd=("$@"); else cmd=(ros2 run d1_follow "$@"); fi
exec docker exec $([ -t 0 ] && echo -it) d1follow bash -c 'source /opt/ros/humble/setup.bash; source /ws/ros2_ws/install/setup.bash; exec "$@"' _ "${cmd[@]}"
