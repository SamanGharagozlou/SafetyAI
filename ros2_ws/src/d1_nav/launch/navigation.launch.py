"""Nav2 navigation stack for the D1 (planner, MPPI controller, BT navigator, behaviours, velocity smoother).

No map_server/AMCL: RTAB-Map (mapping or localization mode) provides /map and the map->odom TF.
Nav2 outputs /cmd_vel (through the velocity smoother), which agibot_d1_bridge consumes.
Do not run together with local_costmap.launch.py (both would own /costmap).
"""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource


def generate_launch_description():
    bringup = get_package_share_directory('nav2_bringup')
    params = os.path.join(get_package_share_directory('d1_nav'), 'params', 'nav2_params.yaml')
    nav = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(bringup, 'launch', 'navigation_launch.py')),
        launch_arguments={'params_file': params, 'use_sim_time': 'false', 'autostart': 'true',
                          'use_composition': 'False', 'use_respawn': 'False'}.items())
    return LaunchDescription([nav])
