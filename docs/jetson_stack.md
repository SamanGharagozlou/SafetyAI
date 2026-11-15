# Running the D1 stack on the Jetson Orin (Docker)

Why: decode, depth, mapping and Nav2 run next to the robot (direct ethernet), so no video crosses Wi-Fi. Tested 2026-11-15:
RTSP over ethernet gives a steady 15 fps, 0 read failures (the PC over Wi-Fi dropped frames).

## Network (Orin <-> robot, direct cable)
Orin `enP8p1s0` = `192.168.168.10/24`, robot = `192.168.168.168` (RTSP :8554 and SSH open). Use
`robot_ip:=192.168.168.168 local_ip:=192.168.168.10` for the bridge and `url:=rtsp://192.168.168.168:8554/test` for the camera.

## One-time setup (Jetson, JetPack 6 / L4T r36.4)
```bash
git clone --branch develop https://github.com/SamanGharagozlou/SafetyAI.git ~/d1_stack && cd ~/d1_stack
git submodule update --init third_party/agibot_D1_Edu-Ultra      # aarch64 SDK libs (do not recurse further)
sudo docker build -t d1-stack:humble-jp6 docker/d1-jetson         # ~30 min over Wi-Fi: ROS Humble, Nav2, RTAB-Map, PyTorch (JP6.1 wheel), transformers
docker/d1-jetson/run.sh bash -c "source /opt/ros/humble/setup.bash && cd /ws/ros2_ws && colcon build --symlink-install"
```
The image carries no CUDA: `run.sh` mounts the host's `/usr/local/cuda-12.6` and cuDNN/TensorRT libs and uses the NVIDIA runtime.
Check: `docker/d1-jetson/run.sh /opt/d1_depth_venv/bin/python -c "import torch;print(torch.cuda.is_available())"` -> `True`.

## Run
Inside `docker/d1-jetson/run.sh` (host network, `/ws` = the repo, `~/d1_maps` shared with the host):
```bash
source /opt/ros/humble/setup.bash && source /ws/ros2_ws/install/setup.bash
ros2 launch d1_dashboard dashboard.launch.py robot_ip:=192.168.168.168 local_ip:=192.168.168.10 host:=0.0.0.0
```
Open `http://<orin wifi ip>:8080` from the PC. Use a private `ROS_DOMAIN_ID` on this shared box.

## Gotchas
* The Orin is shared: another session ran `apt-get upgrade`, which restarted Docker and killed a build. The Dockerfile uses BuildKit cache mounts so a retry is cheap.
* Pin: `transformers==4.46.3` (works with torch 2.5); the PC uses newer versions with a newer torch.
* First depth run downloads the model into `~/.d1_hf_cache` (needs internet; the ethernet has none, so it goes over Wi-Fi).
