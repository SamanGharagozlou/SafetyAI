"""
Laptop webcam -> human detector.

Run with:
  ros2 launch human_perception webcam.launch.py webcam_device:=0 publish_annotated_image:=true
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration

PASS_THROUGH = ['detector_params', 'model_name', 'device', 'publish_annotated_image', 'rviz']


def generate_launch_description():
    base = os.path.join(get_package_share_directory('human_perception'), 'launch',
                        'human_perception.launch.py')
    args = [DeclareLaunchArgument('webcam_device', default_value='0'),
            DeclareLaunchArgument('detector_params', default_value='detector_cpu.yaml'),
            DeclareLaunchArgument('model_name', default_value=''),
            DeclareLaunchArgument('device', default_value=''),
            DeclareLaunchArgument('publish_annotated_image', default_value=''),
            DeclareLaunchArgument('rviz', default_value='false')]
    forwarded = {'source_type': 'webcam',
                 'webcam_device': LaunchConfiguration('webcam_device')}
    forwarded.update({k: LaunchConfiguration(k) for k in PASS_THROUGH})
    return LaunchDescription(args + [IncludeLaunchDescription(
        PythonLaunchDescriptionSource(base), launch_arguments=forwarded.items())])
