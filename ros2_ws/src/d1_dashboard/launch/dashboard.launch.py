from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, SetEnvironmentVariable
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    args = [('robot_ip', '172.24.1.41'), ('local_ip', '172.24.1.20'), ('port', '8080'), ('host', '127.0.0.1'), ('external_robot', 'false'), ('mono_slam', 'false'), ('lidar_url', ''), ('lidar_x', '0.0'), ('lidar_y', '0.0'), ('lidar_yaw', '0.0')]
    la = [DeclareLaunchArgument(n, default_value=d) for n, d in args]
    c = LaunchConfiguration
    node = Node(package='d1_dashboard', executable='dashboard', name='d1_dashboard', output='screen',
                arguments=['--robot-ip', c('robot_ip'), '--local-ip', c('local_ip'), '--port', c('port'), '--host', c('host'), '--external-robot', c('external_robot'), '--mono-slam', c('mono_slam'), '--lidar-url', c('lidar_url'), '--lidar-x', c('lidar_x'), '--lidar-y', c('lidar_y'),
                           '--lidar-yaw', c('lidar_yaw')])
    return LaunchDescription(la + [SetEnvironmentVariable('PYTHONNOUSERSITE', '1'), node])
