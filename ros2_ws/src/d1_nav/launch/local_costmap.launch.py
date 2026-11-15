"""Standalone Nav2 local costmap (no planner/controller) fed by /camera/points. Lifecycle is auto-started.

Needs: the bridge (TF odom->base_link), camera + depth node (/camera/points), camera TFs.
Typical: ros2 launch d1_depth depth_demo.launch.py rviz:=false   and   ros2 launch d1_nav local_costmap.launch.py
"""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    params = PathJoinSubstitution([FindPackageShare('d1_nav'), 'params', 'local_costmap.yaml'])
    # The executable fixes its own node name to /costmap/costmap, so key params and lifecycle to that.
    costmap = Node(package='nav2_costmap_2d', executable='nav2_costmap_2d', output='screen', parameters=[params])
    manager = Node(package='nav2_lifecycle_manager', executable='lifecycle_manager', name='lifecycle_manager_costmap',
                   output='screen',
                   parameters=[{'autostart': True, 'node_names': ['costmap/costmap'], 'bond_timeout': 0.0}])
    rviz = Node(package='rviz2', executable='rviz2', name='rviz2_costmap', output='log',
                arguments=['-d', PathJoinSubstitution([FindPackageShare('d1_nav'), 'rviz', 'costmap.rviz'])],
                condition=IfCondition(LaunchConfiguration('rviz')))
    return LaunchDescription([DeclareLaunchArgument('rviz', default_value='false'), costmap, manager, rviz])
