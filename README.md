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
