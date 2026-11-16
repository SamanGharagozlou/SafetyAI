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
| `ros2_ws/src/d1_follow` | Lock onto one guest (IoU tracker + OSNet re-ID), others `unknown`, dry-run `/follow/cmd_vel`, opt-in `follow_relay` -> `/cmd_vel` | relay unverified on robot |
| `ros2_ws/src/d1_depth` | Depth Anything V2 monocular metric depth -> `/camera/depth`, `/camera/points` | experimental |
| `ros2_ws/src/d1_slam` | RTAB-Map RGB-only / RGB-D launch files | experimental, **off by default** |
| `ros2_ws/src/d1_localization` | `map_server` + `initial_pose_localizer` on MetaCam LiDAR maps (no drift correction) | working |
| `ros2_ws/src/d1_nav` | Nav2 (MPPI) + local costmap configs | static maps verified, not with guests |
| `ros2_ws/src/d1_gateway` | ROS 2 <-> WebSocket (`:8765`): goals, named places, intents, state | working + mock robot + tests |
| `ros2_ws/src/d1_dashboard` | Developer web UI (`:8080`): start/stop pipeline, maps, click-to-navigate, E-STOP | working |
| `frontend/` | Visitor web app (React 19 + Vite + TS), QR join, voice via Vapi | **demo mode only, no robot connection** |
| `tools/robot-console/` | Operator console (Node, zero deps) behind Tailscale Serve | working |
| `clawmetry/` | Physical-AI control plane: tap + collectors + hash-chained ledger + breach correlator + Cosmos world-model gate + Vapi voice sink + operator-editable venue guardrails + live/trace/guardrails dashboard (pure stdlib) | runs on the robot in front of `d1-vapi-server`, but only governs the live path while `tailscale funnel` points 443 at 8901 instead of 8090; see [clawmetry/README.md](clawmetry/README.md#live-wiring-end-to-end) |
| `tools/` | MetaCam relay and map tools, offline evaluation scripts | dev tools |
| `scripts/` | Credential scan (`audit_secrets.py` + tests, `make audit-secrets`), opt-in pre-commit hook installer, WSL webcam pipe | working |
| `maps/` | 2D occupancy maps built from MetaCam Lite scans | see `maps/README.md` |
| `deploy/jetson/` | systemd units (bridge, watchdog, console) for the Jetson | in use |
| `docker/` | `Dockerfile` (PC perception image), `d1-jetson/` (Orin image: Humble + Nav2 + RTAB-Map + PyTorch) | in use |
| `perception/` | Standalone (non-ROS) YOLO11 test script | legacy/quick test |
| `pitch_deck/` | Hackathon pitch (Sepazu): single-file HTML deck, photos, demo video slide, committed PDF `sepazu_pitch.pdf` | presentation-ready; how to open it is in [README.md](README.md#pitch-deck-sepazu) |
| `presentation/` | Separate ClawMetry deck | not the pitch |
| `third_party/agibot_D1_Edu-Ultra` | Vendor SDK submodule (x86_64 + aarch64 libs) | do not edit |

## Docs index

| Topic | Doc |
|---|---|
| **Start here — the full technical documentation, 15 chapters** | **[docs/README.md](docs/README.md)** |
| All docs combined into one file (snapshot, with PDF) | [docs/combined_documentation/SafetyAI_Technical_Documentation.md](docs/combined_documentation/SafetyAI_Technical_Documentation.md) |
| Product requirements, user journeys, MVP scope | [docs/product_requirements.md](docs/product_requirements.md) |
| System architecture, hardware, network, interfaces | [docs/system_architecture.md](docs/system_architecture.md) |
| Feature: ROS 2 monocular camera on the Orin | [docs/feature_ros2_camera.md](docs/feature_ros2_camera.md) |
| Feature: SLAM and localization (visual + MetaCam LiDAR) | [docs/feature_slam_localization.md](docs/feature_slam_localization.md) |
| Feature: privacy / GDPR face anonymization | [docs/feature_privacy_anonymization.md](docs/feature_privacy_anonymization.md) |
| Feature: human follow (lock-on + follow) | [docs/human_follow.md](docs/human_follow.md) |
| Robot SDK, bridge topics/services | [docs/controls.md](docs/controls.md) |
| Jetson Orin Docker stack | [docs/jetson_stack.md](docs/jetson_stack.md) |
| Person detection | [docs/perception.md](docs/perception.md), [docs/perception_architecture.md](docs/perception_architecture.md) |
| Monocular SLAM experiment | [docs/mono_slam.md](docs/mono_slam.md) |
| Map + localization (MetaCam maps) | [docs/map_localization.md](docs/map_localization.md) |
| SLAM/Nav2 sensor study | [docs/slam_nav2_study.md](docs/slam_nav2_study.md), [docs/costmap_plan.md](docs/costmap_plan.md) |
| Gateway protocol | [docs/robot_gateway.md](docs/robot_gateway.md) |
| Frontend/voice vs robot stack | [docs/frontend_robot_integration.md](docs/frontend_robot_integration.md) |
| Dashboard | [docs/dashboard.md](docs/dashboard.md) |
| Observability + governance (ClawMetry control plane) | [clawmetry/README.md](clawmetry/README.md) |
| Secrets: credential inventory + the repo-wide scan | [docs/secrets.md](docs/secrets.md) |
| Jetson services / watchdog | [deploy/jetson/README.md](deploy/jetson/README.md) |
| Frontend | [frontend/README.md](frontend/README.md) |
| Pitch deck: slides, assets, demo video | [pitch_deck/README.md](pitch_deck/README.md) |

## Environment facts

- **ROS 2 Humble** (Ubuntu 22.04). On the Orin it runs in Docker (`docker/d1-jetson`, JetPack 6 / L4T r36.4, CUDA 12.6 mounted from host). Some laptops use Jazzy for RViz/MetaCam relay only.
- Robot addresses: Ethernet `192.168.168.168` (Orin side `192.168.168.10`), robot Wi-Fi AP `192.168.234.1`, event Wi-Fi example `172.24.1.41`. Camera: `rtsp://<robot>:8554/test`. SDK: UDP `43988`.
- MetaCam Lite: ROS 1 on the device, rosbridge at `ws://10.42.1.1:9089`; device clock is wrong (re-stamp on receive).
- Camera: 1920x1080, lens-corrected by robot ISP, robot stream script `/opt/app_launch/stream_4cams_rtsp.sh` (patched to 15 fps, 5 Mbit/s; firmware updates may reset it).
- Frames: `map -> odom -> base_link -> camera_link -> camera_optical_frame`; Nav2 uses `base_footprint`.

## Build and test

```bash
# Credential scan - no ROS, no Docker, no network. Fastest check in the repo.
make audit-secrets && python3 scripts/test_audit_secrets.py

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

# Pitch deck PDF (headless Chrome or Edge, no packages); commit it with any slide change
python3 pitch_deck/tools/export_pdf.py
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
- **Only exception: `pitch_deck/assets/`.** The team's own pitch photos and the demo video (`pitch_deck/assets/demo.mp4`,
  also on YouTube) may be committed there, because they are presented publicly anyway. Keep it to media the team
  chose for the deck, strip EXIF (location) from photos, keep each file under GitHub's 100 MB limit, and confirm
  consent or blur faces for children and other guests before presenting. Never copy robot camera frames,
  rosbags or MCAP recordings there; this exception does not extend to any other folder.

### Pitch deck (`pitch_deck/`)
- `index.html` is the source of truth; `sepazu_pitch.pdf` is generated from it. **Any slide change regenerates the
  PDF** (`python3 pitch_deck/tools/export_pdf.py`) and commits both in the same commit, so reviewers never read a stale pitch.
- Check the rendered slides (browser or headless Chrome screenshot), not just the HTML: text must not overlap
  photos or run off the 1920x1080 stage, and no dashed placeholder may be visible.
- No hyphen or dash characters in visible slide text (the brief's rule); use underscores in asset names.
- Claims match what was shown: say monitor, predict, flag, alert, deny; never certified, fail safe or prevents
  accidents. Keep the "Controlled test" tag on staged results. Prices and numbers come from the team, never invented.
- Slides use web copies in `pitch_deck/assets/web/` (cropped, EXIF stripped); originals stay untouched. Screenshots
  must not show tokens, tailnet hostnames or other secrets (crop them, as done for the guest UI).
- The demo player must never take keyboard focus (the clicker has to keep working); see `pitch_deck/README.md`.

### Secrets and network
- Never commit tokens, Vapi private keys, SSH passwords or Tailscale auth keys. Use env vars.
  Every variable the stack reads is documented in [`.env.example`](.env.example); the inventory of
  which ones are real credentials and where they live is [docs/secrets.md](docs/secrets.md).
- `make audit-secrets` scans the tree (stdlib Python, no network, <1 s). Run it before a PR.
  `make install-hooks` runs it on every commit. If it flags something public by design, add an
  `ALLOW` entry in `scripts/audit_secrets.py` with the path, the exact value and a reason -
  never widen a rule to silence a hit.
- The Vapi **public** key and the assistant ids are compiled in on purpose and are not secrets
  (reasons at each site and in docs/secrets.md). The Vapi **private** key is in no file here.
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
