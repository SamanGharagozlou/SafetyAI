# D1 developer dashboard

A local web UI for the whole workflow: start the robot pipeline, map, save, load a map, click to navigate, drive, stop.
Plain Python (no extra packages); the page talks to the server over HTTP + Server-Sent Events.

## Run

```bash
cd ros2_ws && source /opt/ros/humble/setup.bash && source install/setup.bash
ros2 launch d1_dashboard dashboard.launch.py robot_ip:=172.24.1.41 local_ip:=172.24.1.20     # or: ros2 run d1_dashboard dashboard
# open http://127.0.0.1:8080
```
Maps and logs live in `~/d1_maps` (`<name>.db` RTAB-Map database, `<name>_grid.pgm/.yaml` occupancy grid, `<name>.json`, `logs/*.log`).
Stopping the dashboard (Ctrl-C) stops the processes it started (the robot link only if the robot is not standing).

## Workflow buttons (left)

| Button | What it does |
|---|---|
| 1 Start process | `agibot_d1_bridge` (robot link, odometry, TF) and camera + depth (`d1_depth depth_demo`) |
| 2 Start map | RTAB-Map RGB-D mapping with a new database (optional name) |
| 3 Save map | saves the occupancy grid, then stops mapping so the database is flushed to disk |
| 4 Load map & navigate | RTAB-Map **localization** in the chosen database + the Nav2 stack (global/local costmaps, planner, MPPI) |
| 5 Clear all | cancels the goal, stops nav/localization/mapping/perception, clears the view. The robot link is kept if the robot is standing |

Each process also has its own start/stop and a log viewer. Header chips show robot link, mode, battery, workflow, localization and Nav state.

## On the map
- **Nav mode** (checkbox): click a spot = goal, drag to set the heading. Cancel button in the goal box. Moving the pad cancels navigation.
- Layers: map, global costmap, local costmap, plan. Wheel = zoom, drag = pan, "Follow robot".

## Robot control (bottom right)
Stand / Lie / Damp, a hold-to-move pad (speed slider 0.1-0.4 m/s) and keys W A S D (move) / Q E (turn). The server stops the robot 0.5 s after the
last pad message (deadman). **E-STOP** (big button, `Esc` or `Space`) = damp + cancel navigation. Do not rely on it as the only safety: it needs the SDK link.

## Tested (robot lying, 2026-11-14)
Start process, Start map, Save map, Load map (relocalized at the end pose of a recorded run), goal -> plan, Cancel, Clear all, page render and live data in a headless browser.
**Not yet tested:** autonomous motion with the robot standing, teleop from the pad on the real robot.

## Known limits
- Localization needs the camera to see something that matches the saved map (visual relocalization); after loading, drive a little if it says "Localized: no".
- Depth is only reliable within ~3 m (see `docs/costmap_plan.md`), so the costmap is short-range.
- The robot needs ~2 s to start moving after standing (SDK state change).


## Monocular SLAM is deactivated by default
Since 2026-11-15 the dashboard starts only the robot link; *Start map*, *Save map*, the camera obstacle layer and rosbag recording are hidden and the API refuses them. Pass `mono_slam:=true` (`--mono-slam true`) to enable them again. `external_robot:=true` makes the dashboard use a bridge that is already running (the SDK allows one client).

## Lidar-map navigation (branch `nav2`)
The workflow is now: Start process -> Load map (lidar map from `maps/` or a saved one) -> Place robot -> click goals -> Save map + start pose. See `docs/nav2_static.md`.
