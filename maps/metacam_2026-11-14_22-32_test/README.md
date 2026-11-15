# 2D map from MetaCam Lite scan (2026-11-14_22-32_test)

Occupancy grid for ROS 2 (Nav2 map_server format): `map.yaml` + `map.pgm` (white free, black wall, grey unknown).
Resolution 0.05 m/cell, size 40.4 x 22.4 m, origin [-19.257, -19.511, 0.0]. Scan: 00:01:51, 18.1m walked.

- Frame `map`: (0, 0) is where the MetaCam started the scan, axes as the scanner faced at that moment.
- Free space: ray-cast from the raw lidar scans and the device trajectory (rays only clear cells where the ray itself is low, so low obstacles are not erased).
- Obstacles: raw scans plus the registered point cloud (Preview.pcd), band 0.15-1.2 m above floor, so box sides and tops are both counted. Overhead things above 1.2 m are ignored; tables count as solid.
- `map_grid.png` has a 1 m grid for reading start poses; `map_trajectory.png` shows the walked path.
- `features_overlay.png`: obstacles coloured by height (red low, orange mid, blue high) plus the walked path.
- Short walk, large hall: far edges are streaky and the outer walls are only partly seen.
