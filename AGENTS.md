# AGENTS.md — SafetyAI (D1 spa companion robot)

Guide for AI coding agents and new contributors. Read this first, then the doc linked for the area you touch.

## What this project is

A **companion and escort robot for a thermal spa** (hackathon project, tourism use case), built on the
**AgiBot D1 Ultra W** (quadruped with wheel-feet, SDK model `zsl-1w`) with a **Jetson Orin Nano** as companion computer.

A guest scans a QR code, opens our web frontend on their phone, and the robot becomes their companion: it greets them,
answers questions about the facility, and **escorts them from place to place** inside the spa.

Product and system requirements: [docs/product_requirements.md](docs/product_requirements.md),
[docs/system_architecture.md](docs/system_architecture.md).

## Repo map

| Path | What | Status |
|---|---|---|
| `ros2_ws/src/agibot_d1_bridge` | ROS 2 <-> D1 SDK: `/cmd_vel`, posture services, `/odom` (own integrated odometry), `/imu`, `/battery`, TF `odom->base_link`, `d1w_gestures` | working on robot |
| `ros2_ws/src/d1_camera` | `rtsp_camera`: robot RTSP H.264 -> `/camera/image_raw` + `camera_info` (OpenCV/FFmpeg, CPU decode) | working; being reworked, see [docs/feature_ros2_camera.md](docs/feature_ros2_camera.md) |
| `ros2_ws/src/human_perception` | YOLO26 person detection -> `/perception/humans/*`, diagnostics | working (PC, Docker) |
| `ros2_ws/src/d1_depth` | Depth Anything V2 monocular metric depth -> `/camera/depth`, `/camera/points` | experimental |
| `ros2_ws/src/d1_slam` | RTAB-Map RGB-only / RGB-D launch files | experimental, **off by default** |
| `ros2_ws/src/d1_localization` | `map_server` + `initial_pose_localizer` on MetaCam LiDAR maps (no drift correction) | working |
| `ros2_ws/src/d1_nav` | Nav2 (MPPI) + local costmap configs | static maps verified, not with guests |
| `ros2_ws/src/d1_companion` | Guest session state machine + HTTP API for the visitor frontend (QR join, places, escort, arrival, next step) | built + sim tested, not on robot |
| `ros2_ws/src/d1_gateway` | ROS 2 <-> WebSocket (`:8765`): goals, named places, intents, state | working + mock robot + tests |
| `ros2_ws/src/d1_dashboard` | Developer web UI (`:8080`): start/stop pipeline, maps, click-to-navigate, E-STOP | working |
| `frontend/` | Visitor web app (React 19 + Vite + TS), QR join, voice via Vapi | demo mode by default; **robot mode** (`npm run dev:robot`) talks to `d1_companion`: QR join, layout of destinations, escort, Stop |
| `tools/robot-console/` | Operator console (Node, zero deps) behind Tailscale Serve | working |
| `tools/` | MetaCam relay and map tools, offline evaluation scripts | dev tools |
| `maps/` | 2D occupancy maps built from MetaCam Lite scans | see `maps/README.md` |
| `deploy/jetson/` | systemd units (bridge, watchdog, console) for the Jetson | in use |
| `docker/` | `Dockerfile` (PC perception image), `d1-jetson/` (Orin image: Humble + Nav2 + RTAB-Map + PyTorch) | in use |
| `perception/` | Standalone (non-ROS) YOLO11 test script | legacy/quick test |
| `third_party/agibot_D1_Edu-Ultra` | Vendor SDK submodule (x86_64 + aarch64 libs) | do not edit |

## Docs index

| Topic | Doc |
|---|---|
| Product requirements, user journeys, MVP scope | [docs/product_requirements.md](docs/product_requirements.md) |
| System architecture, hardware, network, interfaces | [docs/system_architecture.md](docs/system_architecture.md) |
| Feature: ROS 2 monocular camera on the Orin | [docs/feature_ros2_camera.md](docs/feature_ros2_camera.md) |
| Feature: SLAM and localization (visual + MetaCam LiDAR) | [docs/feature_slam_localization.md](docs/feature_slam_localization.md) |
| Feature: privacy / GDPR face anonymization | [docs/feature_privacy_anonymization.md](docs/feature_privacy_anonymization.md) |
| Robot SDK, bridge topics/services | [docs/controls.md](docs/controls.md) |
| Jetson Orin Docker stack | [docs/jetson_stack.md](docs/jetson_stack.md) |
| Person detection | [docs/perception.md](docs/perception.md), [docs/perception_architecture.md](docs/perception_architecture.md) |
| Monocular SLAM experiment | [docs/mono_slam.md](docs/mono_slam.md) |
| Map + localization (MetaCam maps) | [docs/map_localization.md](docs/map_localization.md) |
| SLAM/Nav2 sensor study | [docs/slam_nav2_study.md](docs/slam_nav2_study.md), [docs/costmap_plan.md](docs/costmap_plan.md) |
| Gateway protocol | [docs/robot_gateway.md](docs/robot_gateway.md) |
| Companion state machine (guest flow, states, safety rules) | [docs/companion_state_machine.md](docs/companion_state_machine.md) |
| Companion backend API for the visitor frontend (contract + needed frontend changes) | [docs/companion_api.md](docs/companion_api.md) |
| Frontend/voice vs robot stack | [docs/frontend_robot_integration.md](docs/frontend_robot_integration.md) |
| Dashboard | [docs/dashboard.md](docs/dashboard.md) |
| Jetson services / watchdog | [deploy/jetson/README.md](deploy/jetson/README.md) |
| Frontend | [frontend/README.md](frontend/README.md) |

## Environment facts

- **ROS 2 Humble** (Ubuntu 22.04). On the Orin it runs in Docker (`docker/d1-jetson`, JetPack 6 / L4T r36.4, CUDA 12.6 mounted from host). Some laptops use Jazzy for RViz/MetaCam relay only.
- Robot addresses: Ethernet `192.168.168.168` (Orin side `192.168.168.10`), robot Wi-Fi AP `192.168.234.1`, event Wi-Fi example `172.24.1.41`. Camera: `rtsp://<robot>:8554/test`. SDK: UDP `43988`.
- MetaCam Lite: ROS 1 on the device, rosbridge at `ws://10.42.1.1:9089`; device clock is wrong (re-stamp on receive).
- Camera: 1920x1080, lens-corrected by robot ISP, robot stream script `/opt/app_launch/stream_4cams_rtsp.sh` (patched to 15 fps, 5 Mbit/s; firmware updates may reset it).
- Frames: `map -> odom -> base_link -> camera_link -> camera_optical_frame`; Nav2 uses `base_footprint`.

## Build and test

```bash
# ROS workspace (inside docker/d1-jetson/run.sh on the Orin, or a Humble host)
source /opt/ros/humble/setup.bash
cd ros2_ws && colcon build --symlink-install && source install/setup.bash
colcon test --packages-select human_perception d1_gateway && colcon test-result --verbose

# Gateway protocol tests without ROS
pytest -p no:anyio ros2_ws/src/d1_gateway/test

# PC perception Docker stack
make build && make up rviz=true            # make help for sources (webcam, d1-wifi, d1-ethernet, ...)

# Frontend (Node 24)
cd frontend && npm ci --include=dev && npm run typecheck && npm test && npm run test:e2e
```

## Rules for agents

### Safety (physical robot)
- **One SDK client only.** Never start a second `agibot_d1_bridge` when one runs (on the Orin it is the `d1-bridge` systemd unit). Use `external_robot:=true` for the dashboard.
- **No `/cmd_vel` arbitration exists yet** (Nav2, dashboard pad, teleop, voice agent all can write it). Do not add another writer without going through the design in [docs/system_architecture.md](docs/system_architecture.md#safety-architecture).
- The gateway sends goals and named intents only, never raw velocities. Keep it that way. `estop` (damp) must not be exposed to voice/guests.
- Do not raise speed limits (`SAFE_LIMIT`, Nav2 max vel) without an explicit request. Guests are close, floors may be wet.
- Code that commands the robot must have a mock/offline path (see `d1_gateway` mock, `tools/fake_d1_odom.py`).

### Privacy
- Camera frames show spa guests. **No raw images leave the Orin, are recorded, or are logged** unless the anonymization feature explicitly allows it. See [docs/feature_privacy_anonymization.md](docs/feature_privacy_anonymization.md).
- Never commit images, rosbags, MCAP recordings or audio of real people. Large recordings are not in git.

### Secrets and network
- Never commit tokens, Vapi private keys, SSH passwords or Tailscale auth keys. Use env vars.
- Network services bind to `127.0.0.1` by default. Expose only with a token, via Tailscale Serve. **Never `tailscale funnel` the console port (8443).**

### Code style
- Match the surrounding code: Python ROS nodes use `rclpy`, parameters declared in `__init__`, short comments that state *why*.
- Image topics use sensor-data QoS (BEST_EFFORT, KEEP_LAST small depth). A RELIABLE subscriber will not match a BEST_EFFORT publisher.
- Keep ROS-free logic in plain modules so it can be unit tested without ROS (pattern in `human_perception`, `d1_gateway`).
- `~/.local` NumPy 2.x breaks ROS's OpenCV: nodes strip user site-packages / launch sets `PYTHONNOUSERSITE=1`.
- Every package change updates its doc in `docs/`. Mark things you did not run on the robot as *unverified*.

### Git
- Default branch `main`; feature work on branches, merged via PR. Do not push, force-push or rewrite history without asking.
- Run the relevant tests before claiming work is done.
