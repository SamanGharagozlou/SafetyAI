# Feature: SLAM and localization

Requirements: FR-P2 (must), FR-P3 (could) in [product_requirements.md](product_requirements.md). Status: **two tracks**,
LiDAR maps in use, visual SLAM experimental.

Escort (FR-X1) needs: a map of the operating area, the robot's pose in it while moving, named places and no-go zones in
the same frame. Two tracks are pursued in parallel; the demo uses whichever meets the acceptance criteria.

## 1. Requirements

| ID | Requirement | Target (to verify) |
|---|---|---|
| LOC-1 | 2D occupancy map of the operating area in `map` frame, Nav2 `map_server` format | 5 cm cells, walls and fixed furniture present |
| LOC-2 | Pose of `base_footprint` in `map` while driving | error ≤ 0.3 m position, ≤ 5° yaw after a 50 m escort |
| LOC-3 | Initial placement without a developer | robot starts at a marked home spot, or relocalizes automatically |
| LOC-4 | Detect lost localization and report it (`/diagnostics`, gateway `state.nav_ready`) | robot stops, escort fails gracefully (FR-X6) |
| LOC-5 | Places and keepout zones stored with the map (`maps/<name>/places.yaml`, keepout mask) | editable by staff |
| LOC-6 | Map update procedure for the spa site documented (scan, build, verify) | ≤ 1 h on site |
| LOC-7 | Runs on the Orin Nano alongside camera, detection and anonymization | see compute budget in [system_architecture.md](system_architecture.md#compute-budget-orin-nano) |

## 2. Track L: MetaCam Lite LiDAR (primary)

**Today (done):** handheld scan -> MCAP -> `tools/mcap_to_2d_map.py` + `tools/make_feature_map.py` -> `maps/<scan>/` ->
`d1_localization` (`map_server` + `initial_pose_localizer`) -> Nav2. The robot is placed at the scan origin (or given
`x y yaw`) and then moves by bridge odometry only: **no drift correction**. Details: [map_localization.md](map_localization.md),
[../maps/README.md](../maps/README.md).

**Live relay (built):** `tools/metacam_live_relay.py` bridges the device's ROS 1 topics via rosbridge into ROS 2
(`/metacam/odom`, `/metacam/cloud`, static `map -> mapping_init`). Branch `lidar-live` (not merged) uses it for live localization.

**Next steps**

| Step | What | Done when |
|---|---|---|
| L1 | Merge/review `lidar-live`; define how MetaCam pose corrects `map -> odom` | robot pose stays on the map over a 50 m loop (LOC-2) |
| L2 | Mount MetaCam on the D1 (payload ≤ 5 kg, power from robot 12 V or own battery), measure `base_link -> metacam` extrinsic | mount + static TF committed |
| L3 | Time sync: re-stamp on receive (device clock is wrong) and measure relay latency | latency documented |
| L4 | Relocalize in an existing map at start (MetaCam's own relocalization, or scan-match its cloud against the 2D map with e.g. AMCL on a projected scan) | LOC-3 |
| L5 | Scan the spa's operating area, add `places.yaml` + keepout mask (wet areas, changing rooms) | LOC-5, LOC-6 |

Fallback if MetaCam cannot be mounted: static map + odometry + periodic re-placement at known spots (current state), or an
added 2D/3D LiDAR on the Orin (Livox Mid-360 recommendation in [slam_nav2_study.md](slam_nav2_study.md)).

## 3. Track V: monocular visual SLAM (research)

**Today (built, off by default):** `d1_slam` RTAB-Map RGB-only with bridge `/odom`, optional RGB-D with `d1_depth`
(Depth Anything V2 + floor calibration, reliable ≤ 3 m). Enable with `dashboard.launch.py mono_slam:=true`.
Offline replays (2026-11-14): 0 accepted loop closures in all variants. Details: [mono_slam.md](mono_slam.md),
[costmap_plan.md](costmap_plan.md).

**Dependencies:** this track depends on the camera feature ([feature_ros2_camera.md](feature_ros2_camera.md)): higher FPS,
measured latency and correct stamps, checkerboard intrinsics, measured camera mount. Most failures so far trace back to
those inputs (1.35 s Wi-Fi latency, 3–5 Hz frames, estimated intrinsics).

**Plan**

| Step | What | Done when |
|---|---|---|
| V1 | Re-record a run on the Orin over Ethernet after camera steps 1–4 (anonymized or no guests present) | bag with ≥ 15 Hz images, `/odom`, `/imu` |
| V2 | Re-run RTAB-Map variants offline on that bag (`tools/offline_eval/`) | loop closures accepted; map metrics vs MetaCam map (`compare_maps.py`) |
| V3 | Use RTAB-Map **localization mode** against a visual database recorded on the MetaCam map's frame, as a relocalization aid for LOC-3 | relocalizes from home spot within 5 s |
| V4 | Optional: evaluate a monocular-inertial system (e.g. ORB-SLAM3 mono-inertial) once stamps are good; requires camera-IMU calibration | documented go/no-go |

Visual-only metric mapping is not expected to beat the LiDAR map; the value of track V is relocalization, place
recognition, and not depending on an extra sensor.

## 4. Acceptance test (both tracks)

1. Place robot at home spot, start the stack from boot.
2. Escort route: home -> 3 named places -> home (≥ 50 m), at 0.3 m/s, with people walking around.
3. Record pose in `map` at each place vs tape-measured truth: LOC-2 targets.
4. Cover the camera / unplug the MetaCam mid-route: localization reports degraded, robot stops, gateway sends `goal_failed`.

## 5. Open questions

- Is the MetaCam Lite meant to stay on the robot for the demo, or only for mapping?
- Can MetaCam's on-device SLAM run in a "localization only" mode against a saved map?
- Spa floors: glass, mirrors and water surfaces affect both LiDAR and camera. Which areas are affected?
