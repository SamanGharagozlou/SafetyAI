# ClawMetry frontend / voice stack and the robot development (how they fit)

Written when `develop` was merged into `main` (branch `main-develop`). Facts below come from reading `main`; items marked *unverified* were not run.

## What `main` has
| Piece | Where | What it does |
|---|---|---|
| Visitor frontend | `frontend/` (React 19 + Vite) | Join, call, review request. **Demo mode only: no robot, no backend** (CSP `connect-src 'none'`) |
| Voice | `frontend/scripts/vapi/`, `frontend/src/voice/` | Vapi assistant "ClawMetry D1 Robot (voice)". Its **seven tools** (`robot_status`, `robot_command` stand_up/lie_down/shake_hand, `do_gesture`, `move_robot`, `walk_distance`, `set_gait`, `stop_robot`) are HTTPS calls from Vapi to the Jetson (`https://d1-jetson.tail5ec076.ts.net/vapi`, bearer token) |
| Operator console | `tools/robot-console/` | Local Node proxy + page (buttons, d-pad, voice, STOP). Token stays in Node. Never `tailscale funnel 8443` |
| Jetson services | `deploy/jetson/` | systemd: `d1-bridge` (the ROS bridge), `d1-ros-agent` (rclpy, 127.0.0.1:8091, owns the robot), `d1-vapi-server` (Node, :8090, public edge), watchdog, console |

The `d1-ros-agent` and `d1-vapi-server` **sources are not in this repo** (they live in `~/vapi-robot/` on the Jetson). The bridge you see running on the Orin
(`/home/sepa/SafetyAI/...`) is the `d1-bridge` unit.

## What `develop` adds (now merged here)
`d1_gateway` (ROS 2 <-> WebSocket: goals + intents + state, `docs/robot_gateway.md`), `d1_dashboard` (developer UI), `d1_nav` / `d1_localization` / lidar maps in `maps/`
(static Nav2 navigation, `docs/map_localization.md`), bridge heading-bias compensation, `d1w_gestures`, Jetson Docker image (`docker/d1-jetson`), monocular SLAM (deactivated by default).
Branches `nav2` (static navigation on lidar maps in the dashboard) and `lidar-live` (live MetaCam localization) are **not** in `develop` yet.

## How they overlap and where to connect
| Need | Voice stack today | Our side |
|---|---|---|
| stand / lie / shake / gestures | `robot_command`, `do_gesture` -> ros agent | gateway intents `stand_up`, `lie_down`, `shake_hand`, `say_hi` (gestures: `d1w_gestures` in the bridge package) |
| bounded motion | `move_robot`, `walk_distance` (odometry closed loop) | Nav2 goals via the gateway: `{"type":"goal","place":"reception"}` -> `goal_received / accepted / progress / reached / failed` |
| status | `robot_status` | gateway `state` stream (pose in the map, battery, mode, link, `nav_ready`) |
| stop | `stop_robot` | gateway `stop` (keeps standing); `estop` damps and is **not** exposed to voice (keep it that way) |

**Suggested integration (not done):** add a `go_to_place` tool to the Vapi tool set whose server side (the Node edge on the Jetson) opens a WebSocket to the gateway on
`127.0.0.1:8765`, sends a `goal`, waits for `goal_reached` / `goal_failed` (timeout ~60 s), and returns that to the model. Named places come from `places_file`.
Keep the gateway on loopback (or Tailscale Serve, never Funnel) and set a `token`. The browser frontend stays demo-only until a reviewed backend profile exists (its own docs say so).

## Risks to settle before running both on the robot
1. **One SDK client.** `d1-bridge` owns the SDK port; run only one bridge (use `external_robot:=true` for the dashboard; the gateway only talks ROS).
2. **Several `/cmd_vel` writers:** ros agent (walks), Nav2 velocity smoother, dashboard pad, keyboard teleop. No arbitration exists (`twist_mux` is an open item). Do not give a voice walk while Nav2 is navigating.
3. **`connected` flapping.** `deploy/jetson/README.md` says it was "fixed at the source" (10 s latch, `connected_raw`), but **that change is not in this repo's bridge** (the merged `cmd_vel_bridge.py` has no `connected_raw`). It probably exists only on the Jetson. Commit it, or it will be lost on the next deploy.
4. **Bridge version on the Jetson.** Restarting `d1-bridge` from this tree turns on heading-bias compensation (verified offline only, never on the robot) and the integrated-odometry TF.
5. **`ctrl_mode` names.** The robot emits 0/1/18/21/51; map 21 as unknown, as the console already does (the dashboard still labels it `?`).
6. **Navigation needs a placed robot.** Goals are refused until the robot is placed on the map (and, with `lidar-live`, the MetaCam is connected). Voice has no such step yet.
7. **Battery:** the SDK refuses commands under 20 %.
