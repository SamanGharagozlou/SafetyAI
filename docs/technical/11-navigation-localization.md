# 11 · Navigation and localization

[← 10 Mapping and LiDAR](10-mapping-lidar-spike.md) · [index](../README.md) · next: [12 Dashboards](12-dashboards.md)

Source: `ros2_ws/src/d1_localization`, `ros2_ws/src/d1_nav`,
`ros2_ws/src/d1_gateway`. Working docs:
[map_localization.md](../map_localization.md),
[costmap_plan.md](../costmap_plan.md), [robot_gateway.md](../robot_gateway.md).

---

## 1. Status, honestly

| Capability | State |
|---|---|
| Load a MetaCam map and publish it on `/map` | **working** |
| Place the robot in that map and move by odometry | **working** |
| Nav2 MPPI plans a path and follows it | **built, verified on static maps with the robot lying** |
| Autonomous motion with the robot standing | **not tested** |
| Drift correction / live relocalization | **missing** |
| Keepout zones (wet areas, changing rooms) in the costmap | **missing** |
| Collision monitor | **missing** — needs a range sensor |
| Escorting a guest to a named place | **not demonstrated with guests** |

And on the real hardware, running the real greeter:

> **The deployed robot has no navigation at all.** `guide_to` is removed from
> the tool list when `REAL_ROBOT` is set ([03](03-voice-vapi.md) §5). "Take me
> there" leads one metre with `walk_distance` and gives the rest in words. That
> is a deliberate product decision, not an omission: *leading a metre and
> pointing is the whole job*, and announcing a journey the machine cannot make
> is worse than not offering one.

Everything below is therefore the built-but-not-deployed half of the stack.

---

## 2. Localization: `d1_localization`

Two nodes and no magic:

- **`nav2_map_server`** publishes the saved occupancy grid on `/map`.
- **`initial_pose_localizer`** publishes `map → odom` from the pose you give
  it — launch arguments `x y yaw`, or RViz "2D Pose Estimate" at any time.

The robot then moves through the map **by odometry alone**.

> **No drift correction.** The D1 has no LiDAR topic here, so nothing corrects
> `map → odom` once it is set. Re-place the robot when it has drifted.

```bash
ros2 launch agibot_d1_bridge bridge.launch.py local_ip:=<pc-ip> robot_ip:=<robot-ip>
ros2 launch d1_localization localize.launch.py            # at the scan origin
ros2 launch d1_localization localize.launch.py x:=3.0 y:=1.5 yaw:=1.57
```

### The EKF is off by default, and that is the right default

The bridge already broadcasts `odom → base_link` and `odom → base_footprint`
from its own integrated body-velocity + IMU-yaw odometry, so
`robot_localization` is disabled (`use_ekf:=false`).

Turning it on (`use_ekf:=true`) requires starting the bridge with
`publish_tf:=false publish_footprint_tf:=false` — otherwise **two nodes publish
`odom → base_link`**, and `base_footprint`, which Nav2 uses, stops being
broadcast at all. A TF tree with two publishers for one edge is a bug that
presents as intermittent nonsense.

### Testing without a robot

```bash
ros2 launch d1_localization localize.launch.py x:=1.0 y:=2.0 yaw:=0.5
python3 tools/fake_d1_odom.py            # fake /odom + /imu, drives forward
ros2 run tf2_ros tf2_echo map base_link  # starts at (1,2), moves along 0.5 rad
```

---

## 3. Nav2 configuration, and why each number

`ros2_ws/src/d1_nav/params/nav2_params.yaml` is **generated** from the stock
`nav2_bringup` defaults by `tools/make_nav2_params.py`, which keeps the stock
behaviour-tree plugin lists and overrides only what matters for this robot.
Generating rather than forking means a Nav2 upgrade does not silently drop a
plugin.

| Parameter | Value | Why |
|---|---|---|
| `robot_base_frame` | `base_footprint` | the floor frame the bridge broadcasts |
| `footprint` | `[[0.315,0.18],[0.315,-0.18],[-0.315,-0.18],[-0.315,0.18]]` | the real 0.63 × 0.36 m standing body (the OEM sample uses 0.60 × 0.30) |
| controller | **MPPI**, `motion_model: Omni` | the robot accepts `vx`, `vy`, `wz`; the OEM sample's DiffDrive would throw away strafing |
| `controller_frequency` | 10 Hz | |
| **max velocity** | `vx 0.30`, `vy 0.20`, `wz 0.50`; `vx_min −0.15` | conservative, and **above the SDK minimums** |
| `min_*_velocity_threshold` | `0.05 / 0.10 / 0.10` | matches the SDK's smallest non-zero commands exactly |
| `xy_goal_tolerance` | 0.25 m | `yaw_goal_tolerance` 0.5 rad |
| `inflation_radius` | 0.45 m global, 0.35 m local | |
| `consider_footprint` | `true`, `collision_cost 1e6` | the footprint is not round; a circular approximation would clip doorways |
| `resolution` | 0.05 m | matches the maps |
| progress checker | 0.25 m within 20 s | |

### The SDK minimum-speed trap

The SDK requires **exactly `0`** below 0.05 m/s (vx), 0.1 m/s (vy) and
0.1 rad/s (wz) — the bridge zeroes anything smaller.

A planner that asks for 0.03 m/s therefore does not produce a slow robot, it
produces a **stationary** one. Near a goal, MPPI naturally slows down, so
without the thresholds above the robot stalls a few centimetres short of every
goal and the progress checker eventually fails it. The thresholds and
`vx_min` exist precisely to keep commands out of that dead band.

---

## 4. The costmap problem

A Nav2 costmap needs obstacles in the robot frame. **The only sensor that can
supply them is the one camera**, so obstacles must be estimated from the
image.

Four options were compared ([costmap_plan.md](../costmap_plan.md)):

| # | Approach | Verdict |
|---|---|---|
| A | monocular depth network → `PointCloud2` → Nav2 `VoxelLayer` | **recommended** — this is the official Nav2 recipe |
| B | floor segmentation + ground-plane projection | cheap, fragile on floor colour and shadows; a cross-check |
| C | feed estimated depth to RTAB-Map RGB-D → 3D map + occupancy grid | natural second step; the same depth image serves both |
| D | **add a real sensor** | most reliable — [10](10-mapping-lidar-spike.md) |

### What this camera can and cannot see — measured

Inputs: standing body height **0.376 m** (SDK `z` while standing), camera
~0.02 m above it → **~0.40 m**; tilted **up ~16°**; f ≈ 735 px (HFOV ~105°,
VFOV ~73° after the robot's lens correction). *Both f and tilt are estimates.*

- **Blind zone: the floor first appears ~1.07 m in front of the robot**
  (0.92–1.24 m for f ±10 %). The camera's lowest ray is only ~20° below the
  horizon.
- Low objects are seen late: a 5 cm cable at > 0.94 m, a 10 cm box at
  > 0.80 m, a 20 cm box at > 0.53 m, a 30 cm box at > 0.26 m.
- It also sees **up to 52° above the horizon** — ceilings and beams dominate
  the picture and must be removed with a maximum obstacle height (~0.5 m; the
  robot is 0.42 m tall).

> **A camera-only costmap cannot protect against anything that appears inside
> ~1 m** — people stepping in, low objects not seen earlier.

Mitigations, all of them partial and all of them documented as partial:

- keep speed ≤ 0.3 m/s (the venue clamps to 0.15);
- let obstacles **persist** in the local costmap as the robot approaches —
  marking without aggressive clearing;
- keep a human `damp` available at all times;
- and accept a placement error of roughly `speed × delay` — at 0.2 m/s with a
  0.5 s pipeline delay, **10 cm**.

### Two different blind-spot numbers, and why

| Context | Blind inside | Geometry |
|---|---|---|
| Camera people-guard ([06](06-perception-and-guard.md)) | 0.40–0.57 m | camera at 0.40 m, VFOV 70°, tilted **down** 0–15° |
| Costmap study (this chapter) | ~1.07 m | camera at 0.40 m, VFOV ~73°, tilted **up** ~16° |

Same camera, different assumed tilt, and the two were measured for different
purposes at different times. **Both are estimates, and the disagreement is
itself a finding**: the camera mount has never been measured. That is listed in
[15](15-gaps-and-roadmap.md), and it is the single cheapest piece of
calibration work outstanding — a tape measure and an afternoon would retire
two sets of derived numbers at once.

---

## 5. The gateway: how anything is allowed to ask for motion

`d1_gateway` is a ROS 2 node exposing one WebSocket on `:8765`, and it is the
**only** path by which a frontend or behaviour may command the robot.

```
frontend ──JSON/WebSocket──► d1_gateway :8765 ──► Nav2 action / bridge services
         ◄── state, goal_* , intent_* ──
```

> It sends **goals and named actions only. It never forwards raw velocities**,
> so a frontend cannot drive the robot around the safety layer.

| Frontend → robot | |
|---|---|
| `{"type":"goal","id":"g1","place":"reception"}` | navigate to a named place |
| `{"type":"goal","id":"g1","target":{"x":2.0,"y":0.5,"yaw":1.57}}` | map-frame pose |
| `{"type":"intent","name":"say_hi" / "shake_hand" / "stand_up" / "lie_down" / "say"}` | named action |
| `{"type":"intent","name":"follow_lock" / "follow_release"}` | lock `d1_follow` onto the guest in front |
| `{"type":"stop"}` | cancel the goal, zero velocity, **keep standing** |
| `{"type":"estop"}` | stop **and damp** — the robot drops |
| `{"type":"cancel"}`, `{"type":"ping"}` | |

| Robot → frontend | |
|---|---|
| `goal_received` → `goal_accepted` → `goal_progress` (2 Hz) → `goal_reached` | the happy path |
| `goal_failed` / `goal_canceled` / `goal_rejected` | **exactly one terminal message per goal** |
| `state` at 2 Hz | `pose`, `battery`, `mode`, `connected`, `nav_ready`, `active_goal` |
| `robot_event` | `mode_changed`, `link_up`, `link_down`, `stop`, `estop` |

Design decisions worth noting:

- **Goals are refused up front**, with a reason, for: unknown place, robot
  lying or damped (*"send intent stand_up first"*), robot link down, Nav2 not
  running. A refusal that explains itself is what lets a voice agent say
  something true to a guest.
- **One goal at a time**; a new goal preempts the old one, which ends
  `goal_canceled` with reason `preempted`.
- **Terminal messages are broadcast to all clients**, so a second screen sees
  the same lifecycle.
- **`estop` is available to operators and deliberately not exposed to voice or
  guests** ([08](08-adversarial-agents.md) §3). [AGENTS.md](../../AGENTS.md)
  states this as a project rule: *"`estop` (damp) must not be exposed to
  voice/guests. Keep it that way."*
- **Default bind is `127.0.0.1`.** Exposing it needs a token, and the
  connection is closed with code `4401` if the `hello` does not arrive within
  5 s.
- **`mock:=true`** runs the whole protocol with no robot and no ROS graph.

### The planned bridge between voice and navigation

Not built. The shape is agreed: add a `go_to_place` tool to the Vapi set whose
server side — the Node edge on the Jetson — opens a WebSocket to the gateway on
`127.0.0.1:8765`, sends a `goal`, waits for `goal_reached` / `goal_failed` with
a ~60 s timeout, and returns that to the model. Named places come from
`places_file`.

This is the right shape precisely because it keeps the **one command path**
property: the voice agent would gain a named destination, not a velocity.

---

## 6. Running it

```bash
# full nav stack on a loaded map
ros2 launch d1_nav navigation.launch.py
# or through the developer dashboard: "4 Load map & navigate"

# the gateway
ros2 launch d1_gateway gateway.launch.py                      # ws://127.0.0.1:8765
ros2 launch d1_gateway gateway.launch.py mock:=true           # no robot, no ROS
ros2 launch d1_gateway gateway.launch.py host:=0.0.0.0 token:=CHANGE_ME \
                                        places_file:=/path/places.yaml
```

Verified on 2026-11-14 with the robot **lying down**: start process, start map,
save map, load map (relocalized at the end pose of a recorded run), goal →
plan, cancel, clear all, and live data in a headless browser.

**Not tested:** autonomous motion with the robot standing; teleop from the pad
on the real robot.

---

## 7. What has to be true before a guest is escorted

In rough order of how blocking each is:

1. **`twist_mux`.** Four unranked `/cmd_vel` writers is not a configuration to
   run Nav2 in. ([01](01-system-and-hardware.md) §7)
2. **A live localization source.** Odometry-only drift over a 50 m escort is
   not bounded, and LOC-2 asks for ≤ 0.3 m and ≤ 5°.
3. **Keepout zones in the costmap.** The venue file already forbids the pool
   deck, sauna, relax level, stairs and plant rooms; Nav2 does not know about
   any of them yet. Today that boundary is enforced at the gate
   ([09](09-clawmetry-control-plane.md)) and not in the planner, which means a
   path *through* a forbidden zone is refused rather than re-planned around.
4. **A collision monitor**, which needs a range sensor the robot does not have.
5. **A scan of the actual spa**, with `places.yaml` and a keepout mask.
6. **The emergency remote problem.** The robot's hotspot — which the physical
   remote uses — is off while the robot is on our Wi-Fi, so there is no
   hardware stop. *This must be solved before any autonomous motion.*
   ([10](10-mapping-lidar-spike.md) §7)
7. **Follow-check** (FR-X4): the robot must notice the guest is no longer
   behind it. It cannot — one forward camera. `escort_core.py` implements
   stop-and-look-back every 3 m and gives up after 2 lost checks, which is the
   right behaviour and still unverified.

Item 6 is the one that would stop a deployment on the day.

---

[← 10 Mapping and LiDAR](10-mapping-lidar-spike.md) · [index](../README.md) · next: [12 Dashboards](12-dashboards.md)
