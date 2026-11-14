"""
Detector only: subscribe to an EXISTING image topic from any ROS 2 camera driver.

Run with:
  ros2 launch human_perception detector_only.launch.py image_topic:=/my_camera/image_raw
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration

PASS_THROUGH = ['image_topic', 'detector_params', 'model_name', 'model_path', 'device',
                'backend', 'publish_annotated_image', 'rviz']


def generate_launch_description():
    base = os.path.join(get_package_share_directory('human_perception'), 'launch',
                        'human_perception.launch.py')
    args = [DeclareLaunchArgument('image_topic', default_value='/camera/image_raw'),
            DeclareLaunchArgument('detector_params', default_value='detector_cpu.yaml'),
            DeclareLaunchArgument('model_name', default_value=''),
            DeclareLaunchArgument('model_path', default_value=''),
            DeclareLaunchArgument('device', default_value=''),
            DeclareLaunchArgument('backend', default_value=''),
            DeclareLaunchArgument('publish_annotated_image', default_value=''),
            DeclareLaunchArgument('rviz', default_value='false')]
    forwarded = {'launch_source': 'false'}
    forwarded.update({k: LaunchConfiguration(k) for k in PASS_THROUGH})
    return LaunchDescription(args + [IncludeLaunchDescription(
        PythonLaunchDescriptionSource(base), launch_arguments=forwarded.items())])
