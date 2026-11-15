we do later 

## Perception (human detection)

ROS 2 Humble person detection (YOLO26) on the D1 camera or a webcam. Inference runs on the PC, never on the robot.

```bash
make build                                         # once: Docker image
make up rviz=true                                  # laptop webcam + rviz2
make up source=d1-wifi robot_ip=<robot-ip> rviz=true
make down
```

Details: [docs/perception.md](docs/perception.md). Robot controls: [docs/controls.md](docs/controls.md).
