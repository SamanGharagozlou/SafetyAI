# syntax=docker/dockerfile:1
# D1 stack on a Jetson Orin (JetPack 6 / L4T r36.4, aarch64): ROS 2 Humble + Nav2 + RTAB-Map + PyTorch (CUDA) for the depth node.
# Build:  sudo docker build -t d1-stack:humble-jp6 docker/d1-jetson      Run: see docs/jetson_stack.md
FROM ros:humble-ros-base-jammy
ENV DEBIAN_FRONTEND=noninteractive
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean && apt-get update && apt-get install -y --no-install-recommends \
      ros-humble-navigation2 ros-humble-nav2-bringup ros-humble-rtabmap-ros \
      ros-humble-cv-bridge ros-humble-vision-opencv ros-humble-image-transport \
      ros-humble-tf2-ros ros-humble-tf2-tools ros-humble-rosbag2-storage-default-plugins \
      python3-pip python3-venv python3-colcon-common-extensions python3-opencv python3-yaml \
      python3-websockets python3-numpy python3-scipy curl ca-certificates xz-utils iproute2 iputils-ping

# cuSPARSELt (needed by the JetPack 6.1 PyTorch wheel). Kept outside /usr/local/cuda, which the NVIDIA runtime mounts from the host.
RUN mkdir -p /opt/cusparselt && curl -fsSL https://developer.download.nvidia.com/compute/cusparselt/redist/libcusparse_lt/linux-aarch64/libcusparse_lt-linux-aarch64-0.6.2.3-archive.tar.xz \
      | tar -xJ -C /opt/cusparselt --strip-components=1
ENV LD_LIBRARY_PATH=/opt/cusparselt/lib:/usr/local/cuda/lib64

# Depth-node environment (same layout as on the PC: d1_depth puts $D1_DEPTH_VENV site-packages first on sys.path).
RUN --mount=type=cache,target=/root/.cache/pip python3 -m venv /opt/d1_depth_venv && /opt/d1_depth_venv/bin/pip install -U pip \
 && /opt/d1_depth_venv/bin/pip install "numpy<2" \
      "https://developer.download.nvidia.com/compute/redist/jp/v61/pytorch/torch-2.5.0a0+872d972e41.nv24.08.17622132-cp310-cp310-linux_aarch64.whl" \
 && /opt/d1_depth_venv/bin/pip install "numpy<2" "transformers==4.46.3" "huggingface-hub<1" pillow
ENV D1_DEPTH_VENV=/opt/d1_depth_venv HF_HOME=/hf_cache
WORKDIR /ws
CMD ["bash"]
