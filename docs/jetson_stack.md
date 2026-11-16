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

## Using a bridge that is already running (shared Orin)
The SDK accepts one client. If someone already runs `agibot_d1_bridge` on the Orin, do not start a second one (it fails with
`bind: Address already in use`). Start the dashboard with `external_robot:=true`: it never starts/stops a bridge and
uses the running bridge's `/odom`, `/imu`, `/tf` (same ROS domain), while camera, depth, RTAB-Map and Nav2 run in the container.

## Follow dependencies (ultralytics, boxmot, torchvision)
The image has the Jetson CUDA torch only in `/opt/d1_depth_venv`. `d1_follow` needs ultralytics, boxmot and a torchvision
built for that torch, kept in a persistent venv `~/.d1_hf_cache/follow_venv` (mounted at `/hf_cache`) by `docker/d1-jetson/follow.sh`.
Recipe (run in a container with the `run.sh` CUDA/cuDNN mounts and `--ipc host`; ~25 min, mostly the torchvision build):
1. `python3 -m venv /hf_cache/follow_venv`, then put `/opt/d1_depth_venv/lib/python3.10/site-packages` in a `.pth` file in its site-packages so pip sees the Jetson torch.
2. `pip install -c <numpy<2> ultralytics "boxmot==19.0.0"`, then `pip uninstall -y torch opencv-python huggingface-hub`: pip pulls a CPU torch, a pip OpenCV (shadows the apt one ROS needs) and a newer huggingface-hub (breaks the depth node) that shadow the image's versions.
3. PyPI's torchvision 0.20.0 does not load against `torch 2.5.0a0+nv24.08` (`operator torchvision::nms does not exist`), and a CPU-only build fails in ultralytics' NMS warm-up on CUDA. Build from the v0.20.0 tarball: `apt-get install g++ ninja-build python3-dev libjpeg-dev libpng-dev zlib1g-dev`, then `CUDA_HOME=/usr/local/cuda-12.6 FORCE_CUDA=1 TORCH_CUDA_ARCH_LIST=8.7 MAX_JOBS=2 pip install --no-build-isolation --no-deps .` (`MAX_JOBS=2`: more runs out of RAM).
4. `PYTHONPATH` must name both site-packages explicitly (`.pth` files are not read via `PYTHONPATH`); `follow.sh` sets it.
Also: CUDA allocations fail (`NVML_SUCCESS == r INTERNAL ASSERT`, `CUBLAS_STATUS_ALLOC_FAILED`) when other stacks (depth, RTAB-Map, Nav2) leave under ~2 GB free.
