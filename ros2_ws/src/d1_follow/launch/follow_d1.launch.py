"""
Whole follow pipeline on the D1 robot: RTSP camera -> YOLO person detector -> d1_follow lock/track -> follow_relay.

  ros2 launch d1_follow follow_d1.launch.py
  ros2 run d1_follow drive on      # arm: only then does /cmd_vel move the robot

follow_relay starts DISARMED. The bridge (agibot_d1_bridge) must already be running; this launch never starts it.
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (DeclareLaunchArgument, IncludeLaunchDescription, OpaqueFunction,
                            SetEnvironmentVariable)
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

ARGS = [
    ('url', 'rtsp://192.168.168.168:8554/test', 'Robot RTSP stream'),
    ('fps', '15.0', 'Camera publish rate (Hz)'),
    ('scale', '0.5', 'Camera output size / 1920x1080'),
    # follow drops detections older than stale_s by header stamp, so the camera's default
    # 1.35 s latency back-dating (for SLAM) would make every detection "stale"
    ('stamp_offset_s', '0.0', 'Seconds subtracted from image stamps (keep 0 for follow)'),
    ('detector_params', 'detector_gpu.yaml', 'detector_cpu.yaml | detector_gpu.yaml'),
    ('device', '', 'Detector device override (empty = from YAML)'),
    ('model_name', 'yolo26n.pt', 'Detector model (n = fastest; yolo26s.pt is more accurate)'),
    ('auto_lock', 'true', 'Lock onto the first confirmed person; false = use `ros2 run d1_follow lock`'),
    ('embedder', 'osnet', 'osnet | histogram'),
    ('max_vx', '0.3', 'Relay forward speed cap (m/s)'),
    ('max_wz', '0.5', 'Relay yaw rate cap (rad/s)'),
    ('with_relay', 'true', 'Start follow_relay (disarmed); false = dry run only'),
]


def _bool(value):
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def _setup(context):
    a = {name: LaunchConfiguration(name).perform(context) for name, _, _ in ARGS}
    share = get_package_share_directory('d1_follow')

    camera = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(
            get_package_share_directory('d1_camera'), 'launch', 'camera.launch.py')),
        launch_arguments={k: a[k] for k in ('url', 'fps', 'scale', 'stamp_offset_s')}.items())

    # rtsp_camera publishes BEST_EFFORT: a reliable subscriber would get nothing
    detector_args = {'launch_source': 'false', 'input_reliability': 'best_effort',
                     'detector_params': a['detector_params'], 'rviz': 'false'}
    for k in ('device', 'model_name'):
        if a[k]:
            detector_args[k] = a[k]
    detector = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(
            get_package_share_directory('human_perception'), 'launch',
            'human_perception.launch.py')),
        launch_arguments=detector_args.items())

    follower = Node(
        package='d1_follow', executable='target_follower', name='d1_follow', output='screen',
        emulate_tty=True,
        parameters=[os.path.join(share, 'config', 'follow.yaml'),
                    {'image_reliability': 'best_effort', 'auto_lock': _bool(a['auto_lock']),
                     'embedder': a['embedder']}])
    actions = [camera, detector, follower]

    if _bool(a['with_relay']):
        actions.append(Node(
            package='d1_follow', executable='follow_relay', name='d1_follow_relay',
            output='screen', emulate_tty=True,
            parameters=[{'max_vx': float(a['max_vx']), 'max_wz': float(a['max_wz'])}]))
    return actions


def generate_launch_description():
    return LaunchDescription(
        [SetEnvironmentVariable('PYTHONNOUSERSITE', '1')]
        + [DeclareLaunchArgument(n, default_value=d, description=h) for n, d, h in ARGS]
        + [OpaqueFunction(function=_setup)])
