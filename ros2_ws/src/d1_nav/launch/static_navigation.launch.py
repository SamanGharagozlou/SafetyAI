"""Static navigation on a saved lidar map: map_server + hand-placed localization (map->odom) + Nav2 (global/local costmaps from the map).

  ros2 launch d1_nav static_navigation.launch.py map:=/path/map.yaml [x:=1.0 y:=2.0 yaw:=0.5]

Needs the robot bridge running (it provides /odom and odom->base_link / base_footprint TF and consumes /cmd_vel).
No live sensors: the costmaps are the saved map inflated by the robot footprint. Re-place the robot (RViz "2D Pose Estimate"
or the dashboard) whenever odometry has drifted; the pose can be changed at any time on /initialpose.
"""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration


def generate_launch_description():
    bringup = get_package_share_directory('nav2_bringup')
    params = os.path.join(get_package_share_directory('d1_nav'), 'params', 'nav2_params_static.yaml')
    loc = os.path.join(get_package_share_directory('d1_localization'), 'launch', 'localize.launch.py')
    c = LaunchConfiguration
    args = [DeclareLaunchArgument('map', description='path to the map.yaml'),
            DeclareLaunchArgument('x', default_value='0.0'), DeclareLaunchArgument('y', default_value='0.0'),
            DeclareLaunchArgument('yaw', default_value='0.0'), DeclareLaunchArgument('rviz', default_value='false')]
    localize = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(loc),
        launch_arguments={'map': c('map'), 'x': c('x'), 'y': c('y'), 'yaw': c('yaw'), 'rviz': c('rviz'),
                          'use_ekf': 'false'}.items())
    nav = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(bringup, 'launch', 'navigation_launch.py')),
        launch_arguments={'params_file': params, 'use_sim_time': 'false', 'autostart': 'true',
                          'use_composition': 'False', 'use_respawn': 'False'}.items())
    return LaunchDescription(args + [localize, nav])
