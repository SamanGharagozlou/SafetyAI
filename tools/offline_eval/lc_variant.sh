#!/bin/bash
# usage (inside the container): lc_variant.sh <domain> <name> '<json overrides>' [bag]
D=$1; NAME=$2; OV=${3:-{\}}; BAG=${4:-/root/d1_maps/jetson_big_map_bag}
OUT=/root/d1_maps/eval; mkdir -p $OUT
export ROS_DOMAIN_ID=$D PYTHONNOUSERSITE=1
source /opt/ros/humble/setup.bash; source /ws/ros2_ws/install/setup.bash
ros2 launch /ws/tools/offline_eval/lc_replay.launch.py db:=$OUT/$NAME.db overrides:="$OV" > $OUT/$NAME.rtab.log 2>&1 & P1=$!
python3 /ws/tools/offline_eval/lc_relay.py > $OUT/$NAME.relay.log 2>&1 & P2=$!
sleep 25
ros2 bag play $BAG --topics /camera/image_raw /camera/camera_info /camera/depth /odom /tf /tf_static --remap /tf:=/tf_rec > $OUT/$NAME.play.log 2>&1
sleep 8
ros2 run nav2_map_server map_saver_cli -f $OUT/${NAME}_map --ros-args -p save_map_timeout:=15000.0 > $OUT/$NAME.saver.log 2>&1
kill -INT $P1 $P2 2>/dev/null; sleep 10; echo done > $OUT/$NAME.DONE
