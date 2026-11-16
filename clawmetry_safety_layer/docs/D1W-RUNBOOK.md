# AgiBot D1 Ultra-W at Leukerbad Therme: venue runbook

## Sunday checklist (Vivek, before 08:30)

- [ ] **Ask the Therme (before 08:30):** may we put 2 or 3 printed AprilTag boards on easels (at the Kasse, the
      concourse pillar and the shop door) and a few orange cones (across the stairs / lift lobby and in front of the
      turnstiles)? Neither touches the floor surface. With the tags, drift re-fixes become automatic; with the cones,
      `colour_marker` gives L2 a second boundary signal. If the answer is no, we run on operator re-fixes at the
      landmarks plus `floor_transition`. Write the answer in this file.
- [ ] **ClawMetry Guard rules** on the edge machine's ClawMetry, both `require_approval`, runtime `claude_code` or
      any:
      * tool `mcp__g1-skills__guide_to`: a guest asked for a restricted place; Approve sends STAFF to escort them
        (the robot never enters).
      * tool `mcp__g1-skills__resume_after_hold`: resume after a policy hold (L1 / L2 / L3) once recovery is done.

      Without these rules ClawMetry answers "no matching policy". The edge treats that as "not approved", and the
      mission-control console's Approve / Deny decides instead.
- [ ] Copy the same `EDGE_API_KEY`, `BRAIN_API_KEY` and `COSMOS_API_KEY` into the team PC's `.env`.
      Set `BRAIN_URL=http://spark-7a33:8780` and `COSMOS_URL=http://spark-7a33:8000/v1`.
- [ ] `tailscale up` on the team PC; check `tailscale ping spark-7a33` (docs/REMOTE-OPS.md).
- [ ] Run ClawMetry (sync daemon and dashboard) **as the user that runs the edge**, on the team PC. The robot's
      journal is read only by that user's ClawMetry. On the Spark, :8900 is Vivek's ClawMetry and never shows Eaby's
      runs.
- [ ] Battery above 30 %; the controller and the e-stop are with a named person; Tracking mode is off on the remote.
- [ ] **Team PC (`sepa@172.24.0.239`, on Tailscale first):**
      * its `.env` uses **Vivek's ClawMetry credentials** (the edge journals into his ClawMetry);
      * the **two Guard rules** above exist in that ClawMetry;
      * **only one `/cmd_vel` source**: our edge (twist_mux below, or nobody else running);
      * `D1W_TRANSPORT=ros2`;
      * our edge listens on **8765**.
      Deploy from the Spark: `bash scripts/remote/deploy_edge.sh sepa@edge` (its tailnet name), or
      `sepa@172.24.0.239` from a laptop on the venue LAN.

## What runs where

| Piece | Machine | Command |
|---|---|---|
| Team ROS 2 bridge (`d1_cmd_vel_bridge`, owns the ONE SDK connection) | team PC 172.24.0.239 | `ros2 launch agibot_d1_bridge bridge.launch.py local_ip:=172.24.0.239 robot_ip:=172.24.1.41` |
| Team detector (YOLO on their GPU) | team PC | `perception/.venv/bin/python detect.py --source rtsp://172.24.1.41:8554/test --no-show` (serves `:8090/latest.json`) |
| **Our edge**: skill server, safety FSM, perception, concierge client | team PC | `make edge` (`G1_CONFIG=config/d1w.yaml`, `D1W_TRANSPORT=ros2`, `FRAME_SOURCE=team`) |
| Brain: Cosmos judge relay, console, concierge | DGX Spark (UK) | `make brain`; Cosmos: `scripts/cosmos/serve_reason2.sh` |
| ClawMetry | team PC (edge journal), Spark (brain) | sync daemon + dashboard |

## Prerequisites on the team PC (Ubuntu 22.04)

1. ROS 2 Humble, plus the team's workspace built: `cd ros2_ws && colcon build && source install/setup.bash`.
2. Our repo next to theirs: `bash scripts/setup_edge.sh`. This makes a Python 3.10 venv with ROS system packages,
   installs the `edge` extra, writes `.env` and runs the self-check. Add `YOLO=1` only if their detector can't run.
3. `.env` per the checklist above. `BIND_HOST` is the team PC's tailnet IP (`tailscale ip -4`).

## Launch order

1. The robot is powered and standing. **The operator stands it with the controller**: our code never calls
   stand_up (`auto_stand: false`).
2. The team bridge (table above). Then `ros2 topic echo /connected --once` should print `data: true`, and
   `ros2 topic hz /odom` should show about 10 Hz.
3. The team detector. `curl -s localhost:8090/latest.json | head -c 200` should return recent detections.
4. Our edge: `make edge`. Check `curl -s -H "X-Api-Key: $EDGE_API_KEY" http://$BIND_HOST:8765/state | jq .safety`.
5. On the Spark: `make brain` (plus Cosmos). Open the console at `http://spark-7a33:8780/`.

## Calibration (no tape)

* **Odometry to zones:** run `python -m bridge.zones --calibrate --config config/d1w.yaml`. Drive the robot (with the
  controller) to `kasse_corner`, `pillar` and `shop_door` in turn and press Enter at each. Accept an RMS error of
  0.25 m or less; the transform is written to `config/d1w.yaml`. Re-fix at a landmark during the day with
  `curl -X POST .../refix -d '{"landmark":"pillar"}'`.
* **Floor classes:** `scripts/calibrate_floor.py` on stills **from the robot's own camera**, about 0.3 m high and
  looking up. The shipped calibration comes from a phone walkthrough at chest height (docs/COSMOS.md). Label
  concourse_tile, wood, terracotta and checker_tile.
* **Camera geometry:** measure the lens height and tilt and set them in `perception.camera` in `config/d1w.yaml`
  (# VERIFY). Then stand people at 1 m, 2 m and 3 m and compare against `/observation` `persons[].distance_m`.

## The 30-minute ladder

Keep 1 m clear around the robot at every rung. The e-stop and the controller's **L1+R1** both lie the robot down: that
is the operator's emergency path, never ours.

| # | Rung | How | Pass when |
|---|---|---|---|
| 1 | Connected | `python -m bridge.agibot_d1w_bridge state` (edge stopped), or `/state` | `connected` true, `fresh` true, `ctrl_mode` 1, battery above 30 %, tilt under 3° |
| 2 | Operator stands it | the controller | `mode` `standing`, `balance_ok` true |
| 3 | 0.2 m/s forward for 1 s | `python -m bridge.agibot_d1w_bridge straight` | moved about 0.2 m (mock: 0.201), then stopped; run rungs 1-5 with the edge stopped; no `command.refused` |
| 4 | Lateral 0.1 m/s for 1 s | `… lateral` | about 0.1 m sideways |
| 5 | 90° turn | `… turn` (0.4 rad/s, at most 6 s) | 80–100° |
| 6 | `guide_to` with zones | `POST /guide_to {"poi":"bath_entrance"}` | goes via the concourse; FSM NORMAL; arrives |
| 7 | Person near | someone walks to 1.5 m, then to 0.8 m | CAUTION at 0.15 m/s, then HOLD; HOLD clears by itself once they step back |
| 8 | Staged breach | the operator nudges the robot toward the turnstiles with the controller at walking pace | L1 `breach_predicted` (measured motion) and HOLD before the line; our zero `cmd_vel` |
| 9 | ClawMetry Stop | press Stop mid-guide | zero `cmd_vel`, the robot keeps its stance (never lies down); controller shows "confirmed" |
| 10 | Reset | ClawMetry Reset, then Approve if it was a policy hold | recovery backs into the go zone if needed; FSM NORMAL |

If frames drop on the robot's stream, lower the bitrate in `/opt/app_launch/stream_4cams_rtsp.sh` on the robot. The
team's README measured about 13 of 60 fps arriving over a slow link.

**Teleop warning (found in the mock):** the team's `keyboard_teleop` publishes `cmd_vel` straight to their bridge and
never passes our gate. Our L1-on-measured-motion catches it and stops the robot by publishing zeros. A keyboard held
down, however, keeps publishing. **Do not run teleop while the edge is guiding.**

## One velocity source: twist_mux (decided for the demo)

**During the demo only the safety edge publishes velocity.** Sahil's dashboard drives the robot with goals only:
`/guide_to`, `/pause`, `/resume`, `/say` on the edge (docs/ADAPTER-API.md), never its joystick pad.

As a guard against a stray publisher, put `twist_mux` in front of the team's bridge
(`apt install ros-humble-twist-mux`):

```bash
ros2 launch launch/twist_mux.launch.py                    # config/twist_mux.yaml -> /cmd_vel for the bridge
D1W_CMD_VEL=/cmd_vel_edge make edge                       # our edge publishes on its mux input
ros2 run agibot_d1_bridge keyboard_teleop --ros-args -r cmd_vel:=/cmd_vel_teleop   # only if needed, lowest
```

| Priority | Source | Topic |
|---|---|---|
| 100 (highest) | our safety edge | `/cmd_vel_edge` |
| 50 | the dashboard's deadman pad | `/cmd_vel_dashboard` |
| 30 | Nav2 | `/cmd_vel_nav` |
| 10 (lowest) | keyboard teleop | `/cmd_vel_teleop` |
| lock 255 | operator stop: `ros2 topic pub --once /mux_stop std_msgs/msg/Bool "{data: true}"` | `/mux_stop` |

* **The edge holds the mux while the robot stands:** it sends 20 Hz while driving and a zero keepalive at 5 Hz while
  idle or in HOLD, inside the mux's 0.5 s timeout. So no lower source gets through while the edge runs. To drive by
  hand, stop the edge first.
* **What the mux does not do:** when the robot is not standing, or the edge has handed over (no robot state), the edge
  is silent and a lower source can get through. That is why the rule above stays: nobody else publishes during the
  demo.
* **Gait:** the edge publishes `move` on `/gait` once at connect (logged, journal event `gait.set`) and never again.
* **The human still outranks software:** the vendor controller and the e-stop act inside the robot, under the mux.
* **Without the mux:** don't run the mux, leave `D1W_CMD_VEL` unset (the edge publishes `/cmd_vel`), and run no other
  source.

## No photography in the complex: wet_deck stays uncalibrated by design

The Therme allows no video or photography inside the swimming complex. So we have, and will take, no pool-deck
footage: the `wet_deck` floor class stays uncalibrated on purpose, and `pool_deck` is a `camera_off` zone, so no frame
from there ever reaches perception or the judge. The pool deck is covered by:

* **L1:** `pool_deck` is a restricted zone. Every command, and the measured motion, is predicted 2 s ahead, and a
  predicted exit from the go zone is a HOLD before the turnstile line.
* **The judge (L3):** on the concourse side, "leaves_go_zone" when the floor ahead visibly changes. Measured on venue
  stills it rarely fires (docs/COSMOS.md), so it is a second opinion, not the barrier.
* **The route:** the demo route stays on the concourse (reception, concourse, shop/café, the area in front of the
  turnstiles). `guide_to` never plans a leg within 0.5 m of a no-go zone.

## Simulation body (Isaac gallery) and SDK notes

_(Vivek, 2026-11-14; still accurate. The hardware bridge it anticipates now exists: `bridge/agibot_d1w_bridge.py`.)_

The D1 Ultra-W (model ZYCXD105UW) is AgiBot's wheeled-leg quadruped: 12 leg joints plus a hub motor in each
foot, 22 kg, 5 kg payload, 3.7 m/s, IP54, 1 to 2 h on a 43.2 V 4.6 Ah pack. Under the AgiBot badge it is the
Genisom (zsibot) ZSL-1W: AgiBot's own D1 SDK repository calls the model `zsl-1w`, and the ZSL-1W URDF with meshes
is published by zsibot under BSD-3-Clause. That is what we simulate.

| Piece | Where |
|---|---|
| Isaac body (gallery) | `config/vendors/agibot_d1w.yaml`, USD from `scripts/isaac/import_vendors.py agibot_d1w` |
| Model source | `zsibot/genisom_model` `zsl-1w/urdf/ZSL-1W.urdf` (`config/vendors/sources.yaml`, key `genisom_l1`) |
| Config test (no GPU) | `tests/test_agibot_d1w_config.py` |
| Isaac smoke test | `G1_CONFIG=config/vendors/agibot_d1w.yaml pytest -q tests/test_isaac_vendor_smoke.py` |
| Gallery run | `.venv/bin/python scripts/gallery_isaac.py agibot_d1w` -> `docs/gallery/agibot_d1w.{png,json,mp4}` |
| Hardware bridge | none yet (see "Hardware SDK" below for what it would wrap) |

## Simulation

The body follows the gallery's quadruped contract (`GALLERY.md`): the tray is strapped on the back
(`body.mount` on `BASE_LINK`), `pick towels` confirms the load, `place table_B` is a hand-off, and zone C is
refused by the local policy like every other body. The Isaac puppet moves the base kinematically; the legs are
held in a standing stance (`fixed_joints`: hip 0.7 rad, knee -1.4 rad, within the URDF limits) and the wheels do
not spin. `locomotion: legged_wheels` keeps the quadruped stand/lie semantics; it is deliberately not a
`wheeled_*` base (`bridge/wheeled.py`), which would make `damp` a refusal and `stand` a no-op.

Base height: the thigh (0.200 m) and shank (0.21366 m) at that stance put the axle 0.316 m below the hips; the
foot mesh is 0.141 m across, so `base_z` is 0.386 m. The spec sheet's 485 mm standing height is the top of the
body, which sits 0.09 m above the base origin in the URDF.

Rebuild the USD (headless, a few seconds, no rendering) when the clone changes:

```bash
source scripts/isaac/env.sh
"$ISAAC_PYTHON" scripts/isaac/import_vendors.py agibot_d1w     # -> $VENDOR_USD_DIR/agibot_d1w/ZSL-1W/ZSL-1W.usda
```

Live demo with the Isaac window and ClawMetry, same as the other bodies:

```bash
cat > /tmp/d1w-gui.yaml <<'Y'
extends: config/vendors/agibot_d1w.yaml
isaac_gui: true
isaac_window: [0, 32, 1450, 760]
Y
G1_CONFIG=/tmp/d1w-gui.yaml make demo        # skills API on :8765; the ClawMetry robot id is leukerbad-isaac-agibot_d1w
```

## Hardware SDK (from AgibotTech/agibot_D1_Edu-Ultra, read 2026-11-14; nothing below is verified on a robot)

* **Package.** `AgibotTech/agibot_D1_Edu-Ultra` (BSD-3-Clause) ships prebuilt libraries only:
  `lib/zsl-1w/{aarch64,x86_64}/libmc_sdk_zsl_1w_<arch>.so` plus a Python extension
  `mc_sdk_zsl_1w_py.cpython-310-<arch>-linux-gnu.so`. aarch64 builds exist, so the DGX Spark can host it, but the
  Python binding is **CPython 3.10 only**: it needs a 3.10 interpreter (our `.venv` is 3.12), or the C++ library
  behind a small shim. The wheeled model has HighLevel only, no LowLevel joint interface.
* **Network.** Robot Wi-Fi `192.168.234.1` (DHCP, SSID on the right-side label), wired `192.168.168.168` (no DHCP,
  set a static 192.168.168.x on the host). The SDK is UDP: `initRobot(local_ip, local_port, dog_ip)`, default port
  43988. The robot must be told the client's address in `/opt/export/config/sdk_config.yaml` (`target_ip`,
  `target_port`; ssh `firefly@<ip>`, password `firefly`) and, for anything other than the Wi-Fi subnet,
  `SDK_CLIENT_IP` in `/opt/app_launch/start_motion_control_xgw.sh`. Both need a reboot; firmware updates reset
  them; a wrong `SDK_CLIENT_IP` can leave the robot ignoring its remote.
* **HighLevel API (`class HighLevel`).** `checkConnect()` (heartbeat), `standUp()`, `lieDown()`, `passive()`
  (damping, "for emergency stop" in the vendor text), `move(vx, vy, yaw_rate)` (vx -3.7..3.7, vy -1..1,
  yaw -3..3; standing only; send zeros to stop), `crawl(...)`, `climb(...)`, `cancelCrawl()`, `cancelClimb()`,
  `shakeHand()`, `attitudeControl(roll_vel, pitch_vel, yaw_vel, height_vel)`, `getQuaternion()`, `getRPY()`,
  `getBodyAcc()`, `getBodyGyro()`, `getPosition()`, `getWorldVelocity()`, `getBodyVelocity()`,
  `getBatteryPower()`, `getCurrentCtrlmode()`, `getLeg{Abad,Hip,Knee,Foot}Joint{,Vel,Torque}()`. Return 0 on
  success. `standUp` and `lieDown` cannot be entered while moving.
* **Ports on the body.** 1x RJ45 (1000 Mbps), 2x USB-A (5 V), power 12 V/3 A and 43.2 V/3 A, SBUS, UART. An extra
  LiDAR takes the RJ45, so plan a small switch on the back or drive the SDK over Wi-Fi.

## Rules for a future `bridge/agibot_d1w_bridge.py` (same shape as `bridge/go2_bridge.py`)

* Stop = `move(0, 0, 0)` repeated, then hold standing. **Never `passive()` as a stop path**: it is the damp mode
  (CLAUDE.md: stop paths use the body's own stop-and-settle, never Damp). `damp()` only with `allow_damp: true`.
* `stand()` only confirms the robot is already standing unless `auto_stand: true`, as on the Go2.
* Speed caps come from `policies/behaviour.yaml` (0.2 to 0.4 m/s per zone), far below the 3.7 m/s the SDK accepts;
  the bridge clamps before the SDK ever sees a number.
* Pose: `getPosition()` is the SDK's own odometry (frame unverified); treat it like the Go2's dead reckoning until
  the zone floor is calibrated. Tilt for the monitor from `getRPY()`.
* `jump`, `frontJump`, `backflip` and `attitudeControl` are never exposed as skills.

## Appendix: no floor tape, landmarks and floor transitions (decided 2026-11-14)

The venue does not allow tape on the Leukerbad Therme floor, so nothing in the venue stack depends on it.

* **Zone frame.** `policies/zones_therme.yaml` is anchored on features already there (`landmarks`): `kasse_corner`,
  `pillar` and `shop_door`. Calibration runs `python -m bridge.zones --calibrate --config config/d1w.yaml`. The operator
  drives the robot to each landmark with the controller, presses Enter, and the tool reads odometry there. It fits the
  2D rigid `odom_to_zone` transform (least squares), prints the RMS error (re-measure if it is above 0.25 m) and writes
  the transform into `config/d1w.yaml`.
* **Drift.** `bridge.refix_at_landmark(name)` re-zeroes odometry drift when the robot stands at a landmark: it emits a
  `pose.refix` event with the drift in metres. With `perception.boundary.detector: apriltag_board`, a printed tag on an
  easel is recognised as a landmark (`landmark_seen`). An automatic re-fix within 1 m needs `safety.auto_refix: true`.
* **L2 boundary.** The default detector is `floor_transition`: colour histograms of the floors already there
  (concourse_tile, wood, terracotta, checker_tile, wet_deck). It estimates "transition ahead at ~N m" from the image row,
  and `wet_deck` is no-go. Calibrate with `scripts/calibrate_floor.py` on frames from `assets/venue/spa_walkthrough.mp4`
  and `docs/venue/*.jpg`. **Neither exists yet**, so until calibration the observation carries `boundary_uncalibrated`
  and L2 contributes people only. The other detectors are `colour_marker` (cones, or tape if it is ever allowed) and
  `apriltag_board` (position fixes only).

**Venue permission question (ask the Therme on Sunday morning, before 08:30):** may we place (a) two or three printed
AprilTag boards on easels at the Kasse, the pillar and the shop door, and (b) a few orange cones across the stairs /
lift lobby and in front of the turnstiles? Neither touches the floor surface. With (a), drift re-fixes become automatic.
With (b), `colour_marker` gives a second L2 boundary signal that needs no floor calibration. If the answer is no to
both, we run with landmarks fixed by the operator and with floor_transition.
