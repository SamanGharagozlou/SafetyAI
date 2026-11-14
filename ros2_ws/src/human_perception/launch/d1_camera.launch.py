"""
Teammate's d1_camera driver + human detector in one launch (same container, same DDS vendor).

d1_camera publishes /camera/image_raw as best-effort, so the detector subscribes best-effort.
Image frames never leave the process group, which is what makes 1.5 MB frames reliable.

Run with:
  ros2 launch human_perception d1_camera.launch.py url:=rtsp://<robot-ip>:8554/test
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration

CAMERA_ARGS = ['url', 'fps', 'scale', 'camera_info_file']
DETECTOR_ARGS = ['detector_params', 'model_name', 'device', 'publish_annotated_image', 'rviz']


def generate_launch_description():
    camera = os.path.join(get_package_share_directory('d1_camera'), 'launch',
                          'camera.launch.py')
    detector = os.path.join(get_package_share_directory('human_perception'), 'launch',
                            'detector_only.launch.py')
    args = [DeclareLaunchArgument('url', default_value='rtsp://192.168.234.1:8554/test'),
            DeclareLaunchArgument('fps', default_value='15.0'),
            DeclareLaunchArgument('scale', default_value='0.5'),
            DeclareLaunchArgument('camera_info_file', default_value=''),
            DeclareLaunchArgument('detector_params', default_value='detector_cpu.yaml'),
            DeclareLaunchArgument('model_name', default_value=''),
            DeclareLaunchArgument('device', default_value=''),
            DeclareLaunchArgument('publish_annotated_image', default_value=''),
            DeclareLaunchArgument('rviz', default_value='false')]
    detector_args = {'input_reliability': 'best_effort'}
    detector_args.update({k: LaunchConfiguration(k) for k in DETECTOR_ARGS})
    return LaunchDescription(args + [
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(camera),
            launch_arguments={k: LaunchConfiguration(k) for k in CAMERA_ARGS}.items()),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(detector),
            launch_arguments=detector_args.items()),
    ])
