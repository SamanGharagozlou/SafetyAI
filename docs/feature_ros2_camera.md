# Feature: ROS 2 monocular camera on the Jetson Orin

Requirement: FR-P1 in [product_requirements.md](product_requirements.md). Owner: TBD. Status: **spec**.

## 1. Goal

Make the D1 front camera a first-class, reliable ROS 2 sensor on the Orin Nano: steady frame rate, low and known
latency, correct timestamps and intrinsics, health reporting, and outputs suited to every consumer (SLAM, detection,
privacy filter, remote viewing).

## 2. Current state

```
D1 firmware: camera -> ISP (lens correction) -> H.264 encode (1080p, 15 fps, 5 Mbit/s, all-keyframe)
   -> RTSP server :8554/test   (/opt/app_launch/stream_4cams_rtsp.sh, patched; firmware updates may reset it)
      -> Orin: d1_camera/rtsp_camera (Python, OpenCV + FFmpeg, CPU decode, TCP)
         -> /camera/image_raw (bgr8, 960x540, 15 Hz, BEST_EFFORT depth 2) + /camera/camera_info
```

Code: [ros2_ws/src/d1_camera/d1_camera/rtsp_camera.py](../ros2_ws/src/d1_camera/d1_camera/rtsp_camera.py).
Already good: newest-frame reader thread, auto-reconnect, stale-frame guard, `stamp_offset_s`, calibration YAML support,
video-file replay for tests.

Problems:

| # | Problem | Effect |
|---|---|---|
| P1 | CPU software decode of 1080p H.264 in Python/OpenCV | burns CPU the Orin Nano needs for Nav2/SLAM; caps FPS |
| P2 | Stream limited to 15 fps (was reduced for Wi-Fi) | fewer frames for SLAM/detection; the sensor can do 60 fps |
| P3 | Latency ~1.35 s measured over Wi-Fi; not yet measured over Ethernet | stale images smear maps; `stamp_offset_s` is a fixed guess |
| P4 | Stamps = receive time minus a constant; no hardware timestamp | visual-inertial SLAM needs accurate stamps |
| P5 | Intrinsics estimated (f ≈ 735 px, ±10 %), mount estimated | metric errors in depth/SLAM |
| P6 | Only raw `bgr8` output (~1.5 MB/frame) | unusable over Wi-Fi; no compressed topic for remote viewers |
| P7 | No `/diagnostics`; stats only in the log | operator cannot see camera health |
| P8 | Camera node not started at boot | FR-O3 |

## 3. Requirements

| ID | Requirement | Target (to verify) |
|---|---|---|
| CAM-1 | Publish `/camera/image_raw` + `/camera/camera_info` on the Orin from the robot stream over Ethernet | — |
| CAM-2 | Frame rate | ≥ 25 fps decode, publish rate configurable (default 15–30 Hz), 0 read failures over 10 min |
| CAM-3 | Glass-to-topic latency | median ≤ 200 ms, measured and documented; `stamp_offset_s` set from measurement |
| CAM-4 | CPU usage of the camera pipeline | ≤ 1 core at 960x540 @ 30 Hz (hardware decode) |
| CAM-5 | Reconnect after stream loss or robot reboot | first frame within 5 s of the stream returning, no restart of the node |
| CAM-6 | Stale-frame guard: never publish a frame older than `stale_timeout` | kept from current node |
| CAM-7 | Calibrated intrinsics from a checkerboard (`camera_calibration`), stored in `d1_camera/config/`, scaled with output size | reprojection error < 0.5 px |
| CAM-8 | `/diagnostics` with decode fps, publish Hz, age of last frame, failures, reconnects | OK/WARN/ERROR thresholds |
| CAM-9 | Sensor-data QoS on image topics (BEST_EFFORT, KEEP_LAST 1–2); documented for subscribers | — |
| CAM-10 | Raw `image_raw` stays on the Orin (host-local DDS / shared memory). Only the anonymized topic may be compressed and sent off-device | see [feature_privacy_anonymization.md](feature_privacy_anonymization.md) |
| CAM-11 | Same launch interface as today (`url`, `fps`, `scale`, `camera_info_file`, `stamp_offset_s`) plus `backend:=ffmpeg|gstreamer` | existing launch files keep working |
| CAM-12 | Works in the `d1-stack:humble-jp6` container and from a video file on a PC without a GPU (fallback backend) | CI/offline tests |
| CAM-13 | Starts at boot with the rest of the stack and restarts on crash | systemd or compose `restart: always` |

## 4. Options

### A. Hardware decode on the Orin (recommended first step)
Keep the robot's RTSP stream; replace CPU decode with NVDEC through GStreamer:

```
rtspsrc location=rtsp://192.168.168.168:8554/test protocols=tcp latency=0 drop-on-latency=true
  ! rtph264depay ! h264parse ! nvv4l2decoder ! nvvidconv
  ! video/x-raw,format=BGRx,width=960,height=540 ! videoconvert ! video/x-raw,format=BGR
  ! appsink drop=true max-buffers=1 sync=false
```

Implementation paths, smallest first:
1. In `rtsp_camera`, add a `gstreamer` backend: `cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)`. Needs OpenCV with GStreamer
   in the container (Ubuntu `python3-opencv` has it) **and** the NVIDIA GStreamer plugins (`nvv4l2decoder`, `nvvidconv`)
   available inside the container (L4T base image or mounted from the host, like CUDA in `run.sh`). Verify with
   `gst-inspect-1.0 nvv4l2decoder` inside the container.
2. If Python publish cost dominates at 30 Hz: a small C++ node (or an existing GStreamer->ROS 2 bridge such as `gscam`;
   availability for Humble/aarch64 to check) with intra-process / zero-copy publishing.
- No robot changes needed. Raise the robot stream back to 30 fps once Ethernet is the only consumer (edit
  `FPS=` in `stream_4cams_rtsp.sh`, keep the `.bak`).
- Remaining limits: H.264 encode latency on the robot, no hardware timestamps (P4).

### B. Forward the H.264 bitstream into ROS 2 without decoding
Publish the RTP payload as compressed video (e.g. `ffmpeg_image_transport` / `foxglove_msgs/CompressedVideo`) for
recording and remote viewing at ~5 Mbit/s with zero CPU. **Conflicts with privacy** (raw faces leave the Orin or get
recorded) and needs keyframe handling. Only for internal development rosbags with no guests present.

### C. ROS 2 node on the robot itself
Run a camera node on the D1's RK3588 (direct V4L2 / vendor camera API, Rockchip MPP hardware codec), publishing over DDS on
the Ethernet link. Could give hardware timestamps and lower latency, but needs vendor firmware access, survives updates
badly and has unknown ROS/DDS support on the robot. **Investigate only** (`/opt/app_launch/` inspection, camera device list,
whether `stream_4cams_rtsp.sh` exposes more cameras).

### D. External USB/CSI camera on the Orin
A camera mounted on the robot wired to the Orin gives hardware timestamps and full control (and could be a global-shutter or
stereo camera for better SLAM). Adds hardware and mounting. Fallback if A cannot reach the latency target.

**Decision:** implement A (path 1), measure, then decide on A2/C/D based on CAM-2/3/4 results.

## 5. Topic contract (target)

| Topic | Type | Notes |
|---|---|---|
| `/camera/image_raw` | `sensor_msgs/Image` bgr8 | on-device consumers only (SLAM, detector, privacy filter) |
| `/camera/camera_info` | `sensor_msgs/CameraInfo` | calibrated; `frame_id` `camera_optical_frame` |
| `/camera/image_anonymized` (+ `/compressed`) | `sensor_msgs/Image` / `CompressedImage` | produced by the privacy filter; the only image topic allowed off-device or in rosbags |
| `/diagnostics` | `diagnostic_msgs/DiagnosticArray` | hardware id `d1_front_camera` |

Image stamp = estimated **exposure** time (receive time − measured latency). Document the latency value and how it was measured
next to the parameter.

## 6. Latency measurement method

1. Show a millisecond clock of the Orin on a monitor in front of the camera (`ros2 run` a tiny clock display or a browser page).
2. Record `/camera/image_raw` with header stamps (`stamp_offset_s:=0`), read the displayed time with OCR or by eye on 20 frames.
3. Latency = receive stamp − displayed time. Repeat at 15 and 30 fps, ffmpeg vs gstreamer backend.
4. Cross-check with the existing method (camera yaw rate vs IMU gyro cross-correlation, `tools/offline_eval/latency_study.py`).

## 7. Work plan

| Step | Deliverable | Done when |
|---|---|---|
| 1 | Baseline on Orin over Ethernet with the current node | fps, CPU %, latency recorded in this doc |
| 2 | `gstreamer` backend in `rtsp_camera` + container support for NVIDIA GStreamer plugins | CAM-2, CAM-4 met at 15 fps |
| 3 | Robot stream to 30 fps over Ethernet | CAM-2 met at 30 fps; stream script change documented |
| 4 | Checkerboard calibration + mount measurement | CAM-7; YAML in `config/`, TF values updated in `d1_slam` launch |
| 5 | Diagnostics + boot integration | CAM-8, CAM-13 |
| 6 | Measured latency -> `stamp_offset_s` default | CAM-3 documented |
| 7 | Hand-off to privacy filter and SLAM tracks | consumers use the topic contract above |

## 8. Test plan

- Unit (no ROS): pipeline-string builder, intrinsics scaling, stale-frame logic.
- Replay: video file as `url` on a PC (ffmpeg backend) in CI.
- On Orin: `ros2 topic hz /camera/image_raw`, `ros2 topic delay`, `tegrastats` for CPU/GPU, 10 min soak, unplug/replug Ethernet, reboot robot.
- Consumers: `human_perception` detector and RTAB-Map still receive frames (QoS match).

## 9. Risks

- NVIDIA GStreamer plugins inside a non-L4T container may need extra mounts (`/usr/lib/aarch64-linux-gnu/gstreamer-1.0`, `libnvbufsurface`, ...). Alternative: build the image from an L4T/JetPack base.
- Firmware updates reset `stream_4cams_rtsp.sh`: add a check in the bring-up script that warns when the stream FPS differs from expected.
- The robot encodes all-keyframe at 5 Mbit/s; at 30 fps quality per frame drops. Tune `BPS_TARGET` for Ethernet.
