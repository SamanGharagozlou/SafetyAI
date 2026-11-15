# Live lidar localization (branch `lidar-live`)

The MetaCam Lite's live odometry corrects the robot's pose in the saved lidar map, so the pose no longer drifts. The dashboard shows
**Ready for navigation** once the robot is localized. Package: `ros2_ws/src/d1_lidar`. Hardware link: see `docs/metacam_connection.md` (on the Orin).

## How it works
```
MetaCam (USB, rosbridge ws://10.42.1.1:9089) ──▶ metacam_relay ──▶ /metacam/odom  (frame mapping_init, ~10 Hz)
bridge: TF odom→base_link (drifts) ─────────────┐
dashboard "Place robot" ──▶ /initialpose ───────┴─▶ lidar_localizer ──▶ TF map→odom (20 Hz) + /lidar_localization/status
```
* **Placing the robot** (click + drag, or "robot is at the map origin") tells the node where the robot really stands. With the lidar live this
  also calibrates where the scanner's session origin (`mapping_init`) lies in the map: `T_map_mi = P · T_base_body · inv(T_mi_body)`.
* After that every lidar sample gives the robot pose in the map, `map→odom = T_map_base · inv(T_odom_base)`, so odometry drift is removed
  continuously (smoothing 0.5; a sample that jumps the pose > 1 m / 40° is ignored as a glitch unless it persists 3 s).
* Without lidar data the placed pose is held and the robot navigates by odometry (drifts). The status says so.

| status `state` | meaning | dashboard chip |
|---|---|---|
| `need_pose` | nothing placed yet; goals are refused | **Not ready** · place the robot |
| `localized` | lidar live and aligned | **Ready for navigation** · lidar |
| `odometry_only` | placed, but no lidar data | **Ready** · odometry only (drifts) |
| `lidar_lost` | lidar data stopped for > 1 s, last position held | **Ready** · lidar lost, odometry |

## Scanner mount (IMPORTANT: placeholder until measured)
`lidar_x / lidar_y / lidar_yaw` = pose of the scanner body in `base_link` (m, m, rad; x forward, y left, yaw = scanner heading relative to the robot).
They default to 0. Measure them and pass them: `ros2 launch d1_dashboard dashboard.launch.py lidar_x:=0.12 lidar_y:=0.0 lidar_yaw:=0.0`.
A wrong mount makes the pose wrong when the robot turns (a lever-arm error), so check by turning in place after placing: the pose should stay put.

## Run on the Jetson (hardware)
1. Plug the MetaCam in over USB and bring up the link as in `docs/metacam_connection.md` (`sudo ip addr add 10.42.1.2/24 dev usb2`; ping `10.42.1.1`).
   The scanner must be in **mapping mode** so `/mapping/odom` is published; its `mapping_init` is where that session started.
2. Start the robot link (bridge) and the dashboard in the container, letting the dashboard run the relay:
   `ros2 launch d1_dashboard dashboard.launch.py external_robot:=true lidar_url:=ws://10.42.1.1:9089 lidar_x:=.. lidar_y:=.. lidar_yaw:=.. host:=0.0.0.0`
   (or run `ros2 run d1_lidar metacam_relay --url ws://10.42.1.1:9089` yourself; the image needs `python3-websocket`, added to the Dockerfile).
3. Dashboard: Start process → Load map → keep the robot still → **Place robot** at its real pose → chip turns *Ready for navigation* → Nav mode → a goal 2-3 m away.
Check `ros2 topic echo /lidar_localization/status` and `ros2 topic hz /metacam/odom` (~10 Hz).

## Test without hardware (done)
```bash
export ROS_DOMAIN_ID=97
python3 tools/sim_robot.py --drift 1.0 --scale 1.08 --map-pose 0.5 0.3 0.2     # odometry drifts 1 deg/s and reads 8 % far; truth is exact
python3 tools/fake_metacam.py --map-mi -3 2 1.0 --body 0.1 0 0                  # scanner publishes from the TRUE pose in its own frame
ros2 launch d1_dashboard dashboard.launch.py external_robot:=true lidar_x:=0.1
```
Result (2026-11-15, `maps/metacam_lite_2026-11-14`, 2.8 m goal to (0.94, 3.06)):

| | pose the dashboard believes | TRUE pose | error |
|---|---|---|---|
| with lidar | (0.89, 2.83) | (0.886, 2.823) | ~0.01 m |
| odometry only | (0.89, 2.85) | (1.21, 2.56) | 0.43 m, heading off ~28° |

A 5 s lidar dropout mid-goal gave `lidar_lost` and then `localized` again when data returned; the goal was still reached.
`pytest -p no:anyio ros2_ws/src/d1_lidar/test` checks the transform math.

## Not done / limits
* **Not tested with the real MetaCam or the real robot.** Rates, latency, the real `mapping_init` behaviour and the mount are unverified.
* Alignment to the map relies on the operator's placement (a wrong placement means a wrong map pose, now also with lidar). Automatic alignment by matching the live cloud to the map is a next step.
* The scanner's own SLAM can reset or jump: the jump gate protects against short glitches only.
* No live obstacles yet: `/metacam/cloud` is relayed but not used by the costmap.
