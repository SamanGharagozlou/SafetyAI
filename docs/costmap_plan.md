# Costmap from the single D1 camera: options and plan

Date: 2026-11-14. Status: investigation only, nothing implemented. Builds on `docs/mono_slam.md`.

## 1. Where we are

RGB-only RTAB-Map works (camera + our integrated odometry -> pose graph, ~115 poses after a drive, visible in RViz).
It gives a **trajectory**, not obstacles. A Nav2 costmap needs obstacle data (points or a scan) in the robot frame, and the only
sensor that can supply it is the one camera, so obstacles must be **estimated from the image**.

## 2. Options

| # | Approach | Needs | Verdict |
|---|---|---|---|
| A | **Monocular depth network -> PointCloud2 -> Nav2 `VoxelLayer`** | GPU PC (we have an RTX 3070 8 GB), calibrated camera | **Recommended.** This is the official Nav2 recipe |
| B | Floor segmentation + ground-plane projection (no depth net) | floor classifier, known camera height/tilt | Cheap but fragile (floor colour/texture, shadows); good as a fallback or cross-check |
| C | Feed the estimated depth to RTAB-Map (RGB-D mode) -> 3D map + `/map` occupancy grid | option A's depth image | Natural second step: same depth image serves the costmap *and* the map |
| D | Add a real sensor (2D/3D LiDAR, depth camera) | hardware | Most reliable; see `docs/slam_nav2_study.md` |

Reference for A: the Nav2 tutorial [AI Depth Estimation with Nav2 Costmap](https://github.com/ros-navigation/docs.nav2.org/blob/rolling/docs/tutorials/general_tutorials/depth_ai_integration/depth_ai_integration.md):
USB camera -> crop/decimate -> resize -> **Depth Anything V3 (TensorRT, metric, fp16)** -> `PointCloud2` -> `nav2_costmap_2d::VoxelLayer`
(local costmap 3x3 m rolling window, 5 cm cells, `min_obstacle_height 0.2`, `obstacle_max_range 8`, `raytrace_max_range 6`). It requires a calibrated camera and a CUDA GPU.
Packages: [ika-rwth-aachen/ros2-depth-anything-v3-trt](https://github.com/ika-rwth-aachen/ros2-depth-anything-v3-trt).
A lighter alternative for prototyping: [Depth Anything V2 Metric Indoor Small](https://huggingface.co/depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf) (25 M params, Apache-2.0, max depth 20 m) via PyTorch.

## 3. What this camera can and cannot see (measured/derived)

Inputs: standing body height **0.376 m** (SDK z while standing), camera ~0.02 m above it -> **~0.40 m**; camera tilted **up ~16 deg**; f ~735 px
(HFOV ~105 deg, VFOV ~73 deg after the robot's lens correction). Both f and tilt are estimates (see `mono_slam.md`).

- **Blind zone: the floor first appears ~1.07 m in front of the robot** (0.92-1.24 m for f +-10%). The camera's lowest ray is only ~20 deg below the horizon.
- Low objects are therefore seen late: a 5 cm cable at >0.94 m, a 10 cm box at >0.80 m, a 20 cm box at >0.53 m, a 30 cm box at >0.26 m; anything taller than the camera (0.40 m) at any distance.
- It also sees **up to 52 deg above the horizon**: ceilings and beams dominate the picture (see the sample frame). They must be removed with a maximum obstacle height (~0.5 m, the robot is 0.42 m tall).
- Consequence: **a camera-only costmap cannot protect against anything that appears inside ~1 m** (people stepping in, low objects not seen earlier). Mitigations: keep speed low (<= 0.3 m/s), let obstacles persist in the local costmap as the robot approaches (marking without aggressive clearing), and keep a human/keyboard `damp` available.
- Timing: video latency plus Wi-Fi jitter (~3-5 Hz delivered, stamped at PC receive time) -> obstacles are placed with an error of about speed x delay (0.2 m/s x 0.5 s = 10 cm).

## 4. Proposed pipeline (A, then C)

```
/camera/image_raw --> d1_depth (new node, GPU) --> /camera/depth (32FC1, m) + /camera/points (PointCloud2, optical frame)
                                                        |                                   |
                                    RTAB-Map RGB-D (3D map, /map grid)        Nav2 local costmap VoxelLayer
                                                                                            |
                                                              planner/MPPI -> /cmd_vel -> agibot_d1_bridge
```

Key design points:
1. **Metric scale from our own geometry.** Monocular depth is only approximately metric and depends on camera type/mount. We know camera height, tilt and intrinsics, so every pixel on the floor has an analytic depth. Fit a per-frame scale on floor pixels and rescale the whole depth map (the idea in [arXiv 2603.28890](https://arxiv.org/abs/2603.28890): use known-valid pixels to calibrate learned depth).
2. **Heights are measured from the floor.** Our TF `odom -> base_link` already carries the SDK body height (z), so the floor is z = 0 in `odom`, as the VoxelLayer expects. Set `min_obstacle_height ~0.10`, `max_obstacle_height ~0.5`.
3. **Costmap parameters for this robot:** footprint ~0.63 x 0.36 m (`[[0.315,0.18],[0.315,-0.18],[-0.315,-0.18],[-0.315,0.18]]`), local window ~4x4 m at 5 cm, global frame `odom`, `obstacle_max_range ~4` (monocular error grows with range), MPPI with the **Omni** model, speed caps from the bridge.
4. **Safety layers:** `nav2_collision_monitor` in front of `cmd_vel`, bridge speed clamps, and a stop on stale perception (no new cloud for > 1 s -> zero velocity).
5. **Compute:** inference on this PC's GPU; budget a few Hz (the camera delivers 3-5 Hz anyway). Rough, unmeasured: a small model should run well above that on an RTX 3070.

Already installed here: `nav2_costmap_2d`, `nav2_bringup`, `nav2_mppi_controller`, `nav2_collision_monitor`, `nav2_velocity_smoother`, `nav2_smac_planner`, `rtabmap_util` (has `obstacles_detection`, `point_cloud_xyz`).
Missing: `depth_image_proc`, `pointcloud_to_laserscan` (apt), PyTorch / ONNX Runtime (venv, a few GB), the depth model weights (download).

## 5. Steps

1. Install PyTorch (CUDA) + Transformers in a venv; load Depth Anything V2 Metric Indoor Small; run it on saved frames and on the live stream; check depth sanity against the known floor geometry.
2. `d1_depth` ROS 2 node: depth image + point cloud, floor-based scale fit, stamps from the image.
3. Standalone Nav2 local costmap (no planner yet) fed by the cloud; view in RViz while driving with the keyboard; tune heights/ranges; log false obstacles.
4. Checkerboard camera calibration to replace the estimated intrinsics.
5. RTAB-Map in RGB-D mode with the depth image (3D cloud map + occupancy grid).
6. Bring up the rest of Nav2 (MPPI Omni, collision monitor) at <= 0.2 m/s on a taped-off area.

## 6. Risks / open questions

- Depth quality on a low, upward-looking, lens-corrected wide camera is unproven; the indoor metric model may be biased. Step 1 measures it.
- The ~1 m blind zone is physical; only a different sensor or a different camera tilt removes it.
- Video + control share one Wi-Fi link; a link drop makes the robot damp after ~3 s. A wired link (or running everything on the robot) removes this.
- Nav2 start delay: the robot needs ~2 s to start moving after standing (SDK state change).

## 7. Step 1 results: Depth Anything V2 Metric Indoor Small on real D1 frames (2026-11-14)

Setup: `~/d1_depth_venv` (uv; PyTorch 2.14 + CUDA 13, Transformers, 5.6 GB), RTX 3070. 8 frames from the live stream, robot **lying** (camera ~0.177 m high), compared with the analytic floor depth from camera height, tilt (16 deg up) and f = 735 px.

- **Speed:** 24 ms/frame (~42 fps) including pre/post-processing, 0.22 GB GPU memory, 59 s first load (download), <1 s afterwards. Not a bottleneck.
- **Metric scale is wrong zero-shot:** the model overestimates floor depth by **~3.5x** (ratio 3.1-3.6 across frames; 240-335% error). Expected for a camera far lower than the training data (indoor model, ~human-height cameras).
- **Quality:** the floor gradient is smooth; tables and boxes are well separated. Mid-range objects in front of a far wall (two banners ~2.5 m away) are merged into the far background (>10 m); the model also caps at 20 m.
- **Floor-based calibration fixes the scale.** Median relative error on floor rows vs the analytic depth:

| Method | 0.4-1 m | 1-2 m | 2-4 m |
|---|---|---|---|
| raw model | 241% | 278% | 335% |
| one scale per frame (stable: 0.293 +-5%) | 2% | 10% | 25% |
| global affine in 1/depth, leave-one-out | 3% | 2% | 3% |
| per-frame affine in 1/depth (in-sample) | 0% | 0% | 1% |

  After a global scale (0.293) the banner base comes out at ~2.9 m, matching the floor geometry (2.3-2.8 m). **An affine fit in inverse depth (1/z = a/d + b) on the floor rows is the right calibration** and should be done per frame, because the camera height changes between lying (0.18 m) and standing (0.40 m).
- **Caveats:** all 8 frames are from one static viewpoint, so the leave-one-out numbers are optimistic. Not yet tested: standing height, motion, other scenes, and accuracy of depth on *objects* (only the floor has ground truth).

## 8. Step 2: `d1_depth` node (built, tested offline on saved frames)

Package `ros2_ws/src/d1_depth`. Run: `ros2 launch d1_depth depth.launch.py` (needs `/camera/image_raw` from `d1_camera` and TF `odom -> camera_optical_frame`, i.e. the bridge running).
Torch/Transformers come from `~/d1_depth_venv` (env `D1_DEPTH_VENV`; same Python 3.10, its NumPy 1.26 is put first on `sys.path`).

Per frame: Depth Anything V2 -> camera pose from TF -> analytic floor depth for every pixel -> robust fit `1/z_floor = a/d + b` on floor pixels (outlier trimming, EMA over frames)
-> metric depth image `/camera/depth` (32FC1) and XYZ cloud `/camera/points` (camera optical frame, stride 4, `max_range` 3 m).

Measured (replay of the 8 real frames, robot lying):
- 5.0 Hz sustained (rate-limited; callback ~45-60 ms, model ~35-42 ms, so ~16 Hz capacity), floor fit a = 3.0-3.4, b = 0.10-0.12 on ~19k pixels, median floor error ~1%.
- Cloud ~31k points; floor points land at z = 0.000 +- 0.012 m in the floor frame (independent check of the TF/geometry math).
- Bugs found and fixed on the way: the robust fit could collapse (sigma floor added); the rate gate aliased against the 10 Hz input (tolerance added); large Python-side cost was not the problem (post-processing < 10 ms).

**Hard limit found: usable range is only ~3 m.** The raw model returns 12-20 m for far surfaces (a hall wall that is really 25 m+). Through the floor calibration, 12-19 m maps to 2.5-3.6 m, so a far wall is indistinguishable from a near obstacle.
Masking the model's 20 m cap removed only ~3% of the points; the fix is a hard `max_range` of 3 m (a 3x3-4x4 m local costmap is within that). Objects between ~3 and 6 m are not reliably seen by this setup.

Not yet validated (robot was off): standing height, motion, depth accuracy on real obstacles with measured distances (put a box at 1.5 m and 2.5 m and compare in RViz), false obstacles from far walls at standing height, latency.

Next: with the robot standing, run camera + depth + RViz (PointCloud2 `/camera/points`), measure box distances, then feed the cloud to a standalone Nav2 local costmap (`VoxelLayer`, `min_obstacle_height 0.10`, `max_obstacle_height 0.5`, `obstacle_max_range 3`).
