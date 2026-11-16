# SafetyAI — D1 spa companion robot

A companion and escort robot for a thermal spa on the AgiBot D1 Ultra W with a Jetson Orin Nano. Guests scan a QR code,
talk to the robot from their phone, get answers about the facility, and are walked to where they want to go.

- Product requirements: [docs/product_requirements.md](docs/product_requirements.md)
- System architecture: [docs/system_architecture.md](docs/system_architecture.md)
- Features: [ROS 2 camera](docs/feature_ros2_camera.md), [SLAM/localization](docs/feature_slam_localization.md),
  [privacy/GDPR anonymization](docs/feature_privacy_anonymization.md)
- Contributor and agent guide (repo map, rules, all docs): [AGENTS.md](AGENTS.md)

## Perception (human detection)

ROS 2 Humble person detection (YOLO26) on the D1 camera or a webcam. Inference runs on the PC, never on the robot.

```bash
make build                                         # once: Docker image
make up rviz=true                                  # laptop webcam + rviz2
make up source=d1-wifi robot_ip=<robot-ip> rviz=true
make down
```

Details: [docs/perception.md](docs/perception.md). Robot controls: [docs/controls.md](docs/controls.md).

## Human follow on the D1 (Jetson Orin)

The robot locks onto one person in its camera and follows them (turns and drives forward only; no reverse or sideways).
Pipeline: RTSP camera -> YOLO26n on the Orin GPU -> `d1_follow` (lock + OSNet re-ID) -> `follow_relay` -> `/cmd_vel`.
**Status: lock and tracking verified on the robot camera; motion on the robot is unverified.** Details, tuning and
limits: [docs/human_follow.md](docs/human_follow.md).

Prerequisites (on the Orin):

- The bridge container `d1mono` (or the `d1-bridge` unit) is running. Never start a second SDK client.
- Nothing else writes `/cmd_vel` and the RAM is free: `docker stop d1dash` (and stop any other Nav2/depth/camera stack).
- Someone stands at the remote / E-STOP, with a clear floor.
- ROS packages built once: `docker run --rm -v $PWD:/ws d1-stack:humble-jp6 bash -c 'source /opt/ros/humble/setup.bash; cd /ws/ros2_ws && colcon build --symlink-install --packages-select human_perception d1_camera d1_follow'`
- Python deps (ultralytics, boxmot, CUDA torchvision matching the image's Jetson torch) live in
  `~/.d1_hf_cache/follow_venv`; the image needs `ros-humble-vision-msgs` and `ros-humble-diagnostic-msgs` (in
  `docker/d1-jetson/Dockerfile`). Rebuild recipe: [docs/jetson_stack.md](docs/jetson_stack.md).

Run:

```bash
docker stop d1dash                                              # free RAM, remove other /cmd_vel writers
cd ~/SafetyAI && docker/d1-jetson/follow.sh                     # camera + detector + follow + relay (DISARMED)
docker/d1-jetson/follow-cmd.sh ros2 topic echo /follow/state    # second shell: wait for TRACKING
docker/d1-jetson/follow-cmd.sh drive on                         # arm (expires after 120 s)
docker/d1-jetson/follow-cmd.sh drive off                        # disarm; Ctrl-C in the first shell also stops
docker/d1-jetson/follow-cmd.sh lock                             # manual lock (auto_lock needs a single person in view)
docker/d1-jetson/follow-cmd.sh release
docker rm -f d1follow                                           # before relaunching after a stop
```

Launch options: `docker/d1-jetson/follow.sh auto_lock:=false model_name:=yolo26s.pt device:=cpu max_vx:=0.2 with_relay:=false`
(`with_relay:=false` = dry run, `/follow/cmd_vel` only). Speed caps default to 0.3 m/s and 0.5 rad/s.
