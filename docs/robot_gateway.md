# Robot gateway (`d1_gateway`): ROS 2 <-> WebSocket

A small ROS 2 node that lets any frontend (the ClawMetry layer, a web app, a phone, a test script) command the D1 and
watch it **without knowing ROS**. The frontend sends *goals* and *intents* as JSON over one WebSocket; the robot answers
with what is happening: `goal_received`, `goal_accepted`, `goal_progress`, `goal_reached`, ... plus a `state` stream.

It is independent of the developer dashboard (`d1_dashboard`, :8080). Both can run; they talk to ROS separately.

```
 frontend (ClawMetry, app, ...)            robot PC (ROS 2 Humble)
 ┌──────────────┐   JSON / WebSocket   ┌───────────┐   ROS 2   ┌────────────┐  ┌───────────────────┐
 │ goals,intents│ ───────────────────▶ │ d1_gateway│ ────────▶ │ Nav2       │  │ agibot_d1_bridge  │
 │ state, events│ ◀─────────────────── │ :8765     │ ◀──────── │ (goals)    │  │ (services, topics)│
 └──────────────┘                      └───────────┘           └────────────┘  └───────────────────┘
```

The gateway sends **goals and named actions only**. It never forwards raw velocities, so a frontend cannot drive the
robot around the safety layer. (`stop` and `estop` are always available.)

## Run

```bash
pip install websockets                      # once (or: rosdep install python3-websockets)
cd ros2_ws && colcon build --packages-select d1_gateway --symlink-install && source install/setup.bash

ros2 launch d1_gateway gateway.launch.py                     # ws://127.0.0.1:8765
ros2 launch d1_gateway gateway.launch.py host:=0.0.0.0 token:=CHANGE_ME places_file:=/path/places.yaml
ros2 launch d1_gateway gateway.launch.py mock:=true          # no robot, no ROS graph: simulated robot for frontend work
```

* Default bind is `127.0.0.1`. Expose it (`host:=0.0.0.0` or a tailnet IP) **only with a `token`**.
* For real navigation the bridge (`ros2 launch agibot_d1_bridge bridge.launch.py`) and Nav2 with a loaded map
  (`ros2 launch d1_nav navigation.launch.py ...`, or the dashboard's *Load map*) must be running. Without Nav2, goals are
  answered with `goal_rejected` ("Nav2 is not running").
* Named places: copy `config/places.example.yaml`, edit (map-frame metres), pass `places_file:=`.

## Protocol (version 1)

JSON text frames, one object each, with a string `type`. Frontend messages carry a client-chosen `id` (1-64 chars); every
answer repeats it, so you can match `goal_reached` to the goal you sent. Robot messages carry `ts` (unix seconds).

### Connection
1. On connect the robot sends `hello_ok` `{protocol, intents[], places[], auth_required}` and then a first `state`.
2. With a token configured it sends `hello_required` instead; send `{"type":"hello","token":"..."}` within 5 s, else it closes with code 4401.

### Frontend -> robot

| message | meaning |
|---|---|
| `{"type":"goal","id":"g1","target":{"x":2.0,"y":0.5,"yaw":1.57}}` | navigate there (map frame; `yaw` optional, `frame` optional) |
| `{"type":"goal","id":"g1","place":"reception"}` | navigate to a named place |
| `{"type":"cancel","id":"g1"}` | cancel the active goal (`id` optional) |
| `{"type":"intent","id":"i1","name":"say_hi","args":{"text":"Hello!"}}` | do a named action: `say_hi`, `shake_hand`, `stand_up`, `lie_down`, `say` (`args.text`) |
| `{"type":"intent","id":"i2","name":"follow_lock"}` | lock the follow node (`d1_follow`) onto the guest in front of the camera; fails with "follow node not running" if it is down. It can take a few seconds to answer: `intent_started` comes first, then `intent_done` or `intent_failed` (e.g. reason `no_person`) when the lock resolves |
| `{"type":"intent","id":"i3","name":"follow_release"}` | make `d1_follow` forget the locked guest |
| `{"type":"stop"}` | cancel the goal, zero velocity, keep standing |
| `{"type":"estop"}` | stop **and damp** (the robot drops; use in emergencies only) |
| `{"type":"ping","id":"k"}` | answered with `pong` |

One goal is active at a time. A new `goal` while another runs **preempts** it (the old one ends with `goal_canceled`, reason "preempted").

### Robot -> frontend

| message | when |
|---|---|
| `goal_received` `{id, target, place?}` | immediately, the gateway got and validated it |
| `goal_accepted` `{id}` | Nav2 accepted it, the robot starts moving |
| `goal_progress` `{id, distance_remaining, recoveries, elapsed_s}` | about every 0.5 s while moving |
| `goal_reached` `{id}` | arrived |
| `goal_failed` `{id, reason}` | Nav2 gave up (blocked, timeout) |
| `goal_canceled` `{id, reason}` | cancel, stop, or preempted |
| `goal_rejected` `{id, reason}` | refused up front: unknown place, robot not localized on the map (`localization.ready` false, when the lidar localizer is running), robot lying/damped ("send intent stand_up first"), robot link down, Nav2 not running |
| `intent_received` / `intent_started` / `intent_done` / `intent_failed` `{id, name, reason?}` | lifecycle of an intent |
| `state` `{pose, battery, mode, connected, nav_ready, localization, active_goal}` | 2 Hz. `pose` `{x,y,yaw,frame}` (map frame, odom if no map, else null); `battery` 0..1; `mode` `standing/moving/lying/damping` |
| `robot_event` `{kind, detail}` | `mode_changed`, `link_up`, `link_down`, `stop`, `estop` |
| `error` `{message, id?}` | the last message was invalid; the connection stays open |

Each goal ends with exactly one of `goal_reached`, `goal_failed`, `goal_canceled`, `goal_rejected`.
Final messages are broadcast to **all** connected clients, so a second screen sees the same lifecycle.

### Example session
```
-> {"type":"intent","id":"i1","name":"say_hi","args":{"text":"Welcome!"}}
<- intent_received, intent_started, intent_done
-> {"type":"goal","id":"g1","place":"reception"}
<- goal_received, goal_accepted, goal_progress ..., goal_reached
```

Try it without a robot: `ros2 launch d1_gateway gateway.launch.py mock:=true`, then
`python3 -m websockets ws://127.0.0.1:8765` and paste the JSON above.

## Mapping to ROS

| gateway | ROS 2 |
|---|---|
| `goal` | action `navigate_to_pose` (nav2_msgs/NavigateToPose); feedback -> `goal_progress` |
| `say_hi`, `shake_hand`, `stand_up`, `lie_down` | Trigger services `/d1_cmd_vel_bridge/<name>` (`say_hi` = `shake_hand`, plus `/say` if `args.text`) |
| `say` | publishes `std_msgs/String` on `/say` (for a TTS node) |
| `stop` | cancels the Nav2 goal, publishes zero `/cmd_vel` |
| `estop` | `stop` + `robot_cmd` = `damp` |
| `state` | TF `map`->`base_footprint` (fallback `odom`), `/battery`, `/ctrl_mode`, `/connected` |

Parameters (ROS params on node `d1_gateway`): `bridge_node`, `map_frame`, `odom_frame`, `base_frame`, `cmd_vel_topic`,
`say_topic`, `progress_period_s`.

## Safety notes
* Only one motion source should drive `/cmd_vel` at a time (Nav2, dashboard pad, keyboard teleop). The gateway adds Nav2 goals and `stop`, nothing else.
* `estop` damps the robot. It is the same as the dashboard's E-STOP.
* A goal is refused while the robot is lying or damped; send `stand_up` first (the stand takes about 2 s before it can move).

## Tests
`pytest -p no:anyio ros2_ws/src/d1_gateway/test` runs the protocol end to end over a real WebSocket against the mock robot (no ROS needed).
The ROS adapter was verified against a fake Nav2 action server and fake bridge topics.

## Adding a capability
1. Add the command to `protocol.py` (`COMMANDS` / `INTENTS`) and validation.
2. Route it in `core.py::_dispatch`.
3. Implement it in `ros_robot.py` **and** `mock_robot.py`, answering with `intent_*` / `goal_*` events through `emit`.
4. Document it here and add a test.
