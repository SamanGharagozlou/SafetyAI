#!/usr/bin/env bash
# Run the D1 stack container on a Jetson (JetPack 6). Mounts the host CUDA/cuDNN libraries (the image carries none),
# the repo at /ws, and a persistent HF cache. Usage: docker/d1-jetson/run.sh [command...]   (default: an interactive shell)
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
CUDA=/usr/local/cuda-12.6
MOUNTS=(-v "$CUDA:$CUDA:ro" -v "$REPO:/ws" -v "$HOME/.d1_hf_cache:/hf_cache" -v "$HOME/d1_maps:/root/d1_maps")
for f in /usr/lib/aarch64-linux-gnu/libcudnn*.so* /usr/lib/aarch64-linux-gnu/libnvinfer*.so* /usr/lib/aarch64-linux-gnu/libnvonnxparser*.so*; do
  [ -e "$f" ] && MOUNTS+=(-v "$f:/usr/lib/aarch64-linux-gnu/$(basename "$f"):ro")
done
mkdir -p "$HOME/.d1_hf_cache" "$HOME/d1_maps"
exec sudo docker run --rm -it --runtime nvidia --network host --ipc host \
  -e NVIDIA_VISIBLE_DEVICES=all -e NVIDIA_DRIVER_CAPABILITIES=all \
  -e LD_LIBRARY_PATH="/opt/cusparselt/lib:$CUDA/targets/aarch64-linux/lib:$CUDA/lib64" \
  "${MOUNTS[@]}" d1-stack:humble-jp6 "${@:-bash}"
