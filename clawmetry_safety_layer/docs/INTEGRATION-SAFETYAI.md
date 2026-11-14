# Integration with the SafetyAI team (github.com/SamanGharagozlou/SafetyAI)

Our layer sits **on top of** their robot interface. We never open a second SDK connection, and we add no ROS node
that commands the robot except our own `cmd_vel` publisher, which is capped, exact-zero and governed.

## Their pieces we use as they are

| Theirs (branch) | What we use | Our side |
|---|---|---|
| `ros2_ws/src/agibot_d1_bridge` `d1_cmd_vel_bridge` (develop) | in: `cmd_vel` (Twist), `gait` ("move" only, once). Out: `odom`, `imu`, `battery` (0..1), `ctrl_mode` (0 damp, 1 stand, 3 move, 51 lying), `connected`, all at 10 Hz | `bridge/agibot_d1w_bridge.py` `Ros2Transport` |
| `~/stand_up`, `~/damp` services | only with literal-`true` flags (`auto_stand`, `allow_damp`; both default false) | `Ros2Transport._trigger` |
| `~/lie_down`, `~/passive`, `robot_cmd`, `attitude_cmd` | **never**: no client and no publisher is ever created (tested) | `FORBIDDEN_SERVICES`, `FORBIDDEN_TOPICS` |
| `perception/detect.py` (perception branch) `:8090/latest.json` and `/stream` | detections at 5 Hz with staleness checks; MJPEG as our frame source | `perception/detections.py TeamDetectorClient`, `perception/sources.py MjpegSource` |
| Robot camera `rtsp://172.24.1.41:8554/test` | alternative frame source | `RtspSource` |

## Interfaces each work package can plug into

| Work package | Interface | Where |
|---|---|---|
| **SLAM / localisation** | `PoseSource`: publish `nav_msgs/Odometry` on `/odom` (their bridge does this today) or `geometry_msgs/PoseWithCovarianceStamped` on `/amcl_pose`. A map-frame pose replaces the odom→zone transform: set `d1w.ros2.odom` to your topic and `d1w.odom_to_zone` to your map's origin in the zone frame. Landmark re-fixes (`POST /refix`) keep working | `bridge/agibot_d1w_bridge.py` (`_on_odom`, `to_zone`, `refix_at_landmark`) |
| **Camera / detection** | `Detections`: keep serving `/latest.json` in the current schema `{"ts","frame","fps","width","height","detections":[{"class","conf","box"}]}`. A changing `frame` number is how we know it is alive. Fine-tuned classes for the low, upward-looking camera are welcome; we read `person` (and log the rest) | `perception/detections.py`, `perception/proximity.py` |
| **Voice** | We publish what the robot should say on `/say` (`std_msgs/String`); your TTS speaks it. Send guest speech as text to the edge: `POST /ask {"text", "lang"}` returns `{"reply", "actions"}`, and the reply is also published on `/say` | `skills/edge.py` `/ask`, `bridge/agibot_d1w_bridge.py say()` |
| **App / dashboard** | `GET /state` (1 Hz) and `GET /events` (SSE) for everything the robot, safety, perception and concierge do. Goals only: `/guide_to`, `/say`, `/pause`, `/resume`. Full reference: `docs/ADAPTER-API.md` | `skills/edge.py` |
| **Teleop** | `keyboard_teleop` publishes `cmd_vel` directly, so **don't run it while our edge is guiding**. Our L1-on-measured-motion stops the robot near the go-zone edge, but a held key keeps publishing. For manual moves, stop the edge, or use `python -m bridge.agibot_d1w_bridge straight / lateral / turn` (bounded, capped) | `docs/D1W-RUNBOOK.md` |

## Their develop as of c095ab5 (read 2026-11-14, 23:05 CET)

* **`/odom` is now integrated by default** (`odom_source: integrated`: body velocity times IMU yaw, 50 Hz,
  odom→base_link and odom→base_footprint TF). Our bridge subscribes to the same topic and gets more samples; nothing to
  change. `/odom_sdk` keeps the raw SDK position.
* **Link respawn:** the bridge exits after `link_timeout` (15 s) without the SDK and its launch file respawns it. We hand
  over after 1 s of no state (one `handover` event, zero velocity) and resume on `state_restored`.
* **New `/cmd_vel` publishers.** The robot now has up to **four**:
  * the Nav2 velocity smoother (`d1_nav`);
  * the developer dashboard's deadman pad (`d1_dashboard`, :8080, which also has stand / lie / damp / E-STOP buttons for
    the operator);
  * `keyboard_teleop`;
  * our edge.

  They all go straight to `agibot_d1_bridge`, so whichever publishes last wins. **Open item for Sunday:** run one
  motion source at a time, or put a `twist_mux` in front of the bridge with priorities, highest first:
  1. the human (dashboard pad, keyboard; a deadman);
  2. our governed edge;
  3. Nav2 (autonomous).

  Our L1-on-measured-motion stops the robot near the go-zone edge whoever drives, but it cannot out-publish a source
  that keeps sending. The cleaner future step: Nav2 plans and our edge executes, i.e. Nav2 goals go through our
  policy, or its output is remapped to `cmd_vel_nav` and our edge supervises it.
* **New perception:** `d1_camera` (RTSP to an image topic), `d1_depth` (monocular metric depth, floor calibration)
  and RTAB-Map localisation. These are good `PoseSource` / boundary inputs for later. A map-frame pose on `/amcl_pose`
  or RTAB-Map's `/localization_pose` can replace our odom→zone transform (table above).

## What we ask of their bridge (small, optional)

* Keep publishing `connected` while the SDK is down. It does today, and it's our fastest handover signal.
* Don't switch the gait from our side's topics. We only ever send "move". If someone sends "crawl" or "climb" from the
  keyboard, our caps still apply; their per-gait limits sit underneath.
* If they ever add a `/cmd_vel` mux: our publisher should rank **above** autonomous sources and **below** the human
  controller.

## Launch order on the team PC

The team bridge first, then the team detector, then our edge (`make edge`). The robot's own controller always outranks
software. Details: `docs/D1W-RUNBOOK.md`.
