# 2D map from MetaCam Lite scan (2026-11-14_19-40-52)

2D occupancy grid for ROS 2 (Nav2 `map_server` format), built from the MetaCam lidar scan.

## Files (send these two together, same folder)
| File | What |
|---|---|
| `map.yaml` | map metadata: resolution 0.05 m/cell, origin, thresholds |
| `map.pgm`  | the grid image (white = free, black = wall, grey = unknown) |

Optional: `map_grid.png` (metre grid, for picking positions), `map_trajectory.png` (path walked during the scan).

## Frame and origin (important)
- Frame is `map`. **(0, 0) is where the MetaCam started its scan**, x/y axes are the MetaCam's axes at that moment.
- To use the map, the robot must start at that same physical spot, facing the same direction the scanner faced.
  If it can't, give the robot's real pose in this map instead (x, y, yaw) – see below.
- Scale is metric and real (grid in `map_grid.png` is 1 m).

## Use with the D1
Package `d1_localization` (`ros2_ws/src/d1_localization`) loads this map and places the robot in it.
Full steps: `docs/map_localization.md`. Short version, with the D1 standing at the scan start pose:
```bash
ros2 launch agibot_d1_bridge bridge.launch.py local_ip:=<pc-ip> robot_ip:=<robot-ip>   # terminal 1
ros2 launch d1_localization localize.launch.py                                          # terminal 2
```
Different start pose: add `x:=3.0 y:=1.5 yaw:=1.57` (map frame, metres / radians), or use RViz "2D Pose Estimate".

## Known limits
- One height slab only (obstacles 0.2–0.5 m above floor); ceilings/overhangs are not in the map.
- Hall is larger than the area scanned: top/bottom walls are missing, edges are streaky.
- Rebuild with other settings: `python3 tools/mcap_to_2d_map.py <recording_dir> --max-h 0.5 --range 10`
  (needs the raw recording, which is NOT in git – 1.2 GB).
- RViz on some GPUs draws free space grey (GLSL error): run with `LIBGL_ALWAYS_SOFTWARE=1`; the map file is fine.
