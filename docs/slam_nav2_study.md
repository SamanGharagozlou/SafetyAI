# Study: Mapping & Navigation (SLAM + Nav2) for the AgiBot D1 Edu/Ultra

Date: 2026-11-14 · Status: desk study, nothing built or run on the robot yet.
Robot: D1 Edu/Ultra, wheel-foot variant (`zsl-1w`, `ROBOT_TYPE=XGW`), driven by our `agibot_d1_bridge` (`cmd_vel`).

## 1. Bottom line

1. **The robot cannot map today.** It has no LiDAR or depth camera, only one wide-angle camera and an IMU, neither suitable for
   Nav2-grade SLAM (section 4b). Real mapping needs a **sensor we add**: a cheap 2D LiDAR is the minimum, a 3D LiDAR with built-in IMU the best.
   With the built-in sensors only, the realistic options are dead reckoning, tag-based localisation on a hand-made map, or experimental camera-based navigation.
2. **Recommended stack:** 3D LiDAR (Livox Mid-360) -> **FAST-LIO2-class LiDAR-inertial SLAM** -> 3D `.pcd` map + 2D occupancy grid ->
   **Nav2 (Humble)** with **MPPI (Omni model)** + collision monitor -> `/cmd_vel` -> our bridge.
   This is the same architecture the robot's OEM (ZsiBot / GENISOM) ships in its open-source stack.
3. **Start from the OEM's open-source stack** ([`zsibot/genisom_roamerx_open`](https://github.com/zsibot/genisom_roamerx_open), BSD-3)
   as a reference/fork, but expect to adapt it: its README lists hardware deployment as "TODO" and it is only demonstrated in simulation.
4. **Run it on a computer mounted on the robot and wired to it (Ethernet)**, not over Wi-Fi from a laptop. Our Wi-Fi tests showed
   100 ms to 1.5 s latency and an SDK link that dropped; Nav2 on top of that is unsafe.
5. **Before buying anything:** power the robot on and *read-only* inspect its onboard nav/LiDAR scripts (see section 8). It may already
   support a LiDAR.

## 2. What the robot gives us (and doesn't)

| Item | Finding | Source |
|---|---|---|
| LiDAR / depth camera | **None built in**; add-ons via expansion ports. SLAM/nav "not included" | [OpenELAB](https://openelab.io/blogs/learn/agibot-d1-pro-vs-d1-edu-vs-d1-ultra), [Generation Robots](https://www.generationrobots.com/en/404439-agibot-d1-ultra-quadruped-robot.html) |
| Camera | One wide-angle: DFOV 122 / HFOV 122 / VFOV 70 deg, H.264 RTSP stream (`rtsp://<robot>:8554/test`) | SDK docs 1.3, 5.1 |
| IMU | Standard; raw data via SDK (`getBodyAcc/Gyro/Quaternion`) | SDK docs 1.3, 2.4 |
| Odometry | SDK provides `getPosition`, `getWorldVelocity`, `getBodyVelocity` (state estimate). **Quality unknown.** In our one test `odom.x` read `0.0` after a move, so it is untrusted | our test |
| Expansion ports | 1x GbE RJ45, 2x USB-A, 2x power (12 V <= 36 W, BAT <= 100 W, XT30U), SBUS, UART | SDK docs 1.5 |
| Payload | 5 kg rated; back-mount doc (1.4) is empty in the repo | SDK docs 1.3 |
| Size / speed | 630x360x420 mm standing, 16 kg, max 3.7 m/s, 1-2 h endurance | SDK docs 1.3 |
| Onboard computer | Observed: aarch64 (RK3588-class), 7.9 GB RAM, RT kernel, load average ~7.4 while idle (video encoding + `mc_ctrl` pinned to core 7). **Little headroom** | our SSH inspection |
| On-robot dev | SDK doc 5.7 allows running SDK programs on the robot; one reseller says the onboard compute is not open for user workloads. Conflicting; unverified | SDK docs, [OpenELAB](https://openelab.io/blogs/learn/agibot-d1-pro-vs-d1-edu-vs-d1-ultra) |
| Hints of nav support | `/opt/app_launch/` contains `start_navigation.sh`, `start_zenoh_route.sh`, `start_lcm.sh`, `start_imu_daemon.sh`, `start_linuxptp.sh`, `start_gpsd_chrony.sh`, `stream_4cams_rtsp.sh`. **Not yet inspected** | our SSH listing |

## 3. What Nav2 needs from our side

| Need | Status in `agibot_d1_bridge` | Work |
|---|---|---|
| `cmd_vel` in | Done; clamps to SDK limits, watchdog 0.5 s | none |
| `odom` + TF `odom->base_link` | `odom` topic exists; **no TF broadcast**, no covariances, quality untested | add TF; validate odom or take odom from LiDAR-inertial SLAM instead |
| Scan / point cloud | none | new sensor |
| Footprint | n/a | use ~0.63 x 0.36 m (`[[0.315,0.18],...]`); OEM sample uses 0.60 x 0.30 |
| Min command speeds | SDK needs exactly 0 below 0.05 m/s (vx), 0.1 m/s (vy), 0.1 rad/s (wz); the bridge zeroes them | tune smoother/MPPI so goals don't stall at tiny speeds |
| Holonomic motion | robot accepts vx, vy, wz | use MPPI `Omni` motion model (OEM sample uses DiffDrive) |
| Safety | `damp` service/topic, 3 s SDK-silence damping | add `nav2_collision_monitor` in front of the bridge; keep keyboard `x` = damp |
| Mode handling | stand/lie/gait are separate commands | Nav2 must not run unless robot is standing; add a guard |

## 4. Options compared

### A. OEM open stack: `genisom_roamerx_open` (reference / fork)
- Nav2 (Humble) with NavFn + MPPI, behavior trees, collision monitor, velocity optimizer, waypoint follower.
- SLAM module = FAST-LIO-style (uses `ikd_tree`), config for **Livox** (`lidar_type: 1`, topics `/front_lidar`, `/front_lidar/imu`,
  extrinsic matching a Mid-360 IMU offset). Builds a **3D PCD map and a 2D grid (pcd2pgm)**.
- Localization with `fast_gicp` / `ndt_omp` against the saved PCD map.
- Platforms: Gazebo, `NX_XG3588`, `XG3588`. Our robot's SSID is `XG03-...` and its launcher is `*_xgw.sh`, which suggests the same family, **but not confirmed**.
- Caveats: hardware deployment "TODO"; needs a proprietary `robot-forward_*.deb`; Nav2 odom topic is a sim topic (`/odom/mujoco_odom`);
  MuJoCo/UE5 simulator (`zsibot/matrix`) is available for safe testing.
- Pros: same vendor, same SDK family, mapping+localization+nav in one place. Cons: immature on hardware, vendor-specific glue, extra binary.

### B. Standard open stack on our bridge (recommended baseline)
- LiDAR driver (`livox_ros_driver2`) -> **FAST-LIO2** (or **DLIO**) -> odom + registered cloud.
- Map: save PCD, project to 2D grid (as the OEM does) or run **slam_toolbox** on a `pointcloud_to_laserscan` scan.
- Nav2 Humble: NavFn/Smac2D, **MPPI Omni**, voxel/obstacle layers from the point cloud or scan, velocity smoother, collision monitor -> `/cmd_vel` -> our bridge.
- Pros: well-documented, portable, no vendor binary, we already own the `cmd_vel` end. Cons: we integrate TF/extrinsics/tuning ourselves.
- Evidence: on Mid-360 data, LiDAR-inertial methods beat LiDAR-only (KISS-ICP): FAST-LIO2 0.060 m vs KISS-ICP 0.124 m APE on one indoor set;
  DLIO 0.096 m vs FAST-LIO2 0.112 m on UAV flights ([benchmark](https://discourse.openrobotics.org/t/slam-benchmark-on-livox-mid-360-public-dataset-our-own-uav-flight-data-kiss-icp-dlio-fast-lio2-glim/57912)).
  Aerial data, not a legged robot, so re-test on ours. The community FAST-LIO2 ROS 2 port has map-export gaps; check which port.
- A Go2 write-up of the same pipeline (3D LiDAR -> scan -> slam_toolbox -> Nav2) is a good template:
  [classmethod.jp](https://dev.classmethod.jp/articles/go2-patrol-slam_toolbox/).

### C. Camera + IMU only (what the robot has today)
Classic metric SLAM + Nav2 is **not realistic** with only the built-in sensors. See section 4b for what *is* feasible.

## 4b. What works with only the built-in camera + IMU

Constraints (from the SDK docs/FAQ and our tests):
- **IMU:** raw and "limited accuracy" (the vendor suggests buying a better IMU). Off-board it arrives at **50 Hz over UDP, untimestamped**.
  A 1 kHz IMU exists only in on-robot shared memory (`/imu_shm`), so only a program running *on* the robot can use it.
- **Camera:** one wide-angle (122 deg) **H.264 RTSP** stream: compressed, latency, no depth, no hardware timestamp, no published intrinsics (needs calibration).
- **Odometry:** SDK state estimate at 50 Hz (`getPosition`, velocities). Quality untested.
- Result: camera and IMU cannot be tightly time-synchronised off-board, which is what visual-inertial SLAM needs. Monocular vision alone has no metric scale.

| Tier | Approach | Needs | Realism |
|---|---|---|---|
| 0 | **Dead reckoning**: SDK odometry + IMU, Nav2 to relative goals, no map | nothing new | Works for short moves; drifts. First step regardless: measure odom drift over a known distance |
| 1 | **Camera obstacle avoidance**: metric monocular depth model -> pseudo-laser scan -> Nav2 local costmap | GPU computer (Jetson-class) | Plausible at low speed; model errors on glass/featureless walls. Research-grade, needs testing |
| 2 | **Tag-based localisation**: AprilTags on walls -> `apriltag_ros` + `robot_localization` fusing SDK odom -> `map->odom`; Nav2 on a **static map** (hand-made floor plan) | camera calibration, printed tags, a map you draw | Most robust camera-only metric option. Needs tags in view often; wide FOV helps |
| 3 | **Visual-inertial SLAM** (ORB-SLAM3 mono-inertial, OpenVINS, VINS) | on-robot run for 1 kHz IMU, camera+IMU calibration and sync | Fragile here (sync, gait vibration, weak IMU). Not Nav2-grade; not recommended |
| 4 | **Learned image-goal navigation** (ViNT / NoMaD-style): drive a route once with the keyboard, record images, replay | GPU computer | Experimental; no metric map; fits our `cmd_vel` and keyboard teleop well |

(These are options from general robotics practice, not tested on this robot.)

**Cheapest path to a real map: add a 2D LiDAR** on a USB-A port (about USD 100-300 class), run `slam_toolbox` + Nav2 with the SDK odometry.
It needs far less compute than 3D SLAM and removes the dependence on the weak camera/IMU. Upgrade to a 3D LiDAR (section 5) only if you need ramps or 3D obstacles.

## 5. Sensor choice

| | Livox Mid-360 | Unitree L1 | 2D LiDAR (RPLIDAR/Hokuyo class) |
|---|---|---|---|
| FOV | 360 x ~59 deg | 360 x 90 deg | 360 x 1 plane |
| Weight | ~265 g | ~230 g | ~200-300 g |
| Range | ~40 m @10%, 70 m @80% | 20-30 m | 12-40 m |
| IMU | built in | built in | none |
| Power | ~6.5 W, 9-27 V | n/a here | ~2-5 W |
| Price | ~USD 900-1100 retail | from ~USD 349 | low |
| Fit | Best: OEM stack and FAST-LIO2 configs assume Livox | Cheaper, shorter range; verify driver/ROS 2 support | Enough for flat indoor 2D only; no 3D, no IMU, poor on ramps/stairs |

Sources: [Livox Mid-360 specs](https://www.livoxtech.com/mid-360/specs), [Unitree LiDAR](https://www.unitree.com/LiDAR). Prices vary by reseller; check current quotes.
Mid-360 needs GbE + 9-27 V; the robot's 12 V port (<= 36 W) and one RJ45 can power/host it, but we then need a second link for the compute (see 6).

### 5b. SkylandX MetaCam Lite (asked about)

A **handheld survey scanner**, not a robot sensor module. From the web listings (vendor datasheet pages were not reachable):

| Item | Finding |
|---|---|
| LiDAR | 360 x 65 deg (-20 to +45), 216,000 pts/s, 10 Hz, 0.05-40 m @10% / 70 m @80% |
| Cameras | two wide cameras (~200 deg each) for colourised point clouds |
| Mass / power | ~0.76 kg unit, ~1.12 kg with battery; < 20 W |
| Workflow | on-device SLAM scan, app preview over Wi-Fi, post-processing, cloud 3DGS, multi-format export |
| Robot interface | **No ROS 2 driver, real-time topics, or Ethernet/USB streaming found.** Mounting on a quadruped for the Lite not documented |
| Price | not found (the larger Air2 lists at ~USD 5,000) |

Assessment:
- **As the robot's navigation sensor: not recommended** unless SkylandX confirms a real-time ROS 2 output (point cloud + odometry/pose at >= 10 Hz), external power from our 12 V port, and a mount.
  Nav2 needs live data on the robot; a scanner built for offline capture may not provide it. A Livox Mid-360 has similar LiDAR specs (200k pts/s, 360 x 59 deg), ~6.5 W, a ROS 2 driver, and costs far less (~USD 900-1100).
- **Good use if you already have/own one: offline mapping.** Walk the site, export the point cloud, convert to a 2D occupancy grid for Nav2 and as a digital twin for simulation. The robot still needs its own live sensor to localise in that map (LiDAR, or camera + tags).
- SkylandX also lists a **C01 SLAM LiDAR** described as aimed at robot navigation; worth asking them about it (ROS 2, interface, price). No details were found.

Questions for SkylandX: real-time ROS 2 / SDK? topic list and rates? output over Ethernet or USB? external 12 V power? mount for a 5 kg-payload quadruped? C01 specs?

## 6. Compute and network

| Option | Notes |
|---|---|
| Onboard RK3588 | Free, wired internally. But ~7.4 load already, 8 GB RAM, and may not be open for workloads. Try only after section 8 inspection |
| **Add-on computer on the back** (Jetson Orin NX/AGX class or small x86) | **Recommended.** Within 5 kg payload; power from the 12 V/BAT port; wired to the robot over GbE; runs LiDAR driver + SLAM + Nav2 + our bridge |
| Laptop over Wi-Fi | **No.** Observed latency/dropouts; remote controller is also tied to the robot's hotspot |

Open problem (blocking): the robot's hotspot (used by the remote controller) is off while the robot is on our Wi-Fi, so there is no emergency remote.
With a wired setup the hotspot can stay on for the remote controller. This must be solved before any autonomous motion.

## 7. Risks and unknowns

- LiDAR mounting position/extrinsics on this robot; back-mount doc is empty. Need CAD or measurements.
- Odometry source/quality (SDK estimate vs LiDAR-inertial). Wheel-leg slip and gait vibration affect LIO; needs testing.
- Battery: observed 15% and the robot shut down. Autonomy adds payload and compute load; endurance will drop from the quoted 1-2 h.
- Control authority: SDK and remote controller can't both control; low battery hands control to the remote automatically.
- The OEM stack's compatibility with the D1 is plausible but unconfirmed; it needs a proprietary `.deb` and has no hardware recipe.
- SDK `move` has hard minimum speeds; Nav2 output must respect them or the robot will stall near goals.
- Wheel-foot terrain limits (stairs/gaps) are not modelled by 2D costmaps; keep operation on flat floors initially.

## 8. Proposed plan

1. **Safety first:** restore emergency control (hotspot + remote), wired link, charge battery.
2. **Read-only inspection of the robot** (when powered): `/opt/app_launch/start_navigation.sh`, `launch_ros.env`, `start_zenoh_route.sh`, installed ROS packages, any `front_lidar` references, USB/Ethernet devices. Decides onboard vs add-on and whether a LiDAR is already supported.
3. **Simulation:** run `zsibot/matrix` + the OEM stack, or Gazebo, with our bridge's topic contract to tune Nav2/MPPI params.
4. **Bench LiDAR:** Mid-360 on a stand, `livox_ros_driver2` + FAST-LIO2, record a bag, build a map, check loop/drift.
5. **Mount and integrate:** extrinsics, TF (`base_link -> lidar`, `odom -> base_link`), validate odom while teleoperating with the keyboard.
6. **Nav2 bring-up:** map server + localization, MPPI Omni, collision monitor, velocity smoother, min-speed handling. First tests at <= 0.2 m/s, robot on a stand, then open floor.
7. **Waypoints/patrol**, relocalization from saved PCD, recovery behaviors.

## 9. Decisions needed

1. Sensor: Mid-360 (recommended), Unitree L1, or 2D only?
2. Compute: add-on (Jetson-class, recommended) or try the onboard board first?
3. Stack: start from the OEM stack (A) or the standard stack (B, recommended baseline, borrowing OEM pieces)?
4. Environment: flat indoor only, or ramps/uneven ground (needs 3D-aware costmaps)?

## References
- OEM nav stack: [zsibot/genisom_roamerx_open](https://github.com/zsibot/genisom_roamerx_open) (BSD-3), simulator [zsibot/matrix](https://github.com/zsibot/matrix)
- Robot SDK and docs: [AgibotTech/agibot_D1_Edu-Ultra](https://github.com/AgibotTech/agibot_D1_Edu-Ultra)
- Nav2 MPPI controller: [docs.ros.org](https://docs.ros.org/en/humble/p/nav2_mppi_controller/)
- D1 hardware comparison: [OpenELAB](https://openelab.io/blogs/learn/agibot-d1-pro-vs-d1-edu-vs-d1-ultra), [Generation Robots](https://www.generationrobots.com/en/404439-agibot-d1-ultra-quadruped-robot.html)
