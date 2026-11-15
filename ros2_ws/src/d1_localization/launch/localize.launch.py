"""Load a saved 2D map and place the D1 in it.

  ros2 launch d1_localization localize.launch.py                     # robot at map (0,0,0) = scan start
  ros2 launch d1_localization localize.launch.py x:=3.0 y:=1.5 yaw:=1.57
  ros2 launch d1_localization localize.launch.py map:=/path/to/other/map.yaml

Needs agibot_d1_bridge running. Since develop the bridge broadcasts odom->base_link (and odom->base_footprint)
itself from integrated body velocity + IMU yaw, so the EKF is OFF by default. To use the EKF instead, start the
bridge with publish_tf:=false publish_footprint_tf:=false and launch this with use_ekf:=true.
"""
import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue

DEFAULT_MAP = 'metacam_lite_2026-11-14'


def _maps_dir():
    """<repo>/maps. Env D1_MAPS_DIR wins; otherwise walk up from this file (works with
    `colcon build --symlink-install`, where the installed launch file links back into the repo)."""
    if os.environ.get('D1_MAPS_DIR'):
        return os.environ['D1_MAPS_DIR']
    here = os.path.dirname(os.path.realpath(__file__))
    while here != os.path.dirname(here):
        if os.path.isdir(os.path.join(here, 'maps')):
            return os.path.join(here, 'maps')
        here = os.path.dirname(here)
    return ''


def generate_launch_description():
    share = get_package_share_directory('d1_localization')
    default_map = os.path.join(_maps_dir(), DEFAULT_MAP, 'map.yaml')
    lc = LaunchConfiguration
    return LaunchDescription([
        DeclareLaunchArgument('map', default_value=default_map, description='path to map.yaml'),
        DeclareLaunchArgument('x', default_value='0.0', description='start x in map frame [m]'),
        DeclareLaunchArgument('y', default_value='0.0', description='start y in map frame [m]'),
        DeclareLaunchArgument('yaw', default_value='0.0', description='start heading [rad]'),
        DeclareLaunchArgument('use_ekf', default_value='false',
                              description='fuse /odom + /imu into odom->base_link; needs bridge publish_tf:=false (else two TF sources)'),
        DeclareLaunchArgument('rviz', default_value='true'),
        Node(package='nav2_map_server', executable='map_server', name='map_server',
             parameters=[{'yaml_filename': lc('map')}]),
        Node(package='nav2_lifecycle_manager', executable='lifecycle_manager', name='lm_map',
             parameters=[{'autostart': True, 'node_names': ['map_server']}]),
        # the bridge stamps /imu with frame imu_link; assume it sits at base_link
        Node(package='tf2_ros', executable='static_transform_publisher', name='base_to_imu',
             arguments=['--frame-id', 'base_link', '--child-frame-id', 'imu_link']),
        Node(package='robot_localization', executable='ekf_node', name='ekf_filter_node',
             parameters=[os.path.join(share, 'config', 'ekf.yaml')],
             condition=IfCondition(lc('use_ekf'))),
        Node(package='d1_localization', executable='initial_pose_localizer',
             name='initial_pose_localizer', output='screen',
             parameters=[{k: ParameterValue(lc(k), value_type=float) for k in ('x', 'y', 'yaw')}]),
        Node(package='rviz2', executable='rviz2',
             arguments=['-d', os.path.join(share, 'config', 'view.rviz')],
             condition=IfCondition(lc('rviz'))),
    ])
