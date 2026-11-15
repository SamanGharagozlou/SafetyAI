# Handoff: continue on the Jetson Orin (state as of 2026-11-15)

For: the developer (or a fresh Claude Code session) who continues on the Jetson over SSH. Read top to bottom once; sections 1-3 are what you need to start.

## 1. Where things stand (one paragraph)
Teleop, camera, monocular depth, RTAB-Map mapping, Nav2 click-to-navigate, a developer dashboard and a ROS 2 <-> WebSocket
robot gateway all exist and work on the PC. The stack now also **builds and runs on the Jetson in Docker**: the robot's camera
arrives over the direct ethernet at a steady 15 fps, depth runs at about 8 fps on the Orin GPU, the workspace compiles. **No live
mapping run has been done on the Jetson yet.** It was blocked by the robot battery (17%, the SDK refuses commands under 20%).
Mapping quality on the PC was mediocre: loop closure was never accepted and none of the "improve odometry" options helped (section 6).

## 2. First actions on the Jetson
1. **Charge the robot** to >= 30%. `ctrl_mode` also read `21` (unknown to our code) at the end; check what it means.
2. **Get the right code.** `~/d1_stack` on the Orin is a clone of `develop`, which **lacks the newer work on branch `testing`**
   (1.35 s video-latency compensation, live obstacle layer, rosbag recording, floor-plan map style, heading-bias, Jetson docker, `external_robot`).
   ```bash
   cd ~/d1_stack
   git checkout -- ros2_ws/src/d1_dashboard          # drop my two scp'd files; the same change is committed on testing
   git fetch origin && git checkout testing && git pull
   docker/d1-jetson/run.sh bash -c "source /opt/ros/humble/setup.bash && cd /ws/ros2_ws && colcon build --symlink-install"
   ```
   (Cleaner long term: merge `testing` into `develop` and re-clone.)
3. **Check the shared-box state** (section 3) before starting anything that touches the SDK.
4. Do a mapping run through the dashboard (section 4), then look at the map and the rosbag.

## 3. The Jetson and the robot
| item | value |
|---|---|
| SSH | `ssh sepa@172.24.0.239` (Wi-Fi). The password was given in chat; it is not stored in the repo. `sudo` needs the password |
| OS | Ubuntu 22.04, JetPack 6 (L4T 36.4.3), CUDA 12.6, 7.4 GB RAM, ~25 GB free disk |
| Ethernet to robot | Orin `enP8p1s0` = `192.168.168.10/24` (set by someone else; check it survives a reboot); robot = `192.168.168.168` (RTSP :8554, SSH open) |
| Robot over Wi-Fi | `172.24.1.41` was **unreachable** from both the PC and the Orin on 2026-11-15; use the ethernet address |
| Docker image | `d1-stack:humble-jp6` (ROS Humble, Nav2, RTAB-Map, PyTorch 2.5 JP6 wheel, transformers 4.46.3). Built from `docker/d1-jetson/Dockerfile` |
| Run a container | `docker/d1-jetson/run.sh [cmd]`: host network, NVIDIA runtime, mounts host CUDA 12.6 + cuDNN (the image has no CUDA), repo at `/ws`, `~/d1_maps` at `/root/d1_maps`, HF cache `~/.d1_hf_cache`. It calls `sudo docker`; it needs a terminal for the password |
| Depth model | downloaded and cached; 122 ms per 960x540 frame, 0.22 GB GPU |

**The Orin is shared.** On 2026-11-15 four SSH sessions were open and one ran `apt-get upgrade -y`, which upgraded and restarted Docker and
killed my image build (the Dockerfile now uses BuildKit cache mounts so a retry is cheap).
**A bridge is already running natively** on the Orin (`/home/sepa/SafetyAI`, `ros2 launch agibot_d1_bridge bridge.launch.py`). It holds the SDK's
UDP port 43988 and the SDK accepts **one** client, so a second bridge fails with `bind: Address already in use`. Do not kill it without asking whoever runs it.
It publishes `/odom` (integrated body velocity x IMU yaw), `/odom_sdk`, `/imu` (10 Hz), `/tf` (odom->base_link and odom->base_footprint), `/battery`, `/ctrl_mode`, `/connected`,
and subscribes `/cmd_vel`, `/robot_cmd`, `/gait`, `/attitude_cmd`. It is an older bridge than `testing`'s (no heading-bias compensation).
A dashboard container I started may still be running (`sudo docker ps`; stop it with `sudo docker stop <id>`).

## 4. Running the stack
Use the dashboard in `external_robot` mode so it never starts a second bridge:
```bash
docker/d1-jetson/run.sh bash -c "source /opt/ros/humble/setup.bash && source /ws/ros2_ws/install/setup.bash && \
  ln -sfn /opt/d1_depth_venv /root/d1_depth_venv && \
  ros2 launch d1_dashboard dashboard.launch.py robot_ip:=192.168.168.168 local_ip:=192.168.168.10 host:=0.0.0.0 external_robot:=true"
```
Open `http://172.24.0.239:8080`. Flow: **Start process** (camera+depth) -> **Start map** (RTAB-Map; a rosbag is recorded to `~/d1_maps/<name>_bag`) -> drive -> **Save map**
-> later **Load map** -> click to navigate (Nav2). Details: `docs/dashboard.md`.
Known bug: the dashboard showed `connected: false` while `/connected` was `true` (probably a QoS/subscription mismatch with the running bridge). Look at `server.py` `_on_conn` / its subscriptions.
Use a private `ROS_DOMAIN_ID` for experiments on the shared box; replaying a bag into the live domain interferes with a real run.

Manual pieces (what the dashboard launches):
```
ros2 launch d1_depth depth_demo.launch.py rviz:=false url:=rtsp://192.168.168.168:8554/test      # camera + camera TFs + depth
ros2 launch d1_slam rgbd_rtabmap.launch.py rviz:=false database_path:=/root/d1_maps/x.db delete_db:=true
ros2 launch d1_nav navigation.launch.py                                                          # needs a loaded map (localization:=true)
```

## 5. Repo map
Branches on https://github.com/SamanGharagozlou/SafetyAI: `develop` (integration; has the gateway and a teammate's `d1w-gestures`), `testing` (my latest, **not merged**), `main`, plus teammates' `perception`, `clawmetry-integration`, `feat/visitor-qr-voice-ui`, `feature/metacam-2d-map`.
Merged and deleted: `motion-control`, `mono-slam`, `robot-gateway`.
| package | role |
|---|---|
| `agibot_d1_bridge` | `cmd_vel` -> SDK; services stand_up/lie_down/damp/shake_hand; telemetry; own odometry; `heading_bias.py` (testing only). Docs: `docs/controls.md` |
| `d1_camera` | RTSP -> `/camera/image_raw`; `stamp_offset_s` 1.35 (latency compensation, testing only) |
| `d1_depth` | Depth Anything V2 Metric Indoor Small + per-frame floor calibration -> `/camera/depth`, `/camera/points`. `docs/costmap_plan.md` s7-10 |
| `d1_slam` | `rgbd_rtabmap.launch.py` (mapping/localization), `mono_rtabmap.launch.py`. `docs/mono_slam.md` |
| `d1_nav` | Nav2 params (generated by `tools/make_nav2_params.py`), `navigation.launch.py` |
| `d1_dashboard` | localhost dev UI on :8080, process control, maps in `~/d1_maps`. `docs/dashboard.md` |
| `d1_gateway` | **ROS 2 <-> WebSocket gateway** for frontends (ClawMetry etc.): goals/intents in, `goal_received/accepted/progress/reached`, `state` out. `docs/robot_gateway.md`. Tested with a mock robot and a fake Nav2; **never run against the real robot** |
| `tools/offline_eval/` | scripts for offline experiments on recorded bags (below). Paths use `$EVAL_DIR` (default `~/d1_maps/eval`) |

## 6. What we learned (facts you will need)
* **SDK:** `mc_sdk_zsl_1w_py` (CPython 3.10; aarch64 libs are in `third_party/agibot_D1_Edu-Ultra/lib/zsl-1w/aarch64`). One client only. UDP robot 43997 / client 43988. No traffic for 3 s -> the robot damps.
  Commands are refused under 20% battery. Min speeds vx 0.05, vy 0.1, wz 0.1. First `move` after standing is refused (0x3007) for ~2 s while the state changes. Control modes: 0 damping, 1 standing, 3/18 moving, 51 lying.
* **Odometry:** `getPosition` was wrong; we integrate body velocity with IMU yaw. The SDK yaw drifts about -9.4 deg/min at rest (gyro bias). `heading_bias.py` estimates it at standstill (verified offline only, never live).
* **Camera:** 1920x1080 H.264 RTSP `rtsp://<robot>:8554/test`, robot-side lens correction, estimated intrinsics f=735 px, mounted x=0.28 z=0.02 pitch -0.28 rad (sees the floor only beyond ~1 m), video latency ~1.35 s over Wi-Fi (re-measure over ethernet: it should be smaller).
* **Depth:** reliable only to ~3 m. Scale comes from the analytic floor (mode of z_floor/d), holds the last calibration up to 30 s. A box closer than ~1 m is invisible.
* **Evaluation (offline, same 385 s run, 4 replays):** raw odometry, heading-bias fix, IMU gravity constraint, neighbor refinement: no variant closed a loop, metrics within noise (`docs/mono_slam.md`). Defaults `imu_gravity`/`neighbor_refine` stay off.
* **Unmeasured:** the SDK velocity scale (needs a tape-measure test: command a known time at a known speed, measure the distance).
* **Motion sources:** up to four publish `/cmd_vel` (Nav2 smoother, dashboard pad, keyboard teleop, ClawMetry edge). No mux yet; whichever publishes last wins. A `twist_mux` (human > governed edge > Nav2) is an open item.

## 7. Open items, roughly by priority
1. Charge the robot; run the first live mapping on the Jetson over ethernet; record the bag (the dashboard does).
2. Merge `testing` into `develop`; re-clone/rebuild on the Jetson; fix the dashboard `connected` display.
3. Verify heading-bias live; measure SDK speed scale; re-measure video latency over ethernet and update `stamp_offset_s` if it changed.
4. Loop closure is never accepted: look at the features/lighting (low upward-looking camera), `Rtabmap/LoopThr`, and revisit with better odometry.
5. Try the gateway against the real robot (`ros2 launch d1_gateway gateway.launch.py`); confirm ClawMetry's intent names and the `/say` topic (both were guessed from its docs).
6. `twist_mux`; a physical/remote emergency stop (the robot's remote-controller hotspot issue was never solved: the only stops are damp via service/keyboard `x`).
7. Rosbag `~/d1_maps/run_20261114_212444.bag` (2.9 GB, the run used for all offline testing) exists **only on the PC** (`/home/sparc/d1_maps/`). GitHub rejects files over 100 MB. Decide: Drive, a trimmed/compressed copy, or leave it.
8. Network: the Orin ethernet has no internet, so downloads (docker, models) go over the weak Wi-Fi (~1 MB/s).

## 8. Working agreements (from the user, still in force)
* **Do not reboot the robot** without asking. Do not run the robot (stand/move) without the user present.
* Commit identity `sahilrajpurkar03`; end commits with the Claude co-author line; **after pushing, say where** (repo, branch, commit).
* Record a rosbag on every mapping run.
* Never replay bags into the live ROS domain; use a separate `ROS_DOMAIN_ID`.
* Do not run `apt upgrade`/restart Docker on the shared Orin; do not kill other people's processes.

## 9. Pitfalls we hit
* `~/.local` NumPy 2 breaks ROS's cv2/cv_bridge: run ROS nodes with `PYTHONNOUSERSITE=1` (the launch files do). The depth node puts `$D1_DEPTH_VENV` site-packages first (numpy 1.26).
* `depth.launch.py` sets `D1_DEPTH_VENV=~/d1_depth_venv` itself, so in the container we symlink `/root/d1_depth_venv -> /opt/d1_depth_venv`.
* `pgrep`/`pkill -f` patterns can match your own shell command; use exact PIDs.
* RTAB-Map `/map` is not latched after data stops: grab it while data flows (`tools/offline_eval/grab_map.py`).
* rclpy `Image.data = bytes` is ~1000x slower than `array.array('B')`.
* `git push` on the PC needs a credential helper: `git -c credential.helper= -c credential.helper='!gh auth git-credential' push ...` (the `gh` CLI is logged in; git itself has no helper).
* `pytest` here needs `-p no:anyio`.

## 10. Offline experiment scripts (`tools/offline_eval/`)
Recovered from the chat transcript after the temporary folder was wiped, so later in-place edits may be missing. Read each before trusting it.
`replay_frontend.py` (replay a bag with odometry raw or heading-bias-corrected), `run_variant.sh` (one RTAB-Map replay variant on its own ROS domain),
`grab_map.py`, `compare_maps.py`, `link_stats.py` (neighbor-link comparison between two RTAB-Map DBs), `vo_eval.py` (ORB+PnP visual odometry vs SDK),
`latency_study.py` (camera yaw-rate vs gyro cross-correlation), `clutter_study.py` (depth floor-calibration methods on a photo).
