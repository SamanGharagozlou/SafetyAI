# Maps

One folder per MetaCam scan: `map.yaml` + `map.pgm` (Nav2 `map_server` format, frame `map`, origin = scan start),
`map_grid.png` (1 m grid for picking start poses), `map_trajectory.png`, `README.md`, and where present
`features_overlay.png` (obstacles by height + walked path).

| Folder | Scan | Notes |
|---|---|---|
| `metacam_lite_2026-11-14/` | 84 s, 28 m | first map; low obstacles (boxes) are weak, see below |
| `metacam_2026-11-14_22-35-test2/` | 55 s, 11 m | boxes near the origin |
| `metacam_2026-11-14_22-32_test/` | 111 s, 18 m | same hall, longer walk |
| `metacam_2026-11-14_22-25-20/` | 235 s, 78 m | most complete |

The three newer maps were built with the updated `tools/mcap_to_2d_map.py`: free space from ray-casting, obstacles from
the raw scans plus the registered point cloud in a 0.15-1.2 m band (so box sides and tops count; tables are solid).
The first map used the old 0.15-0.5 m band only. Use any of them with
`ros2 launch d1_localization localize.launch.py map:=<repo>/maps/<folder>/map.yaml`.
Rebuild: `python3 tools/mcap_to_2d_map.py <recording_dir>` then `python3 tools/make_feature_map.py <recording_dir>`
(needs the raw recording, which is not in git).
