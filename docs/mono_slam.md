# Monocular camera + RTAB-Map (experiment)

Branch: `mono-slam`. Status: **wiring verified with a synthetic video; not yet run on the real robot stream.**

## Pieces

| Package | What it does |
|---|---|
| `agibot_d1_bridge` | Robot link. Publishes `/odom` and TF `odom -> base_link` from the SDK state. Exits if the SDK link is down 15 s; `bridge.launch.py` respawns it |
| `d1_camera` | `rtsp_camera`: RTSP H.264 -> `/camera/image_raw` (bgr8) + `/camera/camera_info`. Newest-frame reader, auto-reconnect, optical frame `camera_optical_frame`. A video file can be used as `url` (paced + looped) for tests |
| `d1_slam` | `mono_rtabmap.launch.py`: camera + static TFs (`base_link -> camera_link -> camera_optical_frame`) + RTAB-Map in **RGB-only** mode using `/odom` |

## Run

```bash
cd ros2_ws && source /opt/ros/humble/setup.bash && source install/setup.bash
# 1. robot link (keep running; respawns if the SDK link dies)
ros2 launch agibot_d1_bridge bridge.launch.py local_ip:=<pc-ip> robot_ip:=<robot-ip>
# 2. camera + RTAB-Map
ros2 launch d1_slam mono_rtabmap.launch.py rtabmap_viz:=true
#    useful args: url:=<rtsp or video file>  fps:=10  scale:=0.5  camera_info_file:=<calib.yaml>
#                 cam_x/cam_y/cam_z/cam_roll/cam_pitch/cam_yaw:=<camera mount>  delete_db:=false  localization:=...
```

Drive with `ros2 run agibot_d1_bridge keyboard_teleop` (robot standing, clear space).

## What RGB-only RTAB-Map can and cannot do

- Can: build a **pose graph of image nodes** from the SDK odometry + camera, detect visual loop closures, save/reload a database for place recognition.
- Cannot: build a metric 3D / occupancy map. RTAB-Map needs depth, stereo or LiDAR for that. A pure monocular map is not supported out of the box.
- To get a metric map, add a depth source: a monocular depth network (needs GPU + PyTorch) publishing `depth_topic`, or a real depth sensor / LiDAR.
- `Vis/EstimationType=2` (2D->2D) is experimental and unverified on this robot.

## Known issues / TODO

1. **Stream bandwidth.** The robot streams 1080p60 at ~20 Mbit/s, all keyframes. Over our Wi-Fi it arrives corrupted (H.264 decode errors). Fix on the robot: lower `FPS`/`BPS_*` in `/opt/app_launch/stream_4cams_rtsp.sh` (e.g. 15 fps, ~5 Mbit/s). **Not applied yet.**
2. **Camera calibration.** Intrinsics are a placeholder (ideal pinhole, 122 deg HFOV). The image is already lens-corrected by the robot's FEC, but a real calibration (`camera_calibration`, checkerboard) is needed.
3. **Camera mount.** The `cam_*` transform is a placeholder. The camera is low and looks upward; measure position and tilt.
4. **Timestamps.** Image stamps are PC receive time (no hardware stamp); use `stamp_offset_s` once latency is measured.
5. **SDK `getPosition` is unusable** (measured). At rest it drifts ~0.5 mm/s; when motion starts it jumps ~0.65-1.1 m instantly, then its x value freezes while y moves.
   The bridge therefore publishes its **own odometry**: SDK body velocity integrated at 50 Hz and rotated by the IMU yaw (`/odom`, TF `odom->base_link`), starting at (0,0,yaw 0) after a 1 s warm-up on each (re)connect. The raw SDK pose stays on `/odom_sdk` for comparison; `odom_source:=sdk` restores the old behaviour.
   Validation (out-and-back at 0.2 m/s, robot on the floor): integrated distance matches the velocity integral within 2% (0.559 vs 0.549 m forward, 0.866 vs 0.868 m back); sideways error 6-13 cm per leg; yaw drift ~1.6 deg. **Absolute scale is not yet checked against a tape measure** - the SDK reports ~0.18-0.24 m/s for a 0.2 m/s command.
6. **Standing -> moving takes ~2 s.** The first `move` after `stand_up` is refused (`0x3007`) while the SDK switches state (mode 1 -> 18); the bridge keeps retrying and motion starts after ~2 s. Expect a start delay in teleop and Nav2.
7. **SDK link** can die after Wi-Fi hiccups (`async_receive_from` error) and does not self-heal; the bridge now exits and the launch respawns it.
8. Setup: `ros-humble-rtabmap-ros`, `ros-humble-image-proc`, `ros-humble-camera-calibration` installed. `~/.local` NumPy 2.x conflicts with ROS OpenCV; `d1_camera` strips user site-packages itself.
