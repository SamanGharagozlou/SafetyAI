#!/usr/bin/env bash
# Run the human-follow pipeline (camera -> YOLO -> d1_follow -> follow_relay) in container `d1follow` on the Orin.
# The bridge (container d1mono / d1-bridge unit) must already be running; this never starts a second SDK client.
# Usage: docker/d1-jetson/follow.sh [follow_d1.launch.py args, e.g. auto_lock:=false device:=cpu]
# Other shells: docker/d1-jetson/follow-cmd.sh drive on | drive off | lock | release
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
CUDA=/usr/local/cuda-12.6
# follow_venv (ultralytics, boxmot, torchvision) sits in front of the image's Jetson-torch venv; see docs/human_follow.md
PYLIBS=/hf_cache/follow_venv/lib/python3.10/site-packages:/opt/d1_depth_venv/lib/python3.10/site-packages
MOUNTS=(-v "$CUDA:$CUDA:ro" -v "$REPO:/ws" -v "$HOME/.d1_hf_cache:/hf_cache"
        -v "$HOME/.d1_hf_cache/models:/root/.cache/human_perception/models")
for f in /usr/lib/aarch64-linux-gnu/libcudnn*.so* /usr/lib/aarch64-linux-gnu/libnvinfer*.so* /usr/lib/aarch64-linux-gnu/libnvonnxparser*.so*; do
  [ -e "$f" ] && MOUNTS+=(-v "$f:/usr/lib/aarch64-linux-gnu/$(basename "$f"):ro")
done
mkdir -p "$HOME/.d1_hf_cache/models"
exec docker run --rm -it --name d1follow --runtime nvidia --network host --ipc host \
  -e NVIDIA_VISIBLE_DEVICES=all -e NVIDIA_DRIVER_CAPABILITIES=all \
  -e LD_LIBRARY_PATH="/opt/cusparselt/lib:$CUDA/targets/aarch64-linux/lib:$CUDA/lib64" \
  -e PYTHONPATH="$PYLIBS" -e PYTHONNOUSERSITE=1 -e HF_HOME=/hf_cache \
  "${MOUNTS[@]}" d1-stack:humble-jp6 bash -c \
  'source /opt/ros/humble/setup.bash; source /ws/ros2_ws/install/setup.bash; ros2 launch d1_follow follow_d1.launch.py "$@"' _ "$@"
