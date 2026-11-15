#!/bin/bash
# usage: run_variant.sh <domain> <name> <raw|corrected> <imu_gravity> <neighbor_refine>
D=$1; NAME=$2; MODE=$3; GRAV=$4; REFINE=$5
S=${EVAL_DIR:-$HOME/d1_maps/eval}; RUN=$(cat $S/current_run.txt)
export ROS_DOMAIN_ID=$D PYTHONNOUSERSITE=1
source /opt/ros/humble/setup.bash; source /home/sparc/SafetyAI/ros2_ws/install/setup.bash
LOG=$S/var_$NAME; mkdir -p $LOG
ros2 launch d1_depth depth.launch.py > $LOG/depth.log 2>&1 &  P1=$!
ros2 launch d1_slam rgbd_rtabmap.launch.py rviz:=false database_path:=${RUN}_var_$NAME.db delete_db:=true imu_gravity:=$GRAV neighbor_refine:=$REFINE > $LOG/rtab.log 2>&1 &  P2=$!
python3 $S/replay_frontend.py $MODE 1.35 > $LOG/front.log 2>&1 &  P3=$!
sleep 30
ros2 bag play $RUN.bag --topics /camera/image_raw /camera/camera_info /odom /imu /tf /tf_static \
   --remap /camera/image_raw:=/camera/image_raw_rec /camera/camera_info:=/camera/camera_info_rec /odom:=/odom_rec /imu:=/imu_rec /tf:=/tf_rec > $LOG/play.log 2>&1
sleep 8
ros2 run nav2_map_server map_saver_cli -f ${RUN}_var_${NAME}_map --ros-args -p save_map_timeout:=15000.0 > $LOG/saver.log 2>&1
kill -INT $P2 $P1 $P3 2>/dev/null; sleep 12
echo done > $LOG/DONE
