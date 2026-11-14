# human_perception — ROS 2 human detection (YOLO26)

2D person detection for ROS 2 Humble. The stack runs on a laptop webcam today and on the
**AGIBOT D1 Ultra** camera (RTSP) later. Moving to the D1 changes only the image source; the
detector node is the same. Inference always runs on the laptop or an external companion
computer, never on the robot.

> **Not a safety system.** This is a best-effort perception component. It is not
> safety-rated, it misses people (occlusion, distance, lighting, unusual poses), and it
> produces false positives. A missing or `false` detection must never be read as "no person
> is present". Do not use it to gate robot motion or any safety function.

```
webcam / D1 RTSP / video file            any ROS 2 camera driver
        │                                         │
  [video_source] ──► /camera/image_raw ◄──────────┘   (sensor_msgs/Image)
                            │
                     [human_detector]  (Ultralytics YOLO26, COCO "person")
                            ├─► /perception/humans/detections       vision_msgs/Detection2DArray
                            ├─► /perception/humans/present          std_msgs/Bool
                            ├─► /perception/humans/count            std_msgs/Int32
                            ├─► /perception/humans/annotated_image  sensor_msgs/Image (optional)
                            └─► /diagnostics                        diagnostic_msgs/DiagnosticArray
```

See [perception_architecture.md](perception_architecture.md) for the design (threading, QoS,
failure handling).

**Where things live in this repo:**
- `ros2_ws/src/human_perception/`: the ROS 2 package.
- `Makefile`, `docker-compose.yml`, `docker/`, `scripts/`: the Docker workflow, run from the
  repo root.
- `perception/detect.py`: a separate standalone tool, not ROS. It runs YOLO11 on the RTSP
  stream with a browser MJPEG view and JSON lines, and is useful for a quick look without ROS
  ([perception/README.md](../perception/README.md)).
- `agibot_d1_bridge`, which sits next to this package in `ros2_ws/src/`, handles robot motion.
  This stack never sends motion commands.

---

## Quick start with Docker (recommended, also on the robot PC)

You need Docker with the Compose plugin and `make`; nothing else is installed on the host.

```bash
make build                                 # once: ROS 2 Humble + YOLO26 image, yolo26n weights baked in
make up                                    # laptop webcam -> detector (headless)
make up rviz=true                          # ... plus rviz2 showing boxes and people count
make up source=d1-wifi rviz=true           # D1 camera, rtsp://192.168.234.1:8554/test
make up source=d1-ethernet                 # D1 camera, rtsp://192.168.168.168:8554/test
make up source=rtsp rtsp_url=rtsp://<host>:8554/<path>
make logs                                  # follow logs (perf line every 10 s)
make down                                  # stop everything
```

Other variables:
- `webcam=/dev/video2`: pick a different webcam.
- `model=yolo26s.pt`: switch model.
- `display=localhost:0.0`: send rviz to XLaunch/VcXsrv instead of the default display.
- `domain=5`: set `ROS_DOMAIN_ID`.
- `source=file file=/abs/clip.mp4`: run from a video file.

Run `make help` for the full list.

How the container is set up:
- **Networking.** It uses the host network and IPC, so `ros2 topic echo` on the host (or
  another machine on the same `ROS_DOMAIN_ID`) sees all topics.
- **rviz.** rviz2 renders in software inside the container, so it works with any X server.
  `make up rviz=true` runs `xhost +local:root` when `xhost` exists.
- **Webcams.** `/dev` is mounted with a cgroup rule for V4L2 devices, so cameras plugged in
  later are visible too.
- **GPU.** `make build torch=cu128` builds CUDA PyTorch wheels. Running on the GPU also needs
  the NVIDIA Container Toolkit, and that path is not wired up or tested yet.
- **Credentials.** An RTSP URL containing credentials is visible in `docker inspect` (it is
  passed as a launch argument). Prefer a credential-free URL on shared machines.

The native (venv) setup below is still supported for development.

## 1. Native installation (Ubuntu 22.04 + ROS 2 Humble)

```bash
# ROS 2 Humble itself: https://docs.ros.org/en/humble/Installation/Ubuntu-Install-Debs.html
sudo apt update
sudo apt install -y ros-humble-ros-base ros-humble-vision-msgs ros-humble-rqt-image-view \
                    python3-venv python3-colcon-common-extensions
```

### Create and activate the Python environment

Ultralytics and PyTorch come from pip. Use a venv created from the **system** Python 3.10
with `--system-site-packages` so `rclpy` and the message packages stay importable:

```bash
cd SafetyAI/ros2_ws                               # colcon workspace of this repository
/usr/bin/python3 -m venv --system-site-packages .venv
source .venv/bin/activate

# CPU-only PyTorch (small download, works everywhere):
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
# ...or a CUDA build instead, matching your driver (see https://pytorch.org/get-started/):
# pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128

pip install -r src/human_perception/requirements.txt
```

> Conda/miniconda users: deactivate conda first (`conda deactivate`). If a non-3.10 Python
> comes first on `PATH`, rclpy fails to import.

## 2. Build and source

```bash
source /opt/ros/humble/setup.bash
source .venv/bin/activate
python -m colcon build --symlink-install      # `python -m` runs colcon inside the venv
source install/setup.bash
```

Open a new terminal later? Repeat the three `source` lines (ROS, venv, `install/setup.bash`).

## 3. Run

The first start downloads `yolo26n.pt` (about 5 MB) into `~/.cache/human_perception/models/`.
To run offline, copy the weights there or set `model_path`.

### Laptop webcam

```bash
ros2 launch human_perception human_perception.launch.py source_type:=webcam webcam_device:=0
ros2 launch human_perception human_perception.launch.py rviz:=true     # + rviz2 viewer
# shortcut:
ros2 launch human_perception webcam.launch.py webcam_device:=0 publish_annotated_image:=true
```

### Laptop webcam under WSL2 (development)

WSL2 exposes no `/dev/video*`. With `source=wsl-webcam`, Windows `ffmpeg.exe` captures the
webcam and streams MJPEG through a named pipe (`scripts/wsl_webcam_pipe.sh`), and the
unchanged nodes read it (`source_type:=file`). No network port is opened and nothing is
recorded:

```bash
winget install Gyan.FFmpeg                                   # once, on Windows
make up source=wsl-webcam rviz=true                          # rviz on WSLg (:0)
make up source=wsl-webcam rviz=true display=localhost:0.0    # rviz on XLaunch / VcXsrv
make up source=wsl-webcam camera="USB Camera"                # another Windows camera
```

Camera names: `ffmpeg -list_devices true -f dshow -i dummy` (on Windows). `make down` stops
ffmpeg as well.

### AGIBOT D1 Ultra: camera stream facts

These were measured on the robot by the team; see `perception/README.md`.
- **Server.** MediaMTX on the robot serves `rtsp://<robot-ip>:8554/test`. It is started by
  `robot-launch`, which runs `/opt/app_launch/stream_4cams_rtsp.sh` on the robot.
- **Browser check without ROS.** WebRTC at `http://<robot-ip>:8889/test/` and HLS at
  `http://<robot-ip>:8888/test/`. Use these to tell network problems apart from pipeline
  problems.
- **Camera.** One camera (`/dev/video11`): 1920×1080 at 60 fps, H.264 at about 20 Mbit/s,
  every frame a keyframe.
- **Robot addresses.**
  - `192.168.234.1`: the robot's own Wi-Fi access point.
  - `192.168.168.168`: Ethernet.
  - `172.24.1.41`: what the robot got on a shared event Wi-Fi (see `docs/controls.md`).

  On any other network, pass `robot_ip=<ip>` to make, or `rtsp_url:=...` to the launch file.
- **Settings in `d1_rtsp.yaml`.** It publishes **960×540 at ≤15 fps** (`output_width`,
  `max_publish_fps`). A raw 1080p `bgr8` Image is 6.2 MB, and the detector letterboxes to
  640 px anyway, so detection loses nothing.

```bash
make up source=d1-wifi rviz=true                         # robot AP Wi-Fi (192.168.234.1)
make up source=d1-wifi robot_ip=172.24.1.41 rviz=true    # robot on another network
```

### AGIBOT D1 Ultra with the teammate's `d1_camera` driver

`d1_camera` (calibration-ready `camera_info`, used by the SLAM and depth packages) publishes
`/camera/image_raw` as best-effort, so the detector must subscribe best-effort too. Large
frames only arrive reliably when camera and detector share one container (Fast DDS shared
memory); across a host running another DDS vendor they are dropped.

```bash
make up source=d1-camera robot_ip=<ip>      # d1_camera + detector, one container
ros2 launch human_perception d1_camera.launch.py url:=rtsp://<ip>:8554/test   # native
```

Use this or `source=d1-wifi` (our own `video_source`), never both: they publish the same topic.

### AGIBOT D1 Ultra over Wi-Fi

```bash
ros2 launch human_perception human_perception.launch.py source_type:=rtsp \
    rtsp_url:=rtsp://192.168.234.1:8554/test source_params:=d1_rtsp.yaml
# shortcut (same URL as an overridable default):
ros2 launch human_perception d1_wifi.launch.py
```

### AGIBOT D1 Ultra over Ethernet

```bash
ros2 launch human_perception human_perception.launch.py source_type:=rtsp \
    rtsp_url:=rtsp://192.168.168.168:8554/test source_params:=d1_rtsp.yaml
# shortcut:
ros2 launch human_perception d1_ethernet.launch.py
```

Any other RTSP endpoint works the same way: `rtsp_url:=rtsp://<host>:<port>/<path>`. If the
URL contains credentials (`rtsp://user:pass@host/...`), the logs and diagnostics show only
`rtsp://***@host/...`.

### Detector only (existing camera driver)

No source node starts. The detector subscribes to whatever topic you remap onto
`/camera/image_raw`:

```bash
ros2 launch human_perception detector_only.launch.py image_topic:=/my_camera/image_raw
# or directly:
ros2 run human_perception human_detector --ros-args \
    --params-file install/human_perception/share/human_perception/config/detector_cpu.yaml \
    -r /camera/image_raw:=/my_camera/image_raw
```

The detector accepts raw `bgr8`, `rgb8`, `bgra8`, `rgba8`, `mono8`, `mono16`, `yuv422`
(UYVY) and `yuv422_yuy2` (YUYV). For compressed topics, republish them as raw first:
`ros2 run image_transport republish compressed raw --ros-args -r in/compressed:=/cam/image_raw/compressed -r out:=/cam/image_raw`.

### Common launch arguments

| Argument | Default | Meaning |
|---|---|---|
| `source_type` | `webcam` | `webcam`, `rtsp`, or `file` (video file, useful for headless tests) |
| `webcam_device` | `0` | index or `/dev/videoN` |
| `rtsp_url` | *(empty)* | required for `rtsp` |
| `detector_params` | `detector_cpu.yaml` | file in the package `config/` dir or an absolute path |
| `model_name` / `model_path` | from YAML | e.g. `model_name:=yolo26s.pt`, or a local file |
| `device` / `backend` | from YAML | e.g. `device:=cuda:0`, `backend:=onnx` |
| `publish_annotated_image` | from YAML (`false`) | `true` to publish the debug image |
| `image_topic` | `/camera/image_raw` | detector input (remap) |
| `launch_source` | `true` | `false` = detector only |
| `rviz` | `false` | `true` starts rviz2 with `rviz/human_perception.rviz` and enables the annotated image |

## 4. Inspect the output

```bash
# annotated stream (start with publish_annotated_image:=true)
ros2 run rqt_image_view rqt_image_view /perception/humans/annotated_image

ros2 topic echo /perception/humans/detections     # vision_msgs/Detection2DArray
ros2 topic echo /perception/humans/count
ros2 topic echo /perception/humans/present
ros2 topic echo /diagnostics                       # or: ros2 run rqt_runtime_monitor rqt_runtime_monitor
```

Each `Detection2D` holds one person:
- `bbox.center.position.{x,y}`: box centre in input-image pixels.
- `bbox.size_x` / `bbox.size_y`: box width and height.
- `results[0].hypothesis.class_id`: `"person"`.
- `results[0].hypothesis.score`: confidence.

The header (stamp and `frame_id`) is copied from the input image. Use it to match detections to
frames and to measure latency. `Detection2D.id` is empty because nothing is tracked.

Both nodes log a performance line every `perf_log_period_s` (default 10 s), for example:

```
perf: in=6.3fps proc=5.8fps infer mean=72.8ms p95=82.6ms age mean=178.9ms p95=219.9ms dropped=5 malformed=0 errors=0 people=4
```

On `/diagnostics`:
- `human_perception: human_detector` reports received and processed FPS, inference
  latency (mean and p95), frame age (mean and p95), dropped, malformed and error counts, and the
  last count.
- `human_perception: video_source` reports connection state, capture and publish FPS,
  reconnects, and the last error.

## 5. Parameters

All defaults are in `config/*.yaml`, and any value can be overridden with another YAML or
`-p name:=value`.

**human_detector** (`config/detector_cpu.yaml`):

| Parameter | Default | Notes |
|---|---|---|
| `model_name` | `yolo26n.pt` | `yolo26s.pt` is the slower, more accurate option |
| `model_path` | `""` | local file/dir; overrides `model_name` |
| `weights_dir` | `~/.cache/human_perception/models` | where `model_name` is downloaded |
| `backend` | `pytorch` | `pytorch` · `onnx` · `openvino` · `tensorrt` (model must match) |
| `device` | `cpu` | `cpu`, `cuda:0`, `0`, `intel:cpu`, `intel:gpu`, `mps` |
| `half` | `false` | FP16 on GPU only |
| `confidence_threshold` | `0.45` | |
| `iou_threshold` | `0.7` | NMS IoU |
| `image_size` | `640` | multiple of 32; lower = faster, worse for small/distant people |
| `max_inference_fps` | `10.0` (`0` = unlimited) | caps CPU load; extra frames are dropped |
| `input_topic` | `/camera/image_raw` | |
| `input_reliability` | `reliable` in YAML (`best_effort` in code) | see [QoS and large images](#qos-and-large-images) |
| `publish_annotated_image` | `false` | annotated image is rendered only when someone subscribes |
| `diagnostics_enabled` | `true` | |
| `output_class_id` | `person` | string written to `hypothesis.class_id` |

**video_source** (`config/video_source.yaml`, `config/d1_rtsp.yaml`):
- Source selection: `source_type`, `webcam_device`, `rtsp_url`, `file_path`.
- Capture: `capture_backend` (`auto`/`ffmpeg`/`gstreamer`/`v4l2`), `rtsp_transport`
  (`tcp`/`udp`), `width`/`height`/`fps` (webcam), `max_publish_fps`.
- Output: `frame_id`, `output_topic`, `publish_reliability`.
- Reconnection: `open_timeout_s`, `read_timeout_s`, `stale_frame_timeout_s`,
  `reconnect_initial_delay_s` (0.5), `reconnect_max_delay_s` (10), and
  `reconnect_backoff_multiplier` (2).

Parameters are validated at start-up. An invalid value stops the node with an
`invalid configuration: ...` message instead of letting it run misconfigured.

## 6. Benchmark: YOLO26n vs YOLO26s

The benchmark captures one sample once and replays the identical frames to each model. Each
model runs in its own process, so its peak memory is reported separately. No ROS graph is
needed.

```bash
ros2 run human_perception human_perception_benchmark --source 0 --frames 200          # webcam 0
ros2 run human_perception human_perception_benchmark --source clip.mp4 --target-fps 15
ros2 run human_perception human_perception_benchmark --source 0 --device cuda:0 --imgsz 480
# options: --models yolo26n.pt yolo26s.pt --warmup 10 --conf 0.45 --backend pytorch
#          --output benchmark_results/benchmark.json   (git-ignored)
```

For each model it reports:
- input resolution and inference size;
- backend and device;
- warm-up count and processed frame count;
- mean and p95 inference latency;
- end-to-end FPS;
- peak RSS memory;
- person-detection count.

Results go to the console and to a JSON file. Frames stay in memory and a temporary file that
is deleted afterwards; nothing is kept.

Example run on the development machine (20-core laptop CPU under WSL2, PyTorch CPU, 60 frames
of the Ultralytics `bus.jpg` sample, imgsz 640). Use it only for orientation; measure on your
own hardware:

| model | mean ms | p95 ms | FPS | peak MB | persons |
|---|---|---|---|---|---|
| yolo26n.pt | 49.9 | 63.2 | 19.9 | 575 | 240 |
| yolo26s.pt | 98.3 | 121.1 | 10.1 | 655 | 240 |

Back-to-back runs on the same machine varied by about 25%, so measure more than once.

### Choosing a model

Neither model is better in general. Choose from your own measurements:
- **yolo26n**: when real-time latency or CPU limits dominate. This applies to most CPU-only
  laptops, to setups that also need headroom for other processes, and to high frame rates.
- **yolo26s**: when the benchmark shows your hardware sustains the FPS you need with headroom,
  and the extra accuracy (fewer misses on small, distant or partly hidden people) is worth
  roughly 2× the latency here.

Switching needs no code change: `model_name:=yolo26s.pt` (launch arg) or `model_name` in YAML.
Person counts on unlabelled footage show agreement between models, not accuracy. Judge
accuracy on labelled frames from your real environment.

## 7. Optional deployment backends

Engines are **never exported automatically**. Export them yourself on the machine that will
run them, then select the result in configuration:

```bash
cd ~/.cache/human_perception/models
# ONNX Runtime (CPU; GPU with onnxruntime-gpu):     pip install onnxruntime
yolo export model=yolo26n.pt format=onnx imgsz=640
#   -> backend:=onnx model_path:=~/.cache/human_perception/models/yolo26n.onnx

# OpenVINO (Intel CPU / iGPU / NPU):                pip install openvino
yolo export model=yolo26n.pt format=openvino imgsz=640
#   -> detector_params:=detector_openvino.yaml   (device: intel:cpu | intel:gpu)

# TensorRT (NVIDIA only; export ON the target GPU, engines are not portable
# across GPUs/driver/TensorRT versions):
yolo export model=yolo26n.pt format=engine imgsz=640 half=True device=0
#   -> backend:=tensorrt device:=cuda:0 model_path:=.../yolo26n.engine
```

`config/detector_gpu.yaml` is a CUDA + PyTorch FP16 profile:
`detector_params:=detector_gpu.yaml`. If CUDA is unavailable the node reports a clear model
error and does not silently fall back to CPU. Export with the same `imgsz` you configure.

## 8. Troubleshooting

**D1 stream: low FPS or smeared frames**
- In testing over a slow link (about 265 ms RTT), only about 13 of the 60 fps arrived, and
  some frames had H.264 decode errors. OpenCV still delivers such frames, so expect occasional
  smeared images.
- The real fix is on the robot: lower the bitrate, FPS and keyframe interval in
  `/opt/app_launch/stream_4cams_rtsp.sh`.
- The capture thread always keeps only the newest frame, so a slow link lowers the FPS but
  does not build up latency.

**Host-side `ros2 topic echo` or your own node gets `count`/`present` but no `detections`**
- On WSL2, UDP datagrams above the 1500-byte MTU are IP-fragmented and get lost between the
  container and host processes. Small messages arrive; a `Detection2DArray` with 4 or more
  boxes (about 1.9 KB) never does. Inside the container everything works.
- The image therefore ships `docker/fastdds_profile.xml` (`FASTRTPS_DEFAULT_PROFILES_FILE`):
  the usual shared-memory and UDP transports, with UDP datagrams capped at 1400 bytes so Fast
  DDS fragments them itself. Rebuild with `make build` if you have an older image.
- Verified on WSL2 with synthetic messages of 0/1/2/4/8 boxes (default host profile): all
  arrived; with the old image, 4 boxes never arrived.
- A stale `ros2 daemon` on the host can hang `ros2` commands; `kill -9` it.

**RTSP stream does not connect**
- Test outside ROS first: `ffprobe -rtsp_transport tcp rtsp://192.168.234.1:8554/test`, or
  open the URL in VLC. If that fails, the problem is the network, not this package.
- Make sure the laptop is on the D1's Wi-Fi (`192.168.234.x`) or on the wired subnet
  (`192.168.168.x`), and check firewall rules for TCP/UDP 8554.
- `rtsp_transport: tcp` is the default (robust over Wi-Fi). `udp` can lower latency on a clean
  Ethernet link.
- While disconnected, the source retries with bounded backoff (0.5 s → 10 s), and
  `/diagnostics` shows `DISCONNECTED: … retry #N in X s`.
- Advanced FFmpeg flags: set `OPENCV_FFMPEG_CAPTURE_OPTIONS` yourself; the node then won't
  override it.

**OpenCV / GStreamer**
- Ultralytics installs the pip `opencv-python` wheel, which has FFmpeg but no GStreamer.
  Check with `python -c "import cv2; print(cv2.getBuildInformation())" | grep -A1 GStreamer`.
- `capture_backend: ffmpeg` (the default for RTSP) works with the pip wheel.
- `capture_backend: gstreamer` needs a GStreamer-enabled OpenCV (for example
  `python3-opencv` from apt, used without the pip wheel shadowing it). The node refuses
  `gstreamer` with a clear error when support is missing.
- Webcam not found: `ls /dev/video*` and `v4l2-ctl --list-devices`. Try
  `webcam_device:=/dev/video2` or `capture_backend:=auto`. WSL2 does not expose USB webcams
  without `usbipd`.

**cv_bridge**
- This package does not use cv_bridge. Images are converted with a small built-in codec,
  because Humble's binary cv_bridge is built against numpy 1.21 and often breaks inside an
  Ultralytics venv ("module compiled against API version …").
- If your own nodes use cv_bridge in the same venv, keep `numpy<2` (as `requirements.txt`
  does).

**QoS and large images**
- With default kernel UDP buffers (`net.core.rmem_max` = 208 KB on stock Ubuntu), best-effort
  subscribers can lose every raw frame larger than about 0.5 MB. Each frame is split into many
  UDP fragments, one lost fragment drops the whole frame, and best-effort never resends.
- In testing, a best-effort subscriber received 0 frames of 1.6 MB while a reliable one
  received all of them.
- That is why the shipped YAML sets `input_reliability: reliable`. It still uses `KEEP_LAST`
  with depth 1 (newest frame only, no backlog).
- To use classic sensor-data QoS (`input_reliability: best_effort`), raise the kernel limits
  as described in the ROS 2 DDS tuning guide:
  `sudo sysctl -w net.core.rmem_max=2147483647 net.ipv4.ipfrag_time=3 net.ipv4.ipfrag_high_thresh=134217728`.
- `rqt_image_view` and `ros2 bag` subscribe reliably. The source publishes reliably by default
  so both kinds of subscriber work. If `ros2 topic echo` shows nothing, check
  `ros2 topic info -v <topic>` for incompatible QoS.

**Model download / loading**
- First use needs internet access to download from GitHub (Ultralytics assets).
- Offline: copy `yolo26n.pt` into `~/.cache/human_perception/models/`, or set
  `model_path:=/abs/path/yolo26n.pt`.
- A failed load keeps the node running and reports `ERROR model unavailable: …` on
  `/diagnostics` and in the log. It publishes **no** `present`/`count` messages, because
  "unknown" must not look like "no person".

**GPU**
- Check `python -c "import torch; print(torch.cuda.is_available())"`. If it prints `False`,
  install the CUDA PyTorch wheel that matches your driver (`nvidia-smi`), or use
  `detector_cpu.yaml`.
- FP16 (`half: true`) works only on CUDA.
- TensorRT engines must be built on the same GPU, driver and TensorRT version.

**Tooling**
- `colcon test` uses pytest < 8. Humble's `launch_testing` pytest plugin fails on pytest ≥ 8
  with `PluginValidationError … 'path'`.
- Entry points use the interpreter that ran colcon. Build with `python -m colcon` inside the
  venv.

## 9. Tests

```bash
source /opt/ros/humble/setup.bash && source .venv/bin/activate && source install/setup.bash
python -m colcon test --packages-select human_perception && python -m colcon test-result --verbose
# or quickly, from the package dir:
cd src/human_perception && python -m pytest test -q          # from ros2_ws/
make test                                                    # from repo root: inside the image
```

The tests mock the model, so they need no weights, network, GPU or camera. They cover:
- parameter validation;
- person-class filtering (by name, with COCO index 0 only as a fallback);
- `Detection2DArray` conversion;
- empty detections, and presence/count;
- header preservation;
- malformed frames;
- inference exceptions and an unavailable model;
- reconnection backoff and credential redaction;
- stale-frame dropping;
- an in-process source → detector pipeline on generated frames;
- the benchmark logic;
- flake8 and pep257.

There are deliberately no performance pass/fail thresholds, because hardware varies.

**Known limitation: D1 camera viewpoint**
- The camera is mounted low and looks upward, a view COCO-trained models rarely see.
- Expect missed people when only their legs are visible, and some false positives. The
  `person`-only filter already removes the non-person false positives seen in testing, such as
  "airplane".
- Fine-tuning on robot footage is the planned fix. It is not part of this milestone.

## 10. Privacy

This stack processes camera images of people.
- **No storage by default.** Nothing records, saves or uploads frames. Detections are
  bounding boxes and confidences only.
- **No identification.** No face recognition, re-identification, tracking IDs, demographic
  inference or biometric templates, and no cloud inference.
- **Images still leave the camera.** RTSP video crosses the network (Wi-Fi is often
  unencrypted at the application layer), and `/camera/image_raw` and the annotated topic are
  visible to anything on the same ROS domain. Use a dedicated `ROS_DOMAIN_ID`, a trusted
  network, or SROS2 where that matters.
- **Recording.** `ros2 bag record` of image topics creates personal data. Get consent, follow
  local law and site policy (for example GDPR), and set retention limits.
- Tell people when camera-based detection is active, especially in shared or public spaces.

## 11. Licensing note (not legal advice)

Ultralytics YOLO, including YOLO26 code and pretrained weights, is offered under **AGPL-3.0**,
with a separate commercial **Ultralytics Enterprise License**. AGPL-3.0 has copyleft and
network-use obligations that can apply to software that uses it, including software that is
distributed or offered as a service.

This package's `package.xml` declares `AGPL-3.0-only` as a conservative placeholder consistent
with that dependency. The project owner should confirm the final licence: an Enterprise licence
or a different deployment model may change it. Review the Ultralytics licence terms
(https://www.ultralytics.com/license) and consult your legal team before distributing or
commercialising. Nothing here is legal advice.

## Repository layout (perception parts)

```
Makefile  docker-compose.yml  .dockerignore     # make build / up / down / logs / shell / test
docker/   Dockerfile  entrypoint.sh  fastdds_profile.xml
scripts/  wsl_webcam_pipe.sh                    # WSL2 dev: Windows webcam -> named pipe
docs/     perception.md  perception_architecture.md
perception/  detect.py  README.md               # standalone (non-ROS) YOLO11 viewer
ros2_ws/src/human_perception/
  package.xml setup.py setup.cfg requirements.txt resource/
  human_perception/   config.py capture.py backoff.py metrics.py image_codec.py
                      detection.py model_backend.py annotate.py diagnostics.py
                      ros_conversions.py video_source_node.py human_detector_node.py benchmark.py
  launch/             human_perception, webcam, d1_wifi, d1_ethernet, detector_only (.launch.py)
  config/             video_source, d1_rtsp, detector_cpu, detector_gpu, detector_openvino,
                      wsl_webcam_pipe (.yaml)
  rviz/               human_perception.rviz
  test/               unit, node, pipeline, benchmark and lint tests
```
