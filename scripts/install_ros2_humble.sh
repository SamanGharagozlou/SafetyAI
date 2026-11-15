#!/usr/bin/env bash
# System-wide ROS 2 Humble install for Ubuntu 22.04 (jammy), incl. Jetson aarch64. Run: bash scripts/install_ros2_humble.sh
set -euo pipefail

if [ "$(lsb_release -cs)" != "jammy" ]; then
    echo "This script targets Ubuntu 22.04 (jammy); found $(lsb_release -cs)." >&2
    exit 1
fi

sudo apt-get update
sudo apt-get install -y locales software-properties-common curl
sudo locale-gen en_US en_US.UTF-8
sudo update-locale LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8

sudo add-apt-repository -y universe

ROS_APT_SOURCE_VERSION=$(curl -s https://api.github.com/repos/ros-infrastructure/ros-apt-source/releases/latest | grep -F "tag_name" | awk -F'"' '{print $4}')
curl -L -o /tmp/ros2-apt-source.deb \
    "https://github.com/ros-infrastructure/ros-apt-source/releases/download/${ROS_APT_SOURCE_VERSION}/ros2-apt-source_${ROS_APT_SOURCE_VERSION}.$(lsb_release -cs)_all.deb"
sudo dpkg -i /tmp/ros2-apt-source.deb
rm -f /tmp/ros2-apt-source.deb

sudo apt-get update
sudo apt-get upgrade -y

# ros-base (headless) is enough on the Jetson; add ros-humble-desktop if a GUI is needed.
sudo apt-get install -y \
    ros-humble-ros-base \
    ros-dev-tools \
    python3-colcon-common-extensions \
    python3-rosdep \
    ros-humble-vision-msgs \
    ros-humble-diagnostic-msgs

if [ ! -f /etc/ros/rosdep/sources.list.d/20-default.list ]; then
    sudo rosdep init
fi
rosdep update

echo "Done. Activate with: source /opt/ros/humble/setup.bash"
