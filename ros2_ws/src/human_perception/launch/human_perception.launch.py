"""
Video source + human detector.  All behaviour is driven by launch arguments / YAML.

Usage examples (shell):
  ros2 launch human_perception human_perception.launch.py source_type:=webcam webcam_device:=0
  ros2 launch human_perception human_perception.launch.py source_type:=rtsp \
      rtsp_url:=rtsp://192.168.234.1:8554/test
  ros2 launch human_perception human_perception.launch.py launch_source:=false \
      image_topic:=/my_camera/image_raw
  ros2 launch human_perception human_perception.launch.py rviz:=true
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

PKG = 'human_perception'

ARGS = [
    ('launch_source', 'true', 'Start the video_source node (false = detector only)'),
    ('source_type', 'webcam', 'webcam | rtsp | file'),
    ('webcam_device', '0', 'Webcam index or device path'),
    ('rtsp_url', '', 'RTSP URL (required when source_type:=rtsp)'),
    ('file_path', '', 'Video file (source_type:=file), e.g. for headless tests'),
    ('capture_backend', '', 'auto | ffmpeg | gstreamer | v4l2 (empty = from YAML)'),
    ('frame_id', '', 'Image frame_id (empty = from YAML)'),
    ('source_params', '', 'Extra source YAML layered over video_source.yaml'),
    ('detector_params', 'detector_cpu.yaml',
     'Detector YAML: a file name in this package config/ dir or an absolute path'),
    ('image_topic', '/camera/image_raw', 'Image topic the detector subscribes to (remap)'),
    ('model_name', '', 'Override model, e.g. yolo26s.pt (empty = from YAML)'),
    ('model_path', '', 'Local model file/dir (overrides model_name)'),
    ('device', '', 'cpu | cuda:0 | intel:cpu ... (empty = from YAML)'),
    ('backend', '', 'pytorch | onnx | openvino | tensorrt (empty = from YAML)'),
    ('publish_annotated_image', '', 'true | false (empty = from YAML; rviz:=true turns it on)'),
    ('rviz', 'false', 'Start rviz2 showing the annotated detections'),
    ('rviz_config', 'human_perception.rviz', 'rviz config: file in package rviz/ dir or abs path'),
    ('log_level', 'info', 'ROS log level'),
]


def _cfg(name):
    return os.path.join(get_package_share_directory(PKG), 'config', name)


def _resolve_yaml(value):
    return value if os.path.isabs(value) else _cfg(value)


def _bool(value):
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def _setup(context):
    a = {name: LaunchConfiguration(name).perform(context) for name, _, _ in ARGS}
    actions = []
    ros_args = ['--ros-args', '--log-level', a['log_level']]

    if _bool(a['launch_source']):
        params = [_cfg('video_source.yaml')]
        if a['source_params']:
            params.append(_resolve_yaml(a['source_params']))
        overrides = {'source_type': a['source_type'], 'webcam_device': a['webcam_device']}
        for key in ('rtsp_url', 'file_path', 'capture_backend', 'frame_id'):
            if a[key]:
                overrides[key] = a[key]
        params.append(overrides)
        actions.append(Node(package=PKG, executable='video_source', name='video_source',
                            output='screen', emulate_tty=True, parameters=params,
                            arguments=ros_args))

    det_params = [_resolve_yaml(a['detector_params'])]
    overrides = {}
    for key in ('model_name', 'model_path', 'device', 'backend'):
        if a[key]:
            overrides[key] = a[key]
    if a['publish_annotated_image']:
        overrides['publish_annotated_image'] = _bool(a['publish_annotated_image'])
    elif _bool(a['rviz']):
        overrides['publish_annotated_image'] = True  # rviz shows the annotated image
    if overrides:
        det_params.append(overrides)
    remaps = []
    if a['image_topic'] and a['image_topic'] != '/camera/image_raw':
        remaps.append(('/camera/image_raw', a['image_topic']))
    actions.append(Node(package=PKG, executable='human_detector', name='human_detector',
                        output='screen', emulate_tty=True, parameters=det_params,
                        remappings=remaps, arguments=ros_args))

    if _bool(a['rviz']):
        cfg = a['rviz_config']
        if not os.path.isabs(cfg):
            cfg = os.path.join(get_package_share_directory(PKG), 'rviz', cfg)
        actions.append(Node(package='rviz2', executable='rviz2', name='rviz2', output='log',
                            arguments=['-d', cfg]))
    return actions


def generate_launch_description():
    return LaunchDescription(
        [DeclareLaunchArgument(n, default_value=d, description=h) for n, d, h in ARGS]
        + [OpaqueFunction(function=_setup)])
