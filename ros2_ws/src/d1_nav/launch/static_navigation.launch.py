"""Static navigation on a saved lidar map: map_server + hand-placed localization (map->odom) + Nav2 (global/local costmaps from the map).

  ros2 launch d1_nav static_navigation.launch.py map:=/path/map.yaml [x:=1.0 y:=2.0 yaw:=0.5]

Needs the robot bridge running (it provides /odom and odom->base_link / base_footprint TF and consumes /cmd_vel).
No live sensors: the costmaps are the saved map inflated by the robot footprint. Re-place the robot (RViz "2D Pose Estimate"
or the dashboard) whenever odometry has drifted; the pose can be changed at any time on /initialpose.
"""
import os

import yaml
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, OpaqueFunction
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration

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
    loc = os.path.join(get_package_share_directory('d1_localization'), 'launch', 'localize.launch.py')
    c = LaunchConfiguration
    map_yaml = patched_map(c('map').perform(context))
    localize = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(loc),
        launch_arguments={'map': map_yaml, 'x': c('x'), 'y': c('y'), 'yaw': c('yaw'), 'rviz': c('rviz'),
                          'use_ekf': 'false'}.items())
    nav = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(bringup, 'launch', 'navigation_launch.py')),
        launch_arguments={'params_file': params, 'use_sim_time': 'false', 'autostart': 'true',
                          'use_composition': 'False', 'use_respawn': 'False'}.items())
    return [localize, nav]


def generate_launch_description():
    args = [DeclareLaunchArgument('map', description='path to the map.yaml'),
            DeclareLaunchArgument('x', default_value='0.0'), DeclareLaunchArgument('y', default_value='0.0'),
            DeclareLaunchArgument('yaw', default_value='0.0'), DeclareLaunchArgument('rviz', default_value='false')]
    return LaunchDescription(args + [OpaqueFunction(function=_setup)])
