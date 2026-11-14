from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, SetEnvironmentVariable
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    args = [('robot_ip', '172.24.1.41'), ('local_ip', '172.24.1.20'), ('port', '8080'), ('host', '127.0.0.1')]
    la = [DeclareLaunchArgument(n, default_value=d) for n, d in args]
    c = LaunchConfiguration
    node = Node(package='d1_dashboard', executable='dashboard', name='d1_dashboard', output='screen',
                arguments=['--robot-ip', c('robot_ip'), '--local-ip', c('local_ip'), '--port', c('port'), '--host', c('host')])
    return LaunchDescription(la + [SetEnvironmentVariable('PYTHONNOUSERSITE', '1'), node])
