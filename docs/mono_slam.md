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
ros2 launch d1_slam mono_rtabmap.launch.py rviz:=true      # add rtabmap_viz:=true for RTAB-Map's own GUI
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

1. **Stream bandwidth - fixed on the robot.** The robot streamed 1080p60 at ~20 Mbit/s (all keyframes), which arrived corrupted over our Wi-Fi.
   `/opt/app_launch/stream_4cams_rtsp.sh` now defaults to `FPS=15`, `BPS_TARGET/MIN/MAX=5000000/3000000/6000000` (resolution, FEC lens correction and all-keyframe GOP unchanged; backup `stream_4cams_rtsp.sh.bak_pre_ros`).
   Result: clean decode (0 failed reads, ~9 decode-error lines in 10 s vs a flood), but Wi-Fi jitter still limits us to ~6-17 fps decoded, ~3-5 Hz published. Enough for RTAB-Map at 2 Hz. Applies only until the firmware/launch config is reset by an update.
   The video shares the Wi-Fi with the SDK link: if the SDK link drops while the robot is standing, it damps after ~3 s.
2. **Camera calibration (estimated).** `d1_camera/config/d1_camera_estimated.yaml`: f = 735 px, centre principal point, no distortion. Basis: the robot's own factory calibration
   (`/userdata/bak/calibration/calibration.yaml`: fx 734.7, fy 726.7, raw-lens distortion k1 -0.34) and the 70 deg VFOV spec; the streamed image is already lens-corrected (straight lines stay straight).
   Still an estimate (about +-10% in f): do a checkerboard calibration to confirm.
3. **Camera mount (estimated).** `base_link -> camera`: x = 0.28 m (front face of the vendor body mesh, which spans x -0.273..+0.282, y +-0.111, z -0.047..+0.090), z = 0.02 m,
   pitch = -0.28 rad (camera looks up ~16 deg: horizon appears 210 px below the image centre, f = 735 px). The vendor URDF has no camera link.
4. **Timestamps.** Image stamps are PC receive time (no hardware stamp); use `stamp_offset_s` once latency is measured.
5. **SDK `getPosition` is unusable** (measured). At rest it drifts ~0.5 mm/s; when motion starts it jumps ~0.65-1.1 m instantly, then its x value freezes while y moves.
   The bridge therefore publishes its **own odometry**: SDK body velocity integrated at 50 Hz and rotated by the IMU yaw (`/odom`, TF `odom->base_link`), starting at (0,0,yaw 0) after a 1 s warm-up on each (re)connect. The raw SDK pose stays on `/odom_sdk` for comparison; `odom_source:=sdk` restores the old behaviour.
   Validation (out-and-back at 0.2 m/s, robot on the floor): integrated distance matches the velocity integral within 2% (0.559 vs 0.549 m forward, 0.866 vs 0.868 m back); sideways error 6-13 cm per leg; yaw drift ~1.6 deg. **Absolute scale is not yet checked against a tape measure** - the SDK reports ~0.18-0.24 m/s for a 0.2 m/s command.
6. **Standing -> moving takes ~2 s.** The first `move` after `stand_up` is refused (`0x3007`) while the SDK switches state (mode 1 -> 18); the bridge keeps retrying and motion starts after ~2 s. Expect a start delay in teleop and Nav2.
7. **SDK link** can die after Wi-Fi hiccups (`async_receive_from` error) and does not self-heal; the bridge now exits and the launch respawns it.
8. Setup: `ros-humble-rtabmap-ros`, `ros-humble-image-proc`, `ros-humble-camera-calibration` installed. `~/.local` NumPy 2.x conflicts with ROS OpenCV; `d1_camera` strips user site-packages itself.

## Does IMU gravity / odometry fusion / neighbor refinement help? (offline replay, 2026-11-14)

Four replays of the same 385 s recorded run (separate ROS domains): A raw odometry, B heading-bias-corrected odometry,
C = B + IMU gravity constraint (`imu_gravity:=true`), D = B + `RGBD/NeighborLinkRefining` (`neighbor_refine:=true`).

| variant | nodes | loop closures accepted/rejected | occupied m² | stray % |
|---|---|---|---|---|
| A raw | 46 | 0 / 8 | 9.5 | 9.0 |
| B bias-fix | 33 | 0 / 19 | 11.4 | 9.7 |
| C gravity | 34 | 0 / 9 | 9.9 | 9.9 |
| D refine | 32 | 0 / 8 | 10.8 | 9.9 |

No variant closed a loop and the map metrics differ within run-to-run noise (replays are paced in real time, node counts
vary). Conclusion: none of the options gives a measurable gain on this run, so `imu_gravity` and `neighbor_refine` stay **off**
by default. The heading-bias correction (SDK yaw drifts about -9.4 deg/min at rest) is physically real and stays on in the bridge
(`heading_bias_comp`), but has not been verified live on the robot yet. Speed scale of the SDK velocity is still unmeasured
(needs a tape-measure test).
