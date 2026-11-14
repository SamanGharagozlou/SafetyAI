"""
AGIBOT D1 Ultra camera over Ethernet RTSP -> human detector (inference off-robot).

Run with:
  ros2 launch human_perception d1_ethernet.launch.py
  ros2 launch human_perception d1_ethernet.launch.py rtsp_url:=rtsp://<ip>:8554/<path>
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration

DEFAULT_URL = 'rtsp://192.168.168.168:8554/test'  # overridable default, not hard-wired in the node
PASS_THROUGH = ['rtsp_url', 'capture_backend', 'detector_params', 'model_name', 'device',
                'publish_annotated_image', 'rviz']


def generate_launch_description():
    base = os.path.join(get_package_share_directory('human_perception'), 'launch',
                        'human_perception.launch.py')
    args = [DeclareLaunchArgument('rtsp_url', default_value=DEFAULT_URL),
            DeclareLaunchArgument('capture_backend', default_value=''),
            DeclareLaunchArgument('detector_params', default_value='detector_cpu.yaml'),
            DeclareLaunchArgument('model_name', default_value=''),
            DeclareLaunchArgument('device', default_value=''),
            DeclareLaunchArgument('publish_annotated_image', default_value=''),
            DeclareLaunchArgument('rviz', default_value='false')]
    forwarded = {'source_type': 'rtsp', 'source_params': 'd1_rtsp.yaml'}
    forwarded.update({k: LaunchConfiguration(k) for k in PASS_THROUGH})
    return LaunchDescription(args + [IncludeLaunchDescription(
        PythonLaunchDescriptionSource(base), launch_arguments=forwarded.items())])
