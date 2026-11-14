# human_perception: ROS 2 Humble + Ultralytics YOLO26 person detection (CPU by default).
#   make build                       -> builds this image
#   make build torch=cu128           -> CUDA PyTorch wheels instead of CPU
FROM ros:humble-ros-base-jammy

ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
# Weights baked into the image so the robot PC needs no internet at runtime.
# Space separated; empty = download on first start instead.
ARG PRELOAD_MODELS="yolo26n.pt"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ROS deps from apt (vision_msgs, rviz2) + software OpenGL for rviz2 + lint/test tools.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3-pip python3-colcon-common-extensions python3-pytest \
        ros-humble-vision-msgs ros-humble-diagnostic-msgs \
        ros-humble-rviz2 ros-humble-rviz-default-plugins \
        ros-humble-ament-flake8 ros-humble-ament-pep257 \
        libgl1 libgl1-mesa-dri libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Python ML stack. numpy<2: Humble's binary packages are built against numpy 1.x.
RUN pip3 install torch torchvision --index-url "${TORCH_INDEX_URL}" \
    && pip3 install "numpy>=1.23.5,<2" "ultralytics>=8.4,<9" "psutil>=5.9"

# torch pulls a new setuptools into /usr/local, which needs a newer `packaging` than
# Ubuntu's apt python3-packaging (21.3); without this colcon fails with
# "canonicalize_version() got an unexpected keyword argument 'strip_trailing_zero'".
RUN pip3 install "packaging>=24.2"

WORKDIR /ws
COPY ros2_ws/src/human_perception/ src/human_perception/
COPY ros2_ws/src/d1_camera/ src/d1_camera/
RUN . /opt/ros/humble/setup.sh && colcon build --event-handlers console_direct- \
    && rm -rf build log

RUN mkdir -p /root/.cache/human_perception/models && for m in ${PRELOAD_MODELS}; do \
        python3 -c "from ultralytics import YOLO; YOLO('/root/.cache/human_perception/models/$m')" \
        || exit 1; done

COPY docker/fastdds_profile.xml /etc/fastdds_profile.xml
ENV FASTRTPS_DEFAULT_PROFILES_FILE=/etc/fastdds_profile.xml
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD []
