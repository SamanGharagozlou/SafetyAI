"""
Person detection + lock-on follow (dry-run /follow/cmd_vel).

  ros2 launch d1_follow follow.launch.py source_type:=webcam rviz:=true
  ros2 launch d1_follow follow.launch.py with_perception:=false   # reuse a running detector
  ros2 run d1_follow lock        # lock onto the person in front of the camera
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (DeclareLaunchArgument, IncludeLaunchDescription, OpaqueFunction,
                            SetEnvironmentVariable)
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

PKG = 'd1_follow'

# forwarded to human_perception.launch.py when non-empty
PERCEPTION_ARGS = [
    ('source_type', 'webcam', 'webcam | rtsp | file'),
    ('webcam_device', '0', 'Webcam index or device path'),
    ('rtsp_url', '', 'RTSP URL (source_type:=rtsp)'),
    ('file_path', '', 'Video file (source_type:=file)'),
    ('source_params', '', 'Extra source YAML layered over video_source.yaml'),
    ('detector_params', 'detector_cpu.yaml', 'Detector YAML (file in human_perception config/)'),
    ('model_name', '', 'Override detector model, e.g. yolo26s.pt'),
    ('device', '', 'Detector device: cpu | cuda:0 ... (empty = from YAML)'),
]

ARGS = [
    ('with_perception', 'true', 'Also start video source + human detector'),
    *PERCEPTION_ARGS,
    ('auto_lock', 'false', 'Lock onto the first confirmed person without a service call'),
    ('embedder', 'osnet', 'osnet | histogram'),
    ('params', 'follow.yaml', 'follow YAML: file in this package config/ dir or absolute path'),
    ('rviz', 'false', 'Start rviz2 showing /follow/annotated_image'),
]


def _bool(value):
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def _setup(context):
    a = {name: LaunchConfiguration(name).perform(context) for name, _, _ in ARGS}
    share = get_package_share_directory(PKG)
    actions = []

    if _bool(a['with_perception']):
        forwarded = {name: a[name] for name, _, _ in PERCEPTION_ARGS if a[name]}
        forwarded['rviz'] = 'false'
        launch_file = os.path.join(get_package_share_directory('human_perception'),
                                   'launch', 'human_perception.launch.py')
        actions.append(IncludeLaunchDescription(
            PythonLaunchDescriptionSource(launch_file), launch_arguments=forwarded.items()))

    params = a['params'] if os.path.isabs(a['params']) else os.path.join(
        share, 'config', a['params'])
    actions.append(Node(
        package=PKG, executable='target_follower', name='d1_follow', output='screen',
        emulate_tty=True,
        parameters=[params, {'auto_lock': _bool(a['auto_lock']), 'embedder': a['embedder']}]))

    if _bool(a['rviz']):
        actions.append(Node(package='rviz2', executable='rviz2', name='rviz2', output='log',
                            arguments=['-d', os.path.join(share, 'rviz', 'follow.rviz')]))
    return actions


def generate_launch_description():
    return LaunchDescription(
        # ~/.local numpy 2.x breaks ROS's OpenCV
        [SetEnvironmentVariable('PYTHONNOUSERSITE', '1')]
        + [DeclareLaunchArgument(n, default_value=d, description=h) for n, d, h in ARGS]
        + [OpaqueFunction(function=_setup)])
