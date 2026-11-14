# Using a saved 2D map with the D1

Package: `ros2_ws/src/d1_localization`. Maps: `maps/<name>/{map.yaml,map.pgm}` (one folder per scan).
Current map: `maps/metacam_lite_2026-11-14/` (see its README for frame/origin and limits).

## What it does
- `nav2_map_server` publishes the map on `/map`.
- `initial_pose_localizer` publishes `map -> odom` from the pose you give it (launch args `x y yaw`, or RViz
  "2D Pose Estimate" any time). The robot then moves through the map by odometry. **No drift correction**
  (the D1 has no lidar topic here): re-place it when it has drifted.
- `ekf_node` (robot_localization) fuses `/odom` + `/imu` from the bridge into `odom -> base_link`,
  because the bridge publishes no TF. Disable with `use_ekf:=false` if another node publishes it.

## Setup (once)
```bash
sudo apt install ros-humble-nav2-map-server ros-humble-nav2-lifecycle-manager \
                 ros-humble-robot-localization ros-humble-rviz2        # or ros-jazzy-*
git checkout develop   # or this feature branch
cd ros2_ws && source /opt/ros/$ROS_DISTRO/setup.bash
colcon build --symlink-install --packages-select agibot_d1_bridge d1_localization
source install/setup.bash
```
`--symlink-install` lets the launch file find the repo's `maps/` folder. Otherwise set `D1_MAPS_DIR=<repo>/maps`
or pass `map:=/abs/path/map.yaml`.

## Run
1. Put the D1 on the floor at the physical spot where the MetaCam scan **started**, facing the way the scanner faced.
   (Otherwise note its real x/y/yaw in the map; `maps/.../map_grid.png` has a 1 m grid for reading positions.)
2. Terminal 1: `ros2 launch agibot_d1_bridge bridge.launch.py local_ip:=<pc-ip> robot_ip:=<robot-ip>`
3. Terminal 2: `ros2 launch d1_localization localize.launch.py` (add `x:= y:= yaw:=` if not at the origin)
4. RViz opens in the `map` frame: you should see the map and the TF tree `map -> odom -> base_link`.
5. Drive slowly (`ros2 run agibot_d1_bridge keyboard_teleop`) and check the `base_link` frame moves in RViz the
   way the robot moves in the room and stays inside free space.

## Quick test without the robot
```bash
ros2 launch d1_localization localize.launch.py x:=1.0 y:=2.0 yaw:=0.5      # terminal 1
python3 tools/fake_d1_odom.py                                              # terminal 2: fake /odom + /imu, drives forward
ros2 run tf2_ros tf2_echo map base_link                                    # terminal 3: starts at (1, 2), moves along 0.5 rad
```
In RViz the `base_link` frame should start at (1, 2) and slide across the map.

## Adding another map
Put it in `maps/<new-name>/` (`map.yaml` + `map.pgm`, plus a README with its origin) and launch with
`map:=<repo>/maps/<new-name>/map.yaml`. Build maps from a MetaCam recording with `tools/mcap_to_2d_map.py`.
