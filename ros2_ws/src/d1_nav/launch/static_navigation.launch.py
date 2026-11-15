"""Static navigation on a saved lidar map: map_server + hand-placed localization (map->odom) + Nav2 (global/local costmaps from the map).

  ros2 launch d1_nav static_navigation.launch.py map:=/path/map.yaml [x:=1.0 y:=2.0 yaw:=0.5] [rviz:=true]

rviz:=true opens RViz2 with rviz/static_nav.rviz: lidar map, global/local costmap, plan, footprint, TF; tools "2D Pose Estimate" (= place the robot) and
"2D Goal Pose" (= navigate). Needs a display.

Needs the robot bridge running (it provides /odom and odom->base_link / base_footprint TF and consumes /cmd_vel).
No live sensors: the costmaps are the saved map inflated by the robot footprint. Re-place the robot (RViz "2D Pose Estimate"
or the dashboard) whenever odometry has drifted; the pose can be changed at any time on /initialpose.
"""
import os

import yaml
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, OpaqueFunction
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue

UNKNOWN_PIXEL = 205            # what ROS map_saver writes for "not scanned"


def patched_map(path):
    """Return a map.yaml path where "not scanned" pixels (205) load as UNKNOWN, not free.

    map_server (trinary mode) turns pixel 205 into p = (255-205)/255 = 0.196; with the usual free_thresh 0.25 that is below the
    threshold, so every unscanned cell becomes FREE and Nav2 plans through areas nobody ever saw. Lowering free_thresh to 0.19
    keeps them unknown (the global costmap does not allow planning through unknown space). A patched copy goes to /tmp; the
    original file is untouched. Maps without 205-valued pixels are returned unchanged.
    """
    meta = yaml.safe_load(open(path))
    if str(meta.get('mode', 'trinary')) != 'trinary' or int(meta.get('negate', 0)) != 0:
        return path
    img = meta['image'] if os.path.isabs(meta['image']) else os.path.join(os.path.dirname(os.path.abspath(path)), meta['image'])
    with open(img, 'rb') as f:
        data = f.read()
    if UNKNOWN_PIXEL not in set(data[-(int(len(data) * 0.9)):]):          # skip the header; look at the pixel bytes only
        return path
    unknown_p = (255 - UNKNOWN_PIXEL) / 255.0
    if float(meta.get('free_thresh', 0.25)) < unknown_p - 1e-6:
        return path
    meta['free_thresh'] = round(unknown_p - 0.006, 3)
    meta['image'] = img
    out_dir = '/tmp/d1_nav_maps'
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, os.path.basename(os.path.dirname(os.path.abspath(path))) + '.yaml')
    with open(out, 'w') as f:
        yaml.safe_dump(meta, f)
    return out


def _setup(context):
    bringup = get_package_share_directory('nav2_bringup')
    params = os.path.join(get_package_share_directory('d1_nav'), 'params', 'nav2_params_static.yaml')
    c = LaunchConfiguration
    map_yaml = patched_map(c('map').perform(context))
    if c('localizer').perform(context) == 'manual':
        # hand-placed pose + odometry only (d1_localization), no lidar
        loc = os.path.join(get_package_share_directory('d1_localization'), 'launch', 'localize.launch.py')
        localize = [IncludeLaunchDescription(
            PythonLaunchDescriptionSource(loc),
            launch_arguments={'map': map_yaml, 'x': c('x'), 'y': c('y'), 'yaw': c('yaw'), 'rviz': 'false',
                              'use_ekf': 'false'}.items())]
    else:
        # lidar localizer: placed pose + live MetaCam odometry -> map->odom; falls back to odometry-only when the lidar is absent
        pf = lambda n: ParameterValue(c(n), value_type=float)  # noqa: E731
        localize = [
            Node(package='nav2_map_server', executable='map_server', name='map_server', parameters=[{'yaml_filename': map_yaml}]),
            Node(package='nav2_lifecycle_manager', executable='lifecycle_manager', name='lm_map',
                 parameters=[{'autostart': True, 'node_names': ['map_server']}]),
            Node(package='tf2_ros', executable='static_transform_publisher', name='base_to_imu',
                 arguments=['--frame-id', 'base_link', '--child-frame-id', 'imu_link']),
            Node(package='d1_lidar', executable='lidar_localizer', name='lidar_localizer', output='screen',
                 parameters=[{'x': pf('x'), 'y': pf('y'), 'yaw': pf('yaw'), 'lidar_x': pf('lidar_x'), 'lidar_y': pf('lidar_y'),
                              'lidar_yaw': pf('lidar_yaw')}]),
        ]
    nav = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(bringup, 'launch', 'navigation_launch.py')),
        launch_arguments={'params_file': params, 'use_sim_time': 'false', 'autostart': 'true',
                          'use_composition': 'False', 'use_respawn': 'False'}.items())
    rviz = Node(package='rviz2', executable='rviz2', name='rviz2', output='log',
                arguments=['-d', os.path.join(get_package_share_directory('d1_nav'), 'rviz', 'static_nav.rviz')],
                condition=IfCondition(c('rviz')))
    return localize + [nav, rviz]


def generate_launch_description():
    args = [DeclareLaunchArgument('map', description='path to the map.yaml'),
            DeclareLaunchArgument('x', default_value='nan'), DeclareLaunchArgument('y', default_value='nan'),
            DeclareLaunchArgument('yaw', default_value='0.0'), DeclareLaunchArgument('rviz', default_value='false'),
            DeclareLaunchArgument('localizer', default_value='lidar', description='lidar | manual'),
            DeclareLaunchArgument('lidar_x', default_value='0.0'), DeclareLaunchArgument('lidar_y', default_value='0.0'),
            DeclareLaunchArgument('lidar_yaw', default_value='0.0')]
    return LaunchDescription(args + [OpaqueFunction(function=_setup)])
